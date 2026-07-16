import re
from datetime import datetime

SAFE_CHARS_RE = re.compile(r'[^a-zA-Z0-9._()\-\u00C0-\u024F]')

def safe_fragment(s: str) -> str:
    s = (s or "").strip()
    s = SAFE_CHARS_RE.sub('_', s)
    s = s.rstrip(' .')
    return s[:80] or "x"

def stamp() -> str:
    # 2025-11-07_09-41-33
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
