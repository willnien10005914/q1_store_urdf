#!/usr/bin/env python3
"""Lesson 02 — Q1 drop test on a selectable physics backend (PhysX or Newton MJWarp).

    uv run [--extra isaacsim] python example.py --job-dir <dir> --params <dir>/params.json

Writes progress.json during the run and result.json with settle height, peak contact force and steps/s.
Run it twice with physics=newton_mjwarp and physics=isaacsim_physx, then compare the two result.json files.
"""
from __future__ import annotations

import argparse
import json
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


def progress(phase, iteration, total, **extra):
    elapsed = time.monotonic() - T0
    eta = (elapsed / max(iteration, 1)) * max(total - iteration, 0) if iteration else None
    (JOB / "progress.json").write_text(json.dumps({"phase": phase, "iteration": iteration, "max_iterations": total,
                                                    "elapsed_s": round(elapsed, 1), "eta_s": None if eta is None else round(eta, 1),
                                                    "num_envs": P.get("num_envs"), "updated_at": time.time(), **extra}))


from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

PHYSICS = P.get("physics", "newton_mjwarp")
DT = float(P.get("dt", 0.005))
ITERS = int(P.get("solver_iterations", 4))
sim_cfg = SimulationCfg(dt=DT, device="cuda:0")
if PHYSICS == "newton_mjwarp":
    from isaaclab_newton.physics import NewtonCfg
    # MuJoCo-Warp solver options live on the Newton cfg; iterations map to MuJoCo's solver iterations.
    sim_cfg.physics = NewtonCfg(solver_iterations=ITERS)
elif PHYSICS == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg
    sim_cfg.physics = PhysxCfg(max_position_iteration_count=ITERS, max_velocity_iteration_count=max(1, ITERS // 4))
else:
    raise SystemExit(f"this lesson compares newton_mjwarp and isaacsim_physx; got {PHYSICS!r}")

progress("starting", 0, 1, message=f"launching {PHYSICS} dt={DT}")

with launch_simulation(sim_cfg):
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
    from isaaclab.sensors import ContactSensor, ContactSensorCfg
    from isaaclab.utils import configclass

    sim = sim_utils.SimulationContext(sim_cfg)
    # Contact bodies differ per Q1 variant: feet for the biped, wheels for the wheeled model.
    contact_expr = P.get("contact_bodies", ".*foot.*" if P["robot"] == "leg" else ".*wheel_link")

    @configclass
    class DropSceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                                        joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                            gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0)),
                                        activate_contact_sensors=True),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, float(P.get("drop_height", 1.2))), rot=(0.0, 0.0, 0.0, 1.0),
                                                       joint_pos=P.get("default_joint_positions", {})),
            actuators={"all": ImplicitActuatorCfg(joint_names_expr=[".*"], stiffness=80.0, damping=4.0,
                                                  actuator_effort_limit=120.0, joint_effort_limit=120.0)},
        )
        contacts = ContactSensorCfg(prim_path="{ENV_REGEX_NS}/Robot/" + contact_expr, history_length=3, track_air_time=True)

    scene = InteractiveScene(DropSceneCfg(num_envs=int(P.get("num_envs", 64)), env_spacing=2.5))
    sim.reset()
    robot: Articulation = scene["robot"]
    contacts: ContactSensor = scene["contacts"]
    target = robot.data.default_joint_pos.torch.clone()
    steps = int(float(P.get("sim_seconds", 4.0)) / DT)
    tail = max(1, int(0.5 / DT))
    heights, peak_force = [], 0.0
    wall = time.monotonic()
    for step in range(steps):
        robot.actuators.target_command.set_position_index(value=target)
        scene.write_data_to_sim()
        sim.step()
        scene.update(DT)
        # Re-read ProxyArray properties every step; never cache .torch across resets (Newton rebuilds buffers).
        root_h = robot.data.root_pos_w.torch[:, 2]
        heights.append(root_h.mean().item())
        forces = contacts.data.net_normal_forces_w.torch  # 3.0 contact contract: explicit normal / friction / total
        peak_force = max(peak_force, forces.norm(dim=-1).max().item())
        if step % 40 == 0:
            progress("running", step, steps, message=f"h={root_h.mean().item():.3f} m, peak F={peak_force:.0f} N")
    seconds = time.monotonic() - wall
    final_h = robot.data.root_pos_w.torch[:, 2]
    result = {"physics": PHYSICS, "dt": DT, "solver_iterations": ITERS, "num_envs": scene.num_envs, "steps": steps,
              "settle_height_m": round(sum(heights[-tail:]) / tail, 4), "min_height_m": round(min(heights), 4),
              "max_contact_force_n": round(peak_force, 1), "fallen_fraction": round((final_h < 0.5).float().mean().item(), 3),
              "steps_per_second": round(steps * scene.num_envs / max(seconds, 1e-6)), "wall_s": round(seconds, 2)}
    (JOB / "result.json").write_text(json.dumps(result, indent=2))
    progress("complete", steps, steps, **result)
    print("[lesson02] " + json.dumps(result))
