import datetime as dt
import hashlib
import hmac
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

import app as webapp
from lib.publishing import BunnyConfig, BunnyStorageClient, make_email_draft, safe_remote_path, safe_report_dir, sign_directory_url
from tests.fake_bunny_server import Handler


def config():
    return BunnyConfig("zone", "storage-secret", "storage.bunnycdn.com", "https://zone.b-cdn.net", "token-secret")


def test_directory_signature_has_expected_hmac_and_path():
    expires = 2_000_000_000
    url = sign_directory_url("https://zone.b-cdn.net", "token-secret", "reports/abc", "index.html", expires)
    allowed = "/reports/abc/"
    expected = hmac.new(b"token-secret", f"{allowed}{expires}token_path={allowed}".encode(), hashlib.sha256).digest()
    import base64
    token = "HS256-" + base64.urlsafe_b64encode(expected).decode().rstrip("=")
    assert f"bcdn_token={token}" in url
    assert f"token_path={quote(allowed, safe='')}" in url
    assert url.endswith("/reports/abc/index.html")


def test_safe_paths_reject_traversal(tmp_path):
    report = tmp_path / "valid-report"
    report.mkdir()
    assert safe_report_dir(str(tmp_path), "valid-report") == report.resolve()
    with pytest.raises(ValueError):
        safe_report_dir(str(tmp_path), "../outside")
    for invalid in ("", "/", "reports/../secret"):
        with pytest.raises(ValueError):
            safe_remote_path(invalid)


def test_safe_remote_path_accepts_generated_device_filename():
    remote = "web-link-test/report/screens/example_Chrome_(Pixel_7)_2026-08-24.png"
    assert safe_remote_path(remote) == remote


def test_bunny_url_encodes_parentheses_in_generated_filename():
    cfg = BunnyConfig(
        "zone", "storage-secret", "storage.bunnycdn.com",
        "https://zone.b-cdn.net", "token-secret", remote_prefix="web-link-test",
    )
    url = BunnyStorageClient(cfg, session=Session())._url(
        "web-link-test/report/screens/example_Chrome_(Pixel_7).png"
    )
    assert "Chrome_%28Pixel_7%29.png" in url


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


class Session:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [201])
        self.put_calls = []
        self.delete_calls = []

    def put(self, url, headers, data, timeout):
        self.put_calls.append((url, headers, data.read(), timeout))
        return Response(self.statuses.pop(0))

    def delete(self, url, headers, timeout):
        self.delete_calls.append((url, headers, timeout))
        return Response(200)


def test_bunny_upload_uses_checksum_and_mime(tmp_path):
    source = tmp_path / "index.html"
    source.write_bytes(b"hello")
    session = Session()
    BunnyStorageClient(config(), session=session).upload_file(source, "reports/abc/index.html")
    _, headers, body, _ = session.put_calls[0]
    assert body == b"hello"
    assert headers["Checksum"] == hashlib.sha256(b"hello").hexdigest().upper()
    assert headers["Content-Type"] == "text/html"
    assert "storage-secret" not in session.put_calls[0][0]


def test_bunny_upload_retries_transient_errors_but_not_invalid_key(tmp_path, monkeypatch):
    source = tmp_path / "report.json"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("lib.publishing.time.sleep", lambda _: None)
    retrying = Session([500, 429, 201])
    BunnyStorageClient(config(), session=retrying).upload_file(source, "reports/abc/report.json")
    assert len(retrying.put_calls) == 3

    rejected = Session([401, 201])
    with pytest.raises(RuntimeError, match="HTTP 401"):
        BunnyStorageClient(config(), session=rejected).upload_file(source, "reports/abc/report.json")
    assert len(rejected.put_calls) == 1


def test_real_http_upload_signed_download_and_delete(tmp_path):
    from http.server import ThreadingHTTPServer
    Handler.root = (tmp_path / "remote").resolve()
    Handler.root.mkdir()
    Handler.zone = "test-zone"
    Handler.access_key = "access"
    Handler.token_key = "token-key"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        cfg = BunnyConfig("test-zone", "access", f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}", "token-key")
        local = tmp_path / "bundle"
        local.mkdir()
        (local / "index.html").write_text("<h1>Klientský report</h1>", encoding="utf-8")
        client = BunnyStorageClient(cfg)
        client.upload_tree(local, "reports/integration")
        signed = sign_directory_url(cfg.cdn_base_url, cfg.token_key, "reports/integration", "index.html", 2_000_000_000)
        response = requests.get(signed, timeout=5)
        assert response.status_code == 200
        assert "Klientský report" in response.content.decode("utf-8")
        client.delete_directory("reports/integration")
        assert requests.get(signed, timeout=5).status_code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_delete_never_allows_publication_root():
    client = BunnyStorageClient(config(), session=Session())
    with pytest.raises(ValueError):
        client.delete_directory("reports")
    with pytest.raises(ValueError, match="mimo povolený"):
        client.delete_directory("other/report")
    with pytest.raises(ValueError, match="mimo povolený"):
        client.upload_file(Path(__file__), "other/report/file.txt")


def test_email_draft_variants():
    clean = make_email_draft("https://example.cz", "", {"pages_count": 8, "critical_count": 0, "recommended_count": 0}, "https://report", "1. 1. 2027")
    assert "neodhalila žádný kritický" in clean["body"]
    assert clean["recipient"] == ""
    problems = make_email_draft("https://example.cz", "a@b.cz", {"pages_count": 8, "critical_count": 2, "recommended_count": 3, "priorities": ["Rozbité odkazy"]}, "https://report", "1. 1. 2027")
    assert "2 kritické nálezy" in problems["short_description"]
    assert "Rozbité odkazy" in problems["body"]
    assert problems["subject"] == "Výsledky kontroly webu – example.cz"

    singular = make_email_draft("https://example.cz", "a@b.cz", {"pages_count": 1, "critical_count": 1, "recommended_count": 1}, "https://report", "1. 1. 2027")
    assert "1 kritický nález" in singular["short_description"]
    assert "Kontrola zahrnula 1 stránku" in singular["body"]


def test_public_bundle_excludes_email_and_creates_thumbnail(tmp_path, monkeypatch):
    from PIL import Image
    report_dir = tmp_path / "report"
    (report_dir / "screens").mkdir(parents=True)
    Image.new("RGB", (800, 1600), "white").save(report_dir / "screens" / "shot.png")
    data = {
        "schema_version": 2, "report_id": "report", "base_url": "https://example.cz",
        "generated_at": "2026-08-24T10:00:00+00:00", "duration_sec": 1,
        "rows": [], "excluded": [], "seo_pages": [], "seo_site": {}, "findings": [],
        "summary": {"pages_count": 0, "critical_count": 0, "recommended_count": 0},
        "screenshots": [{"url": "https://example.cz", "device": "Desktop", "file": "shot.png"}],
        "footer": {}, "client_email": "secret@example.cz", "publications": [],
    }
    monkeypatch.setattr(webapp, "html_to_pdf", lambda html, pdf: Path(pdf).write_bytes(b"%PDF-test"))
    stage = report_dir / "published" / "abc"
    progress = []
    with webapp.app.app_context():
        public = webapp._create_public_bundle(
            report_dir, stage, data, {"screens", "summary"},
            "https://signed/index.html", "1.1.2027",
            on_progress=lambda phase, current, total, percent: progress.append(
                (phase, current, total, percent)
            ),
        )
    assert "client_email" not in public
    assert (stage / "thumbs" / "shot.webp").is_file()
    public_json = json.loads((stage / "report.json").read_text(encoding="utf-8"))
    assert "secret@example.cz" not in json.dumps(public_json)
    assert "secret@example.cz" not in (stage / "index.html").read_text(encoding="utf-8")
    assert progress[0][3] == 3
    assert progress[-1][3] == 70
    assert any("náhled" in phase.lower() for phase, *_ in progress)
    assert [item[3] for item in progress] == sorted(item[3] for item in progress)


def test_publish_progress_payload_calculates_elapsed_and_eta():
    job = {
        "status": "running",
        "phase": "Nahrávám soubory",
        "started_at": 100.0,
        "progress_percent": 50,
    }
    payload = webapp._publish_progress_payload(job, now=200.0)
    assert payload["progress_percent"] == 50
    assert payload["elapsed_seconds"] == 100
    assert payload["eta_seconds"] == 100
    assert payload["elapsed"] == "1 min 40 s"
    assert payload["eta"] == "1 min 40 s"
    assert "started_at" not in payload

    complete = webapp._publish_progress_payload(
        {**job, "status": "done", "progress_percent": 100}, now=225.0
    )
    assert complete["eta"] is None
    assert complete["eta_seconds"] is None


def test_publish_progress_payload_does_not_guess_too_early():
    payload = webapp._publish_progress_payload(
        {"status": "running", "started_at": 100.0, "progress_percent": 2},
        now=102.0,
    )
    assert payload["elapsed_seconds"] == 2
    assert payload["eta"] is None


def test_public_data_contains_only_selected_fields():
    data = {
        "rows": [{"url": "https://e.cz", "status": 500, "ms": 42, "error": "secret note"}],
        "seo_pages": [{"url": "https://e.cz", "title": "Title", "lang": "cs", "images_missing_alt": 2}],
        "seo_site": {"robots_ok": True, "security_headers": {"x": False}},
        "findings": [{"title": "Finding"}], "summary": {"critical_count": 1},
        "client_email": "client@example.cz", "publications": [{"remote_dir": "reports/old"}],
    }
    public = webapp._sanitize_public_data(data, {"response", "technical"})
    assert public["rows"] == [{"url": "https://e.cz", "ms": 42}]
    assert "title" not in public["seo_pages"][0]
    assert public["seo_pages"][0]["lang"] == "cs"
    assert public["findings"] == []
    assert "client_email" not in public and "publications" not in public


def test_partial_publish_failure_rolls_back_only_its_remote_directory(tmp_path, monkeypatch):
    report_id = "report-1"
    report_dir = tmp_path / report_id
    report_dir.mkdir()
    data = {
        "schema_version": 2, "report_id": report_id, "base_url": "https://example.cz",
        "summary": {"pages_count": 1}, "client_email": "client@example.cz",
    }
    (report_dir / "report.json").write_text(json.dumps(data), encoding="utf-8")
    deleted = []

    class FailingClient:
        def __init__(self, cfg): pass
        def upload_tree(self, local_dir, remote_dir, on_progress=None):
            if on_progress:
                on_progress(1, 3)
            raise RuntimeError("partial upload")
        def delete_directory(self, remote_dir):
            deleted.append(remote_dir)

    def fake_bundle(report_dir, stage_dir, data, sections, report_url, expires_display, on_progress=None):
        stage_dir.mkdir(parents=True)
        (stage_dir / "index.html").write_text("partial", encoding="utf-8")
        if on_progress:
            on_progress("Balíček je připravený", 1, 1, 70)
        return data

    monkeypatch.setattr(webapp, "REPORTS_ROOT", str(tmp_path))
    monkeypatch.setattr(webapp, "BunnyStorageClient", FailingClient)
    monkeypatch.setattr(webapp, "_create_public_bundle", fake_bundle)
    monkeypatch.setenv("BUNNY_STORAGE_ZONE", "zone")
    monkeypatch.setenv("BUNNY_STORAGE_ACCESS_KEY", "key")
    monkeypatch.setenv("BUNNY_CDN_BASE_URL", "https://zone.b-cdn.net")
    monkeypatch.setenv("BUNNY_TOKEN_AUTH_KEY", "token")
    monkeypatch.setenv("BUNNY_REMOTE_PREFIX", "reports")
    job_id = "publish-job"
    webapp.PUBLISH_JOBS[job_id] = {"status": "running"}
    webapp._run_publish_job(job_id, report_id, {"summary"})

    assert webapp.PUBLISH_JOBS[job_id]["status"] == "error"
    assert len(deleted) == 1 and deleted[0].startswith("reports/")
    publication_id = deleted[0].split("/")[-1]
    assert not (report_dir / "published" / publication_id).exists()
    assert "publications" not in json.loads((report_dir / "report.json").read_text(encoding="utf-8"))


def test_cleanup_deletes_only_expired_publication(tmp_path, monkeypatch):
    report_dir = tmp_path / "report-1"
    old_local = report_dir / "published" / "old"
    new_local = report_dir / "published" / "new"
    old_local.mkdir(parents=True)
    new_local.mkdir(parents=True)
    now = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
    data = {
        "publications": [
            {"remote_dir": "reports/old", "local_dir": "published/old", "delete_after": (now - dt.timedelta(seconds=1)).isoformat()},
            {"remote_dir": "reports/new", "local_dir": "published/new", "delete_after": (now + dt.timedelta(days=1)).isoformat()},
        ]
    }
    (report_dir / "report.json").write_text(json.dumps(data), encoding="utf-8")
    deleted = []
    class FakeClient:
        def __init__(self, cfg): pass
        def delete_directory(self, remote): deleted.append(remote)
    monkeypatch.setattr(webapp, "REPORTS_ROOT", str(tmp_path))
    monkeypatch.setattr(webapp, "BunnyStorageClient", FakeClient)
    monkeypatch.setenv("BUNNY_STORAGE_ZONE", "zone")
    monkeypatch.setenv("BUNNY_STORAGE_ACCESS_KEY", "key")
    monkeypatch.setenv("BUNNY_CDN_BASE_URL", "https://zone.b-cdn.net")
    monkeypatch.setenv("BUNNY_TOKEN_AUTH_KEY", "token")
    assert webapp.cleanup_expired_publications(now) == 1
    assert deleted == ["reports/old"]
    assert not old_local.exists()
    assert new_local.exists()
    saved = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert len(saved["publications"]) == 1


def test_cleanup_keeps_metadata_and_local_copy_when_remote_delete_fails(tmp_path, monkeypatch):
    report_dir = tmp_path / "report-1"
    local_copy = report_dir / "published" / "old"
    local_copy.mkdir(parents=True)
    untouched = tmp_path / "unpublished-report"
    untouched.mkdir()
    (untouched / "report.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    marker = untouched / "keep.txt"
    marker.write_text("local", encoding="utf-8")
    now = dt.datetime(2027, 1, 1, tzinfo=dt.timezone.utc)
    publication = {
        "remote_dir": "reports/old", "local_dir": "published/old",
        "delete_after": (now - dt.timedelta(seconds=1)).isoformat(),
    }
    (report_dir / "report.json").write_text(json.dumps({"publications": [publication]}), encoding="utf-8")

    class FailingClient:
        def __init__(self, cfg): pass
        def delete_directory(self, remote): raise RuntimeError("temporary failure")

    monkeypatch.setattr(webapp, "REPORTS_ROOT", str(tmp_path))
    monkeypatch.setattr(webapp, "BunnyStorageClient", FailingClient)
    monkeypatch.setenv("BUNNY_STORAGE_ZONE", "zone")
    monkeypatch.setenv("BUNNY_STORAGE_ACCESS_KEY", "key")
    monkeypatch.setenv("BUNNY_CDN_BASE_URL", "https://zone.b-cdn.net")
    monkeypatch.setenv("BUNNY_TOKEN_AUTH_KEY", "token")
    assert webapp.cleanup_expired_publications(now) == 0
    assert local_copy.is_dir()
    assert marker.read_text(encoding="utf-8") == "local"
    saved = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    assert saved["publications"] == [publication]
