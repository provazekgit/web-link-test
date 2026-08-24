"""Vytvoří malý realistický report pro browserovou akceptaci publikace."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

import app as webapp
import scanner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", required=True)
    args = parser.parse_args()
    report_dir = Path(args.reports).resolve() / "e2e-klient-20260824-120000"
    screens = report_dir / "screens"
    screens.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (900, 1600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 900, 220), fill="#2454ff")
    draw.text((55, 75), "E2E klientsky web", fill="white")
    draw.text((55, 300), "Ukazkovy plny screenshot", fill="#1c2430")
    filename = "e2e_example_Desktop_Chrome.png"
    image.save(screens / filename)
    rows = [
        {"url": "https://example.test/", "status": 200, "ms": 132, "error": ""},
        {"url": "https://example.test/kontakt", "status": 200, "ms": 245, "error": ""},
        {"url": "https://example.test/stara-stranka", "status": 404, "ms": 88, "error": "HTTP 404"},
    ]
    seo_pages = [{
        "url": "https://example.test/", "title": "Ukázkový klientský web", "title_len": 23,
        "title_ok": True, "title_duplicate": False, "description": None, "description_len": 0,
        "description_ok": False, "description_duplicate": False, "h1_count": 1, "h1_ok": True,
        "noindex": False, "canonical": "https://example.test/", "canonical_matches": True,
        "lang": "cs", "viewport": True, "images_total": 4, "images_missing_alt": 2,
        "mixed_content_count": 0, "open_graph": True, "structured_data": False, "error": "",
    }]
    with webapp.app.app_context():
        scanner.write_report(
            str(report_dir), "https://example.test", rows,
            excluded_urls=["https://example.test/kosik"],
            screenshots=[{"url": "https://example.test/", "device": "Desktop Chrome", "file": filename}],
            duration_sec=12.5, seo_pages=seo_pages,
            seo_site={
                "robots_ok": True, "robots_blocks_all": False, "sitemap_ok": True,
                "https_redirect": True,
                "security_headers": {
                    "strict-transport-security": True, "content-security-policy": False,
                    "x-content-type-options": True, "referrer-policy": False,
                },
            },
            footer_text="Připraveno pro E2E klienta", footer_signature="Testovací administrátor",
            footer_date="2026-08-24", footer_color="#2454ff",
            client_email="klient@example.test",
        )
    print(report_dir.name)


if __name__ == "__main__":
    main()
