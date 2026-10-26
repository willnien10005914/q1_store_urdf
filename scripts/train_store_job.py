#!/usr/bin/env python3
"""Train or record a Q1 Store motion-tracking job in Isaac Lab (runs inside the Isaac Sim python).

Launch through the leg repository's ``scripts/isaac_env.sh`` so PYTHONPATH carries
``leg_humanoid_lab`` and the Isaac Lab sources::

    source "$Q1_LEG_ROOT/scripts/isaac_env.sh" && "$ISAAC_PYTHON" -u scripts/train_store_job.py \
        --job-dir .data/jobs/<id> --motion .data/jobs/<id>/motion.json --phase train --budget-seconds 3600 --headless

The job directory receives ``reference.json``, ``progress.json``, ``latest.pt``, ``contract.json``,
``evaluation.json`` (train phase) and ``rollout.json``, ``record.json``, ``result.mp4`` (record phase).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

STORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STORE_ROOT))
LEG_ROOT = Path(os.environ.get("Q1_LEG_ROOT", Path.home() / "will/q1_leg_urdf")).resolve()
sys.path.insert(0, str(LEG_ROOT / "scripts/reinforcement_learning/rsl_rl"))
# Foot-target curricula belong to the repository sidestep reference, never to store clips.
os.environ.pop("Q1_LEG_CURRICULUM_CONFIG", None)

from isaaclab.app import AppLauncher  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--job-dir", type=Path, required=True)
parser.add_argument("--motion", type=Path, help="Stored /api/motions record (train phase)")
parser.add_argument("--phase", choices=["train", "record"], default="train")
parser.add_argument("--budget-seconds", type=float, default=3600.)
parser.add_argument("--max-iterations", type=int, default=200000)
parser.add_argument("--chunk", type=int, default=20, help="Learning iterations between progress updates")
parser.add_argument("--num_envs", type=int, default=2048)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--resume", action="store_true", help="Continue from latest.pt in the job directory")
parser.add_argument("--video", action="store_true", help="Record result.mp4 during the record phase")
parser.add_argument("--evaluation-episodes", type=int, default=32)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.phase == "record" and args.video:
    args.enable_cameras = True
launcher = AppLauncher(args)
app = launcher.app

import hashlib  # noqa: E402
import shutil  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from rsl_rl.runners import OnPolicyRunner  # noqa: E402
from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper  # noqa: E402
import leg_humanoid_lab.isaac_env as lab_env  # noqa: E402
from leg_humanoid_lab.isaac_env import IsaacLegEnv, LegEnvCfg  # noqa: E402
from leg_humanoid_lab.motion import Motion  # noqa: E402
from leg_humanoid_lab.robot import ROOT, URDF, JOINTS, DEFAULT, LOWER, UPPER, contract, grounded_height  # noqa: E402
from leg_humanoid_lab.isaac_evaluation import evaluate  # noqa: E402
from leg_humanoid_lab.agent_cfg import PPOCfg  # noqa: E402
from runtime_compat import runner_cfg, load_checkpoint  # noqa: E402
from q1store.reference import build_reference  # noqa: E402

JOB = args.job_dir.resolve()
JOB.mkdir(parents=True, exist_ok=True)
REFERENCE = JOB / "reference.json"
PROGRESS = JOB / "progress.json"
LATEST = JOB / "latest.pt"


def write_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def log(message):
    print(f"[store-job] {message}", flush=True)


class StoreMotion(Motion):
    """Repository sidestep contract, fed by the store clip instead of docs/reference."""

    def __init__(self, skill, reference=None):
        super().__init__(skill, reference or REFERENCE)


lab_env.Motion = StoreMotion


def make_env(num_envs, render_mode=None):
    cfg = LegEnvCfg()
    cfg.skill = "sidestep"
    cfg.scene.num_envs = num_envs
    cfg.seed = args.seed
    cfg.sim.device = args.device
    if render_mode:
        cfg.viewer.eye = (3., -2.6, 1.9)
        cfg.viewer.lookat = (0., .10, .75)
        cfg.viewer.resolution = (1280, 720)
    env = IsaacLegEnv(cfg, render_mode=render_mode)
    env.cfg.episode_length_s = float(min(12., max(4., env.motion.duration + 1.)))
    return env


def ensure_reference():
    if REFERENCE.exists() and args.resume:
        return json.loads(REFERENCE.read_text())
    if not args.motion:
        parser.error("--motion is required for the train phase")
    motion = json.loads(args.motion.read_text())
    reference = build_reference(motion, joint_names=list(JOINTS), default=DEFAULT.tolist(), lower=LOWER.tolist(),
                                upper=UPPER.tolist(), urdf_sha256=hashlib.sha256(URDF.read_bytes()).hexdigest(),
                                grounded_height=lambda q: grounded_height(np.asarray(q)), job_id=JOB.name)
    write_json(REFERENCE, reference)
    log(f"reference: {len(reference['time'])} samples, {reference['duration_s']:.2f} s, "
        f"{len(reference['clip']['missing_joints_default'])} joints defaulted")
    return reference


def store_gate(report):
    config = read_json(ROOT / "config/curriculum.json", {}) or {}
    common = config.get("common_gates", {})
    sidestep = config.get("sidestep_gates", {})
    checks = {
        "survival_rate": (report["survival_rate"], ">=", common.get("survival_rate_min", .9)),
        "upright": (report["upright"], ">=", common.get("upright_min", .9)),
        "height_error_m": (report["height_error"], "<", common.get("height_error_m_max", .08)),
        "pose_rmse_rad": (report["pose_rmse"], "<", sidestep.get("joint_rmse_rad_max", .35)),
    }
    results = {}
    for key, (value, op, limit) in checks.items():
        ok = value >= limit if op == ">=" else value < limit
        results[key] = {"value": float(value), "limit": limit, "op": op, "passed": bool(ok)}
    return all(item["passed"] for item in results.values()), results


def prune_snapshots(keep=2):
    snapshots = sorted(JOB.glob("model_*.pt"), key=lambda p: int(p.stem.split("_")[1]))
    for file in snapshots[:-keep]:
        file.unlink(missing_ok=True)


def train():
    reference = ensure_reference()
    previous = read_json(PROGRESS, {}) if args.resume else {}
    elapsed_before = float(previous.get("elapsed_s", 0.)) if previous else 0.
    env = make_env(args.num_envs)
    wrapped = RslRlVecEnvWrapper(env, clip_actions=1.)
    agent = PPOCfg()
    agent.seed = args.seed
    agent.algorithm.entropy_coef = .0001
    agent.save_interval = 10 ** 9
    runner = OnPolicyRunner(wrapped, runner_cfg(agent), log_dir=str(JOB), device=args.device)
    iteration = 0
    if args.resume and LATEST.exists():
        load_checkpoint(runner, LATEST, load_optimizer=True, load_iteration=True)
        # latest.pt is written after the post-chunk increment, so it already names the next iteration.
        iteration = int(runner.current_learning_iteration)
        log(f"resumed from {LATEST.name} at iteration {iteration}")
    # A single TensorBoard writer for the whole run instead of one per chunk.
    runner.logger.init_logging_writer()
    runner.logger.init_logging_writer = lambda: None
    runner.logger.stop_logging_writer = lambda: None
    write_json(JOB / "contract.json", {**contract(), "backend": "isaaclab", "proxy": False, "store_job": JOB.name,
                                       "reference_sha256": reference["reference_sha256"], "num_envs": env.num_envs})
    steps_per_iteration = agent.num_steps_per_env * env.num_envs
    started = time.monotonic()
    budget = max(60., args.budget_seconds)
    remaining_budget = max(0., budget - elapsed_before)
    rate = None
    sps = None

    def publish(phase, **extra):
        elapsed = elapsed_before + (time.monotonic() - started)
        iterations_left = max(0, args.max_iterations - iteration)
        eta_iterations = iterations_left / rate if rate else None
        eta_budget = max(0., budget - elapsed)
        eta = min(filter(lambda v: v is not None, (eta_iterations, eta_budget)), default=eta_budget)
        write_json(PROGRESS, {"phase": phase, "iteration": iteration, "max_iterations": args.max_iterations,
                              "budget_s": budget, "elapsed_s": round(elapsed, 1), "eta_s": round(eta, 1),
                              "iterations_per_second": rate, "steps_per_second": sps, "num_envs": env.num_envs,
                              "steps_per_iteration": steps_per_iteration, "episode_length_s": env.cfg.episode_length_s,
                              "reference_duration_s": reference["duration_s"], "updated_at": time.time(), **extra})

    publish("starting")
    log(f"training {env.num_envs} envs, budget {remaining_budget:.0f} s, chunk {args.chunk} iterations")
    deadline = time.monotonic() + remaining_budget
    while iteration < args.max_iterations and time.monotonic() < deadline:
        chunk = max(1, min(args.chunk, args.max_iterations - iteration))
        chunk_start = time.monotonic()
        runner.learn(num_learning_iterations=chunk, init_at_random_ep_len=False)
        runner.current_learning_iteration += 1
        iteration = int(runner.current_learning_iteration)
        chunk_time = max(1e-6, time.monotonic() - chunk_start)
        rate = chunk / chunk_time
        sps = round(steps_per_iteration * rate)
        runner.save(str(LATEST))
        prune_snapshots()
        publish("training")
        if rate and (deadline - time.monotonic()) < 1.5 / rate:
            break
    runner.save(str(LATEST))
    publish("evaluating")
    log(f"training finished at iteration {iteration}; evaluating")
    report = evaluate(runner, wrapped, env, episodes=min(args.evaluation_episodes, env.num_envs), checkpoint=LATEST, publish=False)
    passed, gates = store_gate(report)
    report["store_gate"] = {"passed": passed, "checks": gates,
                            "scope": "本站 motion tracking 驗收：存活率、直立度、高度誤差與關節 RMSE；不包含 repository 閃身專用的側向位移門檻。"}
    report["passed"] = passed
    report["iterations"] = iteration
    report["num_envs"] = env.num_envs
    report["budget_s"] = budget
    report["elapsed_s"] = round(elapsed_before + (time.monotonic() - started), 1)
    write_json(JOB / "evaluation.json", report)
    publish("trained", passed=passed)
    env.close()


def record():
    reference = json.loads(REFERENCE.read_text())
    saved = read_json(JOB / "contract.json", {})
    expected = contract()
    for key in ("robot", "urdf_sha256", "joint_names", "observation_size"):
        if saved.get(key) != expected[key]:
            raise ValueError(f"Checkpoint contract mismatch: {key}")
    env = make_env(1, render_mode="rgb_array" if args.video else None)
    wrapped = RslRlVecEnvWrapper(env, clip_actions=1.)
    runner = OnPolicyRunner(wrapped, runner_cfg(PPOCfg()), log_dir=None, device=args.device)
    load_checkpoint(runner, LATEST, load_optimizer=False, load_iteration=False)
    policy = runner.get_inference_policy(device=env.device)
    torch.manual_seed(args.seed)
    obs = wrapped.reset()[0]
    writer = None
    video = JOB / "result.mp4"
    if args.video:
        import imageio.v2 as imageio
        writer = imageio.get_writer(str(video), fps=50, codec="libx264", quality=8, macro_block_size=16, ffmpeg_params=["-movflags", "+faststart"])
        for _ in range(12):
            env.sim.render()
            env.render()
    frames, power_series, torque_peaks = [], [], np.zeros(len(JOINTS))
    failed = False
    joint_names = list(JOINTS)
    with torch.inference_mode():
        while app.is_running():
            data = env.robot.data
            torque = data.applied_torque[0, env.jids].cpu().numpy()
            velocity = data.joint_vel[0, env.jids].cpu().numpy()
            power_series.append(float(np.abs(torque * velocity).sum()))
            torque_peaks = np.maximum(torque_peaks, np.abs(torque))
            frames.append({"time": round(float(env.episode_length_buf[0]) * env.step_dt, 4),
                           "q": data.joint_pos[0, env.jids].cpu().tolist(),
                           "root": (data.root_pos_w[0] - env.scene.env_origins[0]).cpu().tolist(),
                           "quaternion": data.root_quat_w[0].cpu().tolist()})
            if writer:
                env.sim.render()
                writer.append_data(np.asarray(env.render(recompute=True)))
            if env.episode_length_buf[0] >= env.max_episode_length - 2:
                break
            obs, _, done, _ = wrapped.step(policy(obs))
            if bool(done[0]):
                failed = bool(env.reset_terminated[0])
                break
    if writer:
        writer.close()
    duration = len(frames) * env.step_dt
    energy = float(np.sum(power_series) * env.step_dt)
    groups = {}
    for name, peak in zip(joint_names, torque_peaks):
        group = name.split("_")[1] if name.startswith(("left_", "right_")) else "waist"
        groups[group] = max(groups.get(group, 0.), float(peak))
    rollout = {"source": "ppo_isaaclab_store", "job_id": JOB.name, "skill": "store_motion_tracking", "joint_names": joint_names,
               "dt": env.step_dt, "frames": frames, "survived": not failed, "duration_s": round(duration, 3),
               "reference": {"duration_s": reference["duration_s"], "store_motion_id": reference.get("store_motion_id")}}
    write_json(JOB / "rollout.json", rollout)
    record_info = {"job_id": JOB.name, "video": video.name if writer else None, "fps": 50, "frames": len(frames),
                   "duration_s": round(duration, 3), "survived": not failed,
                   "checkpoint_sha256": hashlib.sha256(LATEST.read_bytes()).hexdigest(),
                   "mechanical": {"status": "simulated", "energy_j": round(energy, 2),
                                  "mean_power_w": round(energy / max(duration, 1e-6), 2),
                                  "peak_power_w": round(float(max(power_series)) if power_series else 0., 2),
                                  "peak_torque_nm_by_group": {k: round(v, 2) for k, v in groups.items()},
                                  "scope": "PhysX 關節力矩 × 角速度的絕對值積分（模型機械功）；不含馬達電氣損耗、驅動器效率與電池，不等於實機耗電。"}}
    write_json(JOB / "record.json", record_info)
    print("RECORD_RESULT " + json.dumps(record_info), flush=True)
    env.close()


try:
    if args.phase == "train":
        train()
    else:
        record()
except BaseException:
    import traceback
    traceback.print_exc()
    sys.stdout.flush()
    sys.stderr.flush()
    raise
finally:
    app.close()
