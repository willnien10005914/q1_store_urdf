#!/usr/bin/env python3
"""Run locally: python3 server.py --port 8876. No GPU jobs or payments run here."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from q1store.service import APIError, MAX_MOTION, MAX_UPLOAD, Store, VIDEO_EXTENSIONS

ROOT = Path(__file__).resolve().parent
MAX_JSON = 2 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "Q1Store/1.0"

    @property
    def store(self):
        return self.server.store

    def json_response(self, payload, status=200, download=None):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def binary_response(self, body, content_type, download):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        try:
            self.route_get()
        except APIError as error:
            self.json_response({"error": str(error)}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        try:
            self.route_get(head=True)
        except APIError as error:
            self.json_response({"error": str(error)}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route_get(self, head=False):
        path = unquote(urlsplit(self.path).path)
        if path == "/api/catalog":
            return self.json_response(self.store.catalog())
        if path == "/api/robots":
            return self.json_response({"robots": self.store.all("registered_robots")})
        if path == "/api/deployments":
            return self.json_response({"deployments": self.store.all("deployments")})
        if re.fullmatch(r"/api/deployments/[0-9a-f]{32}/download", path):
            deployment, content = self.store.deployment_package(path.split("/")[3])
            return self.binary_response(content, "application/zip", "q1-" + deployment["robot_type"] + "-" + deployment["id"] + ".zip")
        if re.fullmatch(r"/api/deployments/[0-9a-f]{32}", path):
            return self.json_response(self.store.get("deployments", path.split("/")[3]))
        if path == "/api/listings":
            return self.json_response({"listings": self.store.all("listings")})
        if re.fullmatch(r"/api/motions/[0-9a-f]{32}", path):
            motion = self.store.get("motions", path.split("/")[3])
            return self.json_response(motion, download="q1-motion-" + motion["id"] + ".json")
        if path.startswith("/api/jobs/"):
            identifier = path.split("/")[3]
            job = self.store.job(identifier)
            if path.endswith(("/manifest", "/plan")):
                return self.json_response(job["plan"], download="q1-job-" + identifier + ".json")
            return self.json_response(job)
        if path == "/api/health":
            return self.json_response({"ok": True, "service": "q1-motion-store", "gpu_worker": False})
        if path.startswith("/api/"):
            raise APIError("找不到 API。", 404)
        if path.startswith("/media/uploads/"):
            name = path.removeprefix("/media/uploads/")
            if not re.fullmatch(r"[0-9a-f]{32}\.(mp4|webm|mov|m4v)", name):
                raise APIError("找不到影片。", 404)
            file_path = self.store.upload_dir / name
        else:
            relative = "index.html" if path in ("/", "/store", "/store/") else path.lstrip("/")
            candidate = Path(relative)
            if any(part.startswith(".") for part in candidate.parts) or ".." in candidate.parts:
                raise APIError("找不到檔案。", 404)
            allowed = candidate.parts[0] in {"assets", "web", "vendor", "models", "public"} or (
                len(candidate.parts) == 1 and candidate.suffix.lower() in {".html", ".js", ".css", ".ico"})
            if not allowed:
                raise APIError("找不到檔案。", 404)
            # Browser source lives in public; robot and example assets stay at repository root.
            public_candidate = self.store.root / "public" / candidate
            file_path = public_candidate.resolve() if public_candidate.is_file() else (self.store.root / candidate).resolve()
            if not file_path.is_relative_to(self.store.root):
                raise APIError("找不到檔案。", 404)
        if not file_path.is_file():
            raise APIError("找不到檔案。", 404)
        self.serve_file(file_path, head)

    def serve_file(self, path, head=False):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not any(match.groups()):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            low, high = match.groups()
            if low:
                start = int(low)
                end = min(size - 1, int(high)) if high else size - 1
            else:
                start = max(0, size - int(high))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not head:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_POST(self):
        try:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                raise APIError("不接受跨站請求。", 403)
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit():
                raise APIError("需要有效的 Content-Length。", 411)
            length = int(raw_length)
            path = unquote(urlsplit(self.path).path)
            maximum = MAX_UPLOAD if path == "/api/uploads" else MAX_MOTION if path == "/api/motions" else MAX_JSON
            if length <= 0 or length > maximum:
                raise APIError("請求內容超過限制或為空。", 413)
            body = self.rfile.read(length)
            if len(body) != length:
                raise APIError("上傳未完成。")
            if path == "/api/uploads":
                return self.json_response(self.store.save_upload(unquote(self.headers.get("X-Filename", "video.mp4")), body), 201)
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise APIError("請求內容不是有效的 JSON。")
            if not isinstance(payload, dict):
                raise APIError("請求內容必須是 JSON object。")
            if path == "/api/ingest":
                result = self.store.ingest(payload)
            elif path == "/api/jobs":
                result = self.store.create_job(payload)
            elif path == "/api/motions":
                result = self.store.create_motion(payload)
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{32}/cancel", path):
                result = self.store.cancel(path.split("/")[3])
            elif path == "/api/listings":
                result = self.store.create_listing(payload)
            elif path == "/api/robots":
                result = self.store.register_robot(payload)
            elif path == "/api/deployments":
                result = self.store.create_deployment(payload)
            elif path == "/api/purchases":
                result = self.store.purchase(payload)
            else:
                raise APIError("找不到 API。", 404)
            self.json_response(result, 201)
        except APIError as error:
            self.json_response({"error": str(error)}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8876, type=int)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    store = Store(ROOT, args.data_dir)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.store = store
        print(f"Q1 Motion Store: http://{args.host}:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
