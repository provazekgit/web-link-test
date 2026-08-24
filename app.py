# app.py
import os
import re
import time
import uuid
import threading
import traceback
import webbrowser
import copy
import datetime as dt
import json
import shutil
from flask import Flask, render_template, request, send_from_directory, jsonify
from dotenv import load_dotenv
from utils import requires_auth, fmt_duration
import scanner
from lib.url_utils import canonical_url as _canonical_url
from pdf_render import html_to_pdf
from urllib.parse import urlparse, urljoin
from pathlib import Path
from lib.publishing import (
    BunnyConfig, BunnyStorageClient, make_email_draft, publication_times,
    safe_report_dir, sign_directory_url,
)

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

MAP_EXTRA_WAIT_MS = int(os.getenv("MAP_EXTRA_WAIT_MS", "2500"))

# Výstupní složka pro reporty
REPORTS_ROOT = os.path.abspath(
    os.getenv("REPORTS_DIR", os.path.join(os.path.dirname(__file__), "reports"))
)
os.makedirs(REPORTS_ROOT, exist_ok=True)

# Kde aplikace poběží a jestli se má po startu sama otevřít v prohlížeči
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
OPEN_BROWSER = os.getenv("OPEN_BROWSER", "1") == "1"

app = Flask(__name__)

# ---------------------------------------------------------------
# BĚH TESTU NA POZADÍ – stav úloh pro průběh/odhad času na frontendu
# ---------------------------------------------------------------

JOB_STEPS = 5  # 1 hledání stránek, 2 kontrola odkazů, 3 screenshoty, 4 SEO, 5 report/PDF
JOBS: dict = {}
JOBS_LOCK = threading.Lock()
PUBLISH_JOBS: dict = {}
PUBLISH_LOCK = threading.Lock()


def _job_init(job_id: str) -> None:
    now = time.time()
    with JOBS_LOCK:
        # ať se v paměti nehromadí staré dokončené úlohy donekonečna
        stale = [
            jid for jid, j in JOBS.items()
            if j.get("status") in ("done", "error") and now - j.get("started_at", now) > 3600
        ]
        for jid in stale:
            JOBS.pop(jid, None)
        JOBS[job_id] = {
            "status": "running",
            "step": 0,
            "step_total": JOB_STEPS,
            "phase": "Připravuji test…",
            "current": 0,
            "total": 0,
            "started_at": now,
            "phase_started_at": now,
            "result": None,
            "error": None,
        }


def _job_set(job_id: str, **kwargs) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)


def _job_phase(job_id: str, step: int, phase: str, total: int = 0) -> None:
    _job_set(job_id, step=step, phase=phase, current=0, total=total, phase_started_at=time.time())


def _job_get(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


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
    base_url = request.form.get("base_url", "").strip()
    sitemap = request.form.get("sitemap_url", "").strip() or None
    footer_text = request.form.get("footer_text", "").strip() or None
    footer_signature = request.form.get("footer_signature", "").strip() or None
    footer_date = request.form.get("footer_date", "").strip() or None
    footer_color = request.form.get("footer_color", "").strip()
    client_email = request.form.get("client_email", "").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", footer_color or ""):
        footer_color = None

    if not base_url:
        return jsonify({"error": "Zadej Base URL"}), 400
    if client_email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", client_email):
        return jsonify({"error": "E-mail klienta nemá platný formát."}), 400

    base_url = _with_scheme(base_url)
    if sitemap:
        sitemap = _with_scheme(sitemap)

    do_screens = request.form.get("screenshots_enabled") == "1"
    raw_screens = (request.form.get("screenshot_pages", "") or "").strip()
    devices = request.form.getlist("devices")

    job_id = uuid.uuid4().hex[:12]
    _job_init(job_id)

    threading.Thread(
        target=_run_job,
        args=(job_id, base_url, sitemap, do_screens, raw_screens, devices,
              footer_text, footer_signature, footer_date, footer_color, client_email),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


def _run_job(job_id, base_url, sitemap, do_screens, raw_screens, devices,
             footer_text, footer_signature, footer_date, footer_color, client_email):
    """Odpovídá bývalému synchronnímu tělu /run – běží ve vlastním vlákně
    a průběžně zapisuje stav do JOBS, aby ho mohl frontend odsledovat.

    `render_template` (uvnitř scanner.write_report) potřebuje aktivní Flask
    app kontext, který se mimo obsluhu HTTP požadavku (tj. v tomhle vlákně)
    sám nezaloží – proto celé tělo běží uvnitř `app.app_context()`.
    """
    with app.app_context():
        _run_job_body(job_id, base_url, sitemap, do_screens, raw_screens, devices,
                      footer_text, footer_signature, footer_date, footer_color, client_email)


def _run_job_body(job_id, base_url, sitemap, do_screens, raw_screens, devices,
                   footer_text, footer_signature, footer_date, footer_color, client_email):
    started_at = time.time()
    try:
        # --- 1) Získání odkazů (stránky vyžadující přihlášení se
        #         automaticky vynechávají – košík, login, účet, admin…) ---
        _job_phase(job_id, 1, "Hledám stránky webu…")
        urls, excluded_urls = scanner.collect_links(base_url, sitemap)

        # --- 2) Kontrola odkazů ---
        _job_phase(job_id, 2, f"Kontroluji odkazy (0/{len(urls)})…", total=len(urls))

        def on_link_progress(done, total):
            _job_set(job_id, current=done, total=total, phase=f"Kontroluji odkazy ({done}/{total})…")

        rows = scanner.check_links(urls, on_progress=on_link_progress)
        tested_urls = [row["url"] for row in rows]
        pages_count = len(tested_urls)

        screens_manifest = []
        successful_urls = [row["url"] for row in rows if row["status"] != -1 and row["status"] < 400]
        top_pages = successful_urls[:5]
        top_seen = {_canonical_url(u) for u in top_pages}

        # --- 3) Založ finální složku reportu a screenshoty ukládej rovnou tam ---
        job_dir = scanner.make_job_dir(REPORTS_ROOT, base_url)
        screens_dir = os.path.join(job_dir, "screens")

        auto_devices = [
            "Desktop Chrome", "Desktop Firefox", "Desktop Edge", "Desktop Opera",
            "iPhone 13 Safari", "Android Chrome (Pixel 7)",
            "macOS Safari (Desktop)", "macOS Chrome (Desktop)", "Galaxy S23 Chrome"
        ]
        try:
            from lib.visual import screenshot_pages

            auto_total = len(top_pages) * len(auto_devices)
            _job_phase(job_id, 3, f"Vytvářím screenshoty (0/{auto_total})…", total=auto_total)

            def on_shot_progress(done, total):
                _job_set(job_id, current=done, total=total, phase=f"Vytvářím screenshoty ({done}/{total})…")

            print(f"[screenshots:auto] start → {top_pages}")
            manifest = screenshot_pages(
                base_url=base_url,
                pages=top_pages,
                out_dir=screens_dir,
                selected_devices=auto_devices,
                on_progress=on_shot_progress,
            )
            screens_manifest.extend(manifest or [])
            print("[screenshots:auto] done")
        except Exception as e:
            print(f"[screenshots:auto] přeskočeno: {e}")
            traceback.print_exc()

        # --- 4) Uživatelské screenshoty (z formuláře) ---
        try:
            if do_screens and raw_screens and devices:
                requested = [ln.strip() for ln in raw_screens.splitlines() if ln.strip()][:10]
                requested_abs = [urljoin(base_url, u) for u in requested]
                pages = [u for u in requested_abs if _canonical_url(u) not in top_seen]

                if not pages:
                    print("[screenshots:user] skip → vše už pokryto auto screenshoty")
                else:
                    from lib.visual import screenshot_pages

                    user_total = len(pages) * len(devices)
                    _job_phase(job_id, 3, f"Vytvářím vlastní screenshoty (0/{user_total})…", total=user_total)

                    def on_shot_progress2(done, total):
                        _job_set(job_id, current=done, total=total, phase=f"Vytvářím vlastní screenshoty ({done}/{total})…")

                    print(f"[screenshots:user] start → {pages} | devices={devices}")
                    manifest = screenshot_pages(
                        base_url=base_url,
                        pages=pages,
                        out_dir=screens_dir,
                        selected_devices=devices,
                        on_progress=on_shot_progress2,
                    )
                    screens_manifest.extend(manifest or [])
                    print("[screenshots:user] done")
            else:
                print(f"[screenshots:user] skip → enabled={do_screens}, raw='{bool(raw_screens)}', devices={devices}")
        except Exception as e:
            print(f"[screenshots:user] přeskočeno: {e}")
            traceback.print_exc()

        # --- 5) SEO/indexační kontrola (title, meta popis, H1, noindex, canonical,
        #        robots.txt, sitemap.xml) – na stejných top stránkách jako screenshoty ---
        _job_phase(job_id, 4, "Kontroluji SEO a indexaci…")
        seo_pages, seo_site = [], {}
        try:
            from lib import seo

            seo_pages = seo.analyze_pages(successful_urls[:50], max_workers=scanner.MAX_CONCURRENT_REQUESTS)
            seo_site = seo.check_site_indexing(base_url)
        except Exception as e:
            print(f"[seo] přeskočeno: {e}")
            traceback.print_exc()

        # --- 6) Zápis reportu (obsahuje i seskupené screenshoty a vyloučené stránky) ---
        _job_phase(job_id, 5, "Generuji report a PDF…")
        duration_sec = time.time() - started_at
        scanner.write_report(
            job_dir,
            base_url,
            rows,
            excluded_urls=excluded_urls,
            screenshots=screens_manifest,
            duration_sec=duration_sec,
            seo_pages=seo_pages,
            seo_site=seo_site,
            footer_text=footer_text,
            footer_signature=footer_signature,
            footer_date=footer_date,
            footer_color=footer_color,
            client_email=client_email,
        )

        # --- 7) Vytvoření PDF z HTML reportu ---
        index_path = os.path.join(job_dir, "index.html")
        pdf_path = os.path.join(job_dir, "report.pdf")
        try:
            html_to_pdf(index_path, pdf_path)
        except Exception as e:
            print(f"[PDF] Nepodařilo se vytvořit PDF: {e}")

        # --- 8) Výsledky ---
        screens_count = _count_pngs(screens_dir)
        duration_text = fmt_duration(duration_sec)
        print(f"[info] Skutečná doba testu: {duration_text}")

        rel = os.path.relpath(job_dir, REPORTS_ROOT).replace("\\", "/")
        report_url = f"/report/{rel}/index.html"
        simple_report_url = f"/report/{rel}/jednoduchy-report.html"

        _job_set(
            job_id,
            status="done",
            step=JOB_STEPS,
            phase="Hotovo",
            current=1,
            total=1,
            result={
                "pages": pages_count,
                "screens": screens_count,
                "excluded": len(excluded_urls),
                "report_url": report_url,
                "simple_report_url": simple_report_url,
                "duration": duration_text,
                "report_id": rel,
            },
        )
    except Exception as e:
        traceback.print_exc()
        _job_set(job_id, status="error", phase="Chyba", error=str(e)[:500])


@app.get("/api/progress/<job_id>")
@requires_auth
def api_progress(job_id):
    job = _job_get(job_id)
    if not job:
        return jsonify({"error": "Úloha nenalezena (možná byl server mezitím restartován)."}), 404

    now = time.time()
    current = job.get("current") or 0
    total = job.get("total") or 0
    eta_text = None
    if job.get("status") == "running" and current > 0 and total > current:
        phase_elapsed = now - job.get("phase_started_at", now)
        rate = phase_elapsed / current
        eta_text = fmt_duration(rate * (total - current))

    return jsonify({
        "status": job.get("status"),
        "step": job.get("step", 0),
        "step_total": job.get("step_total", JOB_STEPS),
        "phase": job.get("phase"),
        "current": current,
        "total": total,
        "elapsed": fmt_duration(now - job.get("started_at", now)),
        "eta": eta_text,
        "result": job.get("result"),
        "error": job.get("error"),
    })


# ---------------------------------------------------------------
# PUBLIKACE KLIENTSKÉ VERZE
# ---------------------------------------------------------------

def _save_report_data(report_dir: Path, data: dict) -> None:
    target = report_dir / "report.json"
    temporary = report_dir / "report.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, target)


def _sanitize_public_data(data: dict, sections: set[str]) -> dict:
    public_data = copy.deepcopy(data)
    public_data.pop("client_email", None)
    public_data.pop("publications", None)
    public_data["selected_sections"] = sorted(sections)
    row_fields = {"url"}
    if "status" in sections:
        row_fields.add("status")
    if "response" in sections:
        row_fields.add("ms")
    if "note" in sections:
        row_fields.add("error")
    public_data["rows"] = [{key: value for key, value in row.items() if key in row_fields} for row in public_data.get("rows", [])]
    if "summary" not in sections:
        public_data["findings"] = []
        public_data["summary"] = {"pages_count": len(public_data["rows"])}

    seo_fields = {"url", "title", "title_len", "title_ok", "title_duplicate", "description", "description_len", "description_ok", "description_duplicate", "h1_count", "h1_ok", "noindex", "canonical", "canonical_matches", "error"}
    technical_fields = {"url", "lang", "viewport", "images_total", "images_missing_alt", "mixed_content_count", "open_graph", "structured_data", "error"}
    page_fields = set()
    if "seo" in sections:
        page_fields |= seo_fields
    if "technical" in sections:
        page_fields |= technical_fields
    public_data["seo_pages"] = [
        {key: value for key, value in page.items() if key in page_fields}
        for page in public_data.get("seo_pages", [])
    ] if page_fields else []
    site_fields = set()
    if "seo" in sections:
        site_fields |= {"robots_ok", "robots_blocks_all", "sitemap_ok"}
    if "technical" in sections:
        site_fields |= {"https_redirect", "security_headers"}
    public_data["seo_site"] = {key: value for key, value in public_data.get("seo_site", {}).items() if key in site_fields}
    return public_data


def _create_public_bundle(report_dir: Path, stage_dir: Path, data: dict, sections: set[str], report_url: str, expires_display: str) -> dict:
    from PIL import Image

    stage_dir.mkdir(parents=True, exist_ok=False)
    public_data = _sanitize_public_data(data, sections)

    if "screens" in sections:
        (stage_dir / "screens").mkdir()
        (stage_dir / "thumbs").mkdir()
        valid_shots = []
        for shot in public_data.get("screenshots", []):
            filename = str(shot.get("file", ""))
            source = report_dir / "screens" / filename
            if not source.is_file() or source.suffix.lower() != ".png":
                continue
            shutil.copy2(source, stage_dir / "screens" / filename)
            thumb_path = stage_dir / "thumbs" / f"{source.stem}.webp"
            with Image.open(source) as image:
                image.thumbnail((420, 900))
                image.save(thumb_path, "WEBP", quality=78, method=4)
            valid_shots.append(shot)
        public_data["screenshots"] = valid_shots
    else:
        public_data["screenshots"] = []

    scanner.render_report(
        public_data, str(stage_dir / "index.html"), sections=sections,
        is_published=True, expires_display=expires_display, published_report_url=report_url,
    )
    html_to_pdf(str(stage_dir / "index.html"), str(stage_dir / "report.pdf"))
    with (stage_dir / "report.json").open("w", encoding="utf-8") as stream:
        json.dump(public_data, stream, ensure_ascii=False, indent=2)
    return public_data


def _run_publish_job(publish_job_id: str, report_id: str, sections: set[str]) -> None:
    with app.app_context():
        stage_dir: Path | None = None
        remote_dir = ""
        client: BunnyStorageClient | None = None
        try:
            config = BunnyConfig.from_env()
            report_dir = safe_report_dir(REPORTS_ROOT, report_id)
            data = scanner.load_report(str(report_dir))
            if int(data.get("schema_version") or 0) < 2:
                raise ValueError("Tento starší report nelze publikovat. Spusťte nový test.")

            publication_id = uuid.uuid4().hex
            remote_dir = f"{config.remote_prefix}/{publication_id}"
            published_at, expires_at = publication_times(config)
            expires_display = expires_at.astimezone().strftime("%d.%m.%Y %H:%M")
            report_url = sign_directory_url(
                config.cdn_base_url, config.token_key, remote_dir, "index.html", int(expires_at.timestamp())
            )
            stage_dir = report_dir / "published" / publication_id
            with PUBLISH_LOCK:
                PUBLISH_JOBS[publish_job_id].update(phase="Připravuji klientskou verzi…")
            public_data = _create_public_bundle(report_dir, stage_dir, data, sections, report_url, expires_display)

            client = BunnyStorageClient(config)
            def on_upload(done: int, total: int) -> None:
                with PUBLISH_LOCK:
                    PUBLISH_JOBS[publish_job_id].update(
                        current=done, total=total, phase=f"Nahrávám na Bunny ({done}/{total})…"
                    )
            client.upload_tree(stage_dir, remote_dir, on_progress=on_upload)

            delete_after = published_at + dt.timedelta(days=config.retention_days)
            entry = {
                "id": publication_id, "remote_dir": remote_dir,
                "local_dir": f"published/{publication_id}",
                "published_at": published_at.isoformat(), "expires_at": expires_at.isoformat(),
                "delete_after": delete_after.isoformat(), "sections": sorted(sections),
            }
            data.setdefault("publications", []).append(entry)
            _save_report_data(report_dir, data)
            email = make_email_draft(
                str(data.get("base_url", "")), str(data.get("client_email", "")),
                dict(data.get("summary") or {}), report_url, expires_display,
            )
            result = {
                "report_url": report_url, "expires_at": expires_at.isoformat(),
                "expires_display": expires_display, "email": email,
            }
            with PUBLISH_LOCK:
                PUBLISH_JOBS[publish_job_id].update(status="done", phase="Publikováno", result=result)
            try:
                cleanup_expired_publications()
            except Exception:
                # Publikace je už úspěšná; případný úklid se zopakuje při
                # dalším startu nebo v denním retenčním běhu.
                traceback.print_exc()
        except Exception as exc:
            traceback.print_exc()
            if client and remote_dir:
                try:
                    client.delete_directory(remote_dir)
                except Exception:
                    pass
            if stage_dir and stage_dir.is_dir():
                shutil.rmtree(stage_dir, ignore_errors=True)
            with PUBLISH_LOCK:
                PUBLISH_JOBS[publish_job_id].update(status="error", phase="Publikace selhala", error=str(exc)[:500])


@app.post("/api/publish/<report_id>")
@requires_auth
def api_publish(report_id):
    try:
        safe_report_dir(REPORTS_ROOT, report_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    config = BunnyConfig.from_env()
    if config.missing():
        return jsonify({"error": "Publikace není nakonfigurovaná. V .env chybí: " + ", ".join(config.missing())}), 400
    payload = request.get_json(silent=True) or {}
    requested = payload.get("sections") or []
    if not isinstance(requested, list):
        return jsonify({"error": "Neplatný seznam sekcí."}), 400
    sections = set(str(item) for item in requested) & scanner.ALL_SECTIONS
    if not sections:
        return jsonify({"error": "Vyberte alespoň jednu sekci reportu."}), 400
    publish_job_id = uuid.uuid4().hex[:16]
    with PUBLISH_LOCK:
        PUBLISH_JOBS[publish_job_id] = {
            "status": "running", "phase": "Připravuji publikaci…", "current": 0,
            "total": 0, "result": None, "error": None, "started_at": time.time(),
        }
    threading.Thread(target=_run_publish_job, args=(publish_job_id, report_id, sections), daemon=True).start()
    return jsonify({"publish_job_id": publish_job_id})


@app.get("/api/publish-progress/<publish_job_id>")
@requires_auth
def api_publish_progress(publish_job_id):
    with PUBLISH_LOCK:
        job = dict(PUBLISH_JOBS.get(publish_job_id) or {})
    if not job:
        return jsonify({"error": "Publikační úloha nebyla nalezena."}), 404
    return jsonify(job)


def cleanup_expired_publications(now: dt.datetime | None = None) -> int:
    """Smaže pouze publikované balíčky po retenci; při chybě je ponechá pro další pokus."""
    config = BunnyConfig.from_env()
    if config.missing():
        return 0
    now = now or dt.datetime.now(dt.timezone.utc)
    client = BunnyStorageClient(config)
    removed = 0
    for report_dir in Path(REPORTS_ROOT).iterdir():
        report_json = report_dir / "report.json"
        if not report_dir.is_dir() or not report_json.is_file():
            continue
        try:
            data = scanner.load_report(str(report_dir))
        except Exception:
            continue
        publications = list(data.get("publications") or [])
        kept = []
        changed = False
        for publication in publications:
            try:
                delete_after = dt.datetime.fromisoformat(str(publication["delete_after"]))
                if delete_after.tzinfo is None:
                    delete_after = delete_after.replace(tzinfo=dt.timezone.utc)
                if delete_after > now:
                    kept.append(publication)
                    continue
                client.delete_directory(str(publication["remote_dir"]))
                local_dir = (report_dir / str(publication.get("local_dir", ""))).resolve()
                published_root = (report_dir / "published").resolve()
                if local_dir.parent == published_root and local_dir.is_dir():
                    shutil.rmtree(local_dir)
                removed += 1
                changed = True
            except Exception:
                kept.append(publication)
        if changed:
            data["publications"] = kept
            _save_report_data(report_dir, data)
    return removed


def _retention_loop() -> None:
    while True:
        try:
            cleanup_expired_publications()
        except Exception:
            traceback.print_exc()
        time.sleep(24 * 60 * 60)

# ---------------------------------------------------------------
# ZOBRAZENÍ REPORTŮ
# ---------------------------------------------------------------

@app.get("/report/<path:path>")
@requires_auth
def serve_report(path):
    return send_from_directory(REPORTS_ROOT, path)

# ---------------------------------------------------------------
# START APLIKACE – kontrola prostředí, hláška a auto-otevření prohlížeče
# ---------------------------------------------------------------

def _check_playwright_browser() -> bool:
    """Ověří, že jde reálně spustit Chromium (screenshoty/PDF to potřebují)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            launch_options = {"headless": True}
            browser_channel = os.getenv("PDF_BROWSER_CHANNEL", "").strip()
            if browser_channel:
                launch_options["channel"] = browser_channel
            b = p.chromium.launch(**launch_options)
            b.close()
        return True
    except Exception:
        return False

def _check_writable(folder: str) -> bool:
    try:
        probe = os.path.join(folder, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False

def _print_startup_report() -> bool:
    """Zkontroluje .env/prostředí, vypíše přehlednou hlášku a vrátí,
    jestli je aplikace v pořádku ke spuštění (chybí-li povinné údaje, ne)."""
    from utils import AUTH_OFF, BASIC_USER, BASIC_PASS

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    warnings = []
    problems = []

    if not os.path.exists(env_path):
        warnings.append(
            ".env nenalezen – běží se jen s výchozími hodnotami. "
            "Zkopíruj .env.example na .env a uprav si podle sebe."
        )

    if AUTH_OFF:
        warnings.append("AUTH_OFF=1 – přihlašování je VYPNUTÉ (jen pro lokální vývoj!).")
    elif not BASIC_USER or not BASIC_PASS:
        warnings.append(
            "V .env chybí BASIC_USER a/nebo BASIC_PASS – přihlašování je proto "
            "VYPNUTÉ (kdokoliv se dostane dovnitř). Nastav je v .env, až budeš "
            "chtít nástroj chránit."
        )

    reports_ok = _check_writable(REPORTS_ROOT)
    if not reports_ok:
        problems.append(f"Do složky pro reporty se nedá zapisovat: {REPORTS_ROOT}")

    print("Ověřuji prostředí (Playwright prohlížeč)…")
    browser_ok = _check_playwright_browser()
    if not browser_ok:
        warnings.append(
            "Playwright prohlížeč není připravený – screenshoty a PDF export nebudou fungovat. "
            "Spusť: python -m playwright install"
        )

    url = f"http://{HOST}:{PORT}/"
    line = "=" * 60
    print(line)
    print("  Web Link Test")
    print(line)
    print(f"  Adresa:            {url}")
    if AUTH_OFF:
        auth_line = "vypnuto (AUTH_OFF=1)"
    elif BASIC_USER and BASIC_PASS:
        auth_line = f"zapnuto (uživatel: {BASIC_USER})"
    else:
        auth_line = "vypnuto (v .env chybí BASIC_USER/BASIC_PASS)"
    print(f"  Přihlášení:        {auth_line}")
    print(f"  Reporty se ukládají do: {REPORTS_ROOT} {'✅' if reports_ok else '❌'}")
    print(f"  Screenshoty / PDF: {'✅ připraveno' if browser_ok else '❌ chybí Playwright prohlížeč'}")
    bunny_missing = BunnyConfig.from_env().missing()
    print(f"  Bunny publikace:   {'✅ připraveno' if not bunny_missing else '⚠️ nenastaveno (lokální reporty fungují)'}")
    print(
        f"  Šetrnost k webu:   max {scanner.MAX_CONCURRENT_REQUESTS} souběžných "
        f"požadavků, {scanner.REQUEST_DELAY_MS} ms prodleva mezi nimi"
    )
    print(line)

    if warnings:
        print("\nUpozornění:")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if problems:
        print("\nBez tohohle to nepůjde spustit:")
        for p in problems:
            print(f"  ❌ {p}")
        print()
        return False

    print()
    return True

def _open_browser_when_ready(url: str, timeout: float = 15):
    """Počká, až server začne odpovídat, a pak otevře výchozí prohlížeč."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    ready = False
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            ready = True
            break
        except urllib.error.HTTPError:
            # i chybová odpověď (např. 401 Basic Auth) znamená, že server běží
            ready = True
            break
        except Exception:
            time.sleep(0.2)
    if ready:
        try:
            webbrowser.open(url)
        except Exception:
            pass

if __name__ == "__main__":
    ok = _print_startup_report()
    if not ok:
        raise SystemExit(1)

    if OPEN_BROWSER:
        threading.Thread(
            target=_open_browser_when_ready,
            args=(f"http://{HOST}:{PORT}/",),
            daemon=True,
        ).start()

    threading.Thread(target=_retention_loop, daemon=True).start()

    app.run(host=HOST, port=PORT, debug=False)
