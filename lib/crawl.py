import os
import time
from collections import deque
from typing import Set, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from lib.url_utils import same_domain, norm_url, is_excluded_path
from lib.http_client import get_html, HTTP_TIMEOUT

# Prodleva mezi jednotlivými požadavky, aby crawler nezatěžoval provoz webu.
REQUEST_DELAY_MS = int(os.getenv("REQUEST_DELAY_MS", "250"))


def crawl_bfs(base_url: str, seed: Set[str], max_pages: int = 300, max_depth: int = 2) -> Tuple[Set[str], Set[str]]:
    """
    Jednoduchý BFS crawler:
    - prochází stránky stejné domény
    - do hloubky `max_depth`
    - maximálně `max_pages` stránek
    - vychází ze `seed` odkazů (např. homepage nebo sitemap)
    - vynechává stránky vyžadující přihlášení (košík, login, účet…)
    - mezi požadavky čeká `REQUEST_DELAY_MS`, aby zbytečně nezatěžoval server

    Vrací dvojici (nalezené_url, vyloučené_url).
    """
    found: Set[str] = set()
    excluded: Set[str] = set()
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

        if REQUEST_DELAY_MS:
            time.sleep(REQUEST_DELAY_MS / 1000)

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
            if absu in seen:
                continue
            seen.add(absu)
            if is_excluded_path(absu):
                excluded.add(absu)
                continue
            found.add(absu)
            if depth < max_depth:
                q.append((absu, depth + 1))

        if len(seen) >= max_pages:
            break

    return found, excluded
