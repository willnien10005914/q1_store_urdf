#!/usr/bin/env python3
"""Lesson 01 — launch Isaac Lab 3.0 with a chosen physics preset and let Q1 stand.

Run inside an Isaac Lab 3.0 checkout (the store worker does this for you):

    uv run [--extra isaacsim] python example.py --job-dir <dir> --params <dir>/params.json

params.json (written by Q1 Store) carries: robot, urdf, physics, num_envs, viz, sim_seconds.
The script writes progress.json so the tutorial page can show a progress bar.
"""
from __future__ import annotations

import argparse
import json
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
STARTED = time.monotonic()


def _json_safe(value):
    """Coerce values so json.dumps(..., allow_nan=False) never emits NaN/Infinity tokens."""
    import math
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def progress(phase, iteration, total, **extra):
    """Progress contract shared with the Q1 Store worker (progress.json)."""
    elapsed = time.monotonic() - STARTED
    eta = (elapsed / max(iteration, 1)) * max(total - iteration, 0) if iteration else None
    payload = _json_safe({
        "phase": phase, "iteration": iteration, "max_iterations": total, "elapsed_s": round(elapsed, 1),
        "eta_s": None if eta is None else round(eta, 1), "num_envs": P.get("num_envs"), "updated_at": time.time(), **extra})
    (JOB / "progress.json").write_text(json.dumps(payload, indent=2, allow_nan=False))


# ---- 1. choose backend before any runtime import ---------------------------------------------
if P.get("viz", "none") != "none":
    os.environ.setdefault("HEADLESS", "1" if not os.environ.get("DISPLAY") else "0")

from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

sim_cfg = SimulationCfg(dt=1 / 200, device="cuda:0")
# physics=... on the CLI swaps the whole physics section; in code we assign the preset explicitly.
PHYSICS = P.get("physics", "newton_mjwarp")
if PHYSICS == "newton_mjwarp":
    from isaaclab_newton.physics import NewtonCfg  # MuJoCo-Warp solver, no Isaac Sim needed
    sim_cfg.physics = NewtonCfg()
elif PHYSICS == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg  # requires: uv sync --extra isaacsim
    sim_cfg.physics = PhysxCfg()
elif PHYSICS == "ovphysx":
    from isaaclab_ov.physics import OvPhysxCfg  # requires: --extra ovphysx
    sim_cfg.physics = OvPhysxCfg()
else:
    raise SystemExit(f"unknown physics preset {PHYSICS!r}")

if P.get("viz", "none") != "none":
    from isaaclab.visualizers import VisualizerCfg
    sim_cfg.visualizer_cfgs = VisualizerCfg(eye=(3.0, -2.5, 1.8), lookat=(0.0, 0.0, 0.8))

progress("starting", 0, 1, message=f"launching {PHYSICS}")

with launch_simulation(sim_cfg):
    try:
        import lab3_compat
        lab3_compat.patch_urdf_importer()
    except Exception as exc:
        print(f"[q1] lab3_compat patch skipped: {exc}")
    # ---- 2. runtime imports only after launch -----------------------------------------------
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
    from isaaclab.utils import configclass

    sim = sim_utils.SimulationContext(sim_cfg)

    @configclass
    class Q1SceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                                        joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                            gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0))),
            # 3.0: quaternions are XYZW — identity is (0, 0, 0, 1), not (1, 0, 0, 0).
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, P.get("spawn_height", 0.95)), rot=(0.0, 0.0, 0.0, 1.0),
                                                       joint_pos=P.get("default_joint_positions", {})),
            actuators={"all": ImplicitActuatorCfg(joint_names_expr=[".*"], stiffness=80.0, damping=4.0,
                                                  actuator_effort_limit=120.0, joint_effort_limit=120.0)},
        )

    scene = InteractiveScene(Q1SceneCfg(num_envs=int(P.get("num_envs", 16)), env_spacing=2.0))
    sim.reset()
    robot: Articulation = scene["robot"]
    print(f"[lesson01] {P['robot']} loaded: {robot.num_joints} joints, {robot.num_bodies} bodies, backend={PHYSICS}")
    print(f"[lesson01] joint names: {robot.joint_names}")

    # ---- 3. hold the default stance for sim_seconds ----------------------------------------
    steps = int(float(P.get("sim_seconds", 3.0)) / sim_cfg.dt)
    target = robot.data.default_joint_pos.torch.clone()
    for step in range(steps):
        # 3.0: joint targets live on the actuator collection (keyword-only setters).
        robot.actuators.target_command.set_position_index(value=target)
        scene.write_data_to_sim()
        sim.step()
        scene.update(sim_cfg.dt)
        if step % 50 == 0:
            height = robot.data.root_pos_w.torch[:, 2].mean().item()
            progress("running", step, steps, message=f"root height {height:.3f} m")
    height = robot.data.root_pos_w.torch[:, 2]
    summary = _json_safe({
        "physics": PHYSICS, "num_envs": scene.num_envs, "steps": steps,
        "mean_root_height_m": round(float(height.mean().item()), 4),
        "min_root_height_m": round(float(height.min().item()), 4),
        "upright_fraction": round(float((height > 0.5).float().mean().item()), 3),
    })
    (JOB / "result.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    progress("complete", steps, steps, message="done", **summary)
    print("[lesson01] " + json.dumps(summary))
