import gzip
from typing import Optional
import requests

HTTP_TIMEOUT = 12  # s
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0 LinkTester/1.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch_text(url: str, timeout: Optional[int] = None) -> str:
    """Stáhni text (umí i .gz)."""
    r = requests.get(url, timeout=timeout or HTTP_TIMEOUT, headers=HEADERS)
    r.raise_for_status()
    ct = (r.headers.get("Content-Type") or "").lower()
    if url.endswith(".gz") or "application/gzip" in ct or "x-gzip" in ct:
        return gzip.decompress(r.content).decode("utf-8", errors="replace")
    return r.text

def get_html(url: str, timeout: Optional[int] = None) -> requests.Response:
    """GET HTML (pro parsování)."""
    return requests.get(url, timeout=timeout or HTTP_TIMEOUT, headers=HEADERS, allow_redirects=True)

def head(url: str, timeout: Optional[int] = None) -> requests.Response:
    """HEAD (rychlý test statusu)."""
    return requests.head(url, timeout=timeout or HTTP_TIMEOUT, headers=HEADERS, allow_redirects=True)
