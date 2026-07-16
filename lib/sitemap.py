from typing import Set, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from lib.url_utils import same_domain
from lib.http_client import fetch_text

def _parse_sitemap_recursive(sm_url: str, base_url: str, out_set: Set[str],
                             visited: Set[str], max_sitemaps: int) -> None:
    """Projdi sitemap (vč. indexů) rekurzivně a přidej URL do out_set."""
    if len(visited) >= max_sitemaps or sm_url in visited:
        return
    visited.add(sm_url)

    try:
        xml_text = fetch_text(sm_url)
    except Exception:
        return

    xs = BeautifulSoup(xml_text, "xml")
    root = xs.find(True)
    if not root:
        return
    tag = root.name.lower()

    if tag == "sitemapindex":
        for loc in xs.find_all("loc"):
            u = (loc.text or "").strip()
            if same_domain(u, base_url):
                _parse_sitemap_recursive(u, base_url, out_set, visited, max_sitemaps)
    else:
        # urlset / fallback
        urls = xs.find_all("url")
        if urls:
            for uel in urls:
                lo = uel.find("loc")
                if lo:
                    u = (lo.text or "").strip()
                    if same_domain(u, base_url):
                        out_set.add(u)
        else:
            # fallback: zkus i <loc> roztroušené jinde
            for loc in xs.find_all("loc"):
                u = (loc.text or "").strip()
                if same_domain(u, base_url):
                    out_set.add(u)

def collect_from_sitemaps(base_url: str, sitemap_url: Optional[str],
                          out_set: Set[str], max_sitemaps: int) -> None:
    """Najdi sitemapy (zadané, /sitemap.xml, /sitemap_index.xml, robots.txt) a naplň out_set."""
    visited: Set[str] = set()

    def _try(sm: str):
        try:
            _parse_sitemap_recursive(sm, base_url, out_set, visited, max_sitemaps)
        except Exception:
            pass

    if sitemap_url:
        _try(sitemap_url)
    else:
        _try(urljoin(base_url, "/sitemap.xml"))
        _try(urljoin(base_url, "/sitemap_index.xml"))
        # robots.txt
        from lib.http_client import get_html
        try:
            rr = get_html(urljoin(base_url, "/robots.txt"))
            if rr.ok:
                for line in rr.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sm = line.split(":", 1)[1].strip()
                        _try(sm)
        except Exception:
            pass
