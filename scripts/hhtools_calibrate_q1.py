#!/usr/bin/env python3
"""Q1 zero-pose calibration + ballet retarget via the local HHTools checkout.

Run with the HHTools virtualenv (imports ``hhtools``)::

  /home/will/projects/human-humanoid-tools/.venv/bin/python \\
    scripts/hhtools_calibrate_q1.py status
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROBOT = "q1_leg"
REFERENCE = "smplx"


def _preset():
    from hhtools.robot.registry import get

    return get(ROBOT)


def _model():
    from hhtools.robot.loader import load_robot

    return load_robot(_preset(), compile_mjcf=False)


def _assessment_dict(assessment) -> dict:
    return {
        "valid": bool(assessment.valid),
        "score": round(float(assessment.score), 6),
        "changed_joint_count": int(assessment.changed_joint_count),
        "edge_errors_deg": {k: round(float(v), 2) for k, v in assessment.edge_errors_deg.items()},
        "alignment_errors": list(assessment.alignment_errors),
        "alignment_warnings": list(assessment.alignment_warnings),
        "near_limit_joints": list(assessment.near_limit_joints),
        "missing_slots": list(assessment.missing_slots),
        "foot_height_delta_m": assessment.foot_height_delta_m,
    }


def status() -> dict:
    from hhtools.retarget.calibration import load_calibration, resolve_preset_calibration_file

    preset = _preset()
    path = resolve_preset_calibration_file(preset, REFERENCE)
    joint_q = {}
    notes = None
    if path is not None:
        cal = load_calibration(path)
        joint_q = {str(k): float(v) for k, v in cal.calibrated_joint_q.items()}
        notes = getattr(cal, "notes", None)
    return {
        "robot": ROBOT,
        "reference": REFERENCE,
        "preset": str(getattr(preset, "urdf_path", "")),
        "calibrated": bool(joint_q),
        "path": str(path) if path else None,
        "joint_count": len(joint_q),
        "joint_q": joint_q,
        "notes": notes,
        "steps": [
            "1. 對照 RP1：HHTools robot preset（URDF、ik_map、weights、feet）",
            "2. Web Calibrate：SMPL-X T-pose 對 Q1 零位，存 calibrated_joint_q",
            "3. Newton IK retarget（calibration-reference=smplx）",
            "4. URDF 預覽＋關節角／力矩，對照 YouTube 1–4s",
            "5. 小量 PPO 通過後再長訓",
        ],
    }


def propose(seed: dict | None = None) -> dict:
    from hhtools.retarget.calibration.assistant import propose_calibration_pose

    model = _model()
    joint_q, assessment = propose_calibration_pose(model, REFERENCE, seed or {})
    return {"joint_q": {k: float(v) for k, v in joint_q.items()}, "validation": _assessment_dict(assessment)}


def save(joint_q: dict) -> dict:
    from hhtools.retarget.calibration import (
        RobotRetargetCalibration,
        derive_calibration_params,
        save_calibration_for_preset,
    )

    model = _model()
    cal = RobotRetargetCalibration(
        robot=ROBOT,
        reference=REFERENCE,
        calibrated_joint_q={str(k): float(v) for k, v in joint_q.items()},
        notes="Motion Studio Web Calibrate",
    )
    derived = derive_calibration_params(cal, model)
    path = save_calibration_for_preset(cal, _preset(), derived=derived)
    return {"ok": True, "path": str(path), "joint_count": len(joint_q)}


def retarget(body: dict) -> dict:
    """Re-run Newton IK on the cached GVHMR NPZ using the saved calibration."""
    import subprocess

    store = Path(__file__).resolve().parents[1]
    video = body.get("video_id") or "a5R0Xf46hyg"
    npz = store / ".data/roboparty/youtube_gvhmr" / video / "motions/hmr4d_results.npz"
    if not npz.is_file():
        raise SystemExit(f"missing {npz}")
    out = store / ".data/roboparty/youtube_gvhmr" / video / "q1_leg_calibrated.csv"
    hhtools = Path(sys.executable).parent / "hhtools"
    subprocess.run(
        [str(hhtools), "retarget", "run", str(npz), "--robot", ROBOT, "-o", str(out),
         "--calibration-reference", REFERENCE],
        check=True,
    )
    sys.path.insert(0, str(store))
    from q1store.roboparty.youtube_pipeline import qa_leg_motion, slice_hhtools_csv

    start = float(body.get("start", 1))
    end = float(body.get("end", 4))
    sliced = slice_hhtools_csv(out.read_text(encoding="utf-8"), start, end)
    return {"ok": True, "csv": str(out), "qa": qa_leg_motion(sliced), "start": start, "end": end}


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    raw = sys.stdin.read().strip()
    body = json.loads(raw) if raw else {}
    if action == "status":
        out = status()
    elif action == "propose":
        out = propose(body.get("joint_q") or {})
    elif action == "save":
        jq = body.get("joint_q") or {}
        if not jq:
            proposed = propose({})
            jq = proposed["joint_q"]
            out = save(jq)
            out["validation"] = proposed["validation"]
            out["joint_q"] = jq
        else:
            out = save(jq)
    elif action == "retarget":
        out = retarget(body)
    else:
        raise SystemExit(f"unknown action {action}")
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
