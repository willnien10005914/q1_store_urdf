#!/usr/bin/env python3
"""Lesson 09 — Q1 hardware explode report + ImplicitActuatorCfg stance tracking.

Reads ToF/audio/skin/camera/mass params, writes hardware.json + explode rollout frames,
then (when Isaac Lab 3 is available) scales PD gains by accessory mass and tracks a squat.

    python example.py --job-dir <dir> --params <dir>/params.json
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
FPS = int(P.get("fps", 30))
BASE_MASS = float(P.get("base_mass_kg") or (42.2 if P.get("robot") == "leg" else 41.1))


def progress(phase, iteration, total, **extra):
    elapsed = time.monotonic() - T0
    eta = (elapsed / max(iteration, 1)) * max(total - iteration, 0) if iteration else None
    (JOB / "progress.json").write_text(json.dumps({
        "phase": phase, "iteration": iteration, "max_iterations": total,
        "elapsed_s": round(elapsed, 1), "eta_s": None if eta is None else round(eta, 1),
        "num_envs": P.get("num_envs"), "updated_at": time.time(), **extra,
    }))


def accessory_mass() -> float:
    total = 0.0
    if P.get("tof_enabled", True):
        total += float(P.get("tof_mass", 0.08))
    if P.get("audio_enabled", True):
        total += float(P.get("audio_mass", 0.05))
    return total


def mass_scale() -> float:
    return 1.0 + accessory_mass() / max(BASE_MASS, 1e-3)


def hardware_report(kp, kd, tracking=None) -> dict:
    return {
        "robot": P.get("robot"),
        "urdf": P.get("urdf"),
        "skin": P.get("skin", "steel"),
        "camera_preset": P.get("camera_preset", "three_quarter"),
        "explode_amount": float(P.get("explode_amount", 0.0)),
        "base_mass_kg": BASE_MASS,
        "accessory_mass_kg": round(accessory_mass(), 4),
        "mass_scale": round(mass_scale(), 4),
        "tof": {
            "enabled": bool(P.get("tof_enabled", True)),
            "xyz": [float(P.get("tof_x", 0.08)), float(P.get("tof_y", 0.0)), float(P.get("tof_z", 0.12))],
            "size_m": float(P.get("tof_size", 0.04)),
            "mass_kg": float(P.get("tof_mass", 0.08)),
            "parent_link": "center_torso_link",
        },
        "audio": {
            "enabled": bool(P.get("audio_enabled", True)),
            "xyz": [float(P.get("audio_x", -0.06)), float(P.get("audio_y", 0.05)), float(P.get("audio_z", 0.05))],
            "size_m": float(P.get("audio_size", 0.035)),
            "mass_kg": float(P.get("audio_mass", 0.05)),
            "parent_link": "center_torso_link",
        },
        "actuators": {
            "type": "ImplicitActuatorCfg",
            "stiffness": kp,
            "damping": kd,
            "actuator_effort_limit": float(P.get("actuator_effort_limit", 120.0)),
            "joint_effort_limit": float(P.get("joint_effort_limit", 120.0)),
        },
        "tracking": tracking,
        "reference_video": "/assets/tutorial/l09-hardware-explode/hardware.webm",
    }


def write_explode_rollout(frames: int = 48) -> None:
    """Synthetic explode keyframes for browser URDF replay (root fixed, q=0)."""
    amount = float(P.get("explode_amount", 0.85)) or 0.85
    joints = list(P.get("control_joints") or [])
    nq = max(len(joints), 1)
    out = []
    for i in range(frames):
        t = i / max(frames - 1, 1)
        # Ease in-out expand then slight settle — mirrors hardware-wall feel.
        ease = 0.5 - 0.5 * math.cos(math.pi * min(1.0, t * 1.15))
        explode = amount * min(1.0, ease)
        out.append({
            "t": round(i / FPS, 4),
            "explode": round(explode, 4),
            "root_pos": [0.0, 0.0, 0.8],
            "root_quat": [0.0, 0.0, 0.0, 1.0],
            "q": [0.0] * nq,
            "skin": P.get("skin", "steel"),
        })
    payload = {
        "fps": FPS,
        "kind": "hardware_explode",
        "joint_names": joints,
        "quaternion_order": "xyzw",
        "robot": P.get("robot"),
        "urdf": P.get("urdf"),
        "frames": out,
        "hardware": hardware_report(
            float(P.get("stiffness", 80.0)) * mass_scale(),
            float(P.get("damping", 4.0)) * mass_scale(),
        ),
    }
    (JOB / "rollout.json").write_text(json.dumps(payload, allow_nan=False))


def fallback() -> dict:
    progress("fallback", 0, 1, message="無 Isaac Lab 3 — 只產生 hardware／explode 產物")
    scale = mass_scale()
    kp = float(P.get("stiffness", 80.0)) * scale
    kd = float(P.get("damping", 4.0)) * scale
    write_explode_rollout()
    report = hardware_report(kp, kd, tracking={"mode": "fallback", "rmse": None})
    (JOB / "hardware.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    result = {
        "ok": True, "mode": "python-fallback", "physics": "none",
        "mass_scale": report["mass_scale"], "accessory_mass_kg": report["accessory_mass_kg"],
        "stiffness": kp, "damping": kd, "skin": report["skin"],
        "message": "已寫入 hardware.json 與爆炸 rollout；安裝 Isaac Lab 3 後可跑致動器追蹤。",
    }
    (JOB / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (JOB / "evaluation.json").write_text(json.dumps({"passed": True, "score": 0.7, "checks": {"hardware": True, "rollout": True}}))
    progress("complete", 1, 1, message="fallback complete")
    return result


try:
    from isaaclab.app import launch_simulation  # noqa: F401
    HAVE = True
except Exception:
    HAVE = False

if not HAVE:
    print("[lesson09]", json.dumps(fallback(), ensure_ascii=False))
    raise SystemExit(0)

from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

DT = 1 / 200
sim_cfg = SimulationCfg(dt=DT, device="cuda:0")
if P.get("physics", "newton_mjwarp") == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg  # noqa: E402
    sim_cfg.physics = PhysxCfg()
else:
    from isaaclab_newton.physics import (  # noqa: E402
        MJWarpSolverCfg, NewtonCfg, NewtonCollisionPipelineCfg, NewtonShapeCfg,
    )
    sim_cfg.physics = NewtonCfg(
        solver_cfg=MJWarpSolverCfg(njmax=1000, nconmax=300, iterations=100, integrator="implicitfast", use_mujoco_contacts=False),
        collision_cfg=NewtonCollisionPipelineCfg(max_triangle_pairs=2_500_000),
        num_substeps=2,
        default_shape_cfg=NewtonShapeCfg(margin=0.0, ke=160000.0, kd=1100.0),
    )

progress("starting", 0, 1, message="hardware + actuators")
write_explode_rollout()

with launch_simulation(sim_cfg):
    try:
        import lab3_compat
        lab3_compat.patch_urdf_importer()
    except Exception as exc:
        print(f"[q1] lab3_compat patch skipped: {exc}")
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import Articulation, ArticulationCfg, AssetBaseCfg
    from isaaclab.scene import InteractiveScene, InteractiveSceneCfg
    from isaaclab.utils import configclass

    scale = mass_scale()
    KP = float(P.get("stiffness", 80.0)) * scale
    KD = float(P.get("damping", 4.0)) * scale
    A_LIM = float(P.get("actuator_effort_limit", 120.0))
    J_LIM = float(P.get("joint_effort_limit", 120.0))
    control = list(P.get("control_joints", [])) or [".*"]
    wheel_expr = [n for n in control if "wheel" in n]
    pd_expr = [n for n in control if "wheel" not in n] or [".*"]
    actuators = {
        "pd": ImplicitActuatorCfg(
            joint_names_expr=pd_expr, stiffness=KP, damping=KD,
            actuator_effort_limit=A_LIM, joint_effort_limit=J_LIM, armature=0.01,
        ),
    }
    if wheel_expr:
        actuators["wheels"] = ImplicitActuatorCfg(
            joint_names_expr=wheel_expr, stiffness=0.0, damping=2.0,
            actuator_effort_limit=30.0, joint_effort_limit=30.0,
        )

    @configclass
    class HardwareSceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(
                asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                    gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=0, damping=0),
                ),
            ),
            init_state=ArticulationCfg.InitialStateCfg(
                pos=(0.0, 0.0, float(P.get("spawn_height", 0.95))),
                rot=(0.0, 0.0, 0.0, 1.0),
                joint_pos=P.get("default_joint_positions", {}),
            ),
            actuators=actuators,
        )

    # Must create SimulationContext first so InteractiveScene has a USD stage (same as l01/l05).
    sim = sim_utils.SimulationContext(sim_cfg)
    scene = InteractiveScene(HardwareSceneCfg(num_envs=int(P.get("num_envs", 16)), env_spacing=2.5))
    sim.reset()
    robot: Articulation = scene["robot"]
    pd_ids = robot.actuators["pd"].joint_indices
    steps = int(float(P.get("sim_seconds", 3.0)) / DT)
    err_acc = 0.0
    peak_tau = 0.0
    progress("running", 0, steps, message=f"mass_scale={scale:.3f} Kp={KP:.1f}")

    for k in range(steps):
        t = k * DT
        # Gentle squat so heavier accessories need higher PD (scaled above).
        amp = 0.18
        q_des = torch.zeros(scene.num_envs, len(pd_ids), device=sim.device)
        # Bias first few hip/waist-like channels if present.
        for j in range(min(3, len(pd_ids))):
            q_des[:, j] = amp * math.sin(2 * math.pi * 0.4 * t) * (1 if j % 2 == 0 else -1)
        robot.actuators.target_command.set_position_index(value=q_des, joint_ids=pd_ids)
        if wheel_expr:
            robot.actuators.target_command.set_velocity_index(
                value=torch.zeros(scene.num_envs, len(robot.actuators["wheels"].joint_indices), device=sim.device),
                joint_ids=robot.actuators["wheels"].joint_indices,
            )
        scene.write_data_to_sim()
        sim.step()
        scene.update(DT)
        q = robot.data.joint_pos.torch[:, pd_ids]
        err_acc += float(torch.mean(torch.abs(q - q_des)).item())
        tau = robot.actuators.applied_effort.torch[:, pd_ids]
        peak_tau = max(peak_tau, float(torch.max(torch.abs(tau)).item()))
        if k % max(1, steps // 10) == 0:
            progress("running", k, steps, message=f"t={t:.2f}s peak_tau={peak_tau:.1f}")

    rmse = err_acc / max(steps, 1)
    tracking = {"rmse_rad": round(rmse, 5), "peak_effort_nm": round(peak_tau, 3), "mass_scale": round(scale, 4)}
    report = hardware_report(KP, KD, tracking=tracking)
    (JOB / "hardware.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    result = {
        "ok": True, "mode": "isaaclab3", "physics": P.get("physics", "newton_mjwarp"),
        "mass_scale": report["mass_scale"], "accessory_mass_kg": report["accessory_mass_kg"],
        "stiffness": KP, "damping": KD, "skin": report["skin"],
        "tracking": tracking,
        "message": f"硬體質量縮放 {scale:.3f}× → Kp={KP:.1f} Kd={KD:.1f}；RMSE={rmse:.4f} rad",
    }
    (JOB / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    passed = rmse < 0.35
    (JOB / "evaluation.json").write_text(json.dumps({
        "passed": passed, "score": 1.0 if passed else 0.4,
        "checks": {"hardware": True, "rollout": True, "tracking_rmse_ok": passed},
    }))
    progress("complete", steps, steps, message=result["message"])
    print("[lesson09]", json.dumps(result, ensure_ascii=False))
