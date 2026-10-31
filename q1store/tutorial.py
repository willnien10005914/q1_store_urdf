"""Isaac Lab 3 tutorial backend: lessons, users and progress, quizzes, sandboxed example runs,
course proposals discovered on the web, and the ticker that surfaces them.

Lesson content is authored on disk under ``tutorials/`` (json + md + example.py) so Cursor can add
courses by adding a directory. Per-user state lives in ``<data>/tutorial.sqlite3``; example runs
write to ``<data>/tutorial/runs/<id>/`` using the same ``progress.json`` / ``latest.pt`` contract
as the Spark worker, so a run keeps going when the browser (or the Pinggy tunnel) drops.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import research
from .service import APIError

RUN_ACTIVE = ("queued", "starting", "running")
RUN_FINAL = ("complete", "failed", "cancelled", "blocked", "interrupted")
MAX_CODE = 200_000
PASS_SCORE = 60
PROPOSAL_DIRS = ("pending", "approved", "rejected")
DEFAULT_TOPICS = [
    "Isaac Lab 3.0 release notes new features",
    "Isaac Lab Newton MuJoCo Warp physics backend tutorial",
    "Isaac Lab 3 migration ProxyArray actuators target_command",
    "Isaac Sim 5 URDF importer articulation",
    "Isaac Lab visualizer viser rerun recording",
    "Isaac Lab RSL-RL PPO legged robot sim-to-real",
]
CATEGORY_HINTS = {
    "urdf": ("urdf", "usd", "importer", "mesh", "asset"), "actuator": ("actuator", "pd gain", "motor", "torque", "effort"),
    "physics": ("physx", "newton", "mujoco", "warp", "solver", "contact", "physics"), "training": ("ppo", "rl", "reinforcement", "reward", "policy", "rsl"),
    "recording": ("video", "render", "visualizer", "viser", "rerun", "camera", "stream"), "deployment": ("deploy", "onnx", "sim-to-real", "sim2real", "export"),
    "migration": ("migrat", "3.0", "breaking", "upgrade", "deprecated"), "architecture": ("install", "uv", "cli", "launch", "preset"),
}


def now_iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).isoformat(timespec="seconds")


def slugify(text):
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return text[:60] or "course"


def tail(path, count=60, max_bytes=128 * 1024):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            data = stream.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line.rstrip()) for line in data.splitlines()]
    return [line for line in lines if line][-count:]


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def load_json_file(path):
    """Load JSON written by lesson scripts; tolerate Python's non-standard NaN/Infinity."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    text = re.sub(r"\bNaN\b", "null", text)
    text = re.sub(r"\b-?Infinity\b", "null", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_front_matter(text):
    """Minimal YAML-ish front matter: scalars and '- ' lists between two '---' lines."""
    meta, body = {}, text
    if text.startswith("---"):
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            head, body = parts[0][3:], parts[1].lstrip("\n")
            key = None
            for line in head.splitlines():
                if not line.strip():
                    continue
                if line.startswith(("  - ", "- ")) and key:
                    meta.setdefault(key, [])
                    if isinstance(meta[key], list):
                        meta[key].append(line.strip()[2:].strip())
                    continue
                if ":" in line and not line.startswith(" "):
                    key, _, value = line.partition(":")
                    key, value = key.strip(), value.strip()
                    meta[key] = value if value else []
    return meta, body


def dump_front_matter(meta, body):
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif value is not None:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


class Tutorial:
    def __init__(self, root, data_dir, clock=time.time, isaaclab3_root=None, python=None, start_threads=True):
        self.root = Path(root).resolve()
        self.tutorial_root = self.root / "tutorials"
        self.lessons_dir = self.tutorial_root / "lessons"
        self.proposals_dir = self.tutorial_root / "proposals"
        self.data_dir = Path(data_dir).resolve()
        self.runs_dir = self.data_dir / "tutorial" / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "tutorial.sqlite3"
        self.clock = clock
        self.lock = threading.RLock()
        self.python = python or os.environ.get("Q1_TUTORIAL_PYTHON") or sys.executable
        raw_root = isaaclab3_root or os.environ.get("Q1_ISAACLAB3_ROOT")
        self.isaaclab3_root = Path(raw_root).expanduser().resolve() if raw_root else None
        self.uv = shutil.which("uv")
        self.ffmpeg = shutil.which("ffmpeg")
        self._lesson_cache = {}
        self.pollers = {}
        with self.connection() as db:
            for table in ("users", "progress", "runs", "messages", "memory", "settings", "seen", "ticker"):
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, k1 TEXT, k2 TEXT, payload TEXT NOT NULL)")
        self.discovery = Discovery(self)
        self._reconcile_runs()
        if start_threads:
            self.discovery.start()

    # --------------------------------------------------------------- storage
    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    def put(self, table, value, k1=None, k2=None):
        with self.lock, self.connection() as db:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?)",
                       (value["id"], k1, k2, json.dumps(value, ensure_ascii=False, allow_nan=False)))
        return value

    def get(self, table, identifier, default=None):
        with self.connection() as db:
            row = db.execute(f"SELECT payload FROM {table} WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else default

    def select(self, table, k1=None, k2=None, limit=None, newest=True):
        clauses, args = [], []
        if k1 is not None:
            clauses.append("k1=?")
            args.append(k1)
        if k2 is not None:
            clauses.append("k2=?")
            args.append(k2)
        sql = f"SELECT payload FROM {table}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        sql += " ORDER BY rowid " + ("DESC" if newest else "ASC") + (f" LIMIT {int(limit)}" if limit else "")
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute(sql, args)]

    def delete(self, table, k1=None, k2=None, identifier=None):
        with self.lock, self.connection() as db:
            if identifier:
                db.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))
            else:
                db.execute(f"DELETE FROM {table} WHERE k1=? AND k2=?", (k1, k2))

    # --------------------------------------------------------------- lessons
    def categories(self):
        try:
            data = json.loads((self.tutorial_root / "categories.json").read_text())
        except (OSError, json.JSONDecodeError):
            data = {"categories": [], "levels": [{"id": "novice", "label": "新手", "label_en": "Novice", "min_completed": 0, "min_score": 0}]}
        data["categories"] = sorted(data.get("categories", []), key=lambda c: c.get("order", 0))
        return data

    def lessons(self):
        """Lesson metadata from disk (cached by mtime), sorted by order."""
        out = []
        if not self.lessons_dir.is_dir():
            return out
        for directory in sorted(self.lessons_dir.iterdir()):
            meta_path = directory / "lesson.json"
            if not meta_path.is_file():
                continue
            mtime = meta_path.stat().st_mtime_ns
            cached = self._lesson_cache.get(directory.name)
            if cached and cached[0] == mtime:
                out.append(cached[1])
                continue
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError as exc:
                meta = {"id": directory.name, "title": directory.name, "error": f"lesson.json invalid: {exc}"}
            meta.setdefault("id", directory.name)
            meta.setdefault("robots", ["leg", "wheel"])
            meta.setdefault("run", {"kind": "any", "trainable": False})
            meta.setdefault("created_at", now_iso(meta_path.stat().st_mtime))
            meta["has_example"] = (directory / "example.py").is_file()
            meta["quiz_count"] = len(meta.get("quiz", []))
            self._lesson_cache[directory.name] = (mtime, meta)
            out.append(meta)
        return sorted(out, key=lambda m: (m.get("order", 999), m["id"]))

    def lesson_meta(self, lesson_id):
        for meta in self.lessons():
            if meta["id"] == lesson_id:
                return meta
        raise APIError("找不到這堂課。", 404)

    def lesson(self, lesson_id, robot="leg", user_id=None):
        meta = dict(self.lesson_meta(lesson_id))
        directory = self.lessons_dir / lesson_id
        meta["markdown"] = (directory / "lesson.md").read_text() if (directory / "lesson.md").is_file() else ""
        meta["code"] = (directory / "example.py").read_text() if (directory / "example.py").is_file() else ""
        meta["robot"] = robot if robot in meta.get("robots", []) else meta.get("robots", ["leg"])[0]
        meta["robot_facts"] = self.robot_facts(meta["robot"])
        meta["runtime"] = self.runtime_for(meta)
        if user_id:
            row = self.get("progress", f"{user_id}:{lesson_id}")
            meta["progress"] = row
            if row and row.get("draft_code"):
                meta["draft_code"] = row["draft_code"]
            if row and row.get("last_params"):
                meta["last_params"] = row["last_params"]
        meta["quiz"] = [{k: v for k, v in q.items() if k not in ("answer", "explain")} for q in meta.get("quiz", [])]
        return meta

    def robot_facts(self, robot):
        try:
            robots = json.loads((self.root / "assets/robots/manifest.json").read_text()).get("robots", [])
        except (OSError, json.JSONDecodeError):
            robots = []
        entry = next((r for r in robots if r.get("id") == robot), None)
        if not entry:
            return {"id": robot, "available": False}
        urdf = (self.root / str(entry.get("urdf_url", "")).lstrip("/")).resolve()
        return {"id": robot, "available": urdf.is_file(), "name": entry.get("name"), "urdf": str(urdf), "urdf_url": entry.get("urdf_url"),
                "control_joints": entry.get("control_joints", []), "default_joint_positions": entry.get("default_joint_positions", {}),
                "spawn_height": entry.get("spawn_height", 0.85 if robot == "leg" else 0.65)}

    def runtime_for(self, meta, params=None):
        kind = (meta.get("run") or {}).get("kind", "any")
        if kind == "any":
            return {"mode": "python", "label": "本機 python3（純 Python 範例）", "command": [self.python]}
        root = self.isaaclab3_root
        if root and (root / "pyproject.toml").is_file() and self.uv:
            # Always pull the standalone URDF/MJCF importer; full Kit only when PhysX/Kit viz requested.
            command = [self.uv, "run", "--project", str(root), "--extra", "importers"]
            physics = (params or {}).get("physics")
            if physics == "isaacsim_physx" or (params or {}).get("viz") in ("kit", "newton_rtx"):
                command += ["--extra", "isaacsim"]
            elif physics == "ovphysx":
                command += ["--extra", "ovphysx"]
            return {"mode": "isaaclab3", "label": f"Isaac Lab 3 @ {root}", "command": command + ["python"], "root": str(root)}
        reason = ("Q1_ISAACLAB3_ROOT 未設定或不是 Isaac Lab 3 checkout（需要 pyproject.toml 與 uv）。"
                  "請先跑 bash scripts/setup_store.sh（會檢查／安裝 Hermes 與 clone IsaacLab v3.0.0 + uv sync，"
                  "已安裝的會跳過），再 bash scripts/start_store.sh --with-hermes。")
        if (meta.get("run") or {}).get("fallback"):
            return {"mode": "python-fallback", "label": "無 Isaac Lab 3：執行範例的 fallback 分支", "command": [self.python], "reason": reason}
        return {"mode": "blocked", "label": "需要 Isaac Lab 3", "command": None, "reason": reason}

    def catalog(self, robot=None, user_id=None):
        cats = self.categories()
        lessons = [m for m in self.lessons() if not robot or robot in m.get("robots", [])]
        by_cat = {}
        for m in lessons:
            by_cat.setdefault(m.get("category", "other"), []).append(m)
        progress = self.progress(user_id) if user_id else None
        return {"categories": [{**c, "lessons": by_cat.get(c["id"], [])} for c in cats["categories"]] +
                [{"id": cid, "label": cid, "label_en": cid, "lessons": items} for cid, items in by_cat.items()
                 if cid not in {c["id"] for c in cats["categories"]}],
                "levels": cats["levels"], "lesson_count": len(lessons), "robots": ["leg", "wheel"],
                "runtime": {"isaaclab3": self.isaaclab3_root is not None and (self.isaaclab3_root / "pyproject.toml").is_file(),
                            "isaaclab3_root": str(self.isaaclab3_root) if self.isaaclab3_root else None, "uv": bool(self.uv), "python": self.python},
                "proposals": self.proposal_counts(), "discovery": self.discovery.status(), "progress": progress}

    # ----------------------------------------------------------------- users
    @staticmethod
    def user_id_for(name):
        return hashlib.sha256(name.strip().casefold().encode()).hexdigest()[:32]

    def register_user(self, name):
        name = (name or "").strip()
        if not 1 <= len(name) <= 40:
            raise APIError("名字需要 1–40 個字。")
        uid = self.user_id_for(name)
        user = self.get("users", uid) or {"id": uid, "name": name, "created_at": now_iso(self.clock()), "last": None}
        user["last_seen"] = now_iso(self.clock())
        self.put("users", user)
        return {"user": user, "progress": self.progress(uid)}

    def user(self, uid):
        user = self.get("users", uid)
        if not user:
            raise APIError("找不到使用者，請先註冊名字。", 404)
        return user

    def users(self):
        return [{"id": u["id"], "name": u["name"], "last_seen": u.get("last_seen"), "level": self.progress(u["id"])["level"]["id"]}
                for u in self.select("users")]

    def touch_last(self, uid, lesson_id, robot):
        user = self.user(uid)
        user["last"] = {"lesson_id": lesson_id, "robot": robot, "at": now_iso(self.clock())}
        user["last_seen"] = user["last"]["at"]
        self.put("users", user)
        return user

    def progress_row(self, uid, lesson_id):
        return self.get("progress", f"{uid}:{lesson_id}") or {"id": f"{uid}:{lesson_id}", "user_id": uid, "lesson_id": lesson_id,
                                                              "started_at": None, "completed_at": None, "best_score": None, "attempts": [],
                                                              "runs": [], "draft_code": None, "last_params": None, "robot": None}

    def save_progress_row(self, row):
        return self.put("progress", row, k1=row["user_id"], k2=row["lesson_id"])

    def start_lesson(self, uid, lesson_id, robot="leg"):
        self.lesson_meta(lesson_id)
        row = self.progress_row(uid, lesson_id)
        row["started_at"] = row["started_at"] or now_iso(self.clock())
        row["robot"] = robot
        self.save_progress_row(row)
        self.touch_last(uid, lesson_id, robot)
        return row

    def save_draft(self, uid, lesson_id, code=None, params=None):
        row = self.progress_row(uid, lesson_id)
        if code is not None:
            if not isinstance(code, str) or len(code) > MAX_CODE:
                raise APIError("程式碼太大或格式錯誤。")
            row["draft_code"] = code
        if params is not None:
            row["last_params"] = params
        row["updated_at"] = now_iso(self.clock())
        return self.save_progress_row(row)

    def grade_quiz(self, uid, lesson_id, answers):
        meta = self.lesson_meta(lesson_id)
        quiz = meta.get("quiz", [])
        if not quiz:
            raise APIError("這堂課沒有測驗。")
        if not isinstance(answers, dict):
            raise APIError("answers 必須是 {question_id: choice_index}。")
        results, correct = [], 0
        for q in quiz:
            given = answers.get(q["id"])
            ok = isinstance(given, int) and given == q["answer"]
            correct += ok
            results.append({"id": q["id"], "correct": ok, "given": given, "answer": q["answer"], "explain": q.get("explain", "")})
        score = round(100 * correct / len(quiz))
        row = self.progress_row(uid, lesson_id)
        row["started_at"] = row["started_at"] or now_iso(self.clock())
        row["attempts"].append({"at": now_iso(self.clock()), "score": score, "answers": answers})
        row["attempts"] = row["attempts"][-20:]
        row["best_score"] = max(score, row["best_score"] or 0)
        passed = score >= PASS_SCORE
        if passed and not row["completed_at"]:
            row["completed_at"] = now_iso(self.clock())
        self.save_progress_row(row)
        self.touch_last(uid, lesson_id, row.get("robot") or "leg")
        return {"score": score, "passed": passed, "pass_score": PASS_SCORE, "correct": correct, "total": len(quiz), "results": results,
                "progress": self.progress(uid)}

    def progress(self, uid):
        lessons = self.lessons()
        rows = {r["lesson_id"]: r for r in self.select("progress", k1=uid)} if uid else {}
        per_lesson, completed_scores = [], []
        for meta in lessons:
            row = rows.get(meta["id"])
            state = "not_started"
            if row and row.get("completed_at"):
                state = "completed"
                completed_scores.append(row.get("best_score") or 0)
            elif row and row.get("started_at"):
                state = "in_progress"
            per_lesson.append({"lesson_id": meta["id"], "title": meta.get("title"), "title_en": meta.get("title_en"), "category": meta.get("category"),
                               "state": state, "best_score": row.get("best_score") if row else None,
                               "started_at": row.get("started_at") if row else None, "completed_at": row.get("completed_at") if row else None,
                               "runs": len(row.get("runs", [])) if row else 0, "robot": row.get("robot") if row else None})
        total = len(lessons)
        completed = len(completed_scores)
        avg = round(sum(completed_scores) / completed) if completed else 0
        levels = self.categories()["levels"]
        level = levels[0]
        for candidate in levels:
            if completed >= candidate.get("min_completed", 0) and avg >= candidate.get("min_score", 0):
                level = candidate
        idx = levels.index(level)
        next_level = levels[idx + 1] if idx + 1 < len(levels) else None
        cats = {}
        for item in per_lesson:
            c = cats.setdefault(item["category"], {"total": 0, "completed": 0})
            c["total"] += 1
            c["completed"] += item["state"] == "completed"
        user = self.get("users", uid) if uid else None
        return {"user_id": uid, "name": user.get("name") if user else None, "total": total, "completed": completed,
                "in_progress": sum(i["state"] == "in_progress" for i in per_lesson), "percent": round(100 * completed / total) if total else 0,
                "average_score": avg, "level": level, "next_level": next_level,
                "to_next": None if not next_level else {"lessons": max(0, next_level["min_completed"] - completed),
                                                        "score": max(0, next_level["min_score"] - avg)},
                "categories": cats, "lessons": per_lesson, "last": user.get("last") if user else None}

    # ------------------------------------------------------------------ runs
    def run_dir(self, run_id):
        return self.runs_dir / run_id

    def create_run(self, uid, lesson_id, robot, params=None, code=None):
        meta = self.lesson_meta(lesson_id)
        self.user(uid)
        if robot not in meta.get("robots", []):
            raise APIError("這堂課不支援此機型。")
        facts = self.robot_facts(robot)
        if not facts.get("available"):
            raise APIError("找不到此機型的 URDF。", 500)
        self._heal_stale_runs()
        active = [r for r in self.select("runs", k1=uid) if r.get("state") in RUN_ACTIVE]
        if active:
            cur = active[0]
            raise APIError(
                f"已有執行在進行（{cur.get('state')} · {cur['id'][:8]}）。"
                "請先等待完成，或按「取消」後再執行。",
                409,
            )
        clean = self.validate_params(meta, params or {})
        code = code if isinstance(code, str) and code.strip() else (self.lessons_dir / lesson_id / "example.py").read_text()
        if len(code) > MAX_CODE:
            raise APIError("程式碼太大。")
        runtime = self.runtime_for(meta, clean)
        run_id = uuid.uuid4().hex
        directory = self.run_dir(run_id)
        directory.mkdir(parents=True)
        full_params = {**clean, "robot": robot, "urdf": facts["urdf"], "control_joints": facts["control_joints"],
                       "default_joint_positions": facts["default_joint_positions"], "spawn_height": facts["spawn_height"],
                       "store_root": str(self.root), "lesson_id": lesson_id, "run_id": run_id}
        (directory / "params.json").write_text(json.dumps(full_params, ensure_ascii=False, indent=2))
        (directory / "example.py").write_text(code)
        run = {"id": run_id, "user_id": uid, "lesson_id": lesson_id, "lesson_title": meta.get("title"), "robot": robot, "params": clean,
               "code_sha256": hashlib.sha256(code.encode()).hexdigest(), "code_modified": code != (self.lessons_dir / lesson_id / "example.py").read_text(),
               "runtime": runtime, "trainable": bool(meta["run"].get("trainable")), "resumable": bool(meta["run"].get("resumable")),
               "state": "queued", "phase": None, "progress": None, "result": None, "error": None, "pid": None,
               "created_at": now_iso(self.clock()), "started_at": None, "finished_at": None, "resumed": 0}
        if runtime["mode"] == "blocked":
            run["state"], run["error"] = "blocked", runtime["reason"]
        self.put("runs", run, k1=uid, k2=lesson_id)
        row = self.progress_row(uid, lesson_id)
        row["started_at"] = row["started_at"] or run["created_at"]
        row["robot"] = robot
        row["runs"] = (row.get("runs") or [])[-49:] + [run_id]
        row["last_params"] = clean
        if run["code_modified"]:
            row["draft_code"] = code
        self.save_progress_row(row)
        self.touch_last(uid, lesson_id, robot)
        self._pump()
        return self.run(run_id)

    def validate_params(self, meta, params):
        clean = {}
        for spec in meta.get("params", []):
            key, kind = spec["key"], spec.get("type", "text")
            value = params.get(key, spec.get("default"))
            try:
                if kind == "int":
                    value = int(value)
                    value = min(max(value, int(spec.get("min", value))), int(spec.get("max", value)))
                elif kind == "float":
                    value = float(value)
                    value = min(max(value, float(spec.get("min", value))), float(spec.get("max", value)))
                elif kind == "bool":
                    value = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "yes", "on")
                elif kind == "select":
                    if value not in spec.get("options", []):
                        raise ValueError
                else:
                    value = str(value)[:500]
            except (TypeError, ValueError):
                raise APIError(f"參數 {key} 的值無效。")
            clean[key] = value
        return clean

    def _launch(self, run, resume=False):
        directory = self.run_dir(run["id"])
        runtime = run["runtime"]
        # lab3_runner applies the kitless PhysxJointAPI patch before example.py, so old drafts still work.
        runpath = Path(__file__).resolve().parent / "runpath"
        runner = runpath / "lab3_runner.py"
        script_args = ["example.py", "--job-dir", str(directory), "--params", str(directory / "params.json")]
        if resume and run["resumable"]:
            script_args.append("--resume")
        if runtime.get("mode") == "isaaclab3" and runner.is_file():
            command = list(runtime["command"]) + [str(runner), *script_args]
        else:
            command = list(runtime["command"]) + script_args
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8",
               "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "HEADLESS": "1", "Q1_TUTORIAL_RUN": run["id"]}
        # sitecustomize + lab3_compat MetaPathFinder also patch when PYTHONPATH includes runpath.
        env["PYTHONPATH"] = str(runpath) + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else "")
        for key in ("CUDA_VISIBLE_DEVICES", "HF_HOME", "XDG_CACHE_HOME", "DISPLAY", "LD_LIBRARY_PATH",
                    "ISAACSIM_PATH", "UV_CACHE_DIR", "Q1_ISAACLAB3_ROOT"):
            if key in os.environ:
                env[key] = os.environ[key]
        log = (directory / "run.log").open("ab")
        try:
            process = subprocess.Popen(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        except OSError as exc:
            log.close()
            run.update(state="failed", error=f"無法啟動：{exc}", finished_at=now_iso(self.clock()))
            self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
            return
        log.close()
        run.update(state="running", pid=process.pid, started_at=run.get("started_at") or now_iso(self.clock()), finished_at=None,
                   command=command, resumed=run.get("resumed", 0) + (1 if resume else 0), error=None)
        self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
        self._watch(run["id"], process)

    def _watch(self, run_id, process=None):
        if run_id in self.pollers:
            return
        thread = threading.Thread(target=self._poll, args=(run_id, process), daemon=True)
        self.pollers[run_id] = thread
        thread.start()

    def _timeout_for(self, run):
        minutes = float(run["params"].get("budget_minutes") or 0)
        base = ((self.lesson_meta(run["lesson_id"]).get("run") or {}).get("estimated_minutes") or 5) * 6
        return max(base, minutes + 30) * 60

    def _finalize_exited_run(self, run, code=None, reason=None):
        """Mark a run finished from progress/result on disk (handles NaN JSON and lost pollers)."""
        directory = self.run_dir(run["id"])
        progress = load_json_file(directory / "progress.json")
        result = load_json_file(directory / "result.json")
        if progress:
            run["progress"] = progress
            run["phase"] = progress.get("phase")
        run["result"] = result
        run["finished_at"] = now_iso(self.clock())
        ok_result = result is not None and not result.get("error")
        done_phase = bool(progress and progress.get("phase") == "complete")
        if (code in (0, None) and ok_result) or (code is None and (ok_result or done_phase)):
            run["state"] = "complete"
            run["error"] = None
            if result is None and progress:
                run["result"] = {k: progress.get(k) for k in ("physics", "num_envs", "steps", "message") if k in progress}
        else:
            run["state"] = "failed" if code not in (0, None) else "interrupted"
            run["error"] = (
                (result or {}).get("error")
                or reason
                or (f"exit code {code}; 見 run.log" if code not in (0, None) else "程序已結束但狀態未更新；見 run.log")
            )
        if self.ffmpeg and (directory / "rollout.json").exists() and not (directory / "result.mp4").exists():
            run["video_note"] = "rollout.json 可在瀏覽器重播；mp4 需 viz=kit 或 scripts/rollout_to_mp4.py"
        self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
        return run

    def _poll(self, run_id, process):
        try:
            deadline = time.monotonic() + self._timeout_for(self.get("runs", run_id))
            while True:
                run = self.get("runs", run_id)
                if run is None or run["state"] in ("cancelled",):
                    break
                directory = self.run_dir(run_id)
                progress = load_json_file(directory / "progress.json")
                if progress:
                    run["progress"] = progress
                    run["phase"] = progress.get("phase")
                exited = process.poll() is not None if process is not None else not pid_alive(run.get("pid"))
                if exited:
                    code = process.returncode if process is not None else None
                    self._finalize_exited_run(run, code=code)
                    break
                if time.monotonic() > deadline:
                    self._kill(run)
                    run.update(state="failed", error="超過時間上限，已終止。", finished_at=now_iso(self.clock()))
                    self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
                    break
                self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
                time.sleep(2)
        finally:
            self.pollers.pop(run_id, None)
            self._pump()

    def _kill(self, run):
        pid = run.get("pid")
        if pid and pid_alive(pid):
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except OSError:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass

    def _heal_stale_runs(self):
        """Recover starting/running rows whose process died (lost poller / NaN result.json)."""
        changed = False
        for run in self.select("runs"):
            if run.get("state") not in ("starting", "running"):
                continue
            if pid_alive(run.get("pid")):
                if run["id"] not in self.pollers:
                    self._watch(run["id"])
                continue
            self._finalize_exited_run(
                run,
                code=None,
                reason="程序已不在（可能上次結果含 NaN 導致狀態沒更新）。",
            )
            changed = True
        return changed

    def _pump(self):
        """Start queued runs: python-only runs immediately, one Isaac Lab run at a time."""
        with self.lock:
            self._heal_stale_runs()
            runs = self.select("runs", newest=False)
            gpu_busy = any(r["state"] in ("starting", "running") and r["runtime"]["mode"] == "isaaclab3" for r in runs)
            active_gpu = next((r for r in runs if r["state"] in ("starting", "running") and r["runtime"]["mode"] == "isaaclab3"), None)
            for run in runs:
                if run["state"] != "queued":
                    continue
                if run["runtime"]["mode"] == "isaaclab3":
                    if gpu_busy:
                        # Surface why the black log stays empty while queued.
                        reason = (
                            f"排隊等待 GPU：另一個 Isaac Lab 執行佔用中"
                            f"（{active_gpu['id'][:8]} · {active_gpu.get('state')}）。"
                            if active_gpu else
                            "排隊等待 GPU。"
                        )
                        if run.get("wait_reason") != reason:
                            run["wait_reason"] = reason
                            self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
                        continue
                    gpu_busy = True
                run["state"] = "starting"
                run.pop("wait_reason", None)
                self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
                self._launch(run, resume=bool(run.get("resume_requested")))
                run["resume_requested"] = False

    def _reconcile_runs(self):
        for run in self.select("runs"):
            if run["state"] in ("starting", "running"):
                if pid_alive(run.get("pid")):
                    self._watch(run["id"])
                else:
                    directory = self.run_dir(run["id"])
                    progress = load_json_file(directory / "progress.json")
                    result = load_json_file(directory / "result.json")
                    if (result and not result.get("error")) or (progress and progress.get("phase") == "complete"):
                        self._finalize_exited_run(run, code=None)
                    else:
                        run["state"] = "interrupted"
                        run["error"] = "store 重啟時程序已不在。" + ("可續訓（latest.pt 存在）。" if (directory / "latest.pt").exists() else "")
                        run["finished_at"] = now_iso(self.clock())
                        self.put("runs", run, k1=run["user_id"], k2=run["lesson_id"])
        self._pump()

    def run(self, run_id, log_lines=80):
        self._heal_stale_runs()
        run = self.get("runs", run_id)
        if not run or not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
            raise APIError("找不到這次執行。", 404)
        directory = self.run_dir(run_id)
        # Keep progress fresh even if the poller thread is behind.
        progress = load_json_file(directory / "progress.json")
        if progress:
            run["progress"] = progress
            run["phase"] = progress.get("phase")
        run["log_tail"] = tail(directory / "run.log", log_lines)
        run["files"] = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name in MEDIA) if directory.is_dir() else []
        # Refresh artifacts from disk so Hermes / Lab UI can analyze after the process exits.
        disk_result = load_json_file(directory / "result.json") if directory.is_dir() else None
        if disk_result is not None:
            run["result"] = disk_result
        disk_eval = load_json_file(directory / "evaluation.json") if directory.is_dir() else None
        if disk_eval is not None:
            run["evaluation"] = disk_eval
        run["can_resume"] = run["state"] in ("interrupted", "failed", "complete", "cancelled") and run["resumable"] and (directory / "latest.pt").exists()
        run["media_url"] = f"/media/tutorial/{run_id}/"
        if run.get("state") == "queued" and run["runtime"].get("mode") == "isaaclab3" and not run.get("wait_reason"):
            active = next(
                (r for r in self.select("runs") if r["id"] != run_id and r.get("state") in ("starting", "running")
                 and r.get("runtime", {}).get("mode") == "isaaclab3"),
                None,
            )
            if active:
                run["wait_reason"] = (
                    f"排隊等待 GPU：另一個 Isaac Lab 執行佔用中（{active['id'][:8]} · {active.get('state')}）。"
                )
        return run

    def runs(self, uid=None, lesson_id=None, limit=100):
        out = []
        for run in self.select("runs", k1=uid, k2=lesson_id, limit=limit):
            directory = self.run_dir(run["id"])
            run["files"] = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name in MEDIA) if directory.is_dir() else []
            run["can_resume"] = run["state"] in ("interrupted", "failed", "complete", "cancelled") and run["resumable"] and (directory / "latest.pt").exists()
            out.append(run)
        return out

    def cancel_run(self, run_id):
        run = self.run(run_id)
        if run["state"] in RUN_FINAL:
            return run
        self._kill(run)
        run.update(state="cancelled", finished_at=now_iso(self.clock()))
        self.put("runs", {k: v for k, v in run.items() if k not in ("log_tail", "files", "can_resume", "media_url")}, k1=run["user_id"], k2=run["lesson_id"])
        return self.run(run_id)

    def resume_run(self, run_id, params=None):
        run = self.run(run_id)
        if not run["can_resume"]:
            raise APIError("這次執行無法續訓（沒有 latest.pt 或課程不支援）。")
        stored = {k: v for k, v in run.items() if k not in ("log_tail", "files", "can_resume", "media_url")}
        if params:
            meta = self.lesson_meta(run["lesson_id"])
            stored["params"] = self.validate_params(meta, {**run["params"], **params})
            full = json.loads((self.run_dir(run_id) / "params.json").read_text())
            full.update(stored["params"])
            (self.run_dir(run_id) / "params.json").write_text(json.dumps(full, ensure_ascii=False, indent=2))
        stored.update(state="queued", resume_requested=True, error=None, finished_at=None)
        self.put("runs", stored, k1=run["user_id"], k2=run["lesson_id"])
        self._pump()
        return self.run(run_id)

    def run_media(self, run_id, name):
        if name not in MEDIA or not re.fullmatch(r"[0-9a-f]{32}", run_id or ""):
            raise APIError("找不到檔案。", 404)
        path = self.run_dir(run_id) / name
        if not path.is_file():
            raise APIError("找不到檔案。", 404)
        return path, MEDIA[name]

    def run_code(self, run_id):
        path = self.run_dir(run_id) / "example.py"
        if not path.is_file():
            raise APIError("找不到檔案。", 404)
        return path.read_text()

    # --------------------------------------------------------- conversations
    def append_message(self, uid, lesson_id, role, content, **meta):
        msg = {"id": uuid.uuid4().hex, "user_id": uid, "lesson_id": lesson_id, "role": role, "content": content,
               "created_at": now_iso(self.clock()), **meta}
        self.put("messages", msg, k1=uid, k2=lesson_id)
        return msg

    def history(self, uid, lesson_id, limit=40):
        return list(reversed(self.select("messages", k1=uid, k2=lesson_id, limit=limit)))

    def clear_history(self, uid, lesson_id):
        self.delete("messages", k1=uid, k2=lesson_id)

    def memory_get(self, uid):
        return self.get("memory", uid) or {"id": uid, "facts": [], "updated_at": None}

    def memory_add(self, uid, facts):
        mem = self.memory_get(uid)
        for fact in facts:
            fact = str(fact).strip()[:300]
            if fact and fact not in mem["facts"]:
                mem["facts"].append(fact)
        mem["facts"] = mem["facts"][-60:]
        mem["updated_at"] = now_iso(self.clock())
        return self.put("memory", mem)

    # ------------------------------------------------------------- proposals
    def proposal_path(self, proposal_id):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", proposal_id or ""):
            raise APIError("找不到提案。", 404)
        for status in PROPOSAL_DIRS:
            path = self.proposals_dir / status / f"{proposal_id}.md"
            if path.is_file():
                return path, status
        raise APIError("找不到提案。", 404)

    def proposals(self, status=None):
        out = []
        for folder in PROPOSAL_DIRS:
            directory = self.proposals_dir / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                meta, _ = parse_front_matter(path.read_text())
                item = {"id": path.stem, "folder": folder, "status": "created" if meta.get("created_lesson_id") else folder,
                        "title": meta.get("title", path.stem), "summary": meta.get("summary", ""), "category": meta.get("category", "other"),
                        "sources": meta.get("sources", []), "discovered_at": meta.get("discovered_at"), "approved_at": meta.get("approved_at"),
                        "created_lesson_id": meta.get("created_lesson_id"), "created_at": meta.get("created_at"), "robots": meta.get("robots", []),
                        "origin": meta.get("origin", "hermes")}
                if status is None or item["status"] == status:
                    out.append(item)
        return out

    def proposal_counts(self):
        items = self.proposals()
        return {"pending": sum(i["status"] == "pending" for i in items), "approved": sum(i["status"] == "approved" for i in items),
                "created": sum(i["status"] == "created" for i in items), "rejected": sum(i["status"] == "rejected" for i in items)}

    def proposal(self, proposal_id):
        path, folder = self.proposal_path(proposal_id)
        text = path.read_text()
        meta, body = parse_front_matter(text)
        return {"id": proposal_id, "folder": folder, "status": "created" if meta.get("created_lesson_id") else folder, "meta": meta,
                "markdown": body, "raw": text, "path": str(path.relative_to(self.root))}

    def write_proposal(self, title, summary, category, sources, body, robots=("leg", "wheel"), origin="hermes", proposal_id=None):
        self.proposals_dir.joinpath("pending").mkdir(parents=True, exist_ok=True)
        pid = proposal_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d')}-{slugify(title)}"
        base, n = pid, 2
        while any((self.proposals_dir / s / f"{pid}.md").exists() for s in PROPOSAL_DIRS):
            pid = f"{base}-{n}"
            n += 1
        meta = {"title": title, "summary": summary, "category": category if category in CATEGORY_HINTS else "other", "status": "pending",
                "origin": origin, "discovered_at": now_iso(self.clock()), "robots": list(robots),
                "sources": [s if isinstance(s, str) else f"{s.get('type', 'web')} | {s.get('title', '')} | {s.get('url', '')}" for s in sources]}
        path = self.proposals_dir / "pending" / f"{pid}.md"
        path.write_text(dump_front_matter(meta, body))
        self.add_ticker(f"新課程提案：{title}", kind="proposal", proposal_id=pid)
        return self.proposal(pid)

    def set_proposal_status(self, proposal_id, status):
        if status not in ("approved", "rejected", "pending"):
            raise APIError("狀態無效。")
        path, folder = self.proposal_path(proposal_id)
        meta, body = parse_front_matter(path.read_text())
        meta["status"] = status
        if status == "approved":
            meta["approved_at"] = now_iso(self.clock())
        target = self.proposals_dir / status / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_front_matter(meta, body))
        if target != path:
            path.unlink()
        return self.proposal(proposal_id)

    def mark_created(self, proposal_id, lesson_id):
        path, folder = self.proposal_path(proposal_id)
        self.lesson_meta(lesson_id)
        meta, body = parse_front_matter(path.read_text())
        meta.update(status="created", created_lesson_id=lesson_id, created_at=now_iso(self.clock()))
        path.write_text(dump_front_matter(meta, body))
        lesson_path = self.lessons_dir / lesson_id / "lesson.json"
        lesson = json.loads(lesson_path.read_text())
        lesson.setdefault("created_at", meta["created_at"])
        lesson["from_proposal"] = proposal_id
        lesson_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + "\n")
        self._lesson_cache.pop(lesson_id, None)
        self.add_ticker(f"課程已建立：{lesson.get('title', lesson_id)}", kind="created", proposal_id=proposal_id, lesson_id=lesson_id)
        return self.proposal(proposal_id)

    def cursor_prompt(self, proposal_id):
        """Prompt text the user pastes into Cursor to build the lesson from an approved proposal."""
        item = self.proposal(proposal_id)
        return ("請在 branch isaac_v3 依下列提案建立新的 Isaac Lab 3 tutorial 課程：\n"
                f"- 提案檔：{item['path']}\n- 在 tutorials/lessons/<order>-<slug>/ 新增 lesson.json、lesson.md、example.py（遵循既有課程 schema：params、quiz 3–5 題、references、run.kind）。\n"
                "- lesson.json 要有 created_at 與 from_proposal。\n"
                f"- 完成後執行 python3 -m q1store.tutorial mark-created {proposal_id} <lesson_id>，並 commit / push 到 isaac_v3。\n\n"
                f"--- 提案內容 ---\n{item['raw']}")

    # ---------------------------------------------------------------- ticker
    def add_ticker(self, text, kind="info", **extra):
        item = {"id": uuid.uuid4().hex, "text": text, "kind": kind, "created_at": now_iso(self.clock()), **extra}
        self.put("ticker", item, k1=kind)
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM ticker WHERE id NOT IN (SELECT id FROM ticker ORDER BY rowid DESC LIMIT 60)")
        return item

    def ticker(self, limit=20):
        return self.select("ticker", limit=limit)


MEDIA = {"result.mp4": "video/mp4", "rollout.json": "application/json", "result.json": "application/json", "evaluation.json": "application/json",
         "record.json": "application/json", "progress.json": "application/json", "run.log": "text/plain; charset=utf-8",
         "latest.pt": "application/octet-stream", "policy.onnx": "application/octet-stream", "policy.pt": "application/octet-stream",
         "policy_meta.json": "application/json", "params.json": "application/json", "example.py": "text/x-python; charset=utf-8",
         "scan.json": "application/json", "joints.json": "application/json"}


class Discovery:
    """Hermes' continuous watch for new Isaac Lab 3 material. Writes pending proposals, feeds the ticker."""

    def __init__(self, tutorial, searcher=None):
        self.t = tutorial
        self.searcher = searcher or research
        self.thread = None
        self.stop_event = threading.Event()
        self.running = False

    def settings(self):
        return self.t.get("settings", "discovery") or {"id": "discovery", "enabled": False, "interval_minutes": 360, "topics": DEFAULT_TOPICS,
                                                        "last_run": None, "last_result": None, "max_per_run": 3}

    def save(self, settings):
        return self.t.put("settings", settings)

    def status(self):
        s = self.settings()
        return {**s, "running": self.running, "pending": self.t.proposal_counts()["pending"]}

    def configure(self, enabled=None, interval_minutes=None, topics=None, max_per_run=None):
        s = self.settings()
        if enabled is not None:
            s["enabled"] = bool(enabled)
        if interval_minutes is not None:
            s["interval_minutes"] = int(min(max(int(interval_minutes), 15), 7 * 24 * 60))
        if topics is not None:
            s["topics"] = [str(x)[:120] for x in topics if str(x).strip()][:20] or DEFAULT_TOPICS
        if max_per_run is not None:
            s["max_per_run"] = int(min(max(int(max_per_run), 1), 10))
        self.save(s)
        if s["enabled"]:
            self.start()
        return self.status()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.wait(60):
            s = self.settings()
            if not s["enabled"]:
                continue
            last = s.get("last_run_ts") or 0
            if self.t.clock() - last >= s["interval_minutes"] * 60:
                try:
                    self.run_once()
                except Exception as exc:  # keep the watcher alive; record the failure
                    s = self.settings()
                    s["last_result"] = {"error": f"{type(exc).__name__}: {exc}"}
                    self.save(s)

    @staticmethod
    def relevance(item):
        text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        score = 0
        for word, weight in (("isaac lab", 3), ("isaaclab", 3), ("isaac sim", 2), ("newton", 1), ("mujoco warp", 2), ("3.0", 1), ("tutorial", 1),
                             ("urdf", 1), ("rsl", 1), ("ppo", 1), ("humanoid", 1), ("legged", 1), ("wheeled", 1)):
            if word in text:
                score += weight
        return score

    @staticmethod
    def guess_category(text):
        text = text.lower()
        best, best_n = "other", 0
        for cat, words in CATEGORY_HINTS.items():
            n = sum(w in text for w in words)
            if n > best_n:
                best, best_n = cat, n
        return best

    def run_once(self, topics=None):
        self.running = True
        s = self.settings()
        try:
            found, created = [], []
            seen_ids = set()
            for topic in topics or s["topics"]:
                batches = [self.searcher.web_search(topic, max_results=6)]
                if any(w in topic.lower() for w in ("paper", "sim-to-real", "rl", "ppo", "locomotion")):
                    batches.append(self.searcher.arxiv_search(topic, max_results=4))
                if "video" in topic.lower() or "tutorial" in topic.lower():
                    batches.append(self.searcher.youtube_search(topic, max_results=3))
                for batch in batches:
                    for item in batch.get("results", []):
                        key = hashlib.sha256(item["url"].encode()).hexdigest()[:32]
                        if key in seen_ids or self.t.get("seen", key):
                            continue
                        seen_ids.add(key)
                        score = self.relevance(item)
                        self.t.put("seen", {"id": key, "url": item["url"], "title": item["title"], "score": score, "topic": topic,
                                            "seen_at": now_iso(self.t.clock())}, k1=item.get("type"))
                        if score >= 3:
                            found.append({**item, "topic": topic, "score": score})
            found.sort(key=lambda i: -i["score"])
            for item in found[: s.get("max_per_run", 3)]:
                created.append(self.propose_from(item)["id"])
            result = {"at": now_iso(self.t.clock()), "candidates": len(found), "proposals": created}
            s = self.settings()
            s.update(last_run=result["at"], last_run_ts=self.t.clock(), last_result=result)
            self.save(s)
            if not created:
                self.t.add_ticker("Hermes 掃描完成：沒有新的 Isaac Lab 3 課程素材", kind="scan")
            return result
        finally:
            self.running = False

    def propose_from(self, item):
        title = item["title"][:90]
        category = self.guess_category(f"{title} {item.get('snippet', '')} {item.get('topic', '')}")
        sources = [{"type": item.get("type", "web"), "title": title, "url": item["url"]}]
        body = (f"# 課程提案：{title}\n\n## 摘要\n\n{item.get('snippet') or '（搜尋結果沒有摘要，請打開來源確認）'}\n\n"
                f"## 來源\n\n- [{item.get('type', 'web')}] {item['url']}\n\n"
                f"## 建議教學重點\n\n- 對應分類：`{category}`\n- 機型：leg 與 wheel 各一個 params 預設\n"
                "- 需要說明這項新功能與 Isaac Lab 2.x 的差異、以及會動到 Q1 現有 example.py 的哪些 API\n\n"
                "## 給 Cursor 的 prompt\n\n"
                f"依本提案建立 tutorials/lessons/<order>-{slugify(title)}/（lesson.json、lesson.md、example.py），"
                "quiz 3–5 題，references 必含上方來源；example.py 須遵守 progress.json／result.json 契約。\n")
        return self.t.write_proposal(title, (item.get("snippet") or "")[:280], category, sources, body)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Q1 tutorial maintenance commands")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-proposals")
    sub.add_parser("list-lessons")
    mark = sub.add_parser("mark-created")
    mark.add_argument("proposal_id")
    mark.add_argument("lesson_id")
    sub.add_parser("discover-once")
    prompt = sub.add_parser("cursor-prompt")
    prompt.add_argument("proposal_id")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    t = Tutorial(root, os.environ.get("Q1_STORE_DATA", root / ".data"), start_threads=False)
    if args.cmd == "list-proposals":
        for p in t.proposals():
            print(f"{p['status']:9} {p['id']:50} {p['title']}")
    elif args.cmd == "list-lessons":
        for m in t.lessons():
            print(f"{m.get('order', 0):3} {m['id']:40} {m.get('category', ''):12} created={m.get('created_at')}")
    elif args.cmd == "mark-created":
        print(json.dumps(t.mark_created(args.proposal_id, args.lesson_id)["meta"], ensure_ascii=False, indent=2))
    elif args.cmd == "discover-once":
        print(json.dumps(t.discovery.run_once(), ensure_ascii=False, indent=2))
    elif args.cmd == "cursor-prompt":
        print(t.cursor_prompt(args.proposal_id))


if __name__ == "__main__":
    main()
