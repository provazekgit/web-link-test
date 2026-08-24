"""Ruční smoke test skutečné Bunny Storage/Pull Zone konfigurace.

Nevypisuje klíče ani podepsanou URL. Vytvoří náhodný testovací adresář,
ověří upload a autorizaci přes CDN a v bloku finally jej odstraní.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from lib.publishing import BunnyConfig, BunnyStorageClient, sign_directory_url


def fetch_with_propagation(url: str) -> requests.Response:
    response: requests.Response | None = None
    for attempt in range(12):
        response = requests.get(url, timeout=20, allow_redirects=False)
        if response.status_code == 200:
            return response
        if response.status_code not in (404, 429) and response.status_code < 500:
            return response
        if attempt < 11:
            time.sleep(2)
    assert response is not None
    return response


def main() -> int:
    load_dotenv()
    config = BunnyConfig.from_env()
    missing = config.missing()
    if missing:
        print(json.dumps({"ok": False, "stage": "configuration", "missing": missing}, ensure_ascii=False))
        return 2

    smoke_id = uuid.uuid4().hex
    remote_dir = f"{config.remote_prefix}/_smoke/{smoke_id}"
    client = BunnyStorageClient(config)
    uploaded = False
    result: dict[str, object] = {"ok": False, "test_id": smoke_id[:8]}

    try:
        with tempfile.TemporaryDirectory(prefix="web-link-test-bunny-") as temporary:
            bundle = Path(temporary)
            (bundle / "assets").mkdir()
            marker = f"bunny-smoke-{smoke_id}"
            (bundle / "assets" / "marker.txt").write_text(marker, encoding="utf-8")
            (bundle / "report.json").write_text(json.dumps({"smoke": smoke_id}), encoding="utf-8")
            (bundle / "report.pdf").write_bytes(b"%PDF-1.4\n% Bunny smoke test\n%%EOF\n")
            (bundle / "index.html").write_text(
                f"<!doctype html><meta charset=utf-8><title>Bunny smoke test</title><p>{marker}</p>",
                encoding="utf-8",
            )

            uploaded_count = client.upload_tree(bundle, remote_dir)
            uploaded = True

            storage_response = requests.get(
                client._url(f"{remote_dir}/index.html"),
                headers={"AccessKey": config.access_key},
                timeout=20,
            )
            storage_content_ok = storage_response.status_code == 200 and marker in storage_response.text

        expires = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)).timestamp())
        signed_index = sign_directory_url(
            config.cdn_base_url, config.token_key, remote_dir, "index.html", expires
        )
        signed_asset = signed_index.rsplit("/", 1)[0] + "/assets/marker.txt"
        unsigned_index = f"{config.cdn_base_url.rstrip('/')}/{remote_dir}/index.html"

        index_response = fetch_with_propagation(signed_index)
        asset_response = fetch_with_propagation(signed_asset)
        unsigned_response = requests.get(unsigned_index, timeout=20, allow_redirects=False)

        content_ok = index_response.status_code == 200 and marker in index_response.text
        asset_ok = asset_response.status_code == 200 and asset_response.text == marker
        token_protected = unsigned_response.status_code in (401, 403)
        result.update(
            ok=storage_content_ok and content_ok and asset_ok and token_protected,
            cdn_host=urlparse(config.cdn_base_url).hostname,
            uploaded_files=uploaded_count,
            storage_read_status=storage_response.status_code,
            storage_content_ok=storage_content_ok,
            signed_index_status=index_response.status_code,
            signed_asset_status=asset_response.status_code,
            unsigned_status=unsigned_response.status_code,
            content_ok=content_ok,
            asset_ok=asset_ok,
            token_protected=token_protected,
        )
    except Exception as exc:
        result.update(stage="live_test", error=f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        if uploaded:
            try:
                client.delete_directory(remote_dir)
                result["cleanup"] = "deleted"
            except Exception as exc:
                result["cleanup"] = f"failed: {type(exc).__name__}: {str(exc)[:200]}"

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") and result.get("cleanup") == "deleted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
