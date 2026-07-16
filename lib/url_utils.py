from urllib.parse import urlparse, urldefrag

def norm_host(u: str) -> str | None:
    """Vrať normalizovaný hostname (bez www., lowercase)."""
    h = urlparse(u).hostname
    if not h:
        return None
    return h.lower().lstrip("www.")

def same_domain(a: str, b: str) -> bool:
    """Jsou obě URL na stejné doméně?"""
    ha, hb = norm_host(a), norm_host(b)
    return bool(ha and hb and ha == hb)

def norm_url(u: str) -> str:
    """Odstraň #kotvy a sjednoť trailing slash – proti duplicitám."""
    u, _ = urldefrag(u)
    return u[:-1] if u.endswith("/") else u
