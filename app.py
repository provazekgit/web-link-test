# app.py
import os
import time
import traceback
from flask import Flask, render_template, request, send_from_directory
from dotenv import load_dotenv
from utils import requires_auth, fmt_duration
import scanner
from lib.url_utils import canonical_url as _canonical_url
from pdf_render import html_to_pdf
from urllib.parse import urlparse, urljoin

# ---------------------------------------------------------------
# Pomocné funkce
# ---------------------------------------------------------------

def _count_pngs(folder: str) -> int:
    """Spočítá počet screenshotů (PNG) ve složce."""
    try:
        return sum(1 for f in os.listdir(folder) if f.lower().endswith(".png"))
    except Exception:
        return 0

def _with_scheme(u: str) -> str:
    """Doplní https:// pokud chybí."""
    if not u:
        return u
    return u if urlparse(u).scheme else f"https://{u}"

# ---------------------------------------------------------------
# Konfigurace a app init
# ---------------------------------------------------------------

load_dotenv()

# Odhady časů – lze měnit přes .env
CHECK_PER_PAGE_SEC = int(os.getenv("CHECK_PER_PAGE_SEC", "3"))
SHOT_PER_ITEM_SEC = int(os.getenv("SHOT_PER_ITEM_SEC", "8"))
MAP_EXTRA_WAIT_MS = int(os.getenv("MAP_EXTRA_WAIT_MS", "2500"))

# Výstupní složka pro reporty
REPORTS_ROOT = os.path.abspath(
    os.getenv("REPORTS_DIR", os.path.join(os.path.dirname(__file__), "reports"))
)
os.makedirs(REPORTS_ROOT, exist_ok=True)

app = Flask(__name__)

# ---------------------------------------------------------------
# DOMOVSKÁ STRÁNKA
# ---------------------------------------------------------------

@app.route("/")
@requires_auth
def index():
    # pošleme prázdný result, aby Jinja měla co číst (nebude padat na 'result is undefined')
    return render_template(
    "index.html",
    reports_root=REPORTS_ROOT,
    result=None,  # na GET neposíláme nic → šablona si to ošetří
    )


# ---------------------------------------------------------------
# SPUŠTĚNÍ TESTU
# ---------------------------------------------------------------

@app.post("/run")
@requires_auth
def run():
    started_at = time.time()

    base_url = request.form.get("base_url", "").strip()
    sitemap = request.form.get("sitemap_url", "").strip() or None

    if not base_url:
        return "Zadej Base URL", 400

    base_url = _with_scheme(base_url)
    if sitemap:
        sitemap = _with_scheme(sitemap)

    # --- 1) Získání a testování odkazů (stránky vyžadující přihlášení
    #         se automaticky vynechávají – košík, login, účet, admin…) ---
    urls, excluded_urls = scanner.collect_links(base_url, sitemap)
    rows = scanner.check_links(urls)
    tested_urls = list({row["url"] for row in rows})
    pages_count = len(tested_urls)

    # --- 2) Výpočet odhadu času (základ) ---
    estimate_sec = pages_count * CHECK_PER_PAGE_SEC
    screens_manifest = []
    top_seen = set()

    # --- 3) Založ finální složku reportu a screenshoty ukládej rovnou tam ---
    job_dir = scanner.make_job_dir(REPORTS_ROOT, base_url)
    screens_dir = os.path.join(job_dir, "screens")

    try:
        from lib.visual import screenshot_pages

        top_pages = tested_urls[:5]
        top_seen = {_canonical_url(u) for u in top_pages}
        auto_devices = [
            "Desktop Chrome", "Desktop Firefox", "Desktop Edge", "Desktop Opera",
            "iPhone 13 Safari", "Android Chrome (Pixel 7)",
            "macOS Safari (Desktop)", "macOS Chrome (Desktop)", "Galaxy S23 Chrome"
        ]

        # Přičti do odhadu
        estimate_sec += len(top_pages) * len(auto_devices) * (
            SHOT_PER_ITEM_SEC + MAP_EXTRA_WAIT_MS / 1000
        )

        print(f"[screenshots:auto] start → {top_pages}")
        manifest = screenshot_pages(
            base_url=base_url,
            pages=top_pages,
            out_dir=screens_dir,
            selected_devices=auto_devices,
        )
        screens_manifest.extend(manifest or [])
        print("[screenshots:auto] done")
    except Exception as e:
        print(f"[screenshots:auto] přeskočeno: {e}")
        traceback.print_exc()

    # --- 4) Uživatelské screenshoty (z formuláře) ---
    try:
        do_screens = request.form.get("screenshots_enabled") == "1"
        raw = (request.form.get("screenshot_pages", "") or "").strip()
        devices = request.form.getlist("devices")

        if do_screens and raw and devices:
            requested = [ln.strip() for ln in raw.splitlines() if ln.strip()][:10]
            requested_abs = [urljoin(base_url, u) for u in requested]
            pages = [u for u in requested_abs if _canonical_url(u) not in top_seen]

            if not pages:
                print("[screenshots:user] skip → vše už pokryto auto screenshoty")
            else:
                from lib.visual import screenshot_pages

                # přičti do odhadu
                estimate_sec += len(pages) * len(devices) * (
                    SHOT_PER_ITEM_SEC + MAP_EXTRA_WAIT_MS / 1000
                )

                print(f"[screenshots:user] start → {pages} | devices={devices}")
                manifest = screenshot_pages(
                    base_url=base_url,
                    pages=pages,
                    out_dir=screens_dir,
                    selected_devices=devices,
                )
                screens_manifest.extend(manifest or [])
                print("[screenshots:user] done")
        else:
            print(f"[screenshots:user] skip → enabled={do_screens}, raw='{bool(raw)}', devices={devices}")
    except Exception as e:
        print(f"[screenshots:user] přeskočeno: {e}")
        traceback.print_exc()

    # --- 5) Zápis reportu (obsahuje i seskupené screenshoty a vyloučené stránky) ---
    duration_sec = time.time() - started_at
    scanner.write_report(
        job_dir,
        base_url,
        rows,
        excluded_urls=excluded_urls,
        screenshots=screens_manifest,
        duration_sec=duration_sec,
    )

    # --- 6) Vytvoření PDF z HTML reportu ---
    index_path = os.path.join(job_dir, "index.html")
    pdf_path = os.path.join(job_dir, "report.pdf")
    try:
        html_to_pdf(index_path, pdf_path)
    except Exception as e:
        print(f"[PDF] Nepodařilo se vytvořit PDF: {e}")

    # --- 7) Výsledky ---
    screens_count = _count_pngs(screens_dir)
    estimate_text = fmt_duration(estimate_sec)
    duration_text = fmt_duration(duration_sec)
    print(f"[info] Odhadovaný čas testu: {estimate_text} | skutečná doba: {duration_text}")

    rel = os.path.relpath(job_dir, REPORTS_ROOT).replace("\\", "/")
    report_url = f"/report/{rel}/index.html"

    return render_template(
        "index.html",
        reports_root=REPORTS_ROOT,
        result={
            "pages": pages_count,
            "screens": screens_count,
            "excluded": len(excluded_urls),
            "report_url": report_url,
            "estimate": estimate_text,
            "duration": duration_text,
        },
    )

# ---------------------------------------------------------------
# ZOBRAZENÍ REPORTŮ
# ---------------------------------------------------------------

@app.get("/report/<path:path>")
@requires_auth
def serve_report(path):
    return send_from_directory(REPORTS_ROOT, path)

# ---------------------------------------------------------------
# START APLIKACE
# ---------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=False)
