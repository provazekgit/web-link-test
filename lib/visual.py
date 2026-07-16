# lib/visual.py
from __future__ import annotations
import os
import traceback
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse, urljoin

# -- Lazy import Playwrightu --
def _lazy_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except Exception:
        return None


# ---- Seznam zařízení a prohlížečů ----
AVAILABLE_DEVICES = {
    # 💻 Desktop
    "Desktop Chrome": ("chromium", None),
    "Desktop Firefox": ("firefox", None),
    "Desktop Edge": ("chromium", None),
    "Desktop Opera": ("chromium", None),
    "macOS Safari (Desktop)": ("webkit", "__MAC_SAFARI__"),
    "macOS Chrome (Desktop)": ("chromium", "__MAC_CHROME__"),

    # 📱 Mobile / Tablet
    "iPhone 13 Safari": ("webkit", "iPhone 13"),
    "iPad Mini Safari": ("webkit", "iPad Mini"),
    "Android Chrome (Pixel 7)": ("chromium", "Pixel 7"),
    "Galaxy S23 Chrome": ("chromium", "__GALAXY_FALLBACK__"),
}


def get_available_devices() -> List[str]:
    return list(AVAILABLE_DEVICES.keys())


# ---- Pomocné funkce ----
def _safe_host(url: str) -> str:
    return (urlparse(url).hostname or "site").replace("/", "_")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sanitize_path_fragment(s: str) -> str:
    s = (s or "").strip().replace(" ", "_").replace("/", "_").replace("\\", "_")
    return s or "home"


# ---- Kontext zařízení ----
def _create_context(p, browser, device_name: str, pw_device: Optional[str]):
    """Vytvoří Playwright context s emulací vybraného zařízení."""
    try:
        if pw_device and pw_device not in ["__GALAXY_FALLBACK__", "__MAC_SAFARI__", "__MAC_CHROME__"]:
            return browser.new_context(**p.devices[pw_device])

        if pw_device == "__GALAXY_FALLBACK__":
            # Galaxy fallback
            return browser.new_context(
                viewport={"width": 412, "height": 915},
                device_scale_factor=3,
                is_mobile=True,
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; SM-S918B) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/119.0.0.0 Mobile Safari/537.36"
                ),
            )

        if pw_device == "__MAC_SAFARI__":
            return browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=2,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15"
                ),
            )

        if pw_device == "__MAC_CHROME__":
            return browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=2,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/119.0.0.0 Safari/537.36"
                ),
            )

        # fallback desktop
        return browser.new_context(viewport={"width": 1366, "height": 768})

    except Exception as e:
        print(f"⚠️ Kontext pro {device_name} selhal: {e}")
        traceback.print_exc()
        return None


# ---- Screenshot jedné stránky ----
def _screenshot_single(p, base_url: str, page_path: str, out_dir: str, device_name: str):
    engine_name, pw_device = AVAILABLE_DEVICES[device_name]
    engine = getattr(p, engine_name)
    browser = engine.launch(headless=True)
    try:
        context = _create_context(p, browser, device_name, pw_device)
        if not context:
            return None

        page = context.new_page()
        target_url = urljoin(base_url, page_path)
        page.goto(target_url, timeout=40000, wait_until="load")

        fname = f"{_safe_host(target_url)}_{_sanitize_path_fragment(device_name)}_{_stamp()}.png"
        path = os.path.join(out_dir, fname)
        page.screenshot(path=path, full_page=True)
        print(f"✅ Screenshot uložen: {fname}")
        return path

    except Exception as e:
        print(f"❌ Chyba u {device_name} ({page_path}): {e}")
        traceback.print_exc()
        return None
    finally:
        browser.close()


# ---- Screenshot víc stránek ----
def screenshot_pages(base_url: str, pages: List[str], out_dir: str, selected_devices: Optional[List[str]] = None):
    sp = _lazy_playwright()
    if sp is None:
        print("⚠️ Playwright není dostupný, screenshoty přeskočeny.")
        return []

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    devices = selected_devices or ["Desktop Chrome"]

    with sp() as p:
        for device_name in devices:
            if device_name not in AVAILABLE_DEVICES:
                print(f"⏭️ Neznámé zařízení '{device_name}', přeskakuji.")
                continue

            for rel_url in pages:
                path = _screenshot_single(p, base_url, rel_url, out_dir, device_name)
                if path:
                    saved.append(path)

    print(f"🧾 Celkem uložených screenshotů: {len(saved)}")
    return saved
