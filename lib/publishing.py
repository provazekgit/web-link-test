"""Bunny.net publikace, podepisování odkazů a lokální publikační metadata."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional
from urllib.parse import quote

import requests


@dataclass(frozen=True)
class BunnyConfig:
    storage_zone: str
    access_key: str
    storage_endpoint: str
    cdn_base_url: str
    token_key: str
    remote_prefix: str = "reports"
    link_days: int = 100
    retention_days: int = 100

    @classmethod
    def from_env(cls) -> "BunnyConfig":
        return cls(
            storage_zone=os.getenv("BUNNY_STORAGE_ZONE", "").strip(),
            access_key=os.getenv("BUNNY_STORAGE_ACCESS_KEY", "").strip(),
            storage_endpoint=os.getenv("BUNNY_STORAGE_ENDPOINT", "storage.bunnycdn.com").strip(),
            cdn_base_url=os.getenv("BUNNY_CDN_BASE_URL", "").strip().rstrip("/"),
            token_key=os.getenv("BUNNY_TOKEN_AUTH_KEY", "").strip(),
            remote_prefix=os.getenv("BUNNY_REMOTE_PREFIX", "reports").strip().strip("/"),
            link_days=max(1, int(os.getenv("REPORT_LINK_DAYS", "100"))),
            retention_days=max(1, int(os.getenv("REPORT_RETENTION_DAYS", "100"))),
        )

    def missing(self) -> list[str]:
        values = {
            "BUNNY_STORAGE_ZONE": self.storage_zone,
            "BUNNY_STORAGE_ACCESS_KEY": self.access_key,
            "BUNNY_CDN_BASE_URL": self.cdn_base_url,
            "BUNNY_TOKEN_AUTH_KEY": self.token_key,
            "BUNNY_REMOTE_PREFIX": self.remote_prefix,
        }
        return [name for name, value in values.items() if not value]


def safe_report_dir(reports_root: str, report_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", report_id or ""):
        raise ValueError("Neplatné ID reportu.")
    root = Path(reports_root).resolve()
    target = (root / report_id).resolve()
    if target.parent != root or not target.is_dir():
        raise ValueError("Report nebyl nalezen.")
    return target


def safe_remote_path(value: str) -> str:
    cleaned = value.strip().strip("/")
    if not cleaned or ".." in cleaned.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", cleaned):
        raise ValueError("Neplatná vzdálená cesta.")
    return cleaned


def sign_directory_url(cdn_base_url: str, security_key: str, directory: str, filename: str, expires_at: int) -> str:
    """Vytvoří Bunny Advanced Token Authentication URL pro celý adresář."""
    directory = safe_remote_path(directory)
    filename = filename.lstrip("/")
    if not filename or ".." in filename.split("/"):
        raise ValueError("Neplatný název souboru.")
    allowed_path = f"/{directory}/"
    signing_data = f"token_path={allowed_path}"
    message = f"{allowed_path}{int(expires_at)}{signing_data}".encode("utf-8")
    digest = hmac.new(security_key.encode("utf-8"), message, hashlib.sha256).digest()
    token = "HS256-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    token_segment = f"bcdn_token={token}&token_path={quote(allowed_path, safe='')}&expires={int(expires_at)}"
    return f"{cdn_base_url.rstrip('/')}/{token_segment}/{directory}/{filename}"


class BunnyStorageClient:
    def __init__(self, config: BunnyConfig, session: Optional[requests.Session] = None):
        missing = config.missing()
        if missing:
            raise ValueError("Chybí konfigurace Bunny: " + ", ".join(missing))
        self.config = config
        self.session = session or requests.Session()

    def _scoped_path(self, remote_path: str) -> str:
        remote_path = safe_remote_path(remote_path)
        prefix = safe_remote_path(self.config.remote_prefix)
        if remote_path != prefix and not remote_path.startswith(prefix + "/"):
            raise ValueError("Vzdálená cesta je mimo povolený publikační adresář.")
        return remote_path

    def _url(self, remote_path: str) -> str:
        remote_path = self._scoped_path(remote_path)
        endpoint = self.config.storage_endpoint.strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        return f"{endpoint}/{quote(self.config.storage_zone, safe='')}/{quote(remote_path, safe='/')}"

    def upload_file(self, local_path: Path, remote_path: str, retries: int = 3) -> None:
        checksum = hashlib.sha256(local_path.read_bytes()).hexdigest().upper()
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        headers = {"AccessKey": self.config.access_key, "Checksum": checksum, "Content-Type": content_type}
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            try:
                with local_path.open("rb") as stream:
                    response = self.session.put(self._url(remote_path), headers=headers, data=stream, timeout=120)
                if response.status_code in (200, 201):
                    return
                if response.status_code < 500 and response.status_code != 429:
                    raise RuntimeError(f"Bunny upload odmítnut (HTTP {response.status_code}).")
                last_error = RuntimeError(f"Bunny upload selhal (HTTP {response.status_code}).")
            except (requests.RequestException, OSError) as exc:
                last_error = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Soubor {local_path.name} se nepodařilo nahrát: {last_error}")

    def upload_tree(self, local_dir: Path, remote_dir: str, on_progress: Optional[Callable[[int, int], None]] = None) -> int:
        remote_dir = self._scoped_path(remote_dir)
        files = [path for path in local_dir.rglob("*") if path.is_file()]
        # index.html musí být poslední, PDF těsně před ním.
        files.sort(key=lambda path: (path.name == "index.html", path.suffix.lower() == ".pdf", path.as_posix()))
        files = [p for p in files if p.name not in ("index.html", "report.pdf")] + [p for p in files if p.name == "report.pdf"] + [p for p in files if p.name == "index.html"]
        for index, path in enumerate(files, 1):
            relative = path.relative_to(local_dir).as_posix()
            self.upload_file(path, f"{remote_dir}/{relative}")
            if on_progress:
                on_progress(index, len(files))
        return len(files)

    def delete_directory(self, remote_dir: str) -> None:
        remote_dir = self._scoped_path(remote_dir)
        if remote_dir == safe_remote_path(self.config.remote_prefix):
            raise ValueError("Kořenový publikační adresář nelze smazat.")
        response = self.session.delete(self._url(remote_dir) + "/", headers={"AccessKey": self.config.access_key}, timeout=60)
        if response.status_code not in (200, 404):
            raise RuntimeError(f"Bunny adresář se nepodařilo smazat (HTTP {response.status_code}).")


def publication_times(config: BunnyConfig, now: Optional[dt.datetime] = None) -> tuple[dt.datetime, dt.datetime]:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now, now + dt.timedelta(days=config.link_days)


def make_email_draft(base_url: str, recipient: str, summary: Dict[str, object], report_url: str, expires_display: str) -> Dict[str, str]:
    from urllib.parse import urlparse

    host = urlparse(base_url).hostname or base_url
    pages = int(summary.get("pages_count") or 0)
    critical = int(summary.get("critical_count") or 0)
    recommended = int(summary.get("recommended_count") or 0)
    priorities = [str(item) for item in summary.get("priorities") or []][:3]
    page_label = "stránku" if pages == 1 else ("stránky" if 2 <= pages <= 4 else "stránek")
    critical_label = "kritický nález" if critical == 1 else ("kritické nálezy" if 2 <= critical <= 4 else "kritických nálezů")
    if critical:
        short = f"Kontrola webu {host} odhalila {critical} {critical_label} a {recommended} doporučení k řešení."
    elif recommended:
        short = f"Web {host} je dostupný; kontrola našla {recommended} doporučených oblastí pro další zlepšení."
    else:
        short = f"Kontrola webu {host} neodhalila žádný kritický ani doporučený problém."
    priority_text = "\n".join(f"- {item}" for item in priorities)
    if priority_text:
        priority_text = f"\n\nHlavní priority:\n{priority_text}"
    body = (
        f"Dobrý den,\n\n"
        f"připravil jsem výsledky automatické kontroly webu {host}. "
        f"Kontrola zahrnula {pages} {page_label}. {short}{priority_text}\n\n"
        f"Kompletní report včetně PDF a screenshotů:\n{report_url}\n\n"
        f"Odkaz je dostupný do {expires_display}.\n\n"
        f"S pozdravem\n[Podpis]"
    )
    return {"recipient": recipient or "", "subject": f"Výsledky kontroly webu – {host}", "short_description": short, "body": body}
