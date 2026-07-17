"""Základní SEO/indexační signály pro klientský report.

Nejde o plnohodnotný SEO audit, jen o pár rychlých, levných kontrol
(title, meta popis, H1, noindex, canonical + robots.txt/sitemap.xml),
které dají klientovi konkrétní, srozumitelné body k opravě.
"""
from typing import Dict, List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from lib.http_client import get_html, HTTP_TIMEOUT
from lib.url_utils import canonical_url as _norm_url

TITLE_MIN, TITLE_MAX = 10, 60
DESC_MIN, DESC_MAX = 50, 160


def analyze_page(url: str) -> Dict[str, object]:
    """Vytáhne základní SEO signály z jedné stránky."""
    result: Dict[str, object] = {
        "url": url, "title": None, "title_len": 0, "title_ok": False,
        "description": None, "description_len": 0, "description_ok": False,
        "h1_count": 0, "h1_ok": False,
        "noindex": False, "canonical": None, "canonical_matches": None, "error": "",
    }
    try:
        r = get_html(url, timeout=HTTP_TIMEOUT)
        if not r.ok:
            result["error"] = f"HTTP {r.status_code}"
            return result
        soup = BeautifulSoup(r.text, "html.parser")

        title_tag = soup.find("title")
        if title_tag and title_tag.text.strip():
            title = title_tag.text.strip()
            result["title"] = title
            result["title_len"] = len(title)
            result["title_ok"] = TITLE_MIN <= len(title) <= TITLE_MAX

        desc_tag = soup.find("meta", attrs={"name": "description"})
        desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
        if desc:
            result["description"] = desc
            result["description_len"] = len(desc)
            result["description_ok"] = DESC_MIN <= len(desc) <= DESC_MAX

        result["h1_count"] = len(soup.find_all("h1"))
        result["h1_ok"] = result["h1_count"] == 1

        robots_tag = soup.find("meta", attrs={"name": "robots"})
        if robots_tag and "noindex" in (robots_tag.get("content") or "").lower():
            result["noindex"] = True

        canonical_tag = soup.find("link", attrs={"rel": "canonical"})
        if canonical_tag and canonical_tag.get("href"):
            canonical_abs = urljoin(url, canonical_tag["href"].strip())
            result["canonical"] = canonical_abs
            # ukazuje canonical sama na sebe, nebo na jinou stránku (časté
            # zdroj duplicitního obsahu / omylem deindexované stránky)?
            result["canonical_matches"] = _norm_url(canonical_abs) == _norm_url(url)

    except Exception as e:
        result["error"] = str(e)[:200]
    return result


def analyze_pages(urls: List[str]) -> List[Dict[str, object]]:
    return [analyze_page(u) for u in urls]


def check_site_indexing(base_url: str) -> Dict[str, object]:
    """Zkontroluje robots.txt (neblokuje omylem celý web?) a existenci sitemap.xml."""
    info: Dict[str, object] = {
        "robots_ok": False,
        "robots_blocks_all": False,
        "sitemap_ok": False,
    }

    try:
        r = get_html(urljoin(base_url, "/robots.txt"), timeout=HTTP_TIMEOUT)
        if r.ok:
            info["robots_ok"] = True
            current_is_star = False
            for line in r.text.lower().splitlines():
                line = line.strip()
                if line.startswith("user-agent:"):
                    current_is_star = line.split(":", 1)[1].strip() == "*"
                elif line.startswith("disallow:") and current_is_star:
                    if line.split(":", 1)[1].strip() == "/":
                        info["robots_blocks_all"] = True
    except Exception:
        pass

    try:
        r = get_html(urljoin(base_url, "/sitemap.xml"), timeout=HTTP_TIMEOUT)
        info["sitemap_ok"] = bool(r.ok)
    except Exception:
        pass

    return info
