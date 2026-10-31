#!/usr/bin/env python3
"""Run locally: python3 server.py --port 8876. Binds all interfaces for LAN access."""
from __future__ import annotations

import argparse
from functools import partial
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import socket
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from q1store.service import APIError, MAX_MOTION, MAX_UPLOAD, Store, VIDEO_EXTENSIONS
from q1store.tutorial import Tutorial
from q1store.llm import LLMManager
from q1store.agent import Agent

ROOT = Path(__file__).resolve().parent
MAX_JSON = 2 * 1024 * 1024
GZIP_SUFFIXES = {".js", ".css", ".stl", ".urdf", ".json", ".svg"}
GZIP_CACHE = {}
GZIP_LOCK = threading.Lock()
SERVICES_LOCK = threading.Lock()
HEX32 = r"[0-9a-f]{32}"
LESSON_ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}"


def attach_services(server, start_threads=True):
    """Tutorial, LLM and agent services share the store's data dir; created once per server."""
    with SERVICES_LOCK:
        if getattr(server, "tutorial", None) is None:
            server.tutorial = Tutorial(server.store.root, server.store.data_dir, clock=server.store.clock, start_threads=start_threads)
        if getattr(server, "llm", None) is None:
            server.llm = LLMManager(server.store.data_dir, clock=server.store.clock)
        if getattr(server, "agent", None) is None:
            server.agent = Agent(server.tutorial, server.llm, clock=server.store.clock)
    return server


class Handler(BaseHTTPRequestHandler):
    server_version = "Q1Store/1.0"

    @property
    def store(self):
        return self.server.store

    @property
    def tutorial(self):
        return attach_services(self.server).tutorial

    @property
    def llm(self):
        return attach_services(self.server).llm

    @property
    def agent(self):
        return attach_services(self.server).agent

    def query(self):
        return {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}

    def sse(self, events):
        """Stream dict events as Server-Sent Events; the generator may raise APIError before the first event."""
        iterator = iter(events)
        try:
            first = next(iterator)
        except StopIteration:
            first = None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in ([first] if first is not None else []):
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        for event in iterator:
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def route_tutorial_get(self, path):
        q = self.query()
        t = self.tutorial
        if path == "/api/tutorial/catalog":
            return self.json_response(t.catalog(robot=q.get("robot"), user_id=q.get("user")))
        match = re.fullmatch(rf"/api/tutorial/lessons/({LESSON_ID})", path)
        if match:
            return self.json_response(t.lesson(match.group(1), robot=q.get("robot", "leg"), user_id=q.get("user")))
        if path == "/api/tutorial/users":
            return self.json_response({"users": t.users()})
        match = re.fullmatch(rf"/api/tutorial/users/({HEX32})", path)
        if match:
            return self.json_response({"user": t.user(match.group(1)), "progress": t.progress(match.group(1))})
        match = re.fullmatch(rf"/api/tutorial/users/({HEX32})/progress", path)
        if match:
            return self.json_response(t.progress(match.group(1)))
        if path == "/api/tutorial/runs":
            return self.json_response({"runs": t.runs(uid=q.get("user"), lesson_id=q.get("lesson"))})
        match = re.fullmatch(rf"/api/tutorial/runs/({HEX32})(/log|/code)?", path)
        if match:
            if match.group(2) == "/log":
                return self.json_response({"id": match.group(1), "log_tail": t.run(match.group(1), log_lines=400)["log_tail"]})
            if match.group(2) == "/code":
                return self.json_response({"id": match.group(1), "code": t.run_code(match.group(1))})
            return self.json_response(t.run(match.group(1)))
        if path == "/api/tutorial/proposals":
            return self.json_response({"proposals": t.proposals(status=q.get("status")), "counts": t.proposal_counts()})
        match = re.fullmatch(r"/api/tutorial/proposals/([a-z0-9][a-z0-9-]{0,80})(/cursor-prompt)?", path)
        if match:
            if match.group(2):
                return self.json_response({"id": match.group(1), "prompt": t.cursor_prompt(match.group(1))})
            return self.json_response(t.proposal(match.group(1)))
        if path == "/api/tutorial/ticker":
            return self.json_response({"items": t.ticker(), "counts": t.proposal_counts(), "discovery": t.discovery.status()})
        if path == "/api/tutorial/discovery":
            return self.json_response(t.discovery.status())
        if path == "/api/llm/status":
            return self.json_response(self.llm.status())
        if path == "/api/llm/downloads":
            return self.json_response({"downloads": self.llm.download_status()})
        if path == "/api/llm/log":
            return self.json_response({"lines": self.llm.server_log()})
        if path == "/api/agent/history":
            if not re.fullmatch(HEX32, q.get("user", "")) or not re.fullmatch(LESSON_ID, q.get("lesson", "")):
                raise APIError("需要 user 與 lesson。")
            return self.json_response({"messages": t.history(q["user"], q["lesson"]), "memory": t.memory_get(q["user"])["facts"]})
        raise APIError("找不到 API。", 404)

    def route_tutorial_post(self, path, payload):
        t = self.tutorial
        if path == "/api/tutorial/users":
            return t.register_user(payload.get("name"))
        match = re.fullmatch(rf"/api/tutorial/users/({HEX32})/lessons/({LESSON_ID})/(start|draft|quiz)", path)
        if match:
            uid, lesson_id, action = match.groups()
            if action == "start":
                return t.start_lesson(uid, lesson_id, payload.get("robot", "leg"))
            if action == "draft":
                return t.save_draft(uid, lesson_id, code=payload.get("code"), params=payload.get("params"))
            return t.grade_quiz(uid, lesson_id, payload.get("answers"))
        if path == "/api/tutorial/runs":
            uid = payload.get("user")
            if not re.fullmatch(HEX32, uid or ""):
                raise APIError("需要 user。")
            return t.create_run(uid, payload.get("lesson"), payload.get("robot", "leg"), payload.get("params") or {}, payload.get("code"))
        match = re.fullmatch(rf"/api/tutorial/runs/({HEX32})/(cancel|resume)", path)
        if match:
            return t.cancel_run(match.group(1)) if match.group(2) == "cancel" else t.resume_run(match.group(1), payload.get("params"))
        match = re.fullmatch(r"/api/tutorial/proposals/([a-z0-9][a-z0-9-]{0,80})/(approve|reject|reopen|mark-created)", path)
        if match:
            pid, action = match.groups()
            if action == "mark-created":
                return t.mark_created(pid, payload.get("lesson_id"))
            return t.set_proposal_status(pid, {"approve": "approved", "reject": "rejected", "reopen": "pending"}[action])
        if path == "/api/tutorial/proposals":
            return t.write_proposal(str(payload.get("title", ""))[:120], str(payload.get("summary", ""))[:500], payload.get("category", "other"),
                                    payload.get("sources") or [], str(payload.get("body", "")), origin="manual")
        if path == "/api/tutorial/discovery":
            if payload.get("run_now"):
                threading.Thread(target=t.discovery.run_once, daemon=True).start()
                return {**t.discovery.status(), "running": True}
            return t.discovery.configure(enabled=payload.get("enabled"), interval_minutes=payload.get("interval_minutes"),
                                         topics=payload.get("topics"), max_per_run=payload.get("max_per_run"))
        if path == "/api/llm/download":
            try:
                return self.llm.start_download(payload.get("model_id"))
            except ValueError as exc:
                raise APIError(str(exc))
        if path == "/api/llm/server":
            try:
                if payload.get("action") == "stop":
                    return self.llm.stop_server()
                return self.llm.start_server(payload.get("model_id"), backend=payload.get("backend"), port=payload.get("port"))
            except ValueError as exc:
                raise APIError(str(exc))
        if path == "/api/agent/clear":
            t.clear_history(payload.get("user"), payload.get("lesson"))
            return {"ok": True}
        if path == "/api/agent/accept":
            return self.agent.accept_proposal(payload.get("user"), payload.get("lesson"), payload.get("proposal_id"))
        raise APIError("找不到 API。", 404)

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
        if path == "/api/specs":
            return self.json_response(self.store.specs())
        if path == "/api/jobs":
            return self.json_response({"jobs": self.store.jobs_summary(), "worker": self.store.worker_status()})
        if path == "/api/worker":
            return self.json_response(self.store.worker_status())
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
            if path.endswith("/log"):
                query = parse_qs(urlsplit(self.path).query)
                lines = min(2000, max(1, int(query.get("lines", ["200"])[0]) if query.get("lines", ["200"])[0].isdigit() else 200))
                job = self.store.job(identifier, log_lines=lines)
                return self.json_response({"id": identifier, "state": job.get("state"), "stage_label": job.get("stage_label"),
                                           "training": job.get("training"), "log_tail": job.get("log_tail", []), "log_url": job.get("log_url")})
            job = self.store.job(identifier)
            if path.endswith(("/manifest", "/plan")):
                return self.json_response(job["plan"], download="q1-job-" + identifier + ".json")
            return self.json_response(job)
        if path == "/api/health":
            worker = self.store.worker_status()
            return self.json_response({"ok": True, "service": "q1-motion-store", "gpu_worker": worker["online"], "worker": worker})
        if path.startswith(("/api/tutorial/", "/api/llm/", "/api/agent/")):
            return self.route_tutorial_get(path)
        if path.startswith("/api/"):
            raise APIError("找不到 API。", 404)
        if path.startswith("/media/tutorial/"):
            match = re.fullmatch(rf"/media/tutorial/({HEX32})/([A-Za-z0-9_.-]+)", path)
            if not match:
                raise APIError("找不到檔案。", 404)
            file_path, content_type = self.tutorial.run_media(match.group(1), match.group(2))
            return self.serve_file(file_path, head, cache="no-cache", content_type=content_type)
        if path.startswith("/media/jobs/"):
            match = re.fullmatch(r"/media/jobs/([0-9a-f]{32})/([A-Za-z0-9_.-]+)", path)
            if not match:
                raise APIError("找不到檔案。", 404)
            file_path, content_type = self.store.job_media(match.group(1), match.group(2))
            return self.serve_file(file_path, head, cache="no-cache" if match.group(2).endswith((".log", ".jsonl", "progress.json")) else "public, max-age=3600",
                                   content_type=content_type)
        if path.startswith("/media/uploads/"):
            name = path.removeprefix("/media/uploads/")
            if not re.fullmatch(r"[0-9a-f]{32}\.(mp4|webm|mov|m4v)", name):
                raise APIError("找不到影片。", 404)
            file_path = self.store.upload_dir / name
        else:
            relative = "index.html" if path in ("/", "/store", "/store/") else "tutorial.html" if path in ("/tutorial", "/tutorial/") else path.lstrip("/")
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

    def compressed_body(self, path):
        if path.suffix.lower() not in GZIP_SUFFIXES or "gzip" not in (self.headers.get("Accept-Encoding") or "").lower():
            return None
        if self.headers.get("Range"):
            return None
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with GZIP_LOCK:
            cached = GZIP_CACHE.get(key)
        if cached is None:
            cached = gzip.compress(path.read_bytes(), compresslevel=1)
            with GZIP_LOCK:
                if len(GZIP_CACHE) > 80:
                    GZIP_CACHE.clear()
                GZIP_CACHE[key] = cached
        return cached

    def serve_file(self, path, head=False, cache=None, content_type=None):
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
        compressed = None if status == 206 else self.compressed_body(path)
        body_size = len(compressed) if compressed is not None else max(0, end - start + 1)
        suffix = path.suffix.lower()
        if cache is None:
            if suffix in {".stl", ".urdf", ".jpg", ".png", ".gif", ".mp4", ".webm"}:
                cache = "public, max-age=86400"
            elif suffix in {".js", ".css"}:
                cache = "public, max-age=60"
            else:
                cache = "no-cache"
        self.send_response(status)
        content_type = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(body_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if compressed is not None:
            self.send_header("Content-Encoding", "gzip")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        if compressed is not None:
            self.wfile.write(compressed)
            return
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
            if path == "/api/agent/chat":
                uid, lesson = payload.get("user"), payload.get("lesson")
                if not re.fullmatch(HEX32, uid or "") or not re.fullmatch(LESSON_ID, lesson or ""):
                    raise APIError("需要 user 與 lesson。")
                return self.sse(self.agent.chat(uid, lesson, payload.get("message"), payload.get("context") or {}))
            if path.startswith(("/api/tutorial/", "/api/llm/", "/api/agent/")):
                return self.json_response(self.route_tutorial_post(path, payload), 200)
            if path == "/api/ingest":
                result = self.store.ingest(payload)
            elif path == "/api/clips":
                result = self.store.save_clip(payload)
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


def lan_urls(port):
    """Advertised URLs for same-subnet browsers when bound to 0.0.0.0."""
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            urls.append(f"http://{ip}:{port}/")
    except OSError:
        pass
    return urls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0 for LAN)")
    parser.add_argument("--port", default=8876, type=int)
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    store = Store(ROOT, args.data_dir)
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        server.store = store
        attach_services(server)
        print(f"Q1 Motion Store: http://{args.host}:{args.port}/", flush=True)
        print(f"  Isaac Lab 3 tutorial: http://{args.host}:{args.port}/tutorial"
              f"  (Isaac Lab 3: {server.tutorial.isaaclab3_root or 'not configured — set Q1_ISAACLAB3_ROOT'})", flush=True)
        if args.host in {"0.0.0.0", "::"}:
            for url in lan_urls(args.port):
                print(f"  open {url}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
