import json

import app as webapp
import scanner
import utils


def test_new_report_schema_and_local_publish_controls(tmp_path):
    report_dir = tmp_path / "example.cz-20260824-120000"
    (report_dir / "screens").mkdir(parents=True)
    rows = [{"url": "https://example.cz", "status": 200, "ms": 123, "error": ""}]
    seo_pages = [{
        "url": "https://example.cz", "title": "Example title", "title_len": 13,
        "title_ok": True, "description": None, "description_len": 0, "description_ok": False,
        "title_duplicate": False, "description_duplicate": False, "h1_count": 1,
        "h1_ok": True, "canonical": "https://example.cz", "canonical_matches": True,
        "noindex": False, "lang": "cs", "viewport": True, "images_total": 1,
        "images_missing_alt": 0, "mixed_content_count": 0, "open_graph": True,
        "structured_data": True, "error": "",
    }]
    with webapp.app.app_context():
        scanner.write_report(
            str(report_dir), "https://example.cz", rows, seo_pages=seo_pages,
            seo_site={"robots_ok": True, "robots_blocks_all": False, "sitemap_ok": True, "https_redirect": True, "security_headers": {}},
            client_email="client@example.cz", duration_sec=2,
        )
    data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    html = (report_dir / "index.html").read_text(encoding="utf-8")
    assert data["schema_version"] == 2
    assert data["client_email"] == "client@example.cz"
    assert "Publikovat pro klienta" in html
    assert "HTTP odezva" in html
    assert "client@example.cz" not in html


def test_run_rejects_invalid_client_email(monkeypatch):
    monkeypatch.setattr(utils, "AUTH_OFF", True)
    client = webapp.app.test_client()
    response = client.post("/run", data={"base_url": "https://example.cz", "client_email": "not-an-email"})
    assert response.status_code == 400
    assert "platný formát" in response.get_json()["error"]
