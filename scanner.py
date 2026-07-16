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

def check_links(urls: List[str]) -> List[Dict[str, object]]:
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
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as ex:
        futures = [ex.submit(probe, u) for u in urls]
        for f in as_completed(futures):
            rows.append(f.result())
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


def write_report(
    job_dir: str,
    base_url: str,
    rows: List[Dict[str, object]],
    excluded_urls: Optional[List[str]] = None,
    screenshots: Optional[List[Dict[str, str]]] = None,
    duration_sec: Optional[float] = None,
    seo_pages: Optional[List[Dict[str, object]]] = None,
    seo_site: Optional[Dict[str, object]] = None,
) -> str:
    """Zapíše report.json a index.html do už existující `job_dir`
    (viz `make_job_dir`) – screenshoty do ní ukládá volající ještě předtím."""
    from flask import render_template

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    excluded_urls = excluded_urls or []
    screenshots = screenshots or []
    seo_pages = seo_pages or []
    seo_site = seo_site or {}

    # JSON výstup (necháváme surové hodnoty pro další zpracování)
    with open(os.path.join(job_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "base_url": base_url,
                "generated_at": ts,
                "rows": rows,
                "excluded": excluded_urls,
                "screenshots": screenshots,
                "seo_pages": seo_pages,
                "seo_site": seo_site,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    total = len(rows)
    ok_count = sum(1 for r in rows if r["status"] != -1 and r["status"] < 400)
    failed = total - ok_count
    avg_ms = int(sum(r["ms"] for r in rows) / total) if total else 0

    # nejdřív chyby (nejdůležitější pro klienta), pak OK podle URL
    sorted_rows = sorted(
        rows,
        key=lambda r: (0 if (r["status"] == -1 or r["status"] >= 400) else 1, r["url"]),
    )

    # seskupení screenshotů podle stránky (z manifestu, který vrací screenshot_pages)
    pages_gallery: "list[dict]" = []
    by_url: "dict[str, list[dict]]" = {}
    order: List[str] = []
    for shot in screenshots:
        u = shot.get("url", "")
        if u not in by_url:
            by_url[u] = []
            order.append(u)
        by_url[u].append(
            {
                "device": shot.get("device", ""),
                "file": shot.get("file", ""),
                "rel": f"screens/{shot.get('file', '')}",
            }
        )
    for u in order:
        pages_gallery.append({"url": u, "shots": by_url[u]})

    # doplnění osiřelých souborů (kdyby manifest z nějakého důvodu chyběl)
    known_files = {s["file"] for shots in by_url.values() for s in shots}
    screens_dir = os.path.join(job_dir, "screens")
    if os.path.isdir(screens_dir):
        orphans = sorted(
            name for name in os.listdir(screens_dir)
            if name.lower().endswith(".png") and name not in known_files
        )
        if orphans:
            pages_gallery.append(
                {
                    "url": None,
                    "shots": [{"device": "", "file": n, "rel": f"screens/{n}"} for n in orphans],
                }
            )

    duration_text = fmt_duration(duration_sec) if duration_sec is not None else None

    generated_display = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")

    html = render_template(
        "report.html",
        base_url=base_url,
        generated_display=generated_display,
        total=total,
        ok_count=ok_count,
        failed=failed,
        avg_ms=avg_ms,
        fmt_ms=fmt_ms,
        rows=sorted_rows,
        excluded_urls=excluded_urls,
        pages_gallery=pages_gallery,
        duration_text=duration_text,
        seo_pages=seo_pages,
        seo_site=seo_site,
    )

    with open(os.path.join(job_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    return job_dir
