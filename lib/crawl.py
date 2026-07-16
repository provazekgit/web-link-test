from collections import deque
from typing import Set
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from lib.url_utils import same_domain, norm_url
from lib.http_client import get_html, HTTP_TIMEOUT

def crawl_bfs(base_url: str, seed: Set[str], max_pages: int = 300, max_depth: int = 2) -> Set[str]:
    """
    Jednoduchý BFS crawler:
    - prochází stránky stejné domény
    - do hloubky `max_depth`
    - maximálně `max_pages` stránek
    - vychází ze `seed` odkazů (např. homepage nebo sitemap)
    """
    found: Set[str] = set()
    seen: Set[str] = set()
    q: deque[tuple[str, int]] = deque()

    start = norm_url(base_url)
    seen.add(start)
    q.append((start, 0))

    for s in (seed or []):
        s = norm_url(s)
        if same_domain(s, base_url) and s not in seen:
            seen.add(s)
            q.append((s, 1))

    while q and len(seen) < max_pages:
        url, depth = q.popleft()
        if depth > max_depth:
            continue

        try:
            r = get_html(url, timeout=HTTP_TIMEOUT)
            if not r.ok:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            continue

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith(("mailto:", "tel:", "#")):
                continue
            absu = norm_url(urljoin(url, href))
            if not same_domain(absu, base_url):
                continue
            if absu not in seen:
                seen.add(absu)
                found.add(absu)
                if depth < max_depth:
                    q.append((absu, depth + 1))

        if len(seen) >= max_pages:
            break

    return found
