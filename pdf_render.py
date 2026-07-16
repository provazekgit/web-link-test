# pdf_render.py
import os
import pathlib
from playwright.sync_api import sync_playwright

def html_to_pdf(html_path: str, pdf_path: str):
    html_uri = pathlib.Path(html_path).resolve().as_uri()
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.goto(html_uri, wait_until="load")
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            margin={"top": "10mm", "right": "10mm", "bottom": "10mm", "left": "10mm"},
        )
        context.close()
        browser.close()
    return pdf_path
