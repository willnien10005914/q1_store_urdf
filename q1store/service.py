"""Persistent jobs and marketplace records; the demo never launches GPU work."""
from __future__ import annotations

import hashlib
import io
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET


LIBRARIES = [
    {"id": "rsl_rl", "label": "RSL-RL · PPO"},
    {"id": "skrl", "label": "skrl · PPO"},
    {"id": "rl_games", "label": "RL-Games · PPO"},
    {"id": "sb3", "label": "Stable-Baselines3 · PPO"},
    {"id": "torchrl", "label": "TorchRL · PPO"},
]
PHYSICS = [
    {"id": "isaacsim_physx", "label": "Isaac Sim · PhysX"},
    {"id": "newton_mjwarp", "label": "Newton · MuJoCo Warp"},
    {"id": "ovphysx", "label": "OV PhysX · standalone"},
]
REWARDS = [
    {"id": "motion_tracking", "label": "動作追蹤"},
    {"id": "joint_position", "label": "關節位置"},
    {"id": "balance", "label": "平衡與姿態"},
    {"id": "energy", "label": "能耗與力矩"},
    {"id": "smoothness", "label": "動作平滑"},
    {"id": "foot_contact", "label": "足部接觸"},
    {"id": "wheel_velocity", "label": "輪速追蹤"},
]
LANDMARKS = [
    {"id": "taipei101", "label": "台北 101 · Taipei"},
    {"id": "eiffel", "label": "艾菲爾鐵塔 · Paris"},
    {"id": "times-square", "label": "時代廣場 · New York"},
    {"id": "shibuya", "label": "澀谷路口 · Tokyo"},
    {"id": "great-wall", "label": "萬里長城 · Beijing"},
]
DEMO_DURATION = 30
WORKER_HEARTBEAT_MAX_AGE = 90
WORKER_STATES = ("queued_for_worker", "preparing", "waiting_for_gpu", "starting_isaac", "training", "evaluating", "recording", "complete", "failed", "cancelled")
WORKER_ACTIVE_STATES = WORKER_STATES[:7]
JOB_MEDIA = {"result.mp4": "video/mp4", "rollout.json": "application/json", "evaluation.json": "application/json",
             "reference.json": "application/json", "record.json": "application/json", "native.log": "text/plain; charset=utf-8",
             "events.jsonl": "text/plain; charset=utf-8", "latest.pt": "application/octet-stream", "progress.json": "application/json"}
MAX_UPLOAD = 150 * 1024 * 1024
MAX_MOTION = 12 * 1024 * 1024
MAX_MOTION_FRAMES = 18000
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}


class APIError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def read_collection(path, key):
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else value.get(key, [])


def youtube_url(value):
    """Restrict downloading to a single canonical public YouTube video URL."""
    if not isinstance(value, str) or len(value) > 2048:
        raise APIError("YouTube 網址過長或格式不正確。")
    try:
        parts = urlsplit(value)
    except (ValueError, TypeError):
        raise APIError("請輸入有效的 YouTube 影片網址。")
    try:
        port = parts.port
    except ValueError:
        raise APIError("YouTube 網址格式不正確。")
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443):
        raise APIError("YouTube 網址必須使用 HTTPS。")
    host = (parts.hostname or "").lower()
    if host == "youtu.be":
        identifier = parts.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parts.path == "/watch":
            identifier = parse_qs(parts.query).get("v", [""])[0]
        elif parts.path.startswith(("/shorts/", "/embed/")):
            identifier = parts.path.split("/")[2]
        else:
            identifier = ""
    else:
        identifier = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise APIError("請提供單一 YouTube 影片網址（不接受播放清單或其他網站）。")
    return "https://www.youtube.com/watch?v=" + identifier


def resolve_executable(*values):
    for value in values:
        if not value:
            continue
        path = Path(value)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        found = shutil.which(str(value))
        if found:
            return found
    return None


def media_duration(ffmpeg, path):
    result = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise APIError("無法讀取影片長度。", 502)
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def clip_bounds(payload, duration=None):
    if not isinstance(payload, dict) or "start_s" not in payload or "end_s" not in payload:
        raise APIError("請設定片段起點與終點（秒）。")
    start, end = payload.get("start_s"), payload.get("end_s")
    if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
            or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)):
        raise APIError("片段起訖時間必須是有限秒數。")
    if start < 0 or end > 86400 or not .5 <= end - start <= 60:
        raise APIError("片段長度必須介於 0.5 至 60 秒。")
    if duration is not None and end > duration + .05:
        raise APIError(f"這支影片約 {duration:.2f} 秒，請把片段放在影片長度內。")
    return round(float(start), 3), round(float(end), 3)


def action_label(payload, default="YouTube 動作"):
    name = payload.get("action_name", default) if isinstance(payload, dict) else default
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(char) < 32 for char in name):
        raise APIError("動作名稱必須為 1 至 80 字。")
    return name.strip()


class Store:
    def __init__(self, root, data_dir=None, clock=time.time):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir or os.environ.get("Q1_STORE_DATA", self.root / ".data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = self.data_dir / "uploads"
        self.upload_dir.mkdir(exist_ok=True)
        self.deployment_dir = self.data_dir / "deployment-packages"
        self.deployment_dir.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "store.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        self.youtube_lock = threading.Lock()
        self.downloader = resolve_executable(os.environ.get("Q1_YTDLP"), self.root / ".venv/bin/yt-dlp", "yt-dlp")
        self.ffmpeg = resolve_executable(os.environ.get("Q1_FFMPEG"), "ffmpeg")
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.heartbeat_path = self.data_dir / "worker/heartbeat.json"
        with self.connection() as db:
            for table in ("jobs", "uploads", "motions", "listings", "purchases", "registered_robots", "deployments"):
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def put(self, table, value):
        with self.lock, self.connection() as db:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?)", (value["id"], json.dumps(value, ensure_ascii=False, allow_nan=False)))
        return value

    def get(self, table, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise APIError("找不到這筆資料。", 404)
        with self.connection() as db:
            row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise APIError("找不到這筆資料。", 404)
        return json.loads(row[0])

    def all(self, table):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(f"SELECT payload FROM {table} ORDER BY rowid DESC")]

    def worker_status(self):
        """Liveness of the Spark GPU worker from its heartbeat file."""
        try:
            beat = json.loads(self.heartbeat_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"online": False, "state": "offline", "job_id": None, "updated_at": None, "num_envs": None}
        age = self.clock() - float(beat.get("updated_at") or 0)
        online = age < WORKER_HEARTBEAT_MAX_AGE and beat.get("state") != "stopped"
        return {"online": online, "state": beat.get("state") if online else "offline", "job_id": beat.get("job_id") if online else None,
                "updated_at": beat.get("updated_at"), "num_envs": beat.get("num_envs"), "age_s": round(age, 1)}

    def catalog(self):
        worker = self.worker_status()
        return {
            "robots": read_collection(self.root / "assets/robots/manifest.json", "robots"),
            "examples": read_collection(self.root / "assets/examples/catalog.json", "examples"),
            "video_examples": read_collection(self.root / "assets/video-examples/catalog.json", "examples"),
            "video_sources": read_collection(self.root / "assets/video-examples/catalog.json", "sources"),
            "worker": worker,
            "capabilities": {
                "libraries": LIBRARIES, "physics": PHYSICS, "rewards": REWARDS, "landmarks": LANDMARKS,
                "demo_duration_s": DEMO_DURATION, "worker_available": worker["online"],
                "worker_robot_types": ["leg"], "worker_library": "rsl_rl", "worker_physics": "isaacsim_physx",
                "youtube_ingest": bool(self.downloader and self.ffmpeg), "upload": True, "motion_storage": True,
                "video_segments": True, "segment_min_s": .5, "segment_max_s": 60,
                "payments": "demo_only", "training": "demo_or_spark_gpu_worker",
                "robot_registry": True, "deployment_download": True, "hardware_delivery": False,
                "physics_scope": "Spark worker 以 Isaac Sim PhysX + RSL-RL PPO 執行雙足工作；其他選項保留為規劃並記錄在工作 JSON。",
                "rewards_scope": "Q1 任務的 reward 設計預設，並非所有 Isaac Lab 任務的通用選項。",
            },
        }

    def urdf_facts(self, robot):
        """Movable joints, control axes, mass and limit ranges read from the bundled URDF."""
        urdf_path = (self.root / str(robot.get("urdf_url", "")).lstrip("/")).resolve()
        facts = {"available": False}
        if not urdf_path.is_relative_to(self.root / "assets/robots") or not urdf_path.is_file():
            return facts
        try:
            tree = ET.fromstring(urdf_path.read_bytes())
        except ET.ParseError:
            return facts
        movable = [joint for joint in tree.findall("joint") if joint.get("type") != "fixed"]
        efforts, velocities = set(), set()
        for joint in movable:
            limit = joint.find("limit")
            if limit is not None:
                if limit.get("effort"):
                    efforts.add(float(limit.get("effort")))
                if limit.get("velocity"):
                    velocities.add(float(limit.get("velocity")))
        mass = sum(float(link.find("inertial/mass").get("value")) for link in tree.findall("link") if link.find("inertial/mass") is not None)
        controls = robot.get("control_joints") or []
        groups = {}
        for joint in movable:
            name = joint.get("name", "").lower()
            for group in ("waist", "hip", "knee", "ankle", "shoulder", "elbow", "wrist", "finger", "wheel"):
                if group in name:
                    groups.setdefault(group, {"joints": 0, "controlled": 0})
                    groups[group]["joints"] += 1
                    groups[group]["controlled"] += joint.get("name") in controls
                    break
        return {"available": True, "mass_kg": round(mass, 3), "movable_joints": len(movable), "control_joints": len(controls),
                "continuous_joints": sum(joint.get("type") == "continuous" for joint in movable),
                "effort_limits_nm": sorted(efforts), "velocity_limits_rad_s": sorted(velocities), "groups": groups,
                "urdf_sha256": robot.get("urdf_sha256"), "commit": robot.get("commit")}

    def specs(self):
        """SPEC comparison: authored catalog + URDF facts + simulated energy from completed Spark jobs."""
        path = self.root / "assets/specs/catalog.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {"schema": 1, "robots": {}, "motions": [], "joint_groups": {}}
        robots = {robot["id"]: robot for robot in self.catalog()["robots"] if robot.get("id")}
        for robot_type, entry in data.get("robots", {}).items():
            facts = self.urdf_facts(robots.get(robot_type, {}))
            entry["urdf"] = facts
            if facts.get("available"):
                entry.setdefault("specs", []).extend([
                    {"id": "mass", "label": "模型質量", "value": facts["mass_kg"], "unit": "kg", "status": "source",
                     "source": robots[robot_type].get("urdf_url"), "note": "URDF inertial mass 總和，是模型質量不是實機秤重。"},
                    {"id": "control", "label": "控制關節", "value": f"{facts['control_joints']} / {facts['movable_joints']}", "unit": "軸",
                     "status": "source", "source": "/assets/robots/manifest.json", "note": "策略 action 軸數／原生 URDF 可動關節數。"},
                    {"id": "limits", "label": "關節限制（模擬）", "value": " · ".join(f"{v:g} N·m" for v in facts["effort_limits_nm"]) + " / " + " · ".join(f"{v:g} rad/s" for v in facts["velocity_limits_rad_s"]),
                     "unit": "", "status": "source", "source": robots[robot_type].get("urdf_url"), "note": "URDF limit 的 effort / velocity 設定值，不是馬達實測。"},
                ])
        trained = []
        for job in self.all("jobs"):
            if job.get("mode") != "worker" or job.get("state") != "complete" or not job.get("result"):
                continue
            result = job["result"]
            mechanical = result.get("mechanical") or {}
            trained.append({"job_id": job["id"], "robot_type": job["robot_type"], "label": job.get("action_name"), "created_at": job.get("created_at"),
                            "passed": result.get("passed"), "video_url": result.get("video_url"), "rollout_url": result.get("rollout_url"),
                            "provenance": result.get("provenance"),
                            "power": {"status": "simulated" if mechanical else "unmeasured", "energy_j": mechanical.get("energy_j"),
                                      "mean_power_w": mechanical.get("mean_power_w"), "peak_power_w": mechanical.get("peak_power_w"),
                                      "peak_torque_nm_by_group": mechanical.get("peak_torque_nm_by_group"),
                                      "note": mechanical.get("scope") or "此工作沒有模擬機械功記錄。"}})
        for motion in data.get("motions", []):
            for robot_type, support in motion.get("support", {}).items():
                facts = (data.get("robots", {}).get(robot_type) or {}).get("urdf") or {}
                groups = facts.get("groups") or {}
                missing = [group for group in motion.get("groups", []) if group not in groups]
                support["joint_groups_present"] = not missing if facts.get("available") else None
                support["missing_groups"] = missing
        data["trained"] = trained
        data["generated_at"] = self.clock()
        return data

    def save_upload(self, filename, data):
        filename = Path(filename.replace("\\", "/")).name
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            raise APIError("支援 MP4、WebM、MOV 或 M4V 影片。")
        if not data or len(data) > MAX_UPLOAD:
            raise APIError("影片必須介於 1 byte 與 150 MB。", 413)
        # Container signature check prevents HTML or scripts disguised as a video.
        if not (data[4:8] == b"ftyp" or data.startswith(b"\x1aE\xdf\xa3")):
            raise APIError("檔案內容不是支援的影片容器。")
        identifier = uuid.uuid4().hex
        path = self.upload_dir / (identifier + suffix)
        path.write_bytes(data)
        value = {"id": identifier, "url": "/media/uploads/" + path.name, "filename": filename,
                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "created_at": self.clock(),
                 "path": path.name, "type": "upload"}
        self.put("uploads", value)
        return {k: v for k, v in value.items() if k != "path"}

    def child_env(self):
        env = os.environ.copy()
        prefixes = []
        for directory in (Path.home() / ".local/node/bin", Path.home() / ".local/bin"):
            if directory.is_dir():
                prefixes.append(str(directory))
        for executable in (self.downloader, self.ffmpeg):
            if executable:
                prefixes.append(str(Path(executable).resolve().parent))
        if prefixes:
            env["PATH"] = os.pathsep.join(dict.fromkeys(prefixes)) + os.pathsep + env.get("PATH", "")
        return env

    def run_checked(self, command, timeout, label):
        try:
            process = subprocess.run(command, capture_output=True, timeout=timeout, text=True, env=self.child_env())
        except (OSError, subprocess.TimeoutExpired):
            raise APIError(label + "逾時或無法執行。", 502)
        if process.returncode:
            detail = (process.stderr or process.stdout or "").strip().splitlines()
            hint = re.sub(r"https?://\S+", "", detail[-1] if detail else "")
            hint = hint.strip()[:180]
            raise APIError(label + "失敗。" + ((" " + hint) if hint else ""), 502)
        return process

    def ensure_youtube(self, url):
        if not self.downloader or not self.ffmpeg:
            raise APIError("尚未安裝 yt-dlp 與 ffmpeg。請安裝 requirements-motion.txt 後重啟服務。", 503)
        video_id = url.rsplit("v=", 1)[-1]
        folder = self.data_dir / "youtube"
        folder.mkdir(exist_ok=True)
        target = folder / (video_id + ".mp4")
        with self.youtube_lock:
            if target.is_file() and 0 < target.stat().st_size <= MAX_UPLOAD:
                try:
                    return target, media_duration(self.ffmpeg, target)
                except APIError:
                    target.unlink(missing_ok=True)
            template = str(folder / (video_id + ".%(ext)s"))
            command = [self.downloader, "--ignore-config", "--no-exec", "--no-playlist", "--no-cache-dir",
                       "--no-warnings", "--force-ipv4", "--socket-timeout", "20", "--retries", "2",
                       "--js-runtimes", "node", "--max-filesize", str(MAX_UPLOAD),
                       "--format", "b[height<=480][ext=mp4]/b[height<=720][ext=mp4]/b[ext=mp4]/18",
                       "--output", template, "--", url]
            try:
                self.run_checked(command, 180, "YouTube 下載")
            except APIError:
                for path in folder.glob(video_id + ".*"):
                    path.unlink(missing_ok=True)
                raise
            downloaded = [path for path in folder.glob(video_id + ".*") if path.suffix.lower() in VIDEO_EXTENSIONS]
            if not downloaded:
                raise APIError("YouTube 沒有留下可播放的影片檔。", 502)
            source = downloaded[0]
            if source.stat().st_size > MAX_UPLOAD:
                for path in downloaded:
                    path.unlink(missing_ok=True)
                raise APIError("影片超過 150 MB。請改選較短的影片，或上傳已裁好的片段。", 413)
            if source.suffix.lower() != ".mp4":
                converted = target.with_suffix(".transcode.mp4")
                self.run_checked([self.ffmpeg, "-y", "-i", str(source), "-an", "-c:v", "libx264", "-preset", "veryfast",
                                  "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(converted)],
                                 180, "影片轉檔")
                source.unlink(missing_ok=True)
                source = converted
            if source != target:
                source.replace(target)
            for path in folder.glob(video_id + ".*"):
                if path != target:
                    path.unlink(missing_ok=True)
            return target, media_duration(self.ffmpeg, target)

    def write_clip(self, source, start, end, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.run_checked([self.ffmpeg, "-y", "-i", str(source), "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                          "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", str(destination)], 120, "片段裁切")
        if not destination.is_file() or destination.stat().st_size <= 0:
            destination.unlink(missing_ok=True)
            raise APIError("裁切後沒有產生影片。", 502)
        return media_duration(self.ffmpeg, destination)

    def present_media(self, value):
        shown = {key: value[key] for key in (
            "id", "url", "filename", "size", "sha256", "created_at", "type", "youtube_url", "action_name",
            "duration_s", "source_start_s", "source_end_s", "source_duration_s") if key in value}
        if value.get("action_name"):
            shown["title"] = value["action_name"]
            shown["filename"] = value["action_name"]
        if "source_start_s" in value and "source_end_s" in value:
            shown["start_s"] = value["source_start_s"]
            shown["end_s"] = value["source_end_s"]
        if value.get("youtube_url"):
            shown["source_url"] = value["youtube_url"]
        return shown

    def store_clip(self, source, start, end, *, media_type, name, youtube_url=None, source_offset=0, source_duration=None, parent_id=None):
        identifier = uuid.uuid4().hex
        destination = self.upload_dir / (identifier + ".mp4")
        duration = self.write_clip(source, start, end, destination)
        data = destination.read_bytes()
        value = {"id": identifier, "url": "/media/uploads/" + destination.name, "filename": destination.name,
                 "path": destination.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "created_at": self.clock(), "type": media_type, "action_name": name,
                 "duration_s": round(duration, 3), "source_start_s": round(source_offset + start, 3),
                 "source_end_s": round(source_offset + start + duration, 3)}
        if youtube_url:
            value["youtube_url"] = youtube_url
        if source_duration is not None:
            value["source_duration_s"] = round(float(source_duration), 3)
        if parent_id:
            value["parent_id"] = parent_id
        self.put("uploads", value)
        return self.present_media(value)

    def ingest(self, payload):
        if not isinstance(payload, dict):
            raise APIError("請提供 YouTube 網址與片段秒數。")
        url = youtube_url(payload.get("youtube_url", payload.get("url", "")))
        name = action_label(payload)
        start, end = clip_bounds(payload)
        original, full_duration = self.ensure_youtube(url)
        if end > full_duration + .05:
            raise APIError(f"這支影片約 {full_duration:.2f} 秒，請把片段放在影片長度內。")
        return self.store_clip(original, start, end, media_type="youtube", name=name, youtube_url=url,
                               source_duration=full_duration)

    def save_clip(self, payload):
        if not isinstance(payload, dict):
            raise APIError("請提供要保存的影片片段。")
        if not self.ffmpeg:
            raise APIError("尚未安裝 ffmpeg，無法裁切片段。", 503)
        upload = self.get("uploads", payload.get("upload_id", ""))
        name = action_label(payload, upload.get("action_name") or "動作片段")
        source = self.upload_dir / upload.get("path", "")
        if not source.is_file():
            raise APIError("找不到已下載的影片。", 404)
        duration = upload.get("duration_s")
        if not isinstance(duration, (int, float)):
            duration = media_duration(self.ffmpeg, source)
        start, end = clip_bounds(payload, duration)
        if abs(start) < 1e-3 and abs(end - duration) < .08:
            upload["action_name"] = name
            self.put("uploads", upload)
            return self.present_media(upload)
        return self.store_clip(source, start, end, media_type=upload.get("type") or "upload", name=name,
                               youtube_url=upload.get("youtube_url"), source_offset=float(upload.get("source_start_s") or 0),
                               source_duration=upload.get("source_duration_s"), parent_id=upload["id"])

    def create_motion(self, payload):
        """Validate a browser geometric reference against the bundled URDF contract."""
        if not isinstance(payload, dict):
            raise APIError("動作資料必須是 JSON object。")
        try:
            encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError):
            raise APIError("動作資料包含非有限數值或無效格式。")
        if len(encoded.encode()) > MAX_MOTION:
            raise APIError("動作參考超過 12 MB，請縮短錄製片段。", 413)
        robot_type = payload.get("robot_type")
        robot = next((r for r in self.catalog()["robots"] if r.get("id") == robot_type), None)
        if not robot:
            raise APIError("找不到動作對應的機型。")
        urdf_path = (self.root / str(robot.get("urdf_url", "")).lstrip("/")).resolve()
        if not urdf_path.is_relative_to(self.root / "assets/robots") or not urdf_path.is_file():
            raise APIError("機型 URDF 尚未同步完成。", 503)
        try:
            source_bytes = urdf_path.read_bytes()
            urdf = ET.fromstring(source_bytes)
        except (OSError, ET.ParseError):
            raise APIError("機型 URDF 無法讀取。", 503)
        urdf_sha = hashlib.sha256(source_bytes).hexdigest()
        if payload.get("urdf_sha256") and payload["urdf_sha256"] != urdf_sha:
            raise APIError("此動作使用的 URDF 版本已變更，請重新錄製 retargeting。", 409)
        limits = {}
        for joint in urdf.findall("joint"):
            if joint.get("type") == "fixed":
                continue
            limit = joint.find("limit")
            limits[joint.get("name")] = (
                None if joint.get("type") == "continuous" or limit is None else float(limit.get("lower", "-inf")),
                None if joint.get("type") == "continuous" or limit is None else float(limit.get("upper", "inf")),
            )
        names = payload.get("joint_names")
        if not isinstance(names, list) or not names or len(names) > len(limits) or any(not isinstance(name, str) for name in names):
            raise APIError("joint_names 必須是原生 URDF 關節名稱陣列。")
        if len(set(names)) != len(names) or any(name not in limits for name in names):
            raise APIError("動作包含重複、固定或不屬於該 URDF 的關節。")
        frames = payload.get("frames")
        if not isinstance(frames, list) or not 2 <= len(frames) <= MAX_MOTION_FRAMES:
            raise APIError("請先播放並錄製至少 2 個 frame；最多支援 18,000 個 frame。")
        segment = self.normalize_segment(payload.get("segment")) if payload.get("segment") is not None else None
        previous_time = -1.0
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise APIError("frame 必須是 JSON object。")
            timestamp = frame.get("time")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not 0 <= timestamp <= 3600 or timestamp <= previous_time:
                raise APIError("frame time 必須是 0 至 3600 秒內嚴格遞增的有限數值。")
            previous_time = timestamp
            if "source_time" in frame and frame["source_time"] is not None:
                source_time = frame["source_time"]
                if isinstance(source_time, bool) or not isinstance(source_time, (int, float)) or not math.isfinite(source_time) or not 0 <= source_time <= 86400:
                    raise APIError("source_time 必須是有效影片秒數。")
                if segment and not segment["start_s"] - 1e-3 <= source_time <= segment["end_s"] + 1e-3:
                    raise APIError("frame source_time 超出選取的影片片段。")
            q = frame.get("q")
            joints = frame.get("joints", {})
            if not isinstance(q, list) or len(q) != len(names) or not isinstance(joints, dict):
                raise APIError("每個 frame 的 q 長度必須對應 joint_names。")
            if any(name not in names for name in joints):
                raise APIError("frame joints 包含未宣告的關節。")
            for name, value in zip(names, q):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1_000_000:
                    raise APIError("關節位置必須是有限的弧度數值。")
                lower, upper = limits[name]
                if (lower is not None and value < lower - 1e-6) or (upper is not None and value > upper + 1e-6):
                    raise APIError(f"frame {index} 的 {name} 超出原生 URDF 限位。")
                if name in joints:
                    mapped = joints[name]
                    if isinstance(mapped, bool) or not isinstance(mapped, (int, float)) or not math.isfinite(mapped) or abs(mapped - value) > 1e-7:
                        raise APIError("frame joints 與 q 的數值不一致。")
            if "confidence" in frame:
                confidence = frame["confidence"]
                if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                    raise APIError("confidence 必須介於 0 與 1。")
        metadata = {key: value for key, value in payload.items() if key != "frames"}
        if len(json.dumps(metadata, ensure_ascii=False).encode()) > 32768:
            raise APIError("動作 mapping metadata 超過 32 KB。", 413)
        motion = dict(payload)
        if segment:
            motion["segment"] = segment
        motion.update(id=uuid.uuid4().hex, created_at=self.clock(), urdf_sha256=urdf_sha,
                      frame_count=len(frames), duration_s=round(frames[-1]["time"] - frames[0]["time"], 6),
                      physical_simulation=False,
                      validation={"urdf_joint_names": True, "urdf_position_limits": True,
                                  "monotonic_timestamps": True, "physical_validation": False})
        motion["url"] = "/api/motions/" + motion["id"]
        self.put("motions", motion)
        return {key: motion[key] for key in ("id", "url", "robot_type", "frame_count", "duration_s", "urdf_sha256", "validation")}

    def normalize_source(self, source):
        if not isinstance(source, dict):
            raise APIError("請先選擇影片來源。")
        if not isinstance(source.get("type"), str):
            raise APIError("影片來源格式不正確。")
        if source.get("type") == "video_example":
            example = next((e for e in self.catalog()["video_examples"] if e.get("id") == source.get("id")), None)
            if not example:
                raise APIError("找不到分段影片範例。")
            original_url = youtube_url(example.get("source_url", ""))
            start, end = example.get("start_s"), example.get("end_s")
            if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or end <= start):
                raise APIError("分段影片來源時間設定不正確。", 503)
            return {"type": "video_example", "id": example["id"], "title": example.get("title", example["id"]),
                    "robot_type": example.get("robot_type"), "url": example.get("video_url"),
                    "source_url": original_url, "source_video_id": example.get("source_video_id"),
                    "source_start_s": start, "source_end_s": end, "duration_s": end - start,
                    "tracking_scope": example.get("tracking_scope", "robot_video_reference"),
                    "requires_manual_review": True, "is_training_result": False}
        if source.get("type") == "example":
            if source.get("id") == "kungfu-demo":
                return {"type": "example", "id": "kungfu-demo", "procedural": True,
                        "description": "功夫概念流程的程序化骨架；不是影片辨識或新訓練結果。"}
            examples = self.catalog()["examples"]
            example = next((e for e in examples if e.get("id") == source.get("id")), None)
            if not example:
                raise APIError("找不到示範影片。")
            return {"type": "example", "id": example["id"], "url": example.get("source_video_url", example.get("video_url", example.get("video")))}
        if source.get("type") in {"upload", "youtube"} and source.get("id"):
            upload = self.get("uploads", source["id"])
            kept = ("type", "id", "url", "filename", "youtube_url", "action_name", "duration_s",
                    "source_start_s", "source_end_s", "source_duration_s", "sha256")
            return {key: upload[key] for key in kept if key in upload}
        # YouTube can remain a planning source without pretending a blocked download succeeded.
        if source.get("type") == "youtube":
            return {"type": "youtube", "url": youtube_url(source.get("url", "")), "downloaded": False}
        raise APIError("無效的影片來源。")

    def normalize_segment(self, segment, source=None):
        """Local playback bounds plus immutable original-source offsets for a worker."""
        source = source or {}
        duration = source.get("duration_s")
        if segment is None:
            if source.get("type") != "video_example":
                return None
            segment = {"start_s": 0, "end_s": duration}
        if not isinstance(segment, dict):
            raise APIError("segment 必須包含 start_s 和 end_s。")
        start, end = segment.get("start_s"), segment.get("end_s")
        if (isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, (int, float))
                or not isinstance(end, (int, float)) or not math.isfinite(start) or not math.isfinite(end)):
            raise APIError("片段起訖時間必須是有限秒數。")
        if start < 0 or end > 86400 or not .5 <= end - start <= 60:
            raise APIError("片段長度必須介於 0.5 至 60 秒，且起訖時間不可為負數或反向。")
        if isinstance(duration, (int, float)) and end > duration + 1e-3:
            raise APIError("所選片段超出可播放影片的長度。")
        offset = source.get("source_start_s", 0)
        return {"start_s": round(start, 6), "end_s": round(end, 6), "duration_s": round(end - start, 6),
                "source_start_s": round(offset + start, 6), "source_end_s": round(offset + end, 6),
                "timebase": "playable_video_seconds",
                "source_timebase": "original_video_seconds" if source.get("type") == "video_example" else "playable_video_seconds",
                "duration_verified": source.get("type") == "video_example"}

    def create_job(self, payload):
        if not isinstance(payload, dict):
            raise APIError("工作內容必須是 JSON object。")
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError):
            raise APIError("工作內容包含無效數值。")
        robot = payload.get("robot_type")
        if not isinstance(robot, str) or robot not in {"leg", "wheel"}:
            raise APIError("robot_type 必須是 leg 或 wheel。")
        mode = payload.get("mode", "demo")
        if not isinstance(mode, str) or mode not in {"demo", "worker"}:
            raise APIError("模式必須是 demo 或 worker。")
        config = payload.get("config", {})
        if not isinstance(config, dict):
            raise APIError("config 必須是 JSON object。")
        config = {key: value for key, value in config.items() if key in {"library", "physics", "landmark", "rewards", "hours"}}
        aliases = {"taipei": "taipei101", "paris": "eiffel", "tokyo": "shibuya", "newyork": "times-square", "greatwall": "great-wall"}
        if not isinstance(config.get("landmark", "taipei101"), str):
            raise APIError("地標選項格式不正確。")
        config["landmark"] = aliases.get(config.get("landmark"), config.get("landmark", "taipei101"))
        for key, choices, default in (("library", LIBRARIES, "rsl_rl"), ("physics", PHYSICS, "isaacsim_physx"), ("landmark", LANDMARKS, "taipei101")):
            config.setdefault(key, default)
            if not isinstance(config[key], str) or config[key] not in {item["id"] for item in choices}:
                raise APIError("不支援的訓練選項：" + key)
        try:
            hours = float(config.get("hours", 2))
        except (ValueError, TypeError):
            raise APIError("訓練時數必須是數字。")
        if not math.isfinite(hours) or not .25 <= hours <= 72:
            raise APIError("訓練規劃為 0.25 至 72 小時。")
        config["hours"] = hours
        rewards = config.get("rewards", ["motion_tracking", "balance", "smoothness"])
        allowed = {r["id"] for r in REWARDS}
        if not isinstance(rewards, list) or not rewards or any(not isinstance(r, str) or r not in allowed for r in rewards):
            raise APIError("請選擇至少一個有效 reward。")
        if robot == "wheel" and "foot_contact" in rewards:
            raise APIError("足部接觸 reward 適用於雙足；雙輪請選輪速追蹤。")
        if robot == "leg" and "wheel_velocity" in rewards:
            raise APIError("輪速追蹤 reward 適用於雙輪。")
        config["rewards"] = list(dict.fromkeys(rewards))
        source = self.normalize_source(payload.get("source"))
        if source.get("robot_type") and source["robot_type"] != robot:
            raise APIError("影片範例與所選 robot type 不一致。")
        segment = self.normalize_segment(payload.get("segment"), source)
        motion = None
        if payload.get("motion_id"):
            motion = self.get("motions", payload["motion_id"])
            if motion["robot_type"] != robot:
                raise APIError("動作參考與所選 robot type 不一致。")
            if motion.get("segment"):
                if segment is None:
                    segment = self.normalize_segment(motion["segment"], source)
                elif any(abs(motion["segment"][key] - segment[key]) > 1e-6 for key in ("start_s", "end_s")):
                    raise APIError("動作參考與所選影片片段不一致，請重新映射。")
            if segment:
                for frame in motion["frames"]:
                    source_time = frame.get("source_time")
                    if source_time is not None and not segment["start_s"] - 1e-3 <= source_time <= segment["end_s"] + 1e-3:
                        raise APIError("動作參考含有所選影片片段之外的 frame，請重新映射。")
            current_robot = next((r for r in self.catalog()["robots"] if r.get("id") == robot), {})
            if current_robot.get("urdf_sha256") and current_robot["urdf_sha256"] != motion["urdf_sha256"]:
                raise APIError("動作參考對應的 URDF 已更新，請重新錄製。", 409)
        target = payload.get("target_person", {"id": 0, "color": "#42a5ff"})
        if not isinstance(target, (dict, int, str)):
            raise APIError("人物目標格式不正確。")
        action_name = payload.get("action_name", source.get("title", "Q1 Motion"))
        if not isinstance(action_name, str) or not 1 <= len(action_name.strip()) <= 80:
            raise APIError("動作名稱必須為 1 至 80 字。")
        action_name = action_name.strip()
        now = self.clock()
        job = {"id": uuid.uuid4().hex, "robot_type": robot, "mode": mode, "source": source,
               "target_person": target, "config": config, "action_name": action_name, "created_at": now,
               "state": "queued_for_worker" if mode == "worker" else "retargeting", "progress": 0,
               "result": None, "is_demo": mode == "demo", "newly_trained": False,
               "label": "概念流程示範；不執行 PPO" if mode == "demo" else "等待 Spark GPU worker",
               "plan": self.worker_plan(robot, config, source, target)}
        job["plan"]["job_id"] = job["id"]
        job["plan"]["action_name"] = action_name
        job["plan"]["motion_extraction_ready"] = bool(motion and not motion.get("demo", False))
        if mode == "worker":
            trainable = robot == "leg" and bool(motion)
            job["stage_label"] = "等待 Spark GPU worker"
            job["training"] = {"budget_s": hours * 3600., "trainable": trainable,
                               "blocker": None if trainable else ("雙輪 Isaac Lab 任務尚未接入" if robot != "leg" else "缺少 MediaPipe 關節參考")}
            job["plan"]["executor"] = "q1_store_worker" if trainable else None
            job["plan"]["trainable"] = trainable
        if segment:
            job["segment"] = segment
            job["plan"]["segment"] = dict(segment)
        if source.get("type") == "video_example":
            job["plan"]["reference_scope"] = "機器人影片分段參考；MediaPipe 為人體模型，辨識結果必須人工檢查，不代表 Q1 新策略已訓練。"
        if motion:
            job["motion_id"] = motion["id"]
            job["plan"]["motion"] = {key: motion[key] for key in ("id", "url", "robot_type", "frame_count", "duration_s", "urdf_sha256", "validation")}
            job["plan"]["motion"]["source"] = motion.get("source")
            job["plan"]["motion"]["mapping"] = motion.get("mapping")
            job["plan"]["motion"]["demo"] = motion.get("demo")
            if motion.get("segment"):
                job["plan"]["motion"]["segment"] = motion["segment"]
        self.put("jobs", job)
        return self.job(job["id"])

    def worker_plan(self, robot, config, source, target):
        task = "Q1-Leg-Motion-Tracking-v0" if robot == "leg" else "Q1-Wheel-Motion-Tracking-v0"
        manifest_robot = next((entry for entry in self.catalog()["robots"] if entry.get("id") == robot), {})
        robot_reference = {key: manifest_robot.get(key) for key in (
            "id", "urdf_url", "urdf_sha256", "control_joints", "default_joint_positions")}
        robot_reference.update(source_repository=manifest_robot.get("repository"), source_commit=manifest_robot.get("commit"))
        train = ["uv", "run", "isaaclab", "train", "--rl_library", config["library"], "--task", task, "physics=" + config["physics"]]
        play = ["uv", "run", "--extra", "isaacsim,video", "isaaclab", "play", "--rl_library", config["library"], "--task", task,
                "--checkpoint", "<verified-checkpoint.pt>", "physics=" + config["physics"], "--video", "viz:kit", "--viz", "kit"]
        return {"schema": "q1-store.worker.v1", "task": task, "task_registered": False,
                "robot_type": robot, "robot": robot_reference, "config": dict(config),
                "train_command": train, "play_command": play, "source": source, "target_person": target,
                "budget_hours": config["hours"], "rewards": config["rewards"], "landmark": config["landmark"],
                "requires": ["安裝並註冊 Q1 Isaac Lab 3 任務 adapter", "以選定人物的 MediaPipe 關節資料產生 motion reference",
                             "將 reward 預設映射至任務 RewardTermCfg", "驗證所選 physics preset 與 checkpoint 相容",
                             "準備授權地標 USD 場景與 RTX camera", "錄製實際 checkpoint 的 rollout 並附評估證據"],
                "note": "這是規劃清單，不會自動執行指令。Q1 task adapter 和地標 USD 尚待接入；時數為預算，非成功保證。"}

    def job(self, identifier, log_lines=40):
        job = self.get("jobs", identifier)
        elapsed = max(0, self.clock() - job["created_at"])
        job["elapsed_s"] = round(elapsed, 2)
        if job["mode"] == "worker":
            return self.decorate_worker_job(job, log_lines)
        if job.get("state") == "cancelled":
            return job
        fraction = min(1, elapsed / DEMO_DURATION)
        job["progress"] = round(fraction * 100)
        job["state"] = "retargeting" if fraction < .2 else "training_demo" if fraction < .8 else "rendering_demo" if fraction < 1 else "demo_complete"
        job["stage_label"] = {"retargeting": "準備 retargeting 預覽", "training_demo": "PPO 訓練流程示範（未執行訓練）", "rendering_demo": "整理既有模擬影片", "demo_complete": "示範完成 · 播放既有成果"}[job["state"]]
        if fraction == 1:
            example = next((e for e in self.catalog()["examples"] if e.get("robot_type") == job["robot_type"]), None)
            if example:
                job["result"] = {"video_url": example.get("video_url", example.get("video")), "example_id": example["id"],
                                 "title": example.get("title", "既有訓練成果"), "provenance": example.get("provenance", "既有 repository 模擬影片"),
                                 "newly_trained": False, "landmark_rendered": False,
                                 "compatible_with_current_urdf": example.get("compatible_with_current_urdf"),
                                 "label": "既有 PPO 模擬成果；不是本次影片新訓練或地標 RTX 渲染"}
        return job

    def decorate_worker_job(self, job, log_lines=40):
        """Attach live worker evidence (log tail, ETA, liveness) without mutating the stored row."""
        worker = self.worker_status()
        directory = self.jobs_dir / job["id"]
        training = dict(job.get("training") or {})
        state = job.get("state")
        job["stage_label"] = job.get("stage_label") or {"queued_for_worker": "等待 Spark GPU worker"}.get(state, state)
        job["worker"] = worker
        if state in WORKER_ACTIVE_STATES and state != "queued_for_worker" and not worker["online"]:
            job["stage_label"] = "worker 離線 · 重新啟動 worker 後會接續"
            training["stalled"] = True
        if state == "queued_for_worker":
            ahead = [other for other in self.all("jobs") if other.get("mode") == "worker" and other["id"] != job["id"]
                     and other.get("state") in WORKER_ACTIVE_STATES and other["created_at"] < job["created_at"]]
            training["queue_position"] = len(ahead) + 1
            if not training.get("trainable", True):
                job["stage_label"] = "無法訓練：" + str(training.get("blocker"))
        if training.get("eta_at") and state in WORKER_ACTIVE_STATES:
            training["eta_s"] = max(0., round(float(training["eta_at"]) - self.clock(), 1))
        job["training"] = training
        if log_lines and directory.is_dir():
            from .worker import tail_lines
            job["log_tail"] = tail_lines(directory / "native.log", log_lines)
            job["log_url"] = f"/media/jobs/{job['id']}/native.log"
        else:
            job["log_tail"] = []
        return job

    def jobs_summary(self, limit=50):
        keys = ("id", "robot_type", "mode", "state", "progress", "action_name", "created_at", "finished_at", "stage_label", "label", "is_demo", "newly_trained")
        items = []
        for job in self.all("jobs")[:limit]:
            current = self.job(job["id"], log_lines=0)
            summary = {key: current.get(key) for key in keys}
            summary["training"] = {key: (current.get("training") or {}).get(key) for key in ("eta_s", "budget_s", "num_envs", "queue_position", "trainable", "blocker")}
            result = current.get("result") or {}
            summary["result"] = {key: result.get(key) for key in ("video_url", "rollout_url", "passed", "newly_trained", "label")} if result else None
            items.append(summary)
        return items

    def job_media(self, identifier, name):
        if name not in JOB_MEDIA:
            raise APIError("找不到檔案。", 404)
        self.get("jobs", identifier)
        path = (self.jobs_dir / identifier / name).resolve()
        if not path.is_relative_to(self.jobs_dir) or not path.is_file():
            raise APIError("此工作尚未產生這個檔案。", 404)
        return path, JOB_MEDIA[name]

    def cancel(self, identifier):
        job = self.job(identifier)
        if job["state"] in ("demo_complete", "complete"):
            raise APIError("工作已完成，無需取消。", 409)
        if job["state"] in ("failed", "cancelled"):
            raise APIError("工作已結束。", 409)
        job["state"] = "cancelled"
        job["stage_label"] = "工作已取消"
        job["cancelled_at"] = self.clock()
        for key in ("log_tail", "log_url", "worker", "elapsed_s"):
            job.pop(key, None)
        return self.put("jobs", job)

    def create_listing(self, payload):
        job = self.job(payload.get("job_id", ""))
        if job["state"] not in ("demo_complete", "complete") or not job.get("result"):
            raise APIError("請完成示範流程或真實訓練後再分享。", 409)
        real = job["state"] == "complete" and bool(job["result"].get("newly_trained"))
        title = str(payload.get("title", "")).strip()
        if not 1 <= len(title) <= 100:
            raise APIError("請輸入 1 至 100 字的動作名稱。")
        try:
            price = float(payload.get("price", 0))
        except (TypeError, ValueError):
            raise APIError("價格必須是數字。")
        if not math.isfinite(price) or not 0 <= price <= 9999:
            raise APIError("示範價格必須介於 0 與 9999。")
        listing = {"id": uuid.uuid4().hex, "job_id": job["id"], "robot_type": job["robot_type"],
                   "title": title, "description": str(payload.get("description", ""))[:1000], "price": round(price, 2),
                   "currency": "USD", "is_demo": not real, "newly_trained": real, "video_url": job["result"].get("video_url"),
                   "rollout_url": job["result"].get("rollout_url"), "passed": job["result"].get("passed"),
                   "created_at": self.clock(), "provenance": job["result"]["provenance"],
                   "compatible_with_current_urdf": job["result"].get("compatible_with_current_urdf"),
                   "label": "Spark 新訓練 PPO 成果 · 模擬交易不收費" if real else "示範上架 · 既有模擬影片 · 不收取費用"}
        return self.put("listings", listing)

    def robot_contract(self, robot_type):
        """Resolve the bundled contract without accepting caller-supplied model metadata."""
        robot = next((r for r in self.catalog()["robots"] if r.get("id") == robot_type), None)
        if not robot:
            raise APIError("此機型的原生模型尚未同步。", 503)
        sha = robot.get("urdf_sha256")
        controls = robot.get("control_joints")
        if (not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                or not isinstance(controls, list) or not controls
                or any(not isinstance(name, str) for name in controls)):
            raise APIError("此機型的 URDF／控制契約尚未完整。", 503)
        urdf_path = (self.root / str(robot.get("urdf_url", "")).lstrip("/")).resolve()
        if not urdf_path.is_relative_to(self.root / "assets/robots") or not urdf_path.is_file():
            raise APIError("此機型的原生 URDF 檔案尚未同步。", 503)
        if hashlib.sha256(urdf_path.read_bytes()).hexdigest() != sha:
            raise APIError("原生 URDF 檔案與版本契約不一致，請重新同步。", 409)
        return {"id": robot_type, "urdf_url": robot.get("urdf_url"), "urdf_sha256": sha,
                "control_joints": controls, "default_joint_positions": robot.get("default_joint_positions", {}),
                "joints": robot.get("joints", []), "source_repository": robot.get("repository"),
                "source_commit": robot.get("commit")}

    def register_robot(self, payload):
        if not isinstance(payload, dict):
            raise APIError("機器人註冊資料必須是 JSON object。")
        robot_type, name, serial = payload.get("robot_type"), payload.get("name"), payload.get("serial")
        if not isinstance(robot_type, str) or robot_type not in {"leg", "wheel"}:
            raise APIError("註冊機型必須是 leg 或 wheel。")
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 60
                or any(ord(char) < 32 for char in name)):
            raise APIError("請輸入 1 至 60 字的機器人名稱。")
        if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", serial):
            raise APIError("序號需為 1 至 80 個英數、點、冒號、底線或連字號。")
        contract = self.robot_contract(robot_type)
        with self.lock:
            if any(r["serial"].casefold() == serial.casefold() for r in self.all("registered_robots")):
                raise APIError("這個序號已註冊。", 409)
            robot = {"id": uuid.uuid4().hex, "name": name.strip(), "robot_type": robot_type, "serial": serial,
                     "urdf_sha256": contract["urdf_sha256"], "control_joints": contract["control_joints"],
                     "source_commit": contract.get("source_commit"), "created_at": self.clock(),
                     "connection_state": "not_connected", "hardware_delivery": False}
            return self.put("registered_robots", robot)

    @staticmethod
    def package_json(value):
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode()

    def create_deployment(self, payload):
        """Prepare an inert package addressed to a registered robot; never contact hardware."""
        if not isinstance(payload, dict):
            raise APIError("Deploy 內容必須是 JSON object。")
        job = self.job(payload.get("job_id", ""), log_lines=0)
        if job.get("state") not in ("demo_complete", "complete") or not job.get("result"):
            raise APIError("請先完成動作流程，再下載 Deploy 套件。", 409)
        target = self.get("registered_robots", payload.get("robot_id", ""))
        if target["robot_type"] != job["robot_type"]:
            raise APIError("註冊機器人與訓練工作的 robot type 不一致。", 409)
        contract = self.robot_contract(job["robot_type"])
        job_sha = job.get("plan", {}).get("robot", {}).get("urdf_sha256")
        if contract["urdf_sha256"] != target["urdf_sha256"] or contract["urdf_sha256"] != job_sha:
            raise APIError("機器人、工作與目前 URDF 的版本不一致，請重新核對註冊及訓練工作。", 409)
        if target["control_joints"] != contract["control_joints"]:
            raise APIError("註冊機器人的控制關節契約已變更。", 409)
        identifier = uuid.uuid4().hex
        deployment = {"id": identifier, "job_id": job["id"], "robot_id": target["id"],
                      "robot_type": job["robot_type"], "action_name": job.get("action_name", "Q1 Motion"),
                      "target_robot": target, "urdf_sha256": contract["urdf_sha256"],
                      "status": "package_ready", "package_kind": "preview_only", "transport": "browser_download",
                      "deployable": False, "hardware_activation_allowed": False, "checkpoint_included": False,
                      "device_acknowledged": False, "newly_trained": False, "created_at": self.clock(),
                      "compatible_with_current_urdf": job["result"].get("compatible_with_current_urdf"),
                      "download_url": "/api/deployments/" + identifier + "/download",
                      "label": "Demo Deploy 套件；不含可執行策略，尚未傳送至機器人。"}
        files = {"job-plan.json": self.package_json(job["plan"]),
                 "robot-contract.json": self.package_json(contract)}
        if job.get("motion_id"):
            motion = self.get("motions", job["motion_id"])
            if motion["robot_type"] != target["robot_type"] or motion["urdf_sha256"] != contract["urdf_sha256"]:
                raise APIError("動作資料與部署機器人的模型不一致。", 409)
            files["motion.json"] = self.package_json(motion)
        files["README.txt"] = (
            "Q1 Store Demo Deployment Package\n"
            "Target: " + target["name"] + " / " + target["serial"] + "\n"
            "This preview-only package contains no runnable PPO checkpoint or hardware commands.\n"
            "Downloading it does not transfer or activate a policy on the registered robot.\n"
            "A robot-side agent, a compatible verified policy artifact and device acknowledgment are not connected yet.\n"
            "motion.json, when present, is an unvalidated geometric reference, not a control policy.\n"
            "For future deployment, the robot agent must check the target identity, URDF hash, joint order, policy version and evaluation evidence.\n"
            "The historical video and any legacy wheel policy are not included as a runnable checkpoint.\n"
        ).encode()
        manifest = dict(deployment)
        manifest.update(schema="q1-store.deployment.v1", hardware_delivery=False,
                        files=[{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
                               for name, data in sorted(files.items())],
                        result_provenance=job["result"].get("provenance"),
                        required_integrations=["verified compatible policy artifact", "authenticated robot-side download agent", "device acknowledgment"])
        files["manifest.json"] = self.package_json(manifest)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        content = stream.getvalue()
        deployment.update(package_sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
        (self.deployment_dir / (identifier + ".zip")).write_bytes(content)
        return self.put("deployments", deployment)

    def deployment_package(self, identifier):
        deployment = self.get("deployments", identifier)
        path = self.deployment_dir / (deployment["id"] + ".zip")
        try:
            content = path.read_bytes()
        except OSError:
            raise APIError("Deploy 套件已不存在，請重新建立。", 404)
        if hashlib.sha256(content).hexdigest() != deployment.get("package_sha256"):
            raise APIError("Deploy 套件完整性檢查失敗，請重新建立。", 409)
        return deployment, content

    def purchase(self, payload):
        listing = self.get("listings", payload.get("listing_id", ""))
        receipt = {"id": uuid.uuid4().hex, "listing_id": listing["id"], "title": listing["title"],
                   "price": listing["price"], "charged_amount": 0, "currency": "USD", "is_demo": True,
                   "created_at": self.clock(), "label": "示範收藏／購買完成，未收取費用。"}
        return self.put("purchases", receipt)
