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
    # akční odkazy, které nejsou skutečné stránky (jen mění stav – přidání
    # do košíku, kupón, AJAX handler…), typicky WooCommerce a podobné e-shopy
    "add-to-cart", "wc-ajax", "remove_item", "undo_item",
    "apply_coupon", "remove_coupon",
    # Cloudflare si takhle přepisuje mailto: odkazy proti spamu – není to
    # skutečná stránka, jen dekódovací endpoint, který bez JS vrací 404
    "cdn-cgi/l/email-protection",
    # odkazy na odpověď na komentář ve WordPressu (nekonečně variant té samé stránky)
    "replytocom",
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

# ---------------------------------------------------------------
# Vyloučené přípony souborů (obrázky produktů, fonty, přímé odkazy na
# soubory apod.) – to nejsou stránky k testování/screenshotům.
# ---------------------------------------------------------------
_DEFAULT_EXCLUDE_EXTENSIONS = [
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif", ".tiff",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
]


def _load_exclude_extensions() -> tuple[str, ...]:
    """Načte seznam vyloučených přípon, lze přepsat/rozšířit přes .env
    (stejná logika jako u EXCLUDE_KEYWORDS – prázdná hodnota = výchozí seznam)."""
    override = (os.getenv("EXCLUDE_EXTENSIONS") or "").strip()
    exts = (
        [e.strip() for e in override.split(",") if e.strip()]
        if override
        else list(_DEFAULT_EXCLUDE_EXTENSIONS)
    )
    extra = os.getenv("EXTRA_EXCLUDE_EXTENSIONS", "")
    exts += [e.strip() for e in extra.split(",") if e.strip()]
    return tuple((e if e.startswith(".") else "." + e).lower() for e in exts)


EXCLUDE_EXTENSIONS = _load_exclude_extensions()


def is_excluded_path(u: str) -> bool:
    """Je URL stránka, kterou chceme z automatického testu vynechat –
    buď vyžaduje přihlášení (košík/pokladna/administrace), nebo to vůbec
    není skutečná stránka k testování (akční odkaz typu add-to-cart,
    Cloudflare email-protection, přímý odkaz na obrázek/soubor apod.)?
    Kontroluje cestu i query string, protože právě tam bývá
    např. `?add-to-cart=1234`."""
    p = urlparse(u)
    if EXCLUDE_EXTENSIONS and p.path.lower().endswith(EXCLUDE_EXTENSIONS):
        return True
    if not _EXCLUDE_RE:
        return False
    path_and_query = p.path + (("?" + p.query) if p.query else "")
    return bool(_EXCLUDE_RE.search(path_and_query))


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
