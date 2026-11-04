"""Build a BeyondMimic-style reference JSON for Q1 Leg from HHTools motions.

Compared with :func:`q1store.reference.build_reference` (MediaPipe, root XY=0):

* preserves horizontal root trajectory from HHTools
* **re-grounds root Z** with Q1 ``grounded_height(q)`` (HHTools CSV often keeps
  human-scale pelvis ~1.5 m; Isaac height rewards require robot-scale ~0.88 m)
* records a sanitized upright root quaternion (yaw from XY travel)
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


def _yaw_quat(yaw: float) -> list[float]:
    """Return xyzw quaternion for a pure yaw about +Z."""
    half = 0.5 * yaw
    return [0.0, 0.0, math.sin(half), math.cos(half)]


def _sanitize_quat(quat, yaw: float) -> list[float]:
    """Keep HHTools quat only when nearly upright; else replace with yaw-only."""
    x, y, z, w = (float(v) for v in quat)
    # Project world −Z through R(q) → body; upright when ≈ [0, 0, −1].
    # For q=(x,y,z,w): R^T * [0,0,-1] via quaternion math.
    # projected_gravity_b for identity is (0,0,-1).
    gx = 2 * (x * z - w * y)
    gy = 2 * (y * z + w * x)
    gz = 1 - 2 * (x * x + y * y)
    # World down in body for our CSV convention (xyzw); gz≈+1 means upside-down
    # of Isaac's projected gravity check. Prefer upright when |body−Z·world−Z| high.
    upright = abs(gz)  # after HHTools Y-up→Z-up, many clips tip ~90°
    if upright < 0.85 or abs(gx) > 0.35 or abs(gy) > 0.35:
        return _yaw_quat(yaw)
    norm = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    return [x / norm, y / norm, z / norm, w / norm]


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
    max_root_z_slack=0.12,
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
    raw_zs = []
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
            root = [0.0, 0.0, 0.0]
        xy = [float(root[0]), float(root[1])]
        if any(not math.isfinite(v) for v in xy):
            raise ReferenceError("root 軌跡含非有限數。")
        fk_z = float(grounded_height(row))
        if not math.isfinite(fk_z) or fk_z <= 0.05:
            raise ReferenceError("參考姿態的接地高度無效，請重新 retarget。")
        raw_z = float(root[2]) if math.isfinite(float(root[2])) else fk_z
        raw_zs.append(raw_z)
        # Always re-ground Z to Q1 FK height (keeps XY travel from HHTools).
        # Clamp away from fallen/crouch extremes that terminate Isaac episodes.
        fk_z = min(max(fk_z, 0.72), 1.05)
        roots.append([xy[0], xy[1], fk_z])
        times.append(t)
        rows.append(row)

    # Yaw from XY travel for upright quats.
    for i, row in enumerate(rows):
        if i + 1 < len(roots):
            dx = roots[i + 1][0] - roots[i][0]
            dy = roots[i + 1][1] - roots[i][1]
        elif i > 0:
            dx = roots[i][0] - roots[i - 1][0]
            dy = roots[i][1] - roots[i - 1][1]
        else:
            dx = dy = 0.0
        yaw = math.atan2(dy, dx) if (dx * dx + dy * dy) > 1e-8 else 0.0
        quat = frames[i].get("root_quat")
        if isinstance(quat, (list, tuple)) and len(quat) == 4 and all(math.isfinite(float(v)) for v in quat):
            quats.append(_sanitize_quat(quat, yaw))
        else:
            quats.append(_yaw_quat(yaw))

    # Reject clearly human-scale pelvis that was never robot-scaled (joints may still be usable after Z fix).
    sorted_z = sorted(raw_zs)
    median_raw = sorted_z[len(sorted_z) // 2]
    fk_sample = [r[2] for r in roots]
    median_fk = sorted(fk_sample)[len(fk_sample) // 2]
    if median_raw - median_fk > max_root_z_slack:
        # Informational: we already re-grounded; keep training but record the gap.
        pass

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
    # Recompute Z after smoothing joints so height tracks smoothed pose.
    sampled_roots = []
    for i, t in enumerate(sample_times):
        xy = _interp_roots(times, roots, t)
        z = min(max(float(grounded_height(sampled[i])), 0.72), 1.05)
        sampled_roots.append([xy[0], xy[1], z])
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
        q_row = [a + (b - a) * w for a, b in zip(default_row, sampled[0])]
        output_q.append(q_row)
        xy = [a + (b - a) * w for a, b in zip(default_root[:2], sampled_roots[0][:2])]
        z = min(max(float(grounded_height(q_row)), 0.72), 1.05)
        output_root.append([xy[0], xy[1], z])
        output_quat.append([a + (b - a) * w for a, b in zip(default_quat, sampled_quats[0])])
    for q_row, root, quat in zip(sampled, sampled_roots, sampled_quats):
        output_q.append(q_row)
        z = min(max(float(grounded_height(q_row)), 0.72), 1.05)
        output_root.append([root[0], root[1], z])
        output_quat.append(quat)
    for i in range(1, blend_out + 1):
        w = i / blend_out
        w = 0.5 - 0.5 * math.cos(math.pi * w)
        q_row = [a + (b - a) * w for a, b in zip(sampled[-1], default_row)]
        output_q.append(q_row)
        xy = [a + (b - a) * w for a, b in zip(sampled_roots[-1][:2], default_root[:2])]
        z = min(max(float(grounded_height(q_row)), 0.72), 1.05)
        output_root.append([xy[0], xy[1], z])
        output_quat.append([a + (b - a) * w for a, b in zip(sampled_quats[-1], default_quat)])
    for quat in output_quat:
        norm = math.sqrt(sum(v * v for v in quat)) or 1.0
        for i in range(4):
            quat[i] /= norm
    for root in output_root:
        if not math.isfinite(root[2]) or root[2] < 0.70 or root[2] > 1.10:
            raise ReferenceError("參考姿態的接地高度無效，請重新 retarget。")
    time = [round(i * sample_dt, 6) for i in range(len(output_q))]
    encoded = json.dumps({"time": time, "q": output_q, "root": output_root}, separators=(",", ":")).encode()
    mapping = dict(motion.get("mapping") or {})
    mapping.update(
        pipeline="beyondmimic",
        root_motion=True,
        method=mapping.get("method") or "hhtools_retarget_v1",
        root_z_source="grounded_height",
        human_root_z_median=round(median_raw, 6),
        robot_root_z_median=round(median_fk, 6),
    )
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
                "Store JSON carries joint+root for Q1 Leg Motion. Root Z is Q1 FK grounded height; "
                "XY comes from HHTools. Optional NPZ body fields can be produced later via "
                "roboparty convert_motion.py with a MuJoCo Q1 asset."
            ),
            "docs": "https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en",
        },
        "limitations": [
            "參考來自 HHTools / RoboParty retarget：保留 root XY，Z 以 Q1 grounded_height 重接地。",
            "完整 BeyondMimic body_* 欄位需另轉 NPZ；Q1 Leg 環境目前消費 joint+root。",
            "片段前後加入預設站姿過渡，讓訓練從穩定站姿開始與結束。",
        ],
    }
