#!/usr/bin/env python3
"""Lesson 07 — visualizers and recording.

    uv run python example.py --job-dir <dir> --params <dir>/params.json

Produces rollout.json (always) and result.mp4 (when viz=kit and a ManagerBased video recorder is
available — this InteractiveScene demo relies on store-side ffmpeg from rollout for non-kit viz),
plus progress.json with a `viz_url` field the store proxies to /viz/<run_id>/ so the viewer is
reachable through Pinggy.

If Isaac Lab 3 is not importable the script falls back to a kinematic rollout generator so the
recording contract and the browser replay can be learned on any machine.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--job-dir", type=Path, required=True)
parser.add_argument("--params", type=Path, required=True)
args = parser.parse_args()
P = json.loads(args.params.read_text())
JOB = args.job_dir
JOB.mkdir(parents=True, exist_ok=True)
T0 = time.monotonic()

VIZ = P.get("viz", "viser")
FPS = int(P.get("fps", 30))
SECONDS = float(P.get("sim_seconds", 6.0))
MOTION = P.get("motion", "squat")
PORT = int(P.get("viser_port", 8081))
JOINTS = list(P.get("control_joints", []))
DEFAULTS = P.get("default_joint_positions", {})
FRAMES = int(SECONDS * FPS)


def progress(phase, iteration, total, **extra):
    elapsed = time.monotonic() - T0
    eta = (elapsed / max(iteration, 1)) * max(total - iteration, 0) if iteration else None
    (JOB / "progress.json").write_text(json.dumps({"phase": phase, "iteration": iteration, "max_iterations": total,
                                                    "elapsed_s": round(elapsed, 1), "eta_s": None if eta is None else round(eta, 1),
                                                    "updated_at": time.time(), **extra}))


def motion_targets(t: float) -> list[float]:
    """Joint targets for the demo motion. Hip/knee names come from the Q1 URDF; wheels are continuous joints."""
    out = []
    for i, name in enumerate(JOINTS):
        base = float(DEFAULTS.get(name, 0.0))
        lname = name.lower()
        if MOTION == "hold":
            out.append(base)
        elif MOTION == "squat":
            amp = 0.35 if ("knee" in lname or "hip" in lname or "pitch" in lname) else 0.0
            out.append(base + amp * math.sin(2 * math.pi * 0.5 * t))
        elif MOTION == "wave_joints":
            phase = (t * 0.8) % max(len(JOINTS), 1)
            out.append(base + (0.4 if int(phase) == i else 0.0) * math.sin(2 * math.pi * 1.0 * t))
        elif MOTION == "wheel_spin":
            out.append(base + (6.0 * t if "wheel" in lname else 0.0))
        else:
            out.append(base)
    return out


def finite(value, fallback=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    return number if math.isfinite(number) else float(fallback)


def sanitize_frame(frame: dict, last: dict | None) -> dict:
    """Keep browser JSON.parse happy — never emit bare NaN/Infinity."""
    last = last or {"root_pos": [0.0, 0.0, float(P.get("spawn_height", 0.8))],
                    "root_quat": [0.0, 0.0, 0.0, 1.0], "q": [0.0] * len(JOINTS)}
    pos = list(frame.get("root_pos") or last["root_pos"])
    quat = list(frame.get("root_quat") or last["root_quat"])
    q = list(frame.get("q") or last["q"])
    root_pos = [finite(pos[i] if i < len(pos) else None, last["root_pos"][i]) for i in range(3)]
    root_quat = [finite(quat[i] if i < len(quat) else None, last["root_quat"][i]) for i in range(4)]
    nq = max(len(JOINTS), len(last["q"]), len(q))
    clean_q = [finite(q[i] if i < len(q) else None, last["q"][i] if i < len(last["q"]) else 0.0) for i in range(nq)]
    return {"t": round(finite(frame.get("t", frame.get("time", 0.0)), 0.0), 4),
            "root_pos": [round(v, 4) for v in root_pos],
            "root_quat": [round(v, 5) for v in root_quat],
            "q": [round(v, 5) for v in clean_q]}


def write_rollout(frames: list[dict]) -> None:
    clean, last = [], None
    for frame in frames:
        last = sanitize_frame(frame, last)
        clean.append(last)
    payload = {"fps": FPS, "joint_names": JOINTS, "quaternion_order": "xyzw",
               "robot": P.get("robot"), "urdf": P.get("urdf"), "frames": clean}
    (JOB / "rollout.json").write_text(json.dumps(payload, allow_nan=False))


def fallback_kinematic() -> dict:
    """No Isaac Lab 3 available: emit a kinematic rollout so the replay / mp4 pipeline can still be exercised."""
    frames = []
    for k in range(FRAMES):
        t = k / FPS
        q = motion_targets(t)
        z = float(P.get("spawn_height", 0.8))
        if MOTION == "squat":
            z -= 0.06 * (1 - math.cos(2 * math.pi * 0.5 * t)) / 2
        frames.append({"t": round(t, 4), "root_pos": [0.0, 0.0, round(z, 4)], "root_quat": [0.0, 0.0, 0.0, 1.0],
                       "q": [round(v, 5) for v in q]})
        if k % FPS == 0:
            progress("running", k, FRAMES, message=f"kinematic fallback t={t:.1f}s", mode="fallback")
    write_rollout(frames)
    return {"mode": "fallback_kinematic", "frames": FRAMES, "fps": FPS, "viz": "none", "video": False}


try:
    from isaaclab.app import launch_simulation  # noqa: F401
    HAVE_ISAACLAB3 = True
except Exception:  # ImportError or a 2.x isaaclab without launch_simulation
    HAVE_ISAACLAB3 = False

if not HAVE_ISAACLAB3:
    progress("starting", 0, FRAMES, message="isaaclab 3 not importable; kinematic fallback", mode="fallback")
    result = fallback_kinematic()
    (JOB / "result.json").write_text(json.dumps(result, indent=2))
    progress("complete", FRAMES, FRAMES, **result)
    print("[lesson07] " + json.dumps(result))
    raise SystemExit(0)

from isaaclab.sim import SimulationCfg  # noqa: E402
from isaaclab_newton.physics import (  # noqa: E402
    MJWarpSolverCfg,
    NewtonCfg,
    NewtonCollisionPipelineCfg,
    NewtonShapeCfg,
)

DT = 1.0 / FPS
# Same locomotion-capacity Newton preset as l06 — default njmax=300 overflows on Q1 and writes NaN poses.
sim_cfg = SimulationCfg(
    dt=DT,
    device="cuda:0",
    physics=NewtonCfg(
        solver_cfg=MJWarpSolverCfg(
            njmax=int(P.get("njmax", 1000)),
            nconmax=int(P.get("nconmax", 300)),
            iterations=int(P.get("solver_iterations", 100)),
            integrator="implicitfast",
            use_mujoco_contacts=False,
        ),
        collision_cfg=NewtonCollisionPipelineCfg(max_triangle_pairs=2_500_000),
        num_substeps=int(P.get("num_substeps", 2)),
        default_shape_cfg=NewtonShapeCfg(margin=0.0, ke=160000.0, kd=1100.0),
    ),
)

# Concrete visualizer cfgs (isaaclab_visualizers.*). Base VisualizerCfg has no `kind=` kwarg —
# pick ViserVisualizerCfg / RerunVisualizerCfg / NewtonGLVisualizerCfg / KitVisualizerCfg instead.
# Attach them on sim_cfg.visualizer_cfgs (not the removed ViewerCfg / sim.visualizers).
visualizers = []
viz_url = None
viz_note = None
if VIZ == "none":
    os.environ.setdefault("HEADLESS", "1")
elif VIZ == "viser":
    from isaaclab_visualizers.viser import ViserVisualizerCfg  # noqa: E402
    visualizers.append(ViserVisualizerCfg(
        port=PORT, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6), open_browser=False,
    ))
    viz_url = f"http://127.0.0.1:{PORT}/"
elif VIZ == "rerun":
    from isaaclab_visualizers.rerun import RerunVisualizerCfg  # noqa: E402
    visualizers.append(RerunVisualizerCfg(
        web_port=PORT, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6), open_browser=False,
    ))
    viz_url = f"http://127.0.0.1:{PORT}/"
elif VIZ == "newton_gl":
    from isaaclab_visualizers.newton import NewtonGLVisualizerCfg  # noqa: E402
    visualizers.append(NewtonGLVisualizerCfg(
        eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6), headless=True,
    ))
elif VIZ == "kit":
    from isaaclab_visualizers.kit import KitVisualizerCfg  # noqa: E402
    visualizers.append(KitVisualizerCfg(eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)))
else:
    viz_note = f"unknown viz={VIZ!r}; running headless"
    os.environ.setdefault("HEADLESS", "1")
sim_cfg.visualizer_cfgs = visualizers

progress("starting", 0, FRAMES, message=f"launching viz={VIZ}", viz_url=viz_url, note=viz_note)

with launch_simulation(sim_cfg):
    try:
        import lab3_compat
        lab3_compat.patch_urdf_importer()
    except Exception as exc:
        print(f"[q1] lab3_compat patch skipped: {exc}")
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
    from isaaclab.utils import configclass
    import torch

    sim = sim_utils.SimulationContext(sim_cfg)
    is_wheel = P.get("robot") == "wheel"

    @configclass
    class RecSceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                                        joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                            gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0))),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, float(P.get("spawn_height", 0.8))), rot=(0.0, 0.0, 0.0, 1.0),
                                                       joint_pos=DEFAULTS),
            actuators={
                "joints": ImplicitActuatorCfg(joint_names_expr=["^(?!.*wheel).*$"], stiffness=80.0, damping=4.0,
                                              actuator_effort_limit=120.0, joint_effort_limit=120.0),
                "wheels": ImplicitActuatorCfg(joint_names_expr=[".*wheel.*"], stiffness=0.0, damping=2.0,
                                              actuator_effort_limit=40.0, joint_effort_limit=40.0),
            } if is_wheel else {
                "joints": ImplicitActuatorCfg(joint_names_expr=[".*"], stiffness=80.0, damping=4.0,
                                              actuator_effort_limit=120.0, joint_effort_limit=120.0)},
        )

    # VideoRecorderCfg lives on ManagerBasedRLEnvCfg.video_recorders (source="visualizer:kit",
    # video_length=N) — there is no VideoRecorderCfg.create(). This InteractiveScene demo always
    # writes rollout.json; kit/native mp4 needs an RL env, so non-kit / this path use store ffmpeg.
    if VIZ == "kit" and P.get("record_video", True):
        print(
            "[lesson07] VideoRecorderCfg(source='visualizer:kit', video_length=...) attaches to "
            "ManagerBasedRLEnvCfg.video_recorders; InteractiveScene demo records via rollout.json."
        )

    scene = InteractiveScene(RecSceneCfg(num_envs=1, env_spacing=2.0))
    sim.reset()
    robot: Articulation = scene["robot"]
    names = list(robot.joint_names)
    idx = [names.index(j) for j in JOINTS if j in names]
    frames = []
    for k in range(FRAMES):
        t = k / FPS
        tgt = torch.tensor([motion_targets(t)], device=sim.device)
        if is_wheel and MOTION == "wheel_spin":
            wheel_ids = [i for i, n in enumerate(names) if "wheel" in n.lower()]
            robot.actuators.target_command.set_velocity_index(value=torch.full((1, len(wheel_ids)), 6.0, device=sim.device),
                                                              joint_ids=wheel_ids)
        else:
            robot.actuators.target_command.set_position_index(value=tgt[:, :len(idx)], joint_ids=idx)
        scene.write_data_to_sim()
        sim.step()
        scene.update(DT)
        pos = robot.data.root_pos_w.torch[0].tolist()
        quat = robot.data.root_quat_w.torch[0].tolist()  # XYZW in 3.0
        q = robot.data.joint_pos.torch[0, idx].tolist()
        # Prefer commanded targets when physics returns non-finite values (overflow / blow-up).
        if not all(math.isfinite(v) for v in pos + quat + q):
            q = motion_targets(t)
            pos = [0.0, 0.0, float(P.get("spawn_height", 0.8))]
            quat = [0.0, 0.0, 0.0, 1.0]
        frames.append({"t": round(t, 4), "root_pos": pos, "root_quat": quat, "q": q})
        if k % FPS == 0:
            progress("running", k, FRAMES, message=f"t={t:.1f}s viz={VIZ}", viz_url=viz_url)
    write_rollout(frames)
    result = {
        "mode": "isaaclab3", "frames": FRAMES, "fps": FPS, "viz": VIZ, "viz_url": viz_url,
        "video": False, "motion": MOTION,
        "video_note": "rollout.json → store ffmpeg for result.mp4; VideoRecorderCfg needs ManagerBasedRLEnv",
    }
    (JOB / "result.json").write_text(json.dumps(result, indent=2))
    progress("complete", FRAMES, FRAMES, **result)
    print("[lesson07] " + json.dumps(result))
