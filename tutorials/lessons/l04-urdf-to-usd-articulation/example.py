#!/usr/bin/env python3
"""Lesson 04 — convert the Q1 URDF with the 3.0 URDF importer and inspect the resulting Articulation.

    uv run [--extra isaacsim] python example.py --job-dir <dir> --params <dir>/params.json

Default physics=newton_mjwarp uses sim_utils.UrdfFileCfg (kitless; needs --extra importers only).
physics=isaacsim_physx uses the explicit UrdfConverter → UsdFileCfg path (needs full Isaac Sim / Kit).

Writes result.json with a per-joint table (type, limits, drive gains, in control_joints?) and a mass check.
"""
from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as ET
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
    (JOB / "progress.json").write_text(json.dumps({"phase": phase, "iteration": iteration, "max_iterations": total,
                                                    "elapsed_s": round(time.monotonic() - T0, 1), "eta_s": None,
                                                    "updated_at": time.time(), **extra}))


# URDF facts straight from the XML, independent of the importer, so we can cross-check the import.
urdf_root = ET.fromstring(Path(P["urdf"]).read_bytes())
urdf_joints = {}
for joint in urdf_root.findall("joint"):
    limit = joint.find("limit")
    mimic = joint.find("mimic")
    urdf_joints[joint.get("name")] = {
        "type": joint.get("type"), "lower": float(limit.get("lower")) if limit is not None and limit.get("lower") else None,
        "upper": float(limit.get("upper")) if limit is not None and limit.get("upper") else None,
        "effort": float(limit.get("effort")) if limit is not None and limit.get("effort") else None,
        "velocity": float(limit.get("velocity")) if limit is not None and limit.get("velocity") else None,
        "mimic_of": mimic.get("joint") if mimic is not None else None}
urdf_mass = sum(float(link.find("inertial/mass").get("value")) for link in urdf_root.findall("link") if link.find("inertial/mass") is not None)
control = list(P.get("control_joints", []))
progress("starting", 0, 3, message=f"URDF has {len(urdf_joints)} joints, {urdf_mass:.2f} kg")

from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

PHYSICS = P.get("physics", "newton_mjwarp")
sim_cfg = SimulationCfg(dt=1 / 200, device="cuda:0")
if PHYSICS == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg  # requires full Isaac Sim / Kit
    sim_cfg.physics = PhysxCfg()
elif PHYSICS == "newton_mjwarp":
    from isaaclab_newton.physics import NewtonCfg  # kitless; UrdfFileCfg + --extra importers
    sim_cfg.physics = NewtonCfg()
else:
    raise SystemExit(f"this lesson supports newton_mjwarp and isaacsim_physx; got {PHYSICS!r}")

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

    stiffness = float(P.get("stiffness", 80.0))
    damping = float(P.get("damping", 4.0))
    fix_base = bool(P.get("fix_base", False))
    merge_fixed = bool(P.get("merge_fixed_joints", True))
    link_density = float(P.get("link_density", 0.0))
    gains = sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(
        stiffness=stiffness if stiffness > 0 else None, damping=damping if damping > 0 else None)

    # ---- 1. spawn source: explicit converter (PhysX/Kit) or UrdfFileCfg (kitless) ------------
    usd_path = None
    if PHYSICS == "isaacsim_physx":
        from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg
        converter_cfg = UrdfConverterCfg(
            asset_path=P["urdf"], usd_dir=str(JOB / "usd"),
            fix_base=fix_base, merge_fixed_joints=merge_fixed, link_density=link_density,
            joint_drive=UrdfConverterCfg.JointDriveCfg(
                gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(
                    stiffness=stiffness if stiffness > 0 else None, damping=damping if damping > 0 else None)),
        )
        usd_path = UrdfConverter(converter_cfg).usd_path
        spawn = sim_utils.UsdFileCfg(usd_path=usd_path)
        spawn_mode = "UrdfConverter"
        progress("converting", 1, 3, message=f"usd: {usd_path}")
    else:
        # UrdfFileCfg inherits UrdfConverterCfg — Lab does the convert+cache when the Articulation spawns.
        spawn = sim_utils.UrdfFileCfg(
            asset_path=P["urdf"], fix_base=fix_base, merge_fixed_joints=merge_fixed, link_density=link_density,
            joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(gains=gains))
        spawn_mode = "UrdfFileCfg"
        progress("converting", 1, 3, message=f"spawn via {spawn_mode} (kitless)")

    # ---- 2. spawn as an Articulation and inspect --------------------------------------------
    sim = sim_utils.SimulationContext(sim_cfg)
    expr = [name for name in control] or [".*"]

    @configclass
    class InspectSceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot", spawn=spawn,
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, 1.0), rot=(0.0, 0.0, 0.0, 1.0), joint_pos=P.get("default_joint_positions", {})),
            # Only the 23 control joints become actuator channels; mimic followers stay passive.
            actuators={"control": ImplicitActuatorCfg(joint_names_expr=expr, stiffness=stiffness, damping=damping,
                                                      actuator_effort_limit=150.0, joint_effort_limit=150.0)})

    scene = InteractiveScene(InspectSceneCfg(num_envs=int(P.get("num_envs", 4)), env_spacing=2.0))
    sim.reset()
    robot: Articulation = scene["robot"]
    limits = robot.data.joint_pos_limits.torch[0]
    effort_limits = robot.data.joint_effort_limits.torch[0]
    vel_limits = robot.data.joint_vel_limits.torch[0]
    stiff = robot.data.joint_stiffness.torch[0]
    damp = robot.data.joint_damping.torch[0]
    masses = robot.root_view.get_masses() if hasattr(robot, "root_view") else None
    rows = []
    for index, name in enumerate(robot.joint_names):
        source = urdf_joints.get(name, {})
        rows.append({"joint": name, "index": index, "urdf_type": source.get("type"), "mimic_of": source.get("mimic_of"),
                     "lower": round(float(limits[index, 0]), 4), "upper": round(float(limits[index, 1]), 4),
                     "urdf_lower": source.get("lower"), "urdf_upper": source.get("upper"),
                     "effort_limit": round(float(effort_limits[index]), 2), "velocity_limit": round(float(vel_limits[index]), 2),
                     "stiffness": round(float(stiff[index]), 2), "damping": round(float(damp[index]), 2),
                     "in_control_joints": name in control})
    imported = set(robot.joint_names)
    result = {"robot": P["robot"], "physics": PHYSICS, "spawn_mode": spawn_mode, "usd_path": str(usd_path) if usd_path else None,
              "fix_base": fix_base, "merge_fixed_joints": merge_fixed,
              "num_joints": robot.num_joints, "num_bodies": robot.num_bodies,
              "control_joints_missing": [name for name in control if name not in imported],
              "imported_not_in_control": sorted(imported - set(control)),
              "mimic_followers": sorted(name for name, j in urdf_joints.items() if j["mimic_of"] and name in imported),
              "urdf_inertial_mass_kg": round(urdf_mass, 3),
              "articulation_mass_kg": round(float(masses[0].sum()), 3) if masses is not None else None,
              "joints": rows}
    (JOB / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    progress("complete", 3, 3, num_joints=robot.num_joints, missing=len(result["control_joints_missing"]))
    print(f"[lesson04] {robot.num_joints} joints via {spawn_mode}/{PHYSICS}; missing control joints: {result['control_joints_missing']}")
    print(f"[lesson04] URDF mass {urdf_mass:.2f} kg vs articulation {result['articulation_mass_kg']} kg")
