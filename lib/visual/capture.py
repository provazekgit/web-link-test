import os
import re
import time
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

from .sanitize import safe_fragment, stamp
from .pathing import win_longpath, ensure_dir
from lib.url_utils import canonical_url as _canonical_url

# čekací časy – můžeš ladit přes ENV
MAP_EXTRA_WAIT_MS = int(os.getenv("MAP_EXTRA_WAIT_MS", "2500"))   # dříve ~1200
LAZY_SCROLL_STEP  = int(os.getenv("LAZY_SCROLL_STEP", "700"))     # dříve 800
LAZY_SCROLL_PAUSE = int(os.getenv("LAZY_SCROLL_PAUSE_MS", "160")) # dříve 120

# Prodleva mezi jednotlivými navštívenými stránkami/zařízeními, aby test
# zbytečně nezatěžoval provoz testovaného webu.
REQUEST_DELAY_MS = int(os.getenv("REQUEST_DELAY_MS", "250"))

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
}

def _safe_host(url: str) -> str:
    host = urlparse(url).netloc or "site"
    return safe_fragment(host.replace(":", "_"))

def _mk_filename(page_url: str, device_name: str) -> str:
    """Vytvoří bezpečný název souboru podle URL a zařízení (včetně cesty)."""
    parsed = urlparse(page_url)
    host = safe_fragment(parsed.netloc.replace(":", "_"))
    path_part = safe_fragment(parsed.path.strip("/").replace("/", "_") or "root")
    dev = safe_fragment(device_name)
    return f"{host}_{path_part}_{dev}_{stamp()}.png"

# ---------------------------------------------------------------
# Skrývání cookie lišt (CMS-agnostické – funguje na WP i mimo WP)
# ---------------------------------------------------------------
# Selektory nejběžnějších cookie-consent řešení (WP pluginy i obecné,
# celosvětově používané nástroje), aby lišta nebyla vidět na screenshotu.
_COOKIE_BANNER_SELECTORS = [
    # WordPress pluginy
    "#cookie-law-info-bar", ".cli-modal-backdrop", "#cookie-notice", ".cookie-notice-container",
    "#moove_gdpr_cookie_info_bar", ".moove-gdpr-info-bar-container",
    ".cmplz-cookiebanner", ".cmplz-manage-consent-container", "#cmplz-cookiebanner-container",
    "#complianz", "#complianz-consent-modal",
    "#borlabs-cookie-box", ".BorlabsCookie", "#BorlabsCookieBox",
    ".wpcc-container", "#gdpr-cookie-message",
    # Obecná / mezinárodní řešení (fungují i mimo WP)
    "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay",
    "#onetrust-banner-sdk", "#onetrust-consent-sdk", ".ot-sdk-container",
    ".cc-window", ".cc-banner", ".cc-revoke", "#cookieConsent", ".cookieconsent",
    "#usercentrics-root", "#usercentrics-cmp-ui",
    ".osano-cm-window", ".osano-cm-dialog",
    "#truste-consent-track", "#trustarc-banner-container",
    "#qc-cmp2-container", ".qc-cmp2-container",
    "#CookieyesBanner", ".cky-consent-container",
    "#termly-code-snippet-support",
    "iubenda-cs-banner", "#iubenda-cs-banner",
    "[data-testid='cookie-banner']",
    "#cookie-bar", ".cookie-bar", ".cookie-banner", "#cookiebanner", ".cookie-alert",
]

_COOKIE_HIDE_CSS = ", ".join(_COOKIE_BANNER_SELECTORS) + """ {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}
html, body {
    overflow: auto !important;
}
"""

# JS heuristika jako záchranná síť pro neznámé/vlastní cookie lišty:
# skryje pevně/lepivě umístěné panely s textem o cookies/GDPR.
_COOKIE_HEURISTIC_JS = r"""
() => {
  const rx = /cookie|souhlas|gdpr|osobn(í|i)ch? údaj|zásady ochrany/i;
  const nodes = document.querySelectorAll('body *');
  for (const el of nodes) {
    try {
      const style = window.getComputedStyle(el);
      if ((style.position === 'fixed' || style.position === 'sticky') &&
          el.offsetHeight > 0 && el.offsetHeight < 600 &&
          rx.test(el.innerText || '')) {
        el.style.setProperty('display', 'none', 'important');
      }
    } catch (e) { /* ignore */ }
  }
}
"""

def _inject_cookie_hider(context):
    """Vloží CSS, které skryje známé cookie lišty ještě před vykreslením stránky."""
    context.add_init_script(
        "(() => {"
        "const css = " + repr(_COOKIE_HIDE_CSS) + ";"
        "const apply = () => {"
        "  const style = document.createElement('style');"
        "  style.setAttribute('data-linktest', 'cookie-hider');"
        "  style.textContent = css;"
        "  (document.head || document.documentElement).appendChild(style);"
        "};"
        "if (document.head) { apply(); } else { document.addEventListener('DOMContentLoaded', apply); }"
        "})();"
    )

def _hide_cookie_banners_runtime(page):
    """Doplňková JS heuristika pro cookie lišty, které selektory nepokryly."""
    try:
        page.evaluate(_COOKIE_HEURISTIC_JS)
    except Exception:
        pass

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
    """Udělá screenshoty zadaných stránek pro vybraná zařízení.

    Vrací seznam záznamů {"url", "device", "file"} popisujících uložené
    snímky – používá se pro seskupení náhledů v klientském reportu.
    """
    selected_devices = selected_devices or ["Desktop Chrome"]
    ensure_dir(out_dir)

    # 1) Vytvoř kompletní URL z base + rel a udělej kanonizaci
    resolved = [urljoin(base_url, rel) for rel in pages]
    unique_targets = list(dict.fromkeys(_canonical_url(u) for u in resolved))

    manifest: list[dict] = []

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
                _inject_cookie_hider(context)
                try:
                    page = context.new_page()
                    seen_final = set()  # finální URL po redirectech pro dané zařízení

                    for target in unique_targets:
                        if REQUEST_DELAY_MS:
                            time.sleep(REQUEST_DELAY_MS / 1000)
                        print(f"[visual] goto → {target} [{device}]")
                        try:
                            page.goto(target, timeout=timeout_ms, wait_until="load")

                            # 1) probuď lazy-load (zruší loading="lazy"/data-src + projede stránku)
                            _wake_lazy_load(page)

                            # 2) počkej na obrázky, fonty a iframy (mapy)
                            _wait_images_and_iframes(page)

                            # 3) krátká stabilizace (networkidle + doznění)
                            _stabilize(page)

                            # 4) doplňková heuristika pro neznámé cookie lišty (CSS injekce
                            #    z _inject_cookie_hider už běží od začátku, tohle je jen záchranná síť)
                            _hide_cookie_banners_runtime(page)

                            # teprve teď ber finální URL (po případném redirectu)
                            final = _canonical_url(page.url)

                            # 2) Dedup po redirectu (když více vstupů skončí na stejné finální URL)
                            if final in seen_final:
                                print(f"[visual] skip dup → {final} [{device}]")
                                continue
                            seen_final.add(final)

                            fname = _mk_filename(final, device)
                            _page_screenshot(page, out_dir, fname)
                            manifest.append({"url": final, "device": device, "file": fname})

                        except Exception as e:
                            print(f"[visual] ERROR while processing {device} {target}: {e!r}")
                finally:
                    context.close()
        finally:
            browser.close()

    return manifest

def screenshot_site(base_url: str, out_dir: str, devices: list[str] | None = None):
    # convenience wrapper pro domovskou stránku
    return screenshot_pages(base_url=base_url, pages=["/"], out_dir=out_dir, selected_devices=devices)
