"""Build a BeyondMimic-style reference JSON for Q1 Leg from HHTools motions.

Compared with :func:`q1store.reference.build_reference` (MediaPipe, root XY=0):

* preserves horizontal root trajectory from HHTools
* records root quaternion when present
* tags ``source`` as ``hhtools`` / ``beyondmimic`` so the Isaac Motion loader
  accepts the clip (see q1_leg ``Motion`` allowed sources)
* keeps joint resampling / blend-in/out for stable episode starts
"""
from __future__ import annotations

import hashlib
import json
import math

from q1store.reference import (
    BLEND_IN_S,
    BLEND_OUT_S,
    SAMPLE_DT,
    SMOOTHING_TAPS,
    ReferenceError,
    interpolate,
    smooth,
)


def _interp_roots(times, roots, t):
    return interpolate(times, roots, t)


def build_beyondmimic_reference(
    motion,
    *,
    joint_names,
    default,
    lower,
    upper,
    urdf_sha256,
    grounded_height,
    sample_dt=SAMPLE_DT,
    blend_in_s=BLEND_IN_S,
    blend_out_s=BLEND_OUT_S,
    job_id=None,
):
    """Return a Motion-contract dict with root motion for BeyondMimic-style training."""
    names = motion.get("joint_names")
    frames = motion.get("frames")
    if not isinstance(names, list) or not isinstance(frames, list) or len(frames) < 2:
        raise ReferenceError("動作參考至少需要兩個 frame。")
    if len(joint_names) != len(default) or len(joint_names) != len(lower) or len(joint_names) != len(upper):
        raise ReferenceError("控制關節契約長度不一致。")
    missing = [name for name in joint_names if name not in names]
    column = {name: index for index, name in enumerate(names)}
    times, rows, roots, quats = [], [], [], []
    for frame in frames:
        t = float(frame["time"])
        if times and t <= times[-1]:
            raise ReferenceError("frame time 必須嚴格遞增。")
        q = frame["q"]
        row = []
        for k, name in enumerate(joint_names):
            value = float(q[column[name]]) if name in column else float(default[k])
            if not math.isfinite(value):
                raise ReferenceError("關節值必須是有限數。")
            row.append(min(max(value, lower[k]), upper[k]))
        root = frame.get("root")
        if not (isinstance(root, (list, tuple)) and len(root) >= 3):
            # Fall back to grounded-only Z if a frame lacks root (should not happen for HHTools).
            z = float(grounded_height(row))
            root = [0.0, 0.0, z]
        root = [float(root[0]), float(root[1]), float(root[2])]
        if any(not math.isfinite(v) for v in root):
            raise ReferenceError("root 軌跡含非有限數。")
        if root[2] <= 0.05:
            # Keep XY; repair Z with kinematic grounded height.
            root[2] = float(grounded_height(row))
        quat = frame.get("root_quat")
        if isinstance(quat, (list, tuple)) and len(quat) == 4 and all(math.isfinite(float(v)) for v in quat):
            quats.append([float(v) for v in quat])
        else:
            quats.append([0.0, 0.0, 0.0, 1.0])
        times.append(t)
        rows.append(row)
        roots.append(root)
    start = times[0]
    times = [t - start for t in times]
    duration = times[-1]
    if duration < 0.5:
        raise ReferenceError("動作片段至少需要 0.5 秒。")
    if duration > 60:
        raise ReferenceError("動作片段最長 60 秒。")
    steps = int(round(duration / sample_dt))
    sample_times = [min(duration, i * sample_dt) for i in range(steps + 1)]
    sampled = smooth([interpolate(times, rows, t) for t in sample_times])
    sampled = [[min(max(v, lower[k]), upper[k]) for k, v in enumerate(row)] for row in sampled]
    sampled_roots = smooth([_interp_roots(times, roots, t) for t in sample_times])
    # Quaternions: nlerp via component interpolate then renormalize (good enough for blend ends).
    sampled_quats = []
    for t in sample_times:
        q = interpolate(times, quats, t)
        norm = math.sqrt(sum(v * v for v in q)) or 1.0
        sampled_quats.append([v / norm for v in q])

    default_row = [float(v) for v in default]
    default_root = [0.0, 0.0, float(grounded_height(default_row))]
    default_quat = [0.0, 0.0, 0.0, 1.0]
    blend_in = int(round(blend_in_s / sample_dt))
    blend_out = int(round(blend_out_s / sample_dt))
    output_q, output_root, output_quat = [], [], []
    for i in range(blend_in):
        w = i / blend_in
        w = 0.5 - 0.5 * math.cos(math.pi * w)
        output_q.append([a + (b - a) * w for a, b in zip(default_row, sampled[0])])
        output_root.append([a + (b - a) * w for a, b in zip(default_root, sampled_roots[0])])
        output_quat.append([a + (b - a) * w for a, b in zip(default_quat, sampled_quats[0])])
    output_q.extend(sampled)
    output_root.extend(sampled_roots)
    output_quat.extend(sampled_quats)
    for i in range(1, blend_out + 1):
        w = i / blend_out
        w = 0.5 - 0.5 * math.cos(math.pi * w)
        output_q.append([a + (b - a) * w for a, b in zip(sampled[-1], default_row)])
        output_root.append([a + (b - a) * w for a, b in zip(sampled_roots[-1], default_root)])
        output_quat.append([a + (b - a) * w for a, b in zip(sampled_quats[-1], default_quat)])
    for quat in output_quat:
        norm = math.sqrt(sum(v * v for v in quat)) or 1.0
        for i in range(4):
            quat[i] /= norm
    for root in output_root:
        if not math.isfinite(root[2]) or root[2] <= 0.2:
            raise ReferenceError("參考姿態的接地高度無效，請重新 retarget。")
    time = [round(i * sample_dt, 6) for i in range(len(output_q))]
    encoded = json.dumps({"time": time, "q": output_q, "root": output_root}, separators=(",", ":")).encode()
    mapping = dict(motion.get("mapping") or {})
    mapping.update(pipeline="beyondmimic", root_motion=True, method=mapping.get("method") or "hhtools_retarget_v1")
    return {
        "schema": 2,
        "source": "hhtools",
        "pipeline": "beyondmimic",
        "store_motion_id": motion.get("id"),
        "store_job_id": job_id,
        "robot_type": motion.get("robot_type"),
        "joint_names": list(joint_names),
        "time": time,
        "q": output_q,
        "root": output_root,
        "root_quat": output_quat,
        "robot_urdf_sha256": urdf_sha256,
        "sample_dt": sample_dt,
        "duration_s": time[-1],
        "clip": {
            "frames": len(frames),
            "duration_s": round(duration, 6),
            "segment": motion.get("segment"),
            "blend_in_s": blend_in_s,
            "blend_out_s": blend_out_s,
            "missing_joints_default": missing,
            "smoothing_taps": SMOOTHING_TAPS,
            "mapping": mapping,
            "target_person": motion.get("target_person"),
        },
        "reference_sha256": hashlib.sha256(encoded).hexdigest(),
        "beyondmimic": {
            "fields": ["fps", "joint_pos", "joint_vel", "body_pos_w", "body_quat_w", "body_lin_vel_w", "body_ang_vel_w"],
            "note": (
                "Store JSON carries joint+root for Q1 Leg Motion. Optional NPZ body fields "
                "can be produced later via vendor/roboparty/scripts/convert_motion.py with a "
                "MuJoCo Q1 asset (RoboParty RP1 path uses rp1/rpo assets)."
            ),
            "docs": "https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en",
        },
        "limitations": [
            "參考來自 HHTools / RoboParty retarget，含 root 水平位移（非 MediaPipe 零位移）。",
            "完整 BeyondMimic body_* 欄位需另轉 NPZ；Q1 Leg 環境目前消費 joint+root。",
            "片段前後加入預設站姿過渡，讓訓練從穩定站姿開始與結束。",
        ],
    }
