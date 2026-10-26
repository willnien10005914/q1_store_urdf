"""Spark GPU worker: turns queued store jobs into real Isaac Lab PPO runs.

Runs beside the web service as its own process (``scripts/worker.sh``). Every
state transition is written to the shared SQLite store and to
``.data/jobs/<id>/``, so the web service, Pinggy tunnel and even this worker
can restart without losing the training record or its result.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .service import Store

RUNNING_STATES = ("preparing", "waiting_for_gpu", "starting_isaac", "training", "evaluating", "recording")
STAGE_LABELS = {
    "queued_for_worker": "等待 Spark GPU worker", "preparing": "整理關節參考與工作目錄", "waiting_for_gpu": "等待 GPU（其他訓練持有鎖）",
    "starting_isaac": "啟動 Isaac Sim / Isaac Lab", "training": "Isaac Lab PPO 訓練中", "evaluating": "評估策略（32 回合）",
    "recording": "錄製結果影片與 rollout", "complete": "訓練完成", "failed": "訓練失敗", "cancelled": "工作已取消",
}
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
POST_TRAINING_S = 420.  # Isaac startup for evaluation/recording after the budget ends.


def strip_ansi(text):
    return ANSI.sub("", text)


def tail_lines(path, count=80, max_bytes=256 * 1024):
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max_bytes))
            data = stream.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [strip_ansi(line.rstrip()) for line in data.splitlines()]
    return [line for line in lines if line][-count:]


def parse_metrics(lines):
    metrics = {}
    patterns = {"mean_reward": r"Mean reward:\s+(-?[0-9.]+)", "mean_episode_length": r"Mean episode length:\s+([0-9.]+)",
                "steps_per_second": r"Steps per second:\s+([0-9.]+)", "learning_iteration": r"Learning iteration\s+(\d+)/(\d+)"}
    for line in reversed(lines):
        for key, pattern in patterns.items():
            if key in metrics:
                continue
            match = re.search(pattern, line)
            if match:
                metrics[key] = float(match.group(1)) if key != "learning_iteration" else int(match.group(1))
        if len(metrics) == len(patterns):
            break
    return metrics


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


class Worker:
    def __init__(self, root, data_dir=None, leg_root=None, num_envs=None, poll_s=5., clock=time.time):
        self.store = Store(root, data_dir, clock=clock)
        self.clock = clock
        self.root = self.store.root
        self.leg_root = Path(leg_root or os.environ.get("Q1_LEG_ROOT") or Path.home() / "will/q1_leg_urdf").resolve()
        self.lock_path = Path(os.environ.get("Q1_GPU_LOCK") or self.leg_root.parent / ".spark-training-gpu.lock")
        self.num_envs = int(num_envs or os.environ.get("Q1_WORKER_NUM_ENVS") or 2048)
        # Operator cap on a single job's training budget (seconds); jobs keep their requested hours in the plan.
        self.max_budget_s = float(os.environ.get("Q1_WORKER_MAX_BUDGET_S") or 0) or None
        self.poll_s = poll_s
        self.jobs_dir = self.store.data_dir / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.worker_dir = self.store.data_dir / "worker"
        self.worker_dir.mkdir(exist_ok=True)
        self.heartbeat_path = self.worker_dir / "heartbeat.json"
        self.script = self.root / "scripts/train_store_job.py"
        self.lock_file = None
        self.process = None

    # ----------------------------------------------------------------- state
    def heartbeat(self, state, job_id=None, **extra):
        payload = {"pid": os.getpid(), "state": state, "job_id": job_id, "updated_at": self.clock(),
                   "num_envs": self.num_envs, "leg_root": str(self.leg_root), "gpu_lock": str(self.lock_path), **extra}
        tmp = self.heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, self.heartbeat_path)

    def job_dir(self, job_id):
        path = self.jobs_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def update(self, job_id, **fields):
        """Merge fields into the stored job unless the user cancelled meanwhile."""
        job = self.store.get("jobs", job_id)
        if job.get("state") == "cancelled" and fields.get("state") not in (None, "cancelled"):
            return job
        job.update(fields)
        if "state" in fields:
            job["stage_label"] = STAGE_LABELS.get(fields["state"], fields["state"])
            job["label"] = {"complete": "Spark Isaac Lab PPO 新訓練結果", "failed": "Spark 訓練失敗", "cancelled": "已取消"}.get(
                fields["state"], "Spark GPU worker 執行中")
        job["worker_updated_at"] = self.clock()
        self.store.put("jobs", job)
        return job

    def events(self, job_id, message, **extra):
        line = json.dumps({"time": self.clock(), "message": message, **extra}, ensure_ascii=False)
        with (self.job_dir(job_id) / "events.jsonl").open("a") as stream:
            stream.write(line + "\n")
        print(f"[worker {job_id[:8]}] {message}", flush=True)

    # ----------------------------------------------------------------- queue
    def pending(self):
        jobs = [job for job in reversed(self.store.all("jobs")) if job.get("mode") == "worker"]
        stale = [job for job in jobs if job.get("state") in RUNNING_STATES and not pid_alive((job.get("training") or {}).get("worker_pid"))]
        queued = [job for job in jobs if job.get("state") == "queued_for_worker"]
        return stale + queued

    def run_forever(self):
        print(f"Q1 Store worker: leg_root={self.leg_root} num_envs={self.num_envs} data={self.store.data_dir}", flush=True)
        while True:
            try:
                if not self.run_once():
                    self.heartbeat("idle")
                    time.sleep(self.poll_s)
            except KeyboardInterrupt:
                self.heartbeat("stopped")
                raise
            except Exception as error:  # keep serving the queue after an unexpected failure
                print(f"[worker] loop error: {error!r}", flush=True)
                time.sleep(self.poll_s)

    def run_once(self):
        pending = self.pending()
        if not pending:
            return False
        self.process_job(pending[0])
        return True

    # --------------------------------------------------------------- pipeline
    def process_job(self, job):
        job_id = job["id"]
        directory = self.job_dir(job_id)
        if job["robot_type"] != "leg":
            self.fail(job_id, "雙輪 Q1 的 Isaac Lab 訓練任務尚未接入；目前真實訓練支援雙足 Q1 Leg。")
            return
        if not job.get("motion_id"):
            self.fail(job_id, "真實訓練需要 MediaPipe 關節參考；請先辨識影片並重新建立工作。")
            return
        try:
            motion = self.store.get("motions", job["motion_id"])
        except Exception:
            self.fail(job_id, "找不到此工作的關節參考。")
            return
        if job.get("state") == "queued_for_worker" or not (directory / "motion.json").exists():
            (directory / "motion.json").write_text(json.dumps(motion, ensure_ascii=False))
        training = dict(job.get("training") or {})
        budget = float(job["config"].get("hours", 2)) * 3600.
        if self.max_budget_s:
            budget = min(budget, self.max_budget_s)
        training.update(worker_pid=os.getpid(), num_envs=self.num_envs, job_dir=str(directory), budget_s=budget,
                        budget_capped=bool(self.max_budget_s and budget < float(job["config"].get("hours", 2)) * 3600.),
                        started_at=training.get("started_at") or self.clock())
        self.update(job_id, state="preparing", training=training, error=None)
        self.events(job_id, "worker 接手工作", num_envs=self.num_envs)
        try:
            if not self.acquire_gpu(job_id):
                return
            if not (directory / "evaluation.json").exists():
                resume = (directory / "latest.pt").exists()
                code = self.run_phase(job_id, "train", ["--motion", str(directory / "motion.json"), "--budget-seconds", str(training["budget_s"]),
                                                       "--num_envs", str(self.num_envs)] + (["--resume"] if resume else []))
                if code is None:
                    return
                if code != 0:
                    self.fail(job_id, "Isaac Lab 訓練程序結束碼 " + str(code), log=True)
                    return
            if not (directory / "record.json").exists():
                code = self.run_phase(job_id, "record", ["--video"])
                if code is None:
                    return
                if code != 0:
                    self.events(job_id, "錄影失敗，改以 rollout JSON 保留結果", exit_code=code)
                    if not (directory / "rollout.json").exists():
                        self.run_phase(job_id, "record", [])
            self.finish(job_id)
        finally:
            self.release_gpu()

    def acquire_gpu(self, job_id):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.lock_path.open("a+")
        announced = False
        while True:
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if self.store.get("jobs", job_id).get("state") == "cancelled":
                    self.release_gpu()
                    self.events(job_id, "等待 GPU 時被取消")
                    return False
                if not announced:
                    self.update(job_id, state="waiting_for_gpu")
                    self.events(job_id, "GPU 鎖由其他訓練持有，等待中", lock=str(self.lock_path))
                    announced = True
                self.heartbeat("waiting_for_gpu", job_id)
                time.sleep(15)

    def release_gpu(self):
        if self.lock_file is not None:
            try:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            finally:
                self.lock_file.close()
                self.lock_file = None

    def command(self, directory, phase, extra):
        script = "set -euo pipefail; cd \"$1\"; source scripts/isaac_env.sh; exec \"$ISAAC_PYTHON\" -u \"$2\" \"${@:3}\""
        return ["bash", "-c", script, "bash", str(self.leg_root), str(self.script), "--job-dir", str(directory),
                "--phase", phase, "--headless", *extra]

    def run_phase(self, job_id, phase, extra):
        """Run one Isaac phase; returns the exit code, or None when the job was cancelled."""
        directory = self.job_dir(job_id)
        command = self.command(directory, phase, extra)
        env = {k: v for k, v in os.environ.items() if k != "Q1_LEG_CURRICULUM_CONFIG"}
        env.update(Q1_LEG_ROOT=str(self.leg_root), PYTHONUNBUFFERED="1")
        log_path = directory / "native.log"
        with log_path.open("ab") as log:
            log.write(f"\n===== {phase} phase started {time.strftime('%Y-%m-%d %H:%M:%S %z')} =====\n".encode())
            log.flush()
            self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.leg_root), env=env, start_new_session=True)
        state = "starting_isaac" if phase == "train" else "recording"
        training = dict(self.store.get("jobs", job_id).get("training") or {})
        training.update(phase=phase, phase_pid=self.process.pid, worker_pid=os.getpid(), phase_started_at=self.clock(), command=command[4:])
        self.update(job_id, state=state, training=training)
        self.events(job_id, f"{phase} 程序啟動", pid=self.process.pid)
        try:
            while True:
                code = self.process.poll()
                self.tick(job_id, phase)
                if code is not None:
                    self.events(job_id, f"{phase} 程序結束", exit_code=code)
                    return code
                if self.store.get("jobs", job_id).get("state") == "cancelled":
                    self.terminate()
                    self.events(job_id, "使用者取消，已終止 Isaac 程序")
                    self.update(job_id, state="cancelled")
                    return None
                time.sleep(2)
        finally:
            self.process = None

    def terminate(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(25)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(10)
        except ProcessLookupError:
            pass

    def tick(self, job_id, phase):
        directory = self.job_dir(job_id)
        job = self.store.get("jobs", job_id)
        if job.get("state") == "cancelled":
            return
        training = dict(job.get("training") or {})
        progress = {}
        try:
            progress = json.loads((directory / "progress.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        lines = tail_lines(directory / "native.log", 40)
        metrics = parse_metrics(lines)
        budget = float(training.get("budget_s") or 3600.)
        now = self.clock()
        if phase == "train":
            sub = progress.get("phase")
            if sub in ("training", "starting") and progress.get("iteration", 0) > 0:
                state = "training"
            elif sub in ("evaluating", "trained"):
                state = "evaluating"
            else:
                state = "starting_isaac"
            elapsed = float(progress.get("elapsed_s") or 0.)
            fraction = max(elapsed / budget, progress.get("iteration", 0) / max(progress.get("max_iterations", 1), 1)) if progress else 0.
            percent = min(90., 2. + 88. * fraction) if state == "training" else 92. if state == "evaluating" else 1.
            eta = (progress.get("eta_s") if progress.get("eta_s") is not None else budget) + (POST_TRAINING_S if state != "evaluating" else POST_TRAINING_S / 2)
        else:
            state = "recording"
            percent = 96.
            eta = max(30., POST_TRAINING_S / 2 - (now - float(training.get("phase_started_at") or now)))
        training.update(progress=progress, metrics=metrics, eta_s=round(float(eta), 1), eta_at=round(now + float(eta), 1),
                        last_log_at=now, isaac_started=bool(progress) or any("Isaac Sim" in line or "Simulation App" in line for line in lines))
        self.update(job_id, state=state, progress=round(percent), training=training)
        self.heartbeat(state, job_id, eta_s=training["eta_s"])

    def fail(self, job_id, message, log=False):
        detail = message
        if log:
            lines = tail_lines(self.job_dir(job_id) / "native.log", 25)
            detail = message + "\n" + "\n".join(lines[-12:])
        self.update(job_id, state="failed", error=detail, finished_at=self.clock())
        self.events(job_id, "工作失敗", error=message)

    def finish(self, job_id):
        directory = self.job_dir(job_id)
        job = self.store.get("jobs", job_id)
        evaluation = json.loads((directory / "evaluation.json").read_text())
        record = {}
        try:
            record = json.loads((directory / "record.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        media = f"/media/jobs/{job_id}/"
        has_video = bool(record.get("video")) and (directory / "result.mp4").exists()
        passed = bool(evaluation.get("passed"))
        result = {
            "newly_trained": True, "landmark_rendered": False, "compatible_with_current_urdf": True, "passed": passed,
            "video_url": media + "result.mp4" if has_video else None,
            "rollout_url": media + "rollout.json" if (directory / "rollout.json").exists() else None,
            "evaluation_url": media + "evaluation.json", "reference_url": media + "reference.json", "log_url": media + "native.log",
            "checkpoint_url": media + "latest.pt" if (directory / "latest.pt").exists() else None,
            "title": job.get("action_name", "Q1 Motion"),
            "provenance": f"本次 Spark Isaac Sim / PhysX · RSL-RL PPO · {evaluation.get('num_envs')} envs × {evaluation.get('iterations')} updates",
            "label": "新訓練 PPO · 通過本站驗收" if passed else "新訓練 PPO · 未通過驗收（保留結果供檢視）",
            "evaluation": {key: evaluation.get(key) for key in ("survival_rate", "upright", "height_error", "pose_rmse", "velocity_error", "root_error",
                                                             "duration", "episodes", "iterations", "num_envs", "elapsed_s", "store_gate", "passed")},
            "mechanical": record.get("mechanical"), "record": {key: record.get(key) for key in ("frames", "duration_s", "survived", "fps")},
        }
        training = dict(job.get("training") or {})
        training.update(eta_s=0, finished_at=self.clock(), phase="done")
        self.update(job_id, state="complete", progress=100, result=result, training=training, finished_at=self.clock(), newly_trained=True)
        self.events(job_id, "工作完成", passed=passed, video=has_video)
        for file in directory.glob("model_*.pt"):
            file.unlink(missing_ok=True)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--data-dir")
    parser.add_argument("--leg-root")
    parser.add_argument("--num-envs", type=int)
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args(argv)
    worker = Worker(args.root, args.data_dir, args.leg_root, args.num_envs)
    if not shutil.which("bash"):
        sys.exit("bash is required to source the Isaac runtime")
    if args.once:
        worker.heartbeat("idle")
        return 0 if worker.run_once() else 1
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
