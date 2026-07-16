import os
import re
from urllib.parse import urlparse, urldefrag

# ---------------------------------------------------------------
# Vyloučené stránky (košík, login, účet, admin apod.)
# ---------------------------------------------------------------
# Obecné klíčové výrazy nezávislé na konkrétním CMS – fungují jak pro
# WordPress, tak pro jiné projekty (custom weby, POPI apod.).
_DEFAULT_EXCLUDE_KEYWORDS = [
    # přihlášení / registrace
    "wp-login", "wp-admin", "wp-json",
    "login", "log-in", "prihlaseni", "přihlášení", "signin", "sign-in",
    "logout", "odhlaseni", "odhlášení", "signout", "sign-out",
    "register", "registrace", "sign-up", "signup",
    # účet / profil (jen konkrétní spojení – ne holé "account"/"profil",
    # aby to netrefilo běžné stránky typu "accounting-services")
    "muj-ucet", "můj-účet", "moje-ucet", "moje-účet", "my-account", "myaccount",
    "muj-profil", "můj-profil", "user-profile",
    "reset-password", "zapomenute-heslo", "zapomenuté-heslo",
    "lost-password", "forgot-password",
    # nákup / platba (e-shopy)
    "kosik", "košík", "cart", "checkout", "pokladna",
    "objednavka", "objednávka", "order-received",
    "wishlist", "seznam-prani",
    # administrace obecně
    "admin", "administrace",
]


def _load_exclude_keywords() -> list[str]:
    """Načte seznam vyloučených klíčových slov, lze přepsat/rozšířit přes .env.

    Prázdná hodnota EXCLUDE_KEYWORDS (např. jen odkomentovaný, ale nevyplněný
    řádek v .env) se bere jako "nenastaveno" – jinak by nechtěně vypnula
    veškerou ochranu přihlašovaných stránek.
    """
    override = (os.getenv("EXCLUDE_KEYWORDS") or "").strip()
    base = (
        [k.strip() for k in override.split(",") if k.strip()]
        if override
        else list(_DEFAULT_EXCLUDE_KEYWORDS)
    )
    extra = os.getenv("EXTRA_EXCLUDE_KEYWORDS", "")
    base += [k.strip() for k in extra.split(",") if k.strip()]
    return base


EXCLUDE_KEYWORDS = _load_exclude_keywords()
_EXCLUDE_RE = (
    # \b...\b, aby se "cart" netrefil do "cartridge" a "account" do "accounting"
    re.compile(r"\b(?:" + "|".join(re.escape(k) for k in EXCLUDE_KEYWORDS) + r")\b", re.IGNORECASE)
    if EXCLUDE_KEYWORDS
    else None
)


def is_excluded_path(u: str) -> bool:
    """Je URL stránka, kterou chceme z automatického testu vynechat
    (vyžaduje přihlášení, je to košík/pokladna, administrace apod.)?"""
    if not _EXCLUDE_RE:
        return False
    path = urlparse(u).path or ""
    return bool(_EXCLUDE_RE.search(path))


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

def canonical_url(u: str) -> str:
    """Kanonizace pro deduplikaci: lowercase host, bez query/fragmentu,
    bez index.*, bez trailing '/' mimo root."""
    from urllib.parse import urlunparse

    p = urlparse(u)
    scheme = p.scheme or "https"
    netloc = (p.netloc or "").lower()
    path = p.path or "/"

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    for idx in ("index.html", "index.htm", "index.php", "default.asp"):
        if path.lower().endswith("/" + idx):
            path = path[:-(len(idx) + 1)] or "/"
            break

    return urlunparse((scheme, netloc, path, "", "", ""))
