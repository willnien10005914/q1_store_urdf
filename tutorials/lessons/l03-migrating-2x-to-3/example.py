#!/usr/bin/env python3
"""Lesson 03 — heuristic Isaac Lab 2.x → 3.0 migration scanner (pure Python, no Isaac needed).

    python example.py --job-dir <dir> --params <dir>/params.json

params.json: scan_path, show_identity_only, max_findings. Writes result.json and progress.json.
The checks are string heuristics; the authoritative sources are runtime DeprecationWarnings and the
official migration guide (https://isaac-sim.github.io/IsaacLab/main/source/isaaclab/migrating.html).
"""
from __future__ import annotations

import argparse
import json
import re
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

# Each rule: id, regex, 3.0 replacement hint, migration-guide anchor.
RULES = [
    ("proxyarray_missing_torch",
     re.compile(r"\.data\.(root_pos_w|root_quat_w|root_lin_vel_w|root_ang_vel_w|root_lin_vel_b|root_ang_vel_b|projected_gravity_b|"
                r"joint_pos|joint_vel|joint_acc|body_pos_w|body_quat_w|net_forces_w|net_normal_forces_w|default_joint_pos)\b(?!\.torch|\.warp)"),
     "在屬性後加 .torch（或 .warp）：ProxyArray 不是 tensor", "ProxyArray Backend for Asset and Sensor Data"),
    ("quaternion_wxyz_identity",
     re.compile(r"\(\s*1(?:\.0*)?\s*,\s*0(?:\.0*)?\s*,\s*0(?:\.0*)?\s*,\s*0(?:\.0*)?\s*\)"),
     "單位四元數改為 (0.0, 0.0, 0.0, 1.0)（XYZW）", "Quaternion Format"),
    ("quaternion_rot_kw",
     re.compile(r"\brot\s*=\s*\(\s*1(?:\.0*)?\s*,"),
     "InitialStateCfg.rot 是 XYZW；w 放最後", "Quaternion Format"),
    ("write_to_sim_ambiguous",
     re.compile(r"\.write_(root_pose|root_velocity|root_state|joint_state|joint_position|joint_velocity|root_link_pose|root_com_pose)_to_sim\("),
     "改成 _index（部分資料＋env_ids）或 _mask（完整資料＋env_mask）", "Write methods distinguish indices from masks"),
    ("joint_target_on_articulation",
     re.compile(r"\.set_joint_(position|velocity|effort)_target(_index|_mask)?\("),
     "改用 robot.actuators.target_command.set_{position,velocity,effort}_index(value=..., joint_ids=...)", "Actuator API Moves to ActuatorCollection"),
    ("actuator_telemetry_on_data",
     re.compile(r"\.data\.(applied_torque|computed_torque|joint_pos_target|joint_vel_target|joint_effort_target)\b"),
     "改用 robot.actuators.applied_effort / computed_effort / target_command.position 等", "Actuator API Moves to ActuatorCollection"),
    ("actuator_limit_names",
     re.compile(r"\b(effort_limit_sim|velocity_limit_sim|effort_limit)\s*="),
     "改用 actuator_effort_limit / joint_effort_limit / joint_velocity_limit", "Actuator effort and joint-limit names"),
    ("headless_flag",
     re.compile(r"--headless\b|args\.headless|headless\s*=\s*True"),
     "3.0 移除 --headless；不選 --viz 即 headless，無顯示器錄影用 HEADLESS=1", "Visualizer CLI and Headless Behavior"),
    ("applauncher",
     re.compile(r"\bAppLauncher\b"),
     "改用 isaaclab.app.launch_simulation(sim_cfg) 作為啟動入口", "Multi-Backend Architecture and Presets"),
    ("record_video_wrapper",
     re.compile(r"gym\.wrappers\.RecordVideo|render_mode\s*=\s*['\"]rgb_array['\"]"),
     "改用 env_cfg.video_recorders = [VideoRecorderCfg(source='viz:kit', ...)]", "Video Recording"),
    ("viewer_cfg",
     re.compile(r"\.viewer\.(eye|lookat|origin_type|asset_name|resolution)\b"),
     "ViewerCfg 已 deprecated；改 sim.visualizer_cfgs = VisualizerCfg(eye=..., lookat=...)", "Viewport Camera Configuration"),
    ("imu_renamed",
     re.compile(r"\bImuCfg\b|\bImu\("),
     "舊全狀態 Imu 改名 Pva；新 Imu 只有角速度與線加速度", "IMU Sensor Renamed to PVA"),
    ("xformprimview",
     re.compile(r"\bXformPrimView\b|root_physx_view"),
     "XformPrimView → FrameView；root_physx_view → root_view", "Renaming of XformPrimView to FrameView"),
    ("isaaclab_sh",
     re.compile(r"isaaclab\.sh|isaaclab\.bat|--install\b|--conda\b"),
     "3.0 只支援 uv：uv sync --extra isaacsim / uv run isaaclab ...", "Installation"),
    ("rsl_rl_resume",
     re.compile(r"--load_run\b|--resume\b|use_pretrained_checkpoint"),
     "統一用 --checkpoint <path|latest|best|pretrained>", "Unified Checkpoint and Iteration Arguments"),
]

scan_path = Path(str(P.get("scan_path", "")).replace("{store_root}", str(P.get("store_root", "")))).expanduser()
identity_only = bool(P.get("show_identity_only", False))
limit = int(P.get("max_findings", 80))
files = [scan_path] if scan_path.is_file() else sorted(scan_path.rglob("*.py")) if scan_path.is_dir() else []
findings, counts = [], {}
for index, file in enumerate(files):
    try:
        lines = file.read_text(errors="replace").splitlines()
    except OSError:
        continue
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for rule_id, pattern, hint, anchor in RULES:
            if identity_only and rule_id != "quaternion_wxyz_identity":
                continue
            if pattern.search(line):
                counts[rule_id] = counts.get(rule_id, 0) + 1
                if len(findings) < limit:
                    findings.append({"file": str(file), "line": number, "rule": rule_id, "code": stripped[:160], "hint": hint, "guide_section": anchor})
    if index % 5 == 0 or index == len(files) - 1:
        (JOB / "progress.json").write_text(json.dumps({"phase": "running", "iteration": index + 1, "max_iterations": max(len(files), 1),
                                                        "elapsed_s": round(time.monotonic() - T0, 2), "eta_s": 0, "updated_at": time.time(),
                                                        "message": f"scanned {file.name}"}))

result = {"scan_path": str(scan_path), "files_scanned": len(files), "total_findings": sum(counts.values()),
          "counts_by_rule": dict(sorted(counts.items(), key=lambda kv: -kv[1])), "findings": findings,
          "note": "字串啟發式；以執行期 DeprecationWarning 與官方遷移指南為準。"}
if not files:
    result["error"] = f"找不到可掃描的 .py：{scan_path}"
(JOB / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
(JOB / "progress.json").write_text(json.dumps({"phase": "complete", "iteration": len(files), "max_iterations": max(len(files), 1),
                                                "elapsed_s": round(time.monotonic() - T0, 2), "eta_s": 0, "updated_at": time.time(),
                                                "total_findings": result["total_findings"]}))
print(f"[lesson03] scanned {len(files)} files, {result['total_findings']} findings")
for rule, count in result["counts_by_rule"].items():
    print(f"  {rule:32s} {count}")
for item in findings[:20]:
    print(f"  {item['file']}:{item['line']}: [{item['rule']}] {item['code']}")
