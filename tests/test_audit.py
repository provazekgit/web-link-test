from lib.audit import build_findings, summarize_findings
from lib.seo import _mark_duplicates, analyze_page
import lib.seo as seo_module


def test_duplicate_metadata_and_grouped_findings():
    pages = [
        {"url": "https://e.cz/a", "title": "Stejný title", "title_ok": True, "description": "Stejný popis", "description_ok": True, "h1_ok": True, "canonical": "https://e.cz/a", "canonical_matches": True, "lang": "cs", "viewport": True, "images_missing_alt": 0, "mixed_content_count": 0, "error": ""},
        {"url": "https://e.cz/b", "title": "Stejný title", "title_ok": True, "description": "Stejný popis", "description_ok": True, "h1_ok": True, "canonical": "https://e.cz/b", "canonical_matches": True, "lang": "cs", "viewport": True, "images_missing_alt": 0, "mixed_content_count": 0, "error": ""},
    ]
    _mark_duplicates(pages)
    findings = build_findings([], pages, {"sitemap_ok": True, "security_headers": {}, "https_redirect": True})
    duplicates = [item for item in findings if item["key"].startswith("duplicate-")]
    assert len(duplicates) == 2
    assert all(item["count"] == 2 for item in duplicates)


def test_only_proven_breakage_is_critical_and_intentional_settings_are_reviewed():
    findings = build_findings(
        [{"url": "https://e.cz/broken", "status": 500}], [],
        {"robots_blocks_all": True, "sitemap_ok": True, "security_headers": {}, "https_redirect": True},
    )
    assert findings[0]["severity"] == "critical"
    robots = next(item for item in findings if item["key"] == "robots-blocks-all")
    assert robots["severity"] == "recommended"
    assert "záměrné" in robots["detail"]
    summary = summarize_findings(findings)
    assert summary["critical_count"] == 1
    assert summary["recommended_count"] == 1
    assert summary["priorities"]


def test_page_audit_covers_metadata_accessibility_and_insecure_resources(monkeypatch):
    class Response:
        ok = True
        status_code = 200
        text = """
        <html lang="cs"><head>
          <title>Ukázková stránka</title>
          <meta name="description" content="Toto je dostatečně dlouhý popis ukázkové stránky určený pro výsledek vyhledávání.">
          <meta name="viewport" content="width=device-width">
          <meta name="robots" content="noindex">
          <meta property="og:title" content="Sdílení">
          <link rel="canonical" href="https://e.cz/page">
          <link rel="stylesheet" href="http://cdn.example/style.css">
          <script type="application/ld+json">{}</script>
        </head><body><h1>Nadpis</h1>
          <img src="http://cdn.example/a.png" srcset="http://cdn.example/a-2x.png 2x">
        </body></html>
        """

    monkeypatch.setattr(seo_module, "get_html", lambda *args, **kwargs: Response())
    result = analyze_page("https://e.cz/page")
    assert result["title_ok"] and result["description_ok"] and result["h1_ok"]
    assert result["lang"] == "cs" and result["viewport"]
    assert result["canonical_matches"] and result["noindex"]
    assert result["images_missing_alt"] == 1
    assert result["mixed_content_count"] == 3
    assert result["open_graph"] and result["structured_data"]
