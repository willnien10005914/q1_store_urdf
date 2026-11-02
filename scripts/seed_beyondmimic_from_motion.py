#!/usr/bin/env python3
"""Convert a store MediaPipe motion JSON into an HHTools-style CSV with root.

Root Z comes from Q1 Leg ``grounded_height`` (FK). Root XY is recovered by
odometry on the stance foot in the pelvis frame (BeyondMimic needs non-zero
horizontal tracking for travel / ballet; MediaPipe store clips often zero XY).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float, default=None)
    parser.add_argument("--action-name", default="beyondmimic_seed")
    args = parser.parse_args()

    leg_root = Path(__file__).resolve().parents[2].parent / "q1_leg_urdf"
    if not (leg_root / "source/leg_humanoid_lab").is_dir():
        leg_root = Path.home() / "projects/q1_leg_urdf"
    sys.path.insert(0, str(leg_root / "source/leg_humanoid_lab"))
    from leg_humanoid_lab.robot import JOINTS, DEFAULT, grounded_height, fk  # noqa: E402

    motion = json.loads(args.motion.read_text())
    names = motion["joint_names"]
    frames = motion["frames"]
    column = {name: i for i, name in enumerate(names)}
    control = list(JOINTS)

    times, qs, roots, quats = [], [], [], []
    pelvis_xy = np.zeros(2)
    prev_stance = None
    for frame in frames:
        t = float(frame["time"])
        raw = frame["q"]
        q = np.array([float(raw[column[n]]) if n in column else float(DEFAULT[i]) for i, n in enumerate(control)])
        transforms = fk(q)
        foot_xy = {}
        foot_z = {}
        for side in ("left", "right"):
            T = transforms[f"{side}_foot_link"]
            foot_xy[side] = T[:2, 3].copy()
            foot_z[side] = float(T[2, 3])
        stance = "left" if foot_z["left"] <= foot_z["right"] else "right"
        if prev_stance is not None:
            # Hold stance foot fixed in world → move pelvis opposite the foot drift in pelvis frame.
            delta = foot_xy[stance] - prev_stance[1]
            if stance == prev_stance[0]:
                pelvis_xy = pelvis_xy - delta
        prev_stance = (stance, foot_xy[stance].copy())
        z = float(grounded_height(q))
        # Mild horizontal travel from waist yaw for clips that never lift a foot.
        yaw = float(q[control.index("center_waist_Yaw_joint")]) if "center_waist_Yaw_joint" in control else 0.0
        root = [float(pelvis_xy[0] + 0.02 * math.sin(yaw)), float(pelvis_xy[1]), z]
        times.append(t)
        qs.append(q)
        roots.append(root)
        quats.append([0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)])

    if args.sample_rate:
        fps = args.sample_rate
    elif len(times) >= 2:
        fps = (len(times) - 1) / max(times[-1] - times[0], 1e-6)
    else:
        fps = 30.0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as stream:
        stream.write(f"# sample_rate: {fps:.6f}\n")
        stream.write(f"# name: {args.action_name}\n")
        stream.write("# source: q1_store_seed_beyondmimic_from_motion\n")
        header = ["time", "root_x", "root_y", "root_z", "root_qx", "root_qy", "root_qz", "root_qw"]
        header += [f"dof_{n}" for n in control]
        writer = csv.writer(stream)
        writer.writerow(header)
        t0 = times[0]
        for t, q, root, quat in zip(times, qs, roots, quats):
            writer.writerow([round(t - t0, 6), *root, *quat, *q.tolist()])
    print(f"wrote {args.out} frames={len(times)} duration={times[-1]-times[0]:.3f}s fps≈{fps:.2f}")


if __name__ == "__main__":
    main()
