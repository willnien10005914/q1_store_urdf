#!/usr/bin/env python3
"""Lesson 07 — visualizers and recording.

    uv run python example.py --job-dir <dir> --params <dir>/params.json

Produces rollout.json (always) and result.mp4 (when viz=kit and record_video), plus progress.json with a
`viz_url` field the store proxies to /viz/<run_id>/ so the viewer is reachable through Pinggy.

If Isaac Lab 3 is not importable the script falls back to a kinematic rollout generator so the recording
contract and the browser replay can be learned on any machine.
"""
from __future__ import annotations

import argparse
import json
import math
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


def write_rollout(frames: list[dict]) -> None:
    (JOB / "rollout.json").write_text(json.dumps({"fps": FPS, "joint_names": JOINTS, "quaternion_order": "xyzw",
                                                   "robot": P.get("robot"), "urdf": P.get("urdf"), "frames": frames}))


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
from isaaclab_newton.physics import NewtonCfg  # noqa: E402
from isaaclab.visualizers import VisualizerCfg  # noqa: E402

DT = 1.0 / FPS
sim_cfg = SimulationCfg(dt=DT, device="cuda:0", physics=NewtonCfg())

# --viz equivalent: a list of VisualizerCfg. "none" keeps the run fully headless (HEADLESS=1 semantics).
visualizers = []
viz_url = None
if VIZ == "viser":
    visualizers.append(VisualizerCfg(kind="viser", port=PORT, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)))
    viz_url = f"http://127.0.0.1:{PORT}/"
elif VIZ == "rerun":
    visualizers.append(VisualizerCfg(kind="rerun", port=PORT, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)))
    viz_url = f"http://127.0.0.1:{PORT}/"
elif VIZ in ("newton_gl", "kit"):
    visualizers.append(VisualizerCfg(kind=VIZ, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)))
sim_cfg.visualizers = visualizers

progress("starting", 0, FRAMES, message=f"launching viz={VIZ}", viz_url=viz_url)

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

    recorder = None
    if VIZ == "kit" and P.get("record_video", True):
        from isaaclab.visualizers import VideoRecorderCfg
        recorder = VideoRecorderCfg(source="viz:kit", fps=FPS, length=FRAMES, output_dir=str(JOB)).create()

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
        frames.append({"t": round(t, 4), "root_pos": [round(v, 4) for v in pos], "root_quat": [round(v, 5) for v in quat],
                       "q": [round(v, 5) for v in q]})
        if recorder is not None:
            recorder.capture()
        if k % FPS == 0:
            progress("running", k, FRAMES, message=f"t={t:.1f}s viz={VIZ}", viz_url=viz_url)
    write_rollout(frames)
    video = False
    if recorder is not None:
        recorder.close()
        mp4 = next(JOB.glob("*.mp4"), None)
        if mp4 and mp4.name != "result.mp4":
            mp4.rename(JOB / "result.mp4")
        video = (JOB / "result.mp4").exists()
    result = {"mode": "isaaclab3", "frames": FRAMES, "fps": FPS, "viz": VIZ, "viz_url": viz_url, "video": video, "motion": MOTION}
    (JOB / "result.json").write_text(json.dumps(result, indent=2))
    progress("complete", FRAMES, FRAMES, **result)
    print("[lesson07] " + json.dumps(result))
