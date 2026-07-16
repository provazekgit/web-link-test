import os
import re
from urllib.parse import urljoin, urlparse, urlunparse
from playwright.sync_api import sync_playwright

from .sanitize import safe_fragment, stamp
from .pathing import win_longpath, ensure_dir

# čekací časy – můžeš ladit přes ENV
MAP_EXTRA_WAIT_MS = int(os.getenv("MAP_EXTRA_WAIT_MS", "2500"))   # dříve ~1200
LAZY_SCROLL_STEP  = int(os.getenv("LAZY_SCROLL_STEP", "700"))     # dříve 800
LAZY_SCROLL_PAUSE = int(os.getenv("LAZY_SCROLL_PAUSE_MS", "160")) # dříve 120

# Presety zařízení (viewport a emulace se dávají do new_context)
DEFAULT_DEVICES = {
    "Desktop Chrome": {
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        # "user_agent": "...",  # volitelné
    },
    "iPhone 13 Safari": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
        # "user_agent": "...",  # volitelné
    },
    # Příklady dalších presetů (volitelné):
    "Desktop Firefox": {
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
    "Desktop Edge": {
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
    "Desktop Opera": {
        "viewport": {"width": 1366, "height": 768},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
    },
    "Android Chrome (Pixel 7)": {
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 2.625,
        "is_mobile": True,
        "has_touch": True,
    },
    "Galaxy S23 Chrome": {
        "viewport": {"width": 412, "height": 915},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    "iPad Mini Safari": {
        "viewport": {"width": 768, "height": 1024},
        "device_scale_factor": 2,
        "is_mobile": True,
        "has_touch": True,
    },
    "macOS Safari (Desktop)": {
        "viewport": {"width": 1512, "height": 982},
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
    },
    "macOS Chrome (Desktop)": {
        "viewport": {"width": 1512, "height": 982},
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
    },
    "iPhone 13 Safari": {  # ponecháno i zde pro kompatibilitu
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
}

def _safe_host(url: str) -> str:
    host = urlparse(url).netloc or "site"
    return safe_fragment(host.replace(":", "_"))

def _canonical_url(u: str) -> str:
    """Kanonizace pro deduplikaci: lowercase host, bez query/fragmentu, bez index.*, bez trailing '/' mimo root."""
    p = urlparse(u)
    scheme = p.scheme or "https"
    netloc = (p.netloc or "").lower()
    path = p.path or "/"

    # Odstranění koncového lomítka (mimo root)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Zkrácení indexových souborů
    for idx in ("index.html", "index.htm", "index.php", "default.asp"):
        if path.lower().endswith("/" + idx):
            path = path[:-(len(idx) + 1)] or "/"
            break

    return urlunparse((scheme, netloc, path, "", "", ""))

def _mk_filename(page_url: str, device_name: str) -> str:
    """Vytvoří bezpečný název souboru podle URL a zařízení (včetně cesty)."""
    parsed = urlparse(page_url)
    host = safe_fragment(parsed.netloc.replace(":", "_"))
    path_part = safe_fragment(parsed.path.strip("/").replace("/", "_") or "root")
    dev = safe_fragment(device_name)
    return f"{host}_{path_part}_{dev}_{stamp()}.png"

def _dismiss_cookies(page):
    # nejběžnější texty tlačítek
    candidates = [
        "Přijmout vše", "Souhlasím", "Souhlasím se vším",
        "Accept all", "I agree", "Allow all",
        "Rozumím", "OK"
    ]
    for txt in candidates:
        try:
            page.get_by_role("button", name=re.compile(txt, re.I)).first.click(timeout=1500)
            return True
        except Exception:
            pass
    # fallback – některé lišty jsou <a> nebo custom elementy
    try:
        page.locator("text=/Souhlas|Přijmout|Accept/i").first.click(timeout=1500)
        return True
    except Exception:
        return False

def _wake_lazy_load(page):
    # Odeber loading="lazy" a data-src → src
    page.evaluate("""
    () => {
      document.querySelectorAll('img[loading="lazy"]').forEach(el => el.removeAttribute('loading'));
      document.querySelectorAll('img[data-src]').forEach(el => { if(!el.src) el.src = el.getAttribute('data-src'); });
    }
    """)
    # Projeď stránku, ať se triggery IntersectionObserveru chytnou
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    height = page.evaluate("() => document.body.scrollHeight")
    step = LAZY_SCROLL_STEP
    y = 0
    while y < height:
        page.evaluate(f"() => window.scrollTo(0, {y})")
        page.wait_for_timeout(LAZY_SCROLL_PAUSE)
        y += step
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)

def _wait_images_and_iframes(page, extra_map_wait_ms=MAP_EXTRA_WAIT_MS):
    # počkej na dokončení obrázků
    page.evaluate("""
    async () => {
      const imgs = Array.from(document.images || []);
      await Promise.all(imgs.map(img => {
        if (img.complete && img.naturalWidth > 0) return Promise.resolve();
        return new Promise(res => { img.onload = img.onerror = res; });
      }));
      if (document.fonts && document.fonts.ready) { try { await document.fonts.ready; } catch(e){} }
    }
    """)
    # „nakoukni“ do viditelnosti všech iframů (mapy často potřebují čas)
    frames = page.locator("iframe")
    count = frames.count()
    for i in range(min(count, 15)):  # bezpečnostní limit
        try:
            f = frames.nth(i)
            f.scroll_into_view_if_needed(timeout=500)
            # když to vypadá jako mapový iframe, dej mu víc času
            src = f.get_attribute("src") or ""
            if any(k in src for k in ["google.com/maps", "api.mapy.cz", "leaflet", "mapbox", "openstreetmap"]):
                page.wait_for_timeout(extra_map_wait_ms)
            else:
                page.wait_for_timeout(300)
        except Exception:
            pass

def _stabilize(page):
    # networkidle + malé „doznění“
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(300)


def _page_screenshot(page, out_dir: str, filename: str) -> str:
    ensure_dir(out_dir)
    raw_path = os.path.join(out_dir, filename)
    path = win_longpath(raw_path)
    print(f"[visual] screenshot → {raw_path}")
    page.screenshot(path=path, full_page=True)
    return raw_path

def _with_device(device_name: str) -> dict:
    return DEFAULT_DEVICES.get(device_name, DEFAULT_DEVICES["Desktop Chrome"])

def screenshot_pages(
    base_url: str,
    pages: list[str],
    out_dir: str,
    selected_devices: list[str] | None = None,
    timeout_ms: int = 40000,
):
    selected_devices = selected_devices or ["Desktop Chrome"]
    ensure_dir(out_dir)

    # 1) Vytvoř kompletní URL z base + rel a udělej kanonizaci
    resolved = [urljoin(base_url, rel) for rel in pages]
    unique_targets = list(dict.fromkeys(_canonical_url(u) for u in resolved))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for device in selected_devices:
                settings = _with_device(device)
                context = browser.new_context(
                    **settings,
                    locale="cs-CZ",
                    permissions=["geolocation"],
                    geolocation={"latitude": 50.087, "longitude": 14.421}  # Praha jako default
    )
                try:
                    page = context.new_page()
                    seen_final = set()  # finální URL po redirectech pro dané zařízení

                    for target in unique_targets:
                        print(f"[visual] goto → {target} [{device}]")
                        try:
                            page.goto(target, timeout=timeout_ms, wait_until="load")

                            # 1) zavři cookie lištu (pokud je)
                            _dismiss_cookies(page)

                            # 2) probuď lazy-load (zruší loading="lazy"/data-src + projede stránku)
                            _wake_lazy_load(page)

                            # 3) počkej na obrázky, fonty a iframy (mapy)
                            _wait_images_and_iframes(page)

                            # 4) krátká stabilizace (networkidle + doznění)
                            _stabilize(page)

                            # teprve teď ber finální URL (po případném redirectu)
                            final = _canonical_url(page.url)

                            # 2) Dedup po redirectu (když více vstupů skončí na stejné finální URL)
                            if final in seen_final:
                                print(f"[visual] skip dup → {final} [{device}]")
                                continue
                            seen_final.add(final)

                            fname = _mk_filename(final, device)
                            _page_screenshot(page, out_dir, fname)

                        except Exception as e:
                            print(f"[visual] ERROR while processing {device} {target}: {e!r}")
                finally:
                    context.close()
        finally:
            browser.close()

def screenshot_site(base_url: str, out_dir: str, devices: list[str] | None = None):
    # convenience wrapper pro domovskou stránku
    screenshot_pages(base_url=base_url, pages=["/"], out_dir=out_dir, selected_devices=devices)
