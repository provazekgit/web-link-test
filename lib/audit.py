"""Převod surových kontrol na krátké, klientsky srozumitelné nálezy."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Dict, Iterable, List


def _finding(key: str, severity: str, title: str, detail: str, pages: Iterable[Dict[str, object]]) -> Dict[str, object]:
    urls = [str(page.get("url", "")) for page in pages if page.get("url")]
    return {"key": key, "severity": severity, "title": title, "detail": detail, "count": len(urls) or 1, "examples": urls[:3]}


def build_findings(rows: List[Dict[str, object]], pages: List[Dict[str, object]], site: Dict[str, object]) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    broken = [row for row in rows if row.get("status") == -1 or int(row.get("status") or 0) >= 400]
    if broken:
        findings.append(_finding("broken-pages", "critical", "Nedostupné nebo chybové stránky", "Stránky vracejí chybu HTTP nebo se je nepodařilo načíst.", broken))

    checks: List[tuple[str, str, str, Callable[[Dict[str, object]], bool]]] = [
        ("missing-title", "Chybějící title", "Vyhledávače i uživatelé postrádají název stránky.", lambda p: not p.get("title")),
        ("bad-title", "Nevhodná délka title", "Title je velmi krátký nebo delší než doporučených 60 znaků.", lambda p: bool(p.get("title")) and not p.get("title_ok")),
        ("duplicate-title", "Duplicitní title", "Více stránek používá stejný title.", lambda p: bool(p.get("title_duplicate"))),
        ("missing-description", "Chybějící meta popis", "Stránka nemá meta description.", lambda p: not p.get("description")),
        ("bad-description", "Nevhodná délka meta popisu", "Meta popis je mimo doporučený rozsah 50–160 znaků.", lambda p: bool(p.get("description")) and not p.get("description_ok")),
        ("duplicate-description", "Duplicitní meta popis", "Více stránek používá stejný meta popis.", lambda p: bool(p.get("description_duplicate"))),
        ("h1", "Struktura hlavního nadpisu H1", "Stránka by obvykle měla mít právě jeden hlavní nadpis H1.", lambda p: not p.get("h1_ok")),
        ("canonical", "Canonical vyžaduje kontrolu", "Canonical chybí nebo ukazuje na jinou URL; může to být záměr.", lambda p: not p.get("canonical") or p.get("canonical_matches") is False),
        ("noindex", "Stránka obsahuje noindex", "Ověřte, že má být stránka skutečně vyřazena z výsledků vyhledávání.", lambda p: bool(p.get("noindex"))),
        ("lang", "Chybějící jazyk dokumentu", "Element html nemá atribut lang.", lambda p: not p.get("lang")),
        ("viewport", "Chybějící mobilní viewport", "Stránka nemá nastavení viewportu pro mobilní zařízení.", lambda p: not p.get("viewport")),
        ("alt", "Obrázky bez alternativního textu", "Některé obrázky nemají vyplněný atribut alt.", lambda p: int(p.get("images_missing_alt") or 0) > 0),
        ("mixed-content", "Nezabezpečené zdroje na HTTPS", "HTTPS stránka načítá některé zdroje přes nezabezpečené HTTP.", lambda p: int(p.get("mixed_content_count") or 0) > 0),
    ]
    valid_pages = [page for page in pages if not page.get("error")]
    for key, title, detail, predicate in checks:
        affected = [page for page in valid_pages if predicate(page)]
        if affected:
            findings.append(_finding(key, "recommended", title, detail, affected))
    if site.get("https_redirect") is False:
        findings.append(_finding("https-redirect", "recommended", "HTTP se nepřesměruje na HTTPS", "Návštěvníci HTTP adresy nemusí automaticky přejít na zabezpečenou variantu.", []))
    if site.get("robots_blocks_all"):
        findings.append(_finding("robots-blocks-all", "recommended", "Web blokuje indexaci", "robots.txt zakazuje robotům přístup k celému webu; ověřte, že je toto nastavení záměrné.", []))
    if not site.get("sitemap_ok"):
        findings.append(_finding("sitemap", "recommended", "Sitemap nebyla nalezena", "Na výchozí adrese /sitemap.xml nebyla dostupná sitemap.", []))

    missing_headers = [name for name, present in (site.get("security_headers") or {}).items() if not present]
    if missing_headers:
        findings.append({"key": "security-headers", "severity": "informational", "title": "Doporučené bezpečnostní hlavičky", "detail": "Ke zvážení chybí: " + ", ".join(missing_headers) + ".", "count": len(missing_headers), "examples": []})
    if valid_pages and not any(page.get("open_graph") for page in valid_pages):
        findings.append(_finding("open-graph", "informational", "Open Graph metadata nebyla nalezena", "Open Graph ovlivňuje náhledy při sdílení na sociálních sítích.", []))
    if valid_pages and not any(page.get("structured_data") for page in valid_pages):
        findings.append(_finding("structured-data", "informational", "Strukturovaná data nebyla nalezena", "JSON-LD může pomoci vyhledávačům pochopit obsah webu.", []))
    rank = {"critical": 0, "recommended": 1, "informational": 2}
    return sorted(findings, key=lambda item: (rank[item["severity"]], -int(item["count"]), item["title"]))


def summarize_findings(findings: List[Dict[str, object]]) -> Dict[str, object]:
    counts = defaultdict(int)
    for finding in findings:
        counts[str(finding.get("severity"))] += 1
    return {
        "critical_count": counts["critical"],
        "recommended_count": counts["recommended"],
        "informational_count": counts["informational"],
        "priorities": [finding["title"] for finding in findings if finding.get("severity") in ("critical", "recommended")][:3],
    }
