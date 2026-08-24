"""Deterministické technické a on-page SEO kontroly pro klientský report."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from lib.http_client import HEADERS, get_html, HTTP_TIMEOUT
from lib.url_utils import canonical_url as _norm_url

TITLE_MIN, TITLE_MAX = 10, 60
DESC_MIN, DESC_MAX = 50, 160


def analyze_page(url: str) -> Dict[str, object]:
    result: Dict[str, object] = {
        "url": url, "title": None, "title_len": 0, "title_ok": False,
        "description": None, "description_len": 0, "description_ok": False,
        "title_duplicate": False, "description_duplicate": False,
        "h1_count": 0, "h1_ok": False, "noindex": False,
        "canonical": None, "canonical_matches": None, "lang": "", "viewport": False,
        "images_total": 0, "images_missing_alt": 0, "mixed_content_count": 0,
        "open_graph": False, "structured_data": False, "error": "",
    }
    try:
        response = get_html(url, timeout=HTTP_TIMEOUT)
        if not response.ok:
            result["error"] = f"HTTP {response.status_code}"
            return result
        soup = BeautifulSoup(response.text, "html.parser")

        title_tag = soup.find("title")
        if title_tag and title_tag.text.strip():
            title = " ".join(title_tag.text.split())
            result.update(title=title, title_len=len(title), title_ok=TITLE_MIN <= len(title) <= TITLE_MAX)
        desc_tag = soup.find("meta", attrs={"name": lambda value: value and value.lower() == "description"})
        desc = (desc_tag.get("content") or "").strip() if desc_tag else ""
        if desc:
            desc = " ".join(desc.split())
            result.update(description=desc, description_len=len(desc), description_ok=DESC_MIN <= len(desc) <= DESC_MAX)

        result["h1_count"] = len(soup.find_all("h1"))
        result["h1_ok"] = result["h1_count"] == 1
        html_tag = soup.find("html")
        result["lang"] = (html_tag.get("lang") or "").strip() if html_tag else ""
        result["viewport"] = bool(soup.find("meta", attrs={"name": lambda v: v and v.lower() == "viewport"}))
        images = soup.find_all("img")
        result["images_total"] = len(images)
        result["images_missing_alt"] = sum(1 for image in images if not (image.get("alt") or "").strip())
        if urlparse(url).scheme == "https":
            insecure_resources = set()
            for tag in soup.find_all(True):
                for attribute in ("src", "href", "action", "poster"):
                    value = str(tag.get(attribute) or "").strip()
                    if value.lower().startswith("http://"):
                        insecure_resources.add((id(tag), attribute, value))
                for candidate in str(tag.get("srcset") or "").split(","):
                    value = candidate.strip().split(" ", 1)[0]
                    if value.lower().startswith("http://"):
                        insecure_resources.add((id(tag), "srcset", value))
            result["mixed_content_count"] = len(insecure_resources)
        result["open_graph"] = bool(soup.find("meta", attrs={"property": "og:title"}))
        result["structured_data"] = bool(soup.find("script", attrs={"type": lambda v: v and v.lower() == "application/ld+json"}))

        robots_tag = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "robots"})
        if robots_tag and "noindex" in (robots_tag.get("content") or "").lower():
            result["noindex"] = True
        canonical_tag = soup.find("link", attrs={"rel": lambda v: v and "canonical" in v})
        if canonical_tag and canonical_tag.get("href"):
            canonical_abs = urljoin(url, canonical_tag["href"].strip())
            result["canonical"] = canonical_abs
            result["canonical_matches"] = _norm_url(canonical_abs) == _norm_url(url)
    except Exception as exc:
        result["error"] = str(exc)[:200]
    return result


def _mark_duplicates(pages: List[Dict[str, object]]) -> None:
    for field, duplicate_field in (("title", "title_duplicate"), ("description", "description_duplicate")):
        groups: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for page in pages:
            value = " ".join(str(page.get(field) or "").casefold().split())
            if value:
                groups[value].append(page)
        for group in groups.values():
            if len(group) > 1:
                for page in group:
                    page[duplicate_field] = True


def analyze_pages(urls: List[str], max_workers: int = 4) -> List[Dict[str, object]]:
    if not urls:
        return []
    results: Dict[str, Dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(urls)))) as pool:
        futures = {pool.submit(analyze_page, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:
                results[url] = {"url": url, "error": str(exc)[:200]}
    ordered = [results[url] for url in urls]
    _mark_duplicates(ordered)
    return ordered


def check_site_indexing(base_url: str) -> Dict[str, object]:
    import requests
    info: Dict[str, object] = {
        "robots_ok": False, "robots_blocks_all": False, "sitemap_ok": False,
        "https_redirect": None, "security_headers": {},
    }
    try:
        response = get_html(urljoin(base_url, "/robots.txt"), timeout=HTTP_TIMEOUT)
        if response.ok:
            info["robots_ok"] = True
            current_is_star = False
            for line in response.text.lower().splitlines():
                line = line.strip()
                if line.startswith("user-agent:"):
                    current_is_star = line.split(":", 1)[1].strip() == "*"
                elif line.startswith("disallow:") and current_is_star and line.split(":", 1)[1].strip() == "/":
                    info["robots_blocks_all"] = True
    except Exception:
        pass
    try:
        info["sitemap_ok"] = bool(get_html(urljoin(base_url, "/sitemap.xml"), timeout=HTTP_TIMEOUT).ok)
    except Exception:
        pass
    try:
        response = get_html(base_url, timeout=HTTP_TIMEOUT)
        wanted = ("strict-transport-security", "content-security-policy", "x-content-type-options", "referrer-policy")
        info["security_headers"] = {name: bool(response.headers.get(name)) for name in wanted}
    except Exception:
        pass
    parsed = urlparse(base_url)
    if parsed.scheme == "https" and parsed.netloc:
        try:
            response = requests.get(parsed._replace(scheme="http").geturl(), timeout=HTTP_TIMEOUT, headers=HEADERS, allow_redirects=True)
            info["https_redirect"] = urlparse(response.url).scheme == "https"
        except Exception:
            # Neúspěšný test není důkaz, že přesměrování chybí.
            info["https_redirect"] = None
    return info
