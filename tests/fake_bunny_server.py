"""Lokální HTTP náhrada Bunny Storage + CDN pro end-to-end akceptaci."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import mimetypes
import os
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


def _inside(root: Path, relative: str) -> Path:
    target = (root / relative.lstrip("/")).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path traversal")
    return target


class Handler(BaseHTTPRequestHandler):
    root: Path
    access_key: str
    token_key: str
    zone: str

    def log_message(self, format, *args):
        print(f"[fake-bunny] {format % args}", flush=True)

    def _storage_relative(self) -> str:
        path = unquote(urlparse(self.path).path).lstrip("/")
        prefix = self.zone + "/"
        if not path.startswith(prefix):
            raise ValueError("wrong zone")
        return path[len(prefix):]

    def do_PUT(self):
        try:
            if self.headers.get("AccessKey") != self.access_key:
                self.send_error(401)
                return
            target = _inside(self.root / self.zone, self._storage_relative())
            content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            expected = self.headers.get("Checksum", "").upper()
            if expected and hashlib.sha256(content).hexdigest().upper() != expected:
                self.send_error(400, "checksum mismatch")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            self.send_response(201)
            self.end_headers()
        except Exception as exc:
            self.send_error(400, str(exc))

    def do_DELETE(self):
        try:
            if self.headers.get("AccessKey") != self.access_key:
                self.send_error(401)
                return
            target = _inside(self.root / self.zone, self._storage_relative())
            if target == (self.root / self.zone).resolve():
                self.send_error(400, "root delete forbidden")
                return
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            self.send_response(200)
            self.end_headers()
        except Exception as exc:
            self.send_error(400, str(exc))

    def _signed_relative(self) -> str:
        path = urlparse(self.path).path
        parts = path.lstrip("/").split("/", 1)
        if len(parts) != 2 or not parts[0].startswith("bcdn_token="):
            raise PermissionError("missing token")
        parameter_text = parts[0].replace("bcdn_token=", "token=", 1)
        params = {key: values[0] for key, values in parse_qs(parameter_text, keep_blank_values=True).items()}
        token = params.pop("token", "")
        expires = params.pop("expires", "")
        if not expires or int(expires) < int(time.time()):
            raise PermissionError("expired")
        allowed = params.get("token_path", "")
        relative = unquote(parts[1])
        actual_path = "/" + relative
        if not allowed or not actual_path.startswith(allowed):
            raise PermissionError("outside token path")
        signing_data = "&".join(f"{key}={value}" for key, value in sorted(params.items()))
        message = f"{allowed}{expires}{signing_data}".encode("utf-8")
        digest = hmac.new(self.token_key.encode("utf-8"), message, hashlib.sha256).digest()
        expected = "HS256-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if not hmac.compare_digest(token, expected):
            raise PermissionError("invalid token")
        return relative

    def do_GET(self):
        if self.path == "/__health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        try:
            target = _inside(self.root / self.zone, self._signed_relative())
            if not target.is_file():
                self.send_error(404)
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except PermissionError as exc:
            self.send_error(403, str(exc))
        except Exception as exc:
            self.send_error(400, str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--zone", default="test-zone")
    parser.add_argument("--access-key", default="test-access-key")
    parser.add_argument("--token-key", default="test-token-key")
    args = parser.parse_args()
    Handler.root = Path(args.root).resolve()
    Handler.root.mkdir(parents=True, exist_ok=True)
    Handler.zone = args.zone
    Handler.access_key = args.access_key
    Handler.token_key = args.token_key
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake-bunny-ready http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
