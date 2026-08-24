from typing import List, Dict, Optional, Set, Tuple
import time
import datetime
from urllib.parse import urlparse
import os
import json

from utils import fmt_ms, fmt_duration
from lib.crawl import crawl_bfs
from lib.url_utils import same_domain, norm_url, is_excluded_path
from lib.http_client import head, get_html, HTTP_TIMEOUT
from lib.audit import build_findings, summarize_findings

# Kolik souběžných požadavků smí test posílat na testovaný web najednou
# a jaká prodleva se drží mezi jednotlivými požadavky – aby test web
# zbytečně nezatěžoval. Lze doladit přes .env.
MAX_CONCURRENT_REQUESTS = max(1, int(os.getenv("MAX_CONCURRENT_REQUESTS", "4")))
REQUEST_DELAY_MS = int(os.getenv("REQUEST_DELAY_MS", "250"))


def _parse_sitemap_xml(xml_text: str) -> List[str]:
    """Vytáhne <loc> z XML (funguje pro sitemap i sitemap index)."""
    import re
    locs = re.findall(r"<loc>(.*?)</loc>", xml_text, flags=re.IGNORECASE | re.DOTALL)
    return [l.strip() for l in locs if l.strip()]

def _load_url_text(url: str) -> str:
    """Načti text URL (neřeší gzip sofistikovaně – stačí pro sitemap)."""
    import requests
    r = requests.get(url, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def collect_links(base_url: str, sitemap_url: Optional[str] = None, max_pages: int = 300) -> Tuple[List[str], List[str]]:
    """
    1) Pokud je k dispozici sitemap URL:
       - načte sitemapu
       - pokud je to index (obsahuje další sitemapy), načte i ty
       - vybere pouze URL ze stejné domény
    2) Jinak fallback: BFS crawl do hloubky 2 (homepage + interní odkazy)

    Stránky vyžadující přihlášení (košík, login, účet, admin…) se do testu
    automaticky nezařazují – jen se vrátí zvlášť v `excluded`, aby o nich
    šlo klienta v reportu informovat.

    Vrací dvojici (urls_k_otestovani, vyloučené_urls).
    """
    base_url = norm_url(base_url)
    found: Set[str] = set([base_url])
    excluded: Set[str] = set()

    if sitemap_url:
        try:
            text = _load_url_text(sitemap_url)
            locs = _parse_sitemap_xml(text)
            # Je to index? Pokud mezi locs jsou další sitemapy, načti je.
            child_sitemaps = [l for l in locs if l.lower().endswith("sitemap.xml")]
            if child_sitemaps:
                for sm in child_sitemaps:
                    try:
                        t2 = _load_url_text(sm)
                        for u in _parse_sitemap_xml(t2):
                            u = norm_url(u)
                            if same_domain(u, base_url):
                                if is_excluded_path(u):
                                    excluded.add(u)
                                else:
                                    found.add(u)
                            if len(found) >= max_pages:
                                break
                    except Exception:
                        pass
                    if len(found) >= max_pages:
                        break
            else:
                # Rovnou URL stránky
                for u in locs:
                    u = norm_url(u)
                    if same_domain(u, base_url):
                        if is_excluded_path(u):
                            excluded.add(u)
                        else:
                            found.add(u)
                    if len(found) >= max_pages:
                        break
        except Exception:
            # když sitemap selže, pokračujeme fallbackem
            pass

    # fallback nebo doplnění: BFS crawl
    if len(found) < 2:  # sitemap nic nepřinesla → crawl
        seed = set()
        try:
            crawled, crawled_excluded = crawl_bfs(base_url, seed=set(), max_pages=max_pages, max_depth=2)
        except Exception:
            crawled, crawled_excluded = set(), set()
        for u in crawled:
            if same_domain(u, base_url):
                found.add(u)
            if len(found) >= max_pages:
                break
        excluded |= crawled_excluded

    return sorted(found), sorted(excluded)

def check_links(urls: List[str], on_progress: Optional[callable] = None) -> List[Dict[str, object]]:
    """`on_progress(done, total)` se zavolá po každé dokončené URL – slouží
    k zobrazení průběhu/odhadu zbývajícího času na frontendu."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def probe(u: str) -> Dict[str, object]:
        # zdvořilá prodleva, aby souběžné dotazy web zbytečně nezahltily
        if REQUEST_DELAY_MS:
            time.sleep(REQUEST_DELAY_MS / 1000)

        start = time.time()
        status = -1
        err = ""
        try:
            r = head(u, timeout=HTTP_TIMEOUT)
            status = r.status_code
            if status in (405, 403):
                r = get_html(u, timeout=HTTP_TIMEOUT)
                status = r.status_code
        except Exception as e1:
            # HEAD selhal/timeoutnul – řada webů (hlavně za Cloudflare/WAF)
            # bere osamocené HEAD požadavky jako podezřelé a škrtí je nebo
            # je nechá viset. Zkus to ještě jednou jako běžný GET, než to
            # označíme za chybu – ušetří to i zbytečné dvojité čekání na
            # timeout té samé metody.
            err = str(e1)[:300]
            try:
                r = get_html(u, timeout=HTTP_TIMEOUT)
                status = r.status_code
                err = ""
            except Exception as e2:
                err = str(e2)[:300]
        return {
            "url": u,
            "status": status,
            "ms": int((time.time() - start) * 1000),
            "error": err,
        }

    rows: List[Dict[str, object]] = []
    total = len(urls)
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as ex:
        futures = [ex.submit(probe, u) for u in urls]
        for f in as_completed(futures):
            rows.append(f.result())
            if on_progress:
                on_progress(len(rows), total)
    # pro stabilní výstup seřadíme podle URL
    rows.sort(key=lambda r: r["url"])
    return rows


def make_job_dir(report_root: str, base_url: str) -> str:
    """Založí (a rovnou i podsložku screens/) unikátní složku pro jeden běh testu,
    aby do ní šlo ukládat screenshoty ještě předtím, než se zapíše report."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    host = (urlparse(base_url).hostname or "site").replace("/", "_")
    job_dir = os.path.join(report_root, f"{host}-{ts}")
    n = 1
    unique_dir = job_dir
    while os.path.exists(unique_dir):
        n += 1
        unique_dir = f"{job_dir}-{n}"
    os.makedirs(os.path.join(unique_dir, "screens"))
    return unique_dir


ALL_SECTIONS = {"status", "response", "note", "summary", "seo", "technical", "screens"}


def load_report(job_dir: str) -> Dict[str, object]:
    with open(os.path.join(job_dir, "report.json"), encoding="utf-8") as stream:
        return json.load(stream)


def _pages_gallery(data: Dict[str, object], use_thumbnails: bool = False) -> List[Dict[str, object]]:
    by_url: Dict[str, List[Dict[str, str]]] = {}
    order: List[str] = []
    for shot in data.get("screenshots", []):
        url = str(shot.get("url", ""))
        if url not in by_url:
            by_url[url] = []
            order.append(url)
        filename = str(shot.get("file", ""))
        thumb = f"thumbs/{os.path.splitext(filename)[0]}.webp" if use_thumbnails else f"screens/{filename}"
        by_url[url].append({"device": str(shot.get("device", "")), "file": filename, "rel": thumb, "full_rel": f"screens/{filename}"})
    return [{"url": url or None, "shots": by_url[url]} for url in order]


def report_context(
    data: Dict[str, object],
    sections: Optional[Set[str]] = None,
    is_published: bool = False,
    expires_display: Optional[str] = None,
    published_report_url: Optional[str] = None,
) -> Dict[str, object]:
    sections = set(sections or ALL_SECTIONS) & ALL_SECTIONS
    rows = list(data.get("rows", []))
    sorted_rows = sorted(rows, key=lambda row: (0 if (row.get("status") == -1 or int(row.get("status") or 0) >= 400) else 1, str(row.get("url", ""))))
    total = len(rows)
    ok_count = sum(1 for row in rows if row.get("status") != -1 and int(row.get("status") or 0) < 400)
    avg_ms = int(sum(int(row.get("ms") or 0) for row in rows) / total) if total else 0
    footer = data.get("footer") or {}
    generated_at = str(data.get("generated_at") or "")
    try:
        generated_display = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00")).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        generated_display = generated_at
    gallery = _pages_gallery(data, use_thumbnails=is_published)
    if is_published and published_report_url:
        public_base = published_report_url.rsplit("/", 1)[0]
        for page in gallery:
            for shot in page["shots"]:
                shot["full_url"] = f"{public_base}/{shot['full_rel']}"
    return {
        "base_url": data.get("base_url"), "generated_display": generated_display,
        "total": total, "ok_count": ok_count, "failed": total - ok_count, "avg_ms": avg_ms,
        "fmt_ms": fmt_ms, "rows": sorted_rows, "excluded_urls": data.get("excluded", []),
        "pages_gallery": gallery,
        "duration_text": fmt_duration(data.get("duration_sec")) if data.get("duration_sec") is not None else None,
        "seo_pages": data.get("seo_pages", []), "seo_site": data.get("seo_site", {}),
        "findings": data.get("findings", []), "summary": data.get("summary", {}),
        "footer_text": footer.get("text"), "footer_signature": footer.get("signature"),
        "footer_date": footer.get("date"), "footer_color": footer.get("color"),
        "sections": sections, "is_published": is_published, "expires_display": expires_display,
        "published_report_url": published_report_url, "simple_report_url": None if is_published else "jednoduchy-report.html",
        "report_id": data.get("report_id"),
    }


def render_report(
    data: Dict[str, object], output_path: str, sections: Optional[Set[str]] = None,
    is_published: bool = False, expires_display: Optional[str] = None,
    published_report_url: Optional[str] = None,
) -> str:
    from flask import render_template
    html = render_template("report.html", **report_context(data, sections, is_published, expires_display, published_report_url))
    with open(output_path, "w", encoding="utf-8") as stream:
        stream.write(html)
    return output_path


def write_report(
    job_dir: str, base_url: str, rows: List[Dict[str, object]],
    excluded_urls: Optional[List[str]] = None, screenshots: Optional[List[Dict[str, str]]] = None,
    duration_sec: Optional[float] = None, seo_pages: Optional[List[Dict[str, object]]] = None,
    seo_site: Optional[Dict[str, object]] = None, footer_text: Optional[str] = None,
    footer_signature: Optional[str] = None, footer_date: Optional[str] = None,
    footer_color: Optional[str] = None, client_email: Optional[str] = None,
) -> str:
    from flask import render_template

    if footer_date:
        try:
            footer_date = datetime.datetime.strptime(footer_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
    seo_pages, seo_site = seo_pages or [], seo_site or {}
    findings = build_findings(rows, seo_pages, seo_site)
    summary = summarize_findings(findings)
    summary["pages_count"] = len(rows)
    data: Dict[str, object] = {
        "schema_version": 2, "report_id": os.path.basename(job_dir), "base_url": base_url,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "duration_sec": duration_sec, "rows": rows, "excluded": excluded_urls or [],
        "screenshots": screenshots or [], "seo_pages": seo_pages, "seo_site": seo_site,
        "findings": findings, "summary": summary,
        "footer": {"text": footer_text, "signature": footer_signature, "date": footer_date, "color": footer_color},
        "client_email": client_email or "", "publications": [],
    }
    with open(os.path.join(job_dir, "report.json"), "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    render_report(data, os.path.join(job_dir, "index.html"))

    rows_sorted = report_context(data)["rows"]
    flat_screens = sorted([{"file": shot.get("file", ""), "rel": f"screens/{shot.get('file', '')}"} for shot in screenshots or []], key=lambda item: item["file"])
    simple_html = render_template(
        "report_simple.html", base_url=base_url, ts=data["generated_at"], total=len(rows),
        ok_count=report_context(data)["ok_count"], failed=report_context(data)["failed"], fmt_ms=fmt_ms,
        rows=rows_sorted, screenshots=flat_screens, footer_text=footer_text,
        footer_signature=footer_signature, footer_date=footer_date, footer_color=footer_color,
    )
    with open(os.path.join(job_dir, "jednoduchy-report.html"), "w", encoding="utf-8") as stream:
        stream.write(simple_html)
    return job_dir
