# pdf_render.py
import os
import pathlib
from playwright.sync_api import sync_playwright

def html_to_pdf(html_path: str, pdf_path: str):
    html_uri = pathlib.Path(html_path).resolve().as_uri()
    pdf_dir = os.path.dirname(os.path.abspath(pdf_path))
    os.makedirs(pdf_dir, exist_ok=True)
    with sync_playwright() as p:
        launch_options = {"headless": True}
        browser_channel = os.getenv("PDF_BROWSER_CHANNEL", "").strip()
        if browser_channel:
            launch_options["channel"] = browser_channel
        browser = p.chromium.launch(**launch_options)
        context = browser.new_context()
        page = context.new_page()
        page.goto(html_uri, wait_until="load")
        page.emulate_media(media="print")
        page.wait_for_function(
            "Array.from(document.querySelectorAll('img[src]')).every(img => img.complete && img.naturalWidth > 0)",
            timeout=30000,
        )
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={"top": "10mm", "right": "10mm", "bottom": "10mm", "left": "10mm"},
        )
        context.close()
        browser.close()
    return pdf_path
