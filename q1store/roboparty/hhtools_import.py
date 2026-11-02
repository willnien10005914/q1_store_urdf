"""Parse HHTools robot CSV exports into Q1 Store motion payloads.

Matches the CSV schema documented by RoboParty ``convert_motion.py``:

* comment metadata lines ``# key: value`` (expects ``sample_rate``)
* header with ``time``, ``root_x/y/z``, ``root_qx/qy/qz/qw``, ``dof_<joint>``

Joint names are remapped from common RP1 / Unitree-style aliases onto Q1 URDF
names when possible; unknown DOFs are dropped (train-time fills defaults).
"""
from __future__ import annotations

import csv
import math
import re
from typing import Any


class HHToolsImportError(ValueError):
    pass


ROOT_COLUMNS = ("root_x", "root_y", "root_z", "root_qx", "root_qy", "root_qz", "root_qw")

# RP1 / lowercase BeyondMimic aliases → Q1 native URDF joint names.
_JOINT_ALIASES = {
    "left_hip_pitch_joint": "left_hip_Pitch_joint",
    "left_hip_roll_joint": "left_hip_Roll_joint",
    "left_hip_yaw_joint": "left_hip_Yaw_joint",
    "left_knee_joint": "left_knee_Pitch_joint",
    "left_knee_pitch_joint": "left_knee_Pitch_joint",
    "left_ankle_pitch_joint": "left_ankle_Pitch_joint",
    "left_ankle_roll_joint": "left_ankle_Roll_joint",
    "right_hip_pitch_joint": "right_hip_Pitch_joint",
    "right_hip_roll_joint": "right_hip_Roll_joint",
    "right_hip_yaw_joint": "right_hip_Yaw_joint",
    "right_knee_joint": "right_knee_Pitch_joint",
    "right_knee_pitch_joint": "right_knee_Pitch_joint",
    "right_ankle_pitch_joint": "right_ankle_Pitch_joint",
    "right_ankle_roll_joint": "right_ankle_Roll_joint",
    "waist_yaw_joint": "center_waist_Yaw_joint",
    "waist_roll_joint": "center_waist_Roll_joint",
    "waist_pitch_joint": "center_waist_Pitch_joint",
    "left_shoulder_pitch_joint": "left_shoulder_Pitch_joint",
    "left_shoulder_roll_joint": "left_shoulder_Roll_joint",
    "left_shoulder_yaw_joint": "left_shoulder_Yaw_joint",
    "left_elbow_joint": "left_elbow_Pitch_joint",
    "left_elbow_pitch_joint": "left_elbow_Pitch_joint",
    "left_wrist_joint": "left_wrist_Yaw_joint",
    "left_wrist_yaw_joint": "left_wrist_Yaw_joint",
    "right_shoulder_pitch_joint": "right_shoulder_Pitch_joint",
    "right_shoulder_roll_joint": "right_shoulder_Roll_joint",
    "right_shoulder_yaw_joint": "right_shoulder_Yaw_joint",
    "right_elbow_joint": "right_elbow_Pitch_joint",
    "right_elbow_pitch_joint": "right_elbow_Pitch_joint",
    "right_wrist_joint": "right_wrist_Yaw_joint",
    "right_wrist_yaw_joint": "right_wrist_Yaw_joint",
}


def remap_joint_name(name: str) -> str:
    """Map an HHTools / RP1 DOF name onto the Q1 URDF name when known."""
    if name in _JOINT_ALIASES:
        return _JOINT_ALIASES[name]
    key = name.lower()
    if key in _JOINT_ALIASES:
        return _JOINT_ALIASES[key]
    # Preserve Q1-style CamelCase Pitch/Roll/Yaw already matching the URDF.
    if re.search(r"_(Pitch|Roll|Yaw)_joint$", name) or name.startswith("center_waist_"):
        return name
    return name


def remap_joint_names(names: list[str]) -> tuple[list[str], dict[str, str], list[str]]:
    """Return (unique Q1 names, source→q1 map, dropped duplicates/unmapped leftovers)."""
    mapping: dict[str, str] = {}
    ordered: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for name in names:
        target = remap_joint_name(name)
        mapping[name] = target
        if target in seen:
            dropped.append(name)
            continue
        seen.add(target)
        ordered.append(target)
    return ordered, mapping, dropped


def parse_hhtools_csv(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise HHToolsImportError("HHTools CSV 內容是空的。")
    metadata: dict[str, str] = {}
    rows: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            key, separator, value = line[1:].partition(":")
            if separator:
                metadata[key.strip()] = value.strip()
        elif line.strip():
            rows.append(line)
    if not rows:
        raise HHToolsImportError("CSV 沒有資料列；請從 HHTools 匯出含表頭的機器人 CSV。")
    reader = csv.reader(rows)
    header = next(reader, [])
    required = {"time", *ROOT_COLUMNS}
    if len(header) != len(set(header)) or not required.issubset(header):
        raise HHToolsImportError(
            "HHTools CSV 需要 time、root_x/y/z、root_qx/qy/qz/qw 與 dof_* 欄位（含註解 metadata）。"
        )
    raw_dof_names = [column.removeprefix("dof_") for column in header if column.startswith("dof_")]
    if not raw_dof_names:
        raise HHToolsImportError("CSV 沒有 dof_* 關節欄位。")
    if len(raw_dof_names) != len(set(raw_dof_names)):
        raise HHToolsImportError("CSV 含重複關節名稱。")
    dof_names, name_map, dropped = remap_joint_names(raw_dof_names)
    # Prefer first occurrence of each remapped name when aliases collide.
    source_for_target = {}
    for source_name in raw_dof_names:
        target = name_map[source_name]
        source_for_target.setdefault(target, source_name)
    index = {name: i for i, name in enumerate(header)}
    frames = []
    previous = -1.0
    for row in reader:
        if len(row) != len(header):
            raise HHToolsImportError("CSV 列長度與表頭不一致。")
        try:
            values = [float(cell) for cell in row]
        except ValueError as error:
            raise HHToolsImportError("CSV 含有非數值欄位。") from error
        if any(not math.isfinite(v) for v in values):
            raise HHToolsImportError("CSV 含有 NaN / Inf。")
        t = values[index["time"]]
        if t <= previous:
            raise HHToolsImportError("time 必須嚴格遞增。")
        previous = t
        root = [values[index[c]] for c in ("root_x", "root_y", "root_z")]
        root_quat = [values[index[c]] for c in ("root_qx", "root_qy", "root_qz", "root_qw")]
        if sum(q * q for q in root_quat) < 1e-16:
            raise HHToolsImportError("root quaternion 不可為零。")
        q = [values[index[f"dof_{source_for_target[name]}"]] for name in dof_names]
        frames.append({
            "time": t,
            "q": q,
            "joints": {name: q[i] for i, name in enumerate(dof_names)},
            "root": root,
            "root_quat": root_quat,
        })
    if len(frames) < 2:
        raise HHToolsImportError("至少需要 2 個 frame。")
    fps = None
    if "sample_rate" in metadata:
        try:
            fps = float(metadata["sample_rate"])
        except ValueError as error:
            raise HHToolsImportError("sample_rate 必須是數字。") from error
        if not math.isfinite(fps) or fps <= 0:
            raise HHToolsImportError("sample_rate 必須為正。")
    duration = frames[-1]["time"] - frames[0]["time"]
    return {
        "metadata": metadata,
        "fps": fps,
        "joint_names": dof_names,
        "raw_joint_names": raw_dof_names,
        "joint_name_map": name_map,
        "dropped_joint_aliases": dropped,
        "frames": frames,
        "duration_s": duration,
    }


def motion_payload_from_hhtools(
    parsed: dict[str, Any],
    *,
    robot_type: str = "leg",
    urdf_sha256: str | None = None,
    action_name: str | None = None,
    source_title: str | None = None,
) -> dict[str, Any]:
    """Build a `/api/motions` body from :func:`parse_hhtools_csv` output."""
    frames = parsed["frames"]
    # Re-base times to start at 0 for the store contract.
    t0 = frames[0]["time"]
    normalized = []
    for frame in frames:
        item = dict(frame)
        item["time"] = round(frame["time"] - t0, 6)
        normalized.append(item)
    title = action_name or source_title or parsed.get("metadata", {}).get("name") or "HHTools motion"
    mapping_extra = {}
    if parsed.get("joint_name_map"):
        mapping_extra["joint_name_map"] = parsed["joint_name_map"]
    if parsed.get("dropped_joint_aliases"):
        mapping_extra["dropped_joint_aliases"] = parsed["dropped_joint_aliases"]
    if parsed.get("raw_joint_names"):
        mapping_extra["raw_joint_names"] = parsed["raw_joint_names"]
    return {
        "robot_type": robot_type,
        "joint_names": list(parsed["joint_names"]),
        "frames": normalized,
        "urdf_sha256": urdf_sha256,
        "source": "hhtools",
        "pipeline": "beyondmimic",
        "demo": False,
        "action_name": title,
        "mapping": {
            "method": "hhtools_retarget_v1",
            "pipeline": "beyondmimic",
            "root_motion": True,
            "physical_validation": False,
            "hhtools_fps": parsed.get("fps"),
            "hhtools_metadata": parsed.get("metadata") or {},
            "reference": "RoboParty HHTools → BeyondMimic-style root+joint reference",
            **mapping_extra,
        },
        "segment": {
            "start_s": 0.0,
            "end_s": round(normalized[-1]["time"], 6),
            "duration_s": round(normalized[-1]["time"], 6),
            "timebase": "hhtools_export_seconds",
        },
    }


def parse_hhtools_upload(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig")
    else:
        text = raw
    # Strip UTF-8 BOM leftovers
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return parse_hhtools_csv(text)
