"""Convert a browser retargeting record into the Q1 leg Motion reference contract.

The store keeps MediaPipe joint frames for every movable URDF joint. Isaac Lab
training consumes a uniformly sampled reference restricted to the 23 control
joints, with a grounded pelvis height per frame. Pure Python so the web service
can validate references without the Isaac runtime; the trainer supplies the
URDF-derived grounded height function.
"""
from __future__ import annotations

import hashlib
import json
import math

SAMPLE_DT = 0.05
BLEND_IN_S = 1.0
BLEND_OUT_S = 0.5
SMOOTHING_TAPS = 3


class ReferenceError(ValueError):
    pass


def interpolate(times, values, t):
    if t <= times[0]:
        return list(values[0])
    if t >= times[-1]:
        return list(values[-1])
    low, high = 0, len(times) - 1
    while high - low > 1:
        mid = (low + high) // 2
        if times[mid] <= t:
            low = mid
        else:
            high = mid
    span = times[high] - times[low]
    weight = 0.0 if span <= 0 else (t - times[low]) / span
    return [a + (b - a) * weight for a, b in zip(values[low], values[high])]


def smooth(rows, taps=SMOOTHING_TAPS):
    if taps <= 1 or len(rows) < taps:
        return [list(row) for row in rows]
    half = taps // 2
    output = []
    for index in range(len(rows)):
        window = rows[max(0, index - half):min(len(rows), index + half + 1)]
        output.append([sum(col) / len(window) for col in zip(*window)])
    return output


def build_reference(motion, *, joint_names, default, lower, upper, urdf_sha256, grounded_height,
                    sample_dt=SAMPLE_DT, blend_in_s=BLEND_IN_S, blend_out_s=BLEND_OUT_S, job_id=None):
    """Return the Motion-contract dictionary for ``motion`` (a stored /api/motions record)."""
    names = motion.get("joint_names")
    frames = motion.get("frames")
    if not isinstance(names, list) or not isinstance(frames, list) or len(frames) < 2:
        raise ReferenceError("動作參考至少需要兩個 frame。")
    if len(joint_names) != len(default) or len(joint_names) != len(lower) or len(joint_names) != len(upper):
        raise ReferenceError("控制關節契約長度不一致。")
    missing = [name for name in joint_names if name not in names]
    column = {name: index for index, name in enumerate(names)}
    times, rows = [], []
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
        times.append(t)
        rows.append(row)
    start = times[0]
    times = [t - start for t in times]
    duration = times[-1]
    if duration < 0.5:
        raise ReferenceError("動作片段至少需要 0.5 秒。")
    if duration > 60:
        raise ReferenceError("動作片段最長 60 秒。")
    steps = int(round(duration / sample_dt))
    sampled = smooth([interpolate(times, rows, min(duration, i * sample_dt)) for i in range(steps + 1)])
    sampled = [[min(max(v, lower[k]), upper[k]) for k, v in enumerate(row)] for row in sampled]
    default_row = [float(v) for v in default]
    blend_in = int(round(blend_in_s / sample_dt))
    blend_out = int(round(blend_out_s / sample_dt))
    output = []
    for i in range(blend_in):
        w = i / blend_in
        w = 0.5 - 0.5 * math.cos(math.pi * w)
        output.append([a + (b - a) * w for a, b in zip(default_row, sampled[0])])
    output.extend(sampled)
    for i in range(1, blend_out + 1):
        w = i / blend_out
        w = 0.5 - 0.5 * math.cos(math.pi * w)
        output.append([a + (b - a) * w for a, b in zip(sampled[-1], default_row)])
    time = [round(i * sample_dt, 6) for i in range(len(output))]
    root = [[0.0, 0.0, float(grounded_height(row))] for row in output]
    for z in root:
        if not math.isfinite(z[2]) or z[2] <= 0.2:
            raise ReferenceError("參考姿態的接地高度無效，請重新辨識。")
    encoded = json.dumps({"time": time, "q": output}, separators=(",", ":")).encode()
    return {
        "schema": 1, "source": "mediapipe", "store_motion_id": motion.get("id"), "store_job_id": job_id,
        "robot_type": motion.get("robot_type"), "joint_names": list(joint_names), "time": time, "q": output, "root": root,
        "robot_urdf_sha256": urdf_sha256, "sample_dt": sample_dt, "duration_s": time[-1],
        "clip": {"frames": len(frames), "duration_s": round(duration, 6), "segment": motion.get("segment"),
                 "blend_in_s": blend_in_s, "blend_out_s": blend_out_s, "missing_joints_default": missing,
                 "smoothing_taps": SMOOTHING_TAPS, "mapping": motion.get("mapping"), "target_person": motion.get("target_person")},
        "reference_sha256": hashlib.sha256(encoded).hexdigest(),
        "limitations": ["MediaPipe 單眼姿態是近似的動作先驗，不是動作捕捉。",
                        "root 以每個 frame 的接地高度計算，水平位移設為零。",
                        "片段前後加入預設站姿過渡，讓訓練從穩定站姿開始與結束。"],
    }
