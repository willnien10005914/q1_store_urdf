#!/usr/bin/env python3
"""Lesson 05 — Q1 stance tracking with selectable actuator model, gains, limits and a yaw perturbation.

    uv run [--extra isaacsim] python example.py --job-dir <dir> --params <dir>/params.json

Writes result.json: tracking RMSE, peak applied effort, saturation fraction and recovery after the perturbation.
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
    (JOB / "progress.json").write_text(json.dumps({"phase": phase, "iteration": iteration, "max_iterations": total, "elapsed_s": round(elapsed, 1),
                                                    "eta_s": None if eta is None else round(eta, 1), "num_envs": P.get("num_envs"),
                                                    "updated_at": time.time(), **extra}))


from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

DT = 1 / 200
sim_cfg = SimulationCfg(dt=DT, device="cuda:0")
if P.get("physics", "newton_mjwarp") == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg
    sim_cfg.physics = PhysxCfg()
else:
    from isaaclab_newton.physics import NewtonCfg
    sim_cfg.physics = NewtonCfg()
progress("starting", 0, 1)

with launch_simulation(sim_cfg):
    try:
        import lab3_compat
        lab3_compat.patch_urdf_importer()
    except Exception as exc:
        print(f"[q1] lab3_compat patch skipped: {exc}")
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.actuators import DCMotorCfg, DelayedPDActuatorCfg, ImplicitActuatorCfg
    from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg
    from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
    from isaaclab.utils import configclass

    KP, KD = float(P.get("stiffness", 80.0)), float(P.get("damping", 4.0))
    A_LIM, J_LIM = float(P.get("actuator_effort_limit", 120.0)), float(P.get("joint_effort_limit", 120.0))
    ARM = float(P.get("armature", 0.01))
    control = list(P.get("control_joints", [])) or [".*"]
    # Wheels are velocity-driven (Kp = 0); every other control joint is position-driven.
    wheel_expr = [name for name in control if "wheel" in name]
    pd_expr = [name for name in control if "wheel" not in name] or [".*"]
    common = dict(stiffness=KP, damping=KD, actuator_effort_limit=A_LIM, joint_effort_limit=J_LIM, armature=ARM)
    kind = P.get("actuator_type", "implicit")
    if kind == "dc_motor":
        pd_cfg = DCMotorCfg(joint_names_expr=pd_expr, saturation_effort=A_LIM * 1.3, velocity_limit=30.0, **common)
    elif kind == "delayed_pd":
        pd_cfg = DelayedPDActuatorCfg(joint_names_expr=pd_expr, min_delay=2, max_delay=4, **common)
    else:
        pd_cfg = ImplicitActuatorCfg(joint_names_expr=pd_expr, **common)
    actuators = {"pd": pd_cfg}
    if wheel_expr:
        actuators["wheels"] = ImplicitActuatorCfg(joint_names_expr=wheel_expr, stiffness=0.0, damping=2.0,
                                                  actuator_effort_limit=30.0, joint_effort_limit=30.0)

    @configclass
    class StanceSceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                                        joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                            gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=KP, damping=KD))),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, float(P.get("spawn_height", 0.95))), rot=(0.0, 0.0, 0.0, 1.0),
                                                       joint_pos=P.get("default_joint_positions", {})),
            actuators=actuators)

    sim = sim_utils.SimulationContext(sim_cfg)
    scene = InteractiveScene(StanceSceneCfg(num_envs=int(P.get("num_envs", 32)), env_spacing=2.0))
    sim.reset()
    robot: Articulation = scene["robot"]
    pd_ids = robot.actuators["pd"].joint_indices
    target = robot.data.default_joint_pos.torch[:, pd_ids].clone()
    steps = int(float(P.get("sim_seconds", 4.0)) / DT)
    perturb_step, perturb_end = int(2.0 / DT), int(2.1 / DT)
    yaw_torque = torch.zeros(scene.num_envs, 1, 3, device=sim.device)
    yaw_torque[..., 2] = float(P.get("perturb_nm", 15.0))
    zero = torch.zeros_like(yaw_torque)
    sq_err, peak, saturated, samples = 0.0, 0.0, 0, 0
    heights_after = []
    for step in range(steps):
        robot.actuators.target_command.set_position_index(value=target, joint_ids=pd_ids)
        if wheel_expr:
            robot.actuators.target_command.set_velocity_index(value=torch.zeros(scene.num_envs, len(robot.actuators["wheels"].joint_indices), device=sim.device),
                                                              joint_ids=robot.actuators["wheels"].joint_indices)
        # 3.0 wrench composer: permanent wrench is composed, not overwritten; clear it after the pulse.
        if perturb_step <= step < perturb_end:
            robot.set_external_force_and_torque(forces=zero, torques=yaw_torque, body_ids=[0])
        elif step == perturb_end:
            robot.set_external_force_and_torque(forces=zero, torques=zero, body_ids=[0])
        scene.write_data_to_sim()
        sim.step()
        scene.update(DT)
        q = robot.data.joint_pos.torch[:, pd_ids]
        tau = robot.actuators.applied_effort.torch[:, pd_ids]
        sq_err += ((q - target) ** 2).mean().item()
        samples += 1
        peak = max(peak, tau.abs().max().item())
        saturated += (tau.abs() >= A_LIM * 0.98).float().mean().item()
        if step > perturb_end:
            heights_after.append(robot.data.root_pos_w.torch[:, 2])
        if step % 40 == 0:
            progress("running", step, steps, message=f"rmse={ (sq_err / samples) ** .5:.4f} rad, peak={peak:.1f} N·m")
    recovered = heights_after[min(len(heights_after) - 1, int(1.0 / DT))] > 0.8 if heights_after else torch.zeros(0)
    result = {"actuator_type": kind, "stiffness": KP, "damping": KD, "actuator_effort_limit": A_LIM, "joint_effort_limit": J_LIM, "armature": ARM,
              "num_envs": scene.num_envs, "steps": steps, "tracking_rmse_rad": round((sq_err / max(samples, 1)) ** 0.5, 5),
              "peak_effort_nm": round(peak, 2), "saturation_fraction": round(saturated / max(samples, 1), 4),
              "recovered_fraction": round(recovered.float().mean().item(), 3) if len(recovered) else None,
              "note": "joint_effort_limit < actuator_effort_limit 時 solver 會再次裁切（3.0 行為）。"}
    (JOB / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    progress("complete", steps, steps, **{k: v for k, v in result.items() if k != "note"})
    print("[lesson05] " + json.dumps(result, ensure_ascii=False))
