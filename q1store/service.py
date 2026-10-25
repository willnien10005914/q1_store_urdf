"""Persistent jobs and marketplace records; the demo never launches GPU work."""
from __future__ import annotations

import hashlib
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


class Store:
    def __init__(self, root, data_dir=None, clock=time.time):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir or os.environ.get("Q1_STORE_DATA", self.root / ".data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = self.data_dir / "uploads"
        self.upload_dir.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "store.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        self.downloader = os.environ.get("Q1_YTDLP") or shutil.which("yt-dlp")
        with self.connection() as db:
            for table in ("jobs", "uploads", "motions", "listings", "purchases"):
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

    def catalog(self):
        return {
            "robots": read_collection(self.root / "assets/robots/manifest.json", "robots"),
            "examples": read_collection(self.root / "assets/examples/catalog.json", "examples"),
            "video_examples": read_collection(self.root / "assets/video-examples/catalog.json", "examples"),
            "video_sources": read_collection(self.root / "assets/video-examples/catalog.json", "sources"),
            "capabilities": {
                "libraries": LIBRARIES, "physics": PHYSICS, "rewards": REWARDS, "landmarks": LANDMARKS,
                "demo_duration_s": DEMO_DURATION, "worker_available": False,
                "youtube_ingest": bool(self.downloader), "upload": True, "motion_storage": True,
                "video_segments": True, "segment_min_s": .5, "segment_max_s": 60,
                "payments": "demo_only", "training": "demo_or_external_worker_manifest",
                "physics_scope": "實際後端支援須由 Q1 task adapter 驗證；選單是訓練規劃。",
                "rewards_scope": "Q1 任務的 reward 設計預設，並非所有 Isaac Lab 任務的通用選項。",
            },
        }

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

    def ingest(self, payload):
        url = youtube_url(payload.get("youtube_url", payload.get("url", "")))
        if not self.downloader:
            raise APIError("尚未安裝 yt-dlp。請先上傳影片，或安裝 requirements-motion.txt 後重啟。", 503)
        identifier = uuid.uuid4().hex
        template = str(self.upload_dir / (identifier + ".%(ext)s"))
        command = [self.downloader, "--ignore-config", "--no-exec", "--no-playlist", "--no-cache-dir", "--no-warnings", "--quiet",
                   "--socket-timeout", "15", "--retries", "1", "--max-filesize", str(MAX_UPLOAD),
                   "--restrict-filenames", "--format", "best[ext=mp4][height<=480]/best[ext=webm][height<=480]",
                   "--output", template, "--", url]
        try:
            process = subprocess.run(command, capture_output=True, timeout=90, text=True)
        except (OSError, subprocess.TimeoutExpired):
            raise APIError("YouTube 下載逾時或服務無法執行；請上傳有權使用的本機影片。", 502)
        paths = [p for p in self.upload_dir.glob(identifier + ".*") if p.suffix.lower() in VIDEO_EXTENSIONS]
        if process.returncode or not paths:
            for path in self.upload_dir.glob(identifier + ".*"):
                path.unlink(missing_ok=True)
            raise APIError("YouTube 目前無法下載此影片（可能受登入、地區或網站限制）。請上傳本機影片。", 502)
        path = paths[0]
        if path.stat().st_size > MAX_UPLOAD:
            path.unlink()
            raise APIError("影片超過 150 MB。", 413)
        value = {"id": identifier, "url": "/media/uploads/" + path.name, "filename": path.name,
                 "path": path.name, "size": path.stat().st_size, "type": "youtube", "youtube_url": url,
                 "created_at": self.clock()}
        self.put("uploads", value)
        return {k: v for k, v in value.items() if k != "path"}

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
            return {k: upload[k] for k in ("type", "id", "url", "filename")}
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
               "label": "概念流程示範；不執行 PPO" if mode == "demo" else "等待外部 Isaac Lab 3 worker",
               "plan": self.worker_plan(robot, config, source, target)}
        job["plan"]["job_id"] = job["id"]
        job["plan"]["action_name"] = action_name
        job["plan"]["motion_extraction_ready"] = bool(motion and not motion.get("demo", False))
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

    def job(self, identifier):
        job = self.get("jobs", identifier)
        elapsed = max(0, self.clock() - job["created_at"])
        job["elapsed_s"] = round(elapsed, 2)
        if job.get("state") == "cancelled" or job["mode"] == "worker":
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

    def cancel(self, identifier):
        job = self.job(identifier)
        if job["state"] == "demo_complete":
            raise APIError("示範已完成，無需取消。", 409)
        job["state"] = "cancelled"
        job["cancelled_at"] = self.clock()
        return self.put("jobs", job)

    def create_listing(self, payload):
        job = self.job(payload.get("job_id", ""))
        if job["state"] != "demo_complete" or not job.get("result"):
            raise APIError("請完成示範流程後再分享。", 409)
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
                   "currency": "USD", "is_demo": True, "newly_trained": False, "video_url": job["result"]["video_url"],
                   "created_at": self.clock(), "provenance": job["result"]["provenance"],
                   "compatible_with_current_urdf": job["result"].get("compatible_with_current_urdf"),
                   "label": "示範上架 · 既有模擬影片 · 不收取費用"}
        return self.put("listings", listing)

    def purchase(self, payload):
        listing = self.get("listings", payload.get("listing_id", ""))
        receipt = {"id": uuid.uuid4().hex, "listing_id": listing["id"], "title": listing["title"],
                   "price": listing["price"], "charged_amount": 0, "currency": "USD", "is_demo": True,
                   "created_at": self.clock(), "label": "示範收藏／購買完成，未收取費用。"}
        return self.put("purchases", receipt)
