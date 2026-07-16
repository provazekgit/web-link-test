from typing import List, Dict, Optional, Set
import time
import datetime
from urllib.parse import urlparse
import os
import json

from utils import fmt_ms
from lib.crawl import crawl_bfs
from lib.url_utils import same_domain, norm_url
from lib.http_client import head, get_html, HTTP_TIMEOUT


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

def collect_links(base_url: str, sitemap_url: Optional[str] = None, max_pages: int = 300) -> List[str]:
    """
    1) Pokud je k dispozici sitemap URL:
       - načte sitemapu
       - pokud je to index (obsahuje další sitemapy), načte i ty
       - vybere pouze URL ze stejné domény
    2) Jinak fallback: BFS crawl do hloubky 2 (homepage + interní odkazy)
    """
    base_url = norm_url(base_url)
    found: Set[str] = set([base_url])

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
            seed = crawl_bfs(base_url, seed=set(), max_pages=max_pages, max_depth=2)
        except Exception:
            seed = set()
        for u in seed:
            if same_domain(u, base_url):
                found.add(u)
            if len(found) >= max_pages:
                break

    return sorted(found)

def check_links(urls: List[str]) -> List[Dict[str, object]]:
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def probe(u: str) -> Dict[str, object]:
        start = time.time()
        status = -1
        err = ""
        try:
            # jednoduchý retry 2x
            for attempt in range(2):
                try:
                    r = head(u, timeout=HTTP_TIMEOUT)
                    status = r.status_code
                    if status in (405, 403):
                        r2 = get_html(u, timeout=HTTP_TIMEOUT)
                        status = r2.status_code
                    break
                except Exception as e:
                    err = str(e)[:300]
                    if attempt == 1:
                        raise
        except Exception:
            pass
        return {
            "url": u,
            "status": status,
            "ms": int((time.time() - start) * 1000),
            "error": err,
        }

    rows: List[Dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(probe, u) for u in urls]
        for f in as_completed(futures):
            rows.append(f.result())
    # pro stabilní výstup seřadíme podle URL
    rows.sort(key=lambda r: r["url"])
    return rows




def write_report(report_root: str, base_url: str, rows: List[Dict[str, object]]) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    host = (urlparse(base_url).hostname or "site").replace("/", "_")
    job_dir = os.path.join(report_root, f"{host}-{ts}")
    os.makedirs(job_dir, exist_ok=True)

    # JSON výstup (necháváme surové hodnoty pro další zpracování)
    with open(os.path.join(job_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"base_url": base_url, "generated_at": ts, "rows": rows}, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for r in rows if r["status"] != -1 and r["status"] < 400)
    total = len(rows)
    failed = total - ok_count

    # řádky tabulky
    rows_html = "\n".join(
        f"<tr class='{'fail' if (r['status']==-1 or r['status']>=400) else 'ok'}'>"
        f"<td><a href='{r['url']}' target='_blank'>{r['url']}</a></td>"
        f"<td class='status'>{r['status']} {'<span style=\"color:green\">OK</span>' if r['status'] == 200 else ''}</td>"
        f"<td><small style='color:#f2f3f4'>{fmt_ms(r['ms'], sep='  ')}</small></td>"
        f"<td>{r['error']}</td></tr>"
        for r in rows
    )

    # blok náhledů screenshotů (pokud existují)
    screens_block = ""
    try:
        screens_dir = os.path.join(job_dir, "screens")
        if os.path.isdir(screens_dir):
            items = []
            for name in sorted(os.listdir(screens_dir)):
                if name.lower().endswith(".png"):
                    rel = f"screens/{name}"
                    items.append(
                        f"<div style='display:inline-block;margin:6px;text-align:center'>"
                        f"<a href='{rel}' target='_blank'><img src='{rel}' style='height:180px;border:1px solid #ccc'></a>"
                        f"<div style='font-size:12px;color:#444'>{name}</div></div>"
                    )
            if items:
                screens_block = "<h2>Screenshots</h2>" + "".join(items)
    except Exception:
        pass

    # HTML report
    html = f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<title>Report {ts}</title>
<style>
body{{font-family:system-ui,Arial,sans-serif;max-width:1100px;margin:auto;padding:24px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:6px 8px}}
tr.fail{{background:#ffecec}} tr.ok{{background:#f5fff0}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:6px}}
.status span{{font-weight:600}}
</style></head><body>
<h1>Report</h1>
<p><b>Base:</b> <a href="{base_url}" target="_blank">{base_url}</a><br>
<b>Vygenerováno:</b> {ts}<br>
<b>Celkem URL:</b> {total} · <b>OK:</b> {ok_count} · <b>Fail:</b> {failed}</p>
<table>
  <thead><tr><th>URL</th><th>Status</th><th><small style='color:#fff'>Čas [ms]</small></th><th>Chyba</th></tr></thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
{screens_block}
</body></html>"""

    with open(os.path.join(job_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)

    return job_dir
