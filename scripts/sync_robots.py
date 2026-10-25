#!/usr/bin/env python3
"""Snapshot Q1 native URDFs and their referenced meshes from the user's checkouts.

The default operation only reads the sources. --pull is an explicit opt-in to
fast-forward clean source checkouts before making the snapshot.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "leg": ("q1_leg_urdf", "quanta_robot_legged.urdf", "Q1 Leg", "雙足 · BIPED"),
    "wheel": ("q1_wheel_urdf", "quanta_robot_wheeled.urdf", "Q1 Wheel", "雙輪 · WHEELED"),
}


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def arithmetic(node: ast.AST):
    """Read literal configuration without importing simulator code or executing it."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (float, int, str)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -arithmetic(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return arithmetic(node.left) * arithmetic(node.right)
    if isinstance(node, ast.Dict):
        return {arithmetic(k): arithmetic(v) for k, v in zip(node.keys, node.values)}
    raise ValueError("Unexpected configuration expression")


def control_contract(source: Path, kind: str) -> dict:
    if kind == "leg":
        contract = json.loads((source / "config/robot_contract.json").read_text())
        return {
            "control_joints": contract["joint_names"],
            "root_height": contract["root_height"],
            "default_joint_positions": dict(zip(contract["joint_names"], contract["default"])),
        }
    path = source / "source/wheel_humanoid_lab/wheel_humanoid_lab/assets/q1_inertia.py"
    tree = ast.parse(path.read_text())
    values = {}
    defaults = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "Q1_INERTIA_POSITION_JOINTS", "Q1_INERTIA_WHEEL_JOINTS", "Q1_INERTIA_ROOT_Z"
                }:
                    values[target.id] = ast.literal_eval(node.value)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "update"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "Q1_INERTIA_DEFAULT_POS"):
                defaults = arithmetic(call.args[0])
    joints = values["Q1_INERTIA_POSITION_JOINTS"] + values["Q1_INERTIA_WHEEL_JOINTS"]
    return {"control_joints": joints, "root_height": values["Q1_INERTIA_ROOT_Z"],
            "default_joint_positions": {name: defaults.get(name, 0.0) for name in joints}}


def sync_robot(source: Path, kind: str) -> dict:
    repository_name, urdf_name, name, label = SOURCES[kind]
    urdf = source / "urdf/q1_inertia" / urdf_name
    model = ET.parse(urdf).getroot()
    mesh_paths = set()
    for mesh in model.findall(".//mesh"):
        mesh_path = (urdf.parent / mesh.get("filename", "")).resolve()
        if not mesh_path.is_relative_to(source.resolve() / "meshes"):
            raise ValueError(f"Mesh is outside source mesh directory: {mesh_path}")
        if not mesh_path.is_file():
            raise FileNotFoundError(mesh_path)
        mesh_paths.add(mesh_path)
    destination = ROOT / "assets/robots" / kind
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"q1-{kind}-", dir=destination.parent) as temporary:
        stage = Path(temporary) / kind
        copy(urdf, stage / urdf.relative_to(source))
        for mesh in sorted(mesh_paths):
            copy(mesh, stage / mesh.relative_to(source))
        # Only this generated robot snapshot is replaced; user sources are read-only.
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(stage), str(destination))
    joints = []
    for joint in model.findall("joint"):
        limit = joint.find("limit")
        axis = joint.find("axis")
        item = {"name": joint.get("name"), "type": joint.get("type"),
                "lower": float(limit.get("lower")) if limit is not None and limit.get("lower") else None,
                "upper": float(limit.get("upper")) if limit is not None and limit.get("upper") else None,
                "axis": [float(v) for v in axis.get("xyz").split()] if axis is not None else [1, 0, 0]}
        mimic = joint.find("mimic")
        if mimic is not None:
            item["mimic"] = {"joint": mimic.get("joint"), "multiplier": float(mimic.get("multiplier", "1")),
                             "offset": float(mimic.get("offset", "0"))}
        joints.append(item)
    contract = control_contract(source, kind)
    if not set(contract["control_joints"]) <= {j["name"] for j in joints}:
        raise ValueError(f"{kind} control contract does not match its URDF")
    return {
        "id": kind, "name": name, "label": label,
        "urdf_url": f"/assets/robots/{kind}/urdf/q1_inertia/{urdf_name}",
        "repository": f"https://github.com/willnien10005914/{repository_name}",
        "commit": git(source, "rev-parse", "HEAD"),
        "source_dirty": bool(git(source, "status", "--porcelain")),
        "urdf_sha256": digest(urdf), "joints": joints,
        "movable_joints": sum(j["type"] not in ("fixed", "floating") for j in joints),
        "mass_kg": sum(float(mass.get("value")) for mass in model.findall("link/inertial/mass")),
        "mesh_count": len(mesh_paths),
        "meshes": [{"path": path.relative_to(source).as_posix(), "sha256": digest(path)}
                   for path in sorted(mesh_paths)],
        **contract,
    }


def sync_examples(source_root: Path, robots: list[dict]) -> None:
    leg, wheel = source_root / SOURCES["leg"][0], source_root / SOURCES["wheel"][0]
    output = ROOT / "assets/examples"
    leg_video = leg / "docs/results/q1_leg_sidestep_6144_8000.mp4"
    wheel_video = wheel / "docs/results/q1_skate_cubemars_iter3800.mp4"
    copy(leg_video, output / "leg/sidestep.mp4")
    copy(leg / "web/store/assets/sidestep.jpg", output / "leg/sidestep.jpg")
    copy(leg / "docs/reference/sidestep_motion.json", output / "leg/sidestep_motion.json")
    copy(leg / "docs/reference/sidestep_landmarks.json", output / "leg/sidestep_landmarks.json")
    leg_evidence = json.loads((leg / "docs/results/q1_leg_sidestep_6144_8000.json").read_text())
    # Replace the original author's absolute machine path with its repository path.
    leg_evidence["checkpoint"] = "checkpoints/isaac/sidestep/latest.pt"
    leg_evidence["video_sha256"] = digest(leg_video)
    write_json(output / "leg/evidence.json", leg_evidence)
    copy(wheel_video, output / "wheel/skate.mp4")
    copy(wheel / "docs/results/q1_skate_cubemars_iter3800_frames.png", output / "wheel/skate.png")
    wheel_checkpoint = wheel / "checkpoints/q1_skate_ppo.pt"
    wheel_evidence = {
        "source": "Historical Isaac Sim PPO recording documented in upstream README.en.md",
        "source_url": "https://github.com/willnien10005914/q1_wheel_urdf/blob/"
                      + robots[1]["commit"] + "/README.en.md#results-run-skate_cubemars_4096_v2-4096-envs-stage-5-reached-at-iter-3400",
        "checkpoint": "checkpoints/q1_skate_ppo.pt", "iterations": 3800,
        "video_sha256": digest(wheel_video),
        "checkpoint_sha256": digest(wheel_checkpoint) if wheel_checkpoint.exists() else None,
        "contract": "legacy 24 actions / 92 actor observations",
        "compatible_with_current_urdf": False,
        "scope": "Historical recording uses the legacy wheel robot. It is not evidence of current native 23-action URDF compatibility.",
    }
    write_json(output / "wheel/evidence.json", wheel_evidence)
    write_json(output / "catalog.json", {"examples": [
        {"id": "leg-sidestep-8000", "robot_type": "leg", "title": "Q1 Sidestep · 閃身步",
         "description": "原生 PhysX PPO 閃身實錄；獨立模擬評估 31/32 回合存活，尚未實機驗證。",
         "video_url": "/assets/examples/leg/sidestep.mp4", "poster_url": "/assets/examples/leg/sidestep.jpg",
         "motion_url": "/assets/examples/leg/sidestep_motion.json",
         "landmarks_url": "/assets/examples/leg/sidestep_landmarks.json",
         "evidence_url": "/assets/examples/leg/evidence.json",
         "source_url": robots[0]["repository"] + "/blob/" + robots[0]["commit"]
                       + "/docs/results/q1_leg_sidestep_6144_8000.mp4",
         "provenance": "歷史原生 Isaac Sim / PhysX PPO · 6,144 envs × 8,000 updates · selected model_7800",
         "status": "simulation_evaluated", "checkpoint": "checkpoints/isaac/sidestep/latest.pt",
         "compatible_with_current_urdf": leg_evidence.get("evaluation", {}).get("urdf_sha256") == robots[0]["urdf_sha256"]},
        {"id": "wheel-skate-legacy", "robot_type": "wheel", "title": "Q1 Skate · 歷史滑行 PPO",
         "description": "雙輪滑行的真實歷史訓練影片。舊版 24 軸模型，與目前原生 23 軸 URDF 不相容。",
         "video_url": "/assets/examples/wheel/skate.mp4", "poster_url": "/assets/examples/wheel/skate.png",
         "evidence_url": "/assets/examples/wheel/evidence.json", "source_url": wheel_evidence["source_url"],
         "provenance": "歷史 Isaac Sim / RSL-RL PPO · 3,800 updates · legacy 24-action contract",
         "status": "legacy_recording", "checkpoint": "checkpoints/q1_skate_ppo.pt",
         "compatible_with_current_urdf": False},
    ]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.home() / "will")
    parser.add_argument("--pull", action="store_true", help="Fast-forward clean source checkouts from their configured upstreams")
    args = parser.parse_args()
    source_root = args.source_root.expanduser().resolve()
    sources = [source_root / spec[0] for spec in SOURCES.values()]
    # Check every source before changing either source checkout.
    for source in sources:
        git(source, "rev-parse", "--show-toplevel")
        if args.pull and git(source, "status", "--porcelain"):
            raise SystemExit(f"Refusing --pull: source checkout is dirty: {source}")
        if args.pull:
            git(source, "rev-parse", "--abbrev-ref", "@{upstream}")
    if args.pull:
        for source in sources:
            subprocess.run(["git", "-C", str(source), "fetch", "origin"], check=True)
            subprocess.run(["git", "-C", str(source), "merge", "--ff-only", "@{upstream}"], check=True)
    robots = [sync_robot(source_root / spec[0], kind) for kind, spec in SOURCES.items()]
    write_json(ROOT / "assets/robots/manifest.json", {
        "schema": 1, "synced_at": datetime.now(timezone.utc).isoformat(), "robots": robots,
    })
    sync_examples(source_root, robots)
    vendor = ROOT / "public/vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sources[0] / "web/vendor", vendor, dirs_exist_ok=True)
    for robot in robots:
        print(f"{robot['id']}: {robot['commit'][:12]}, {robot['mesh_count']} meshes, "
              f"{robot['movable_joints']} movable joints, {len(robot['control_joints'])} controls")
    print("Updated assets/robots/manifest.json, assets/examples/catalog.json, public/vendor/")


if __name__ == "__main__":
    main()
