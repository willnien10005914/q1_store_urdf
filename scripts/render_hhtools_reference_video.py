#!/usr/bin/env python3
"""Render HHTools / BeyondMimic reference as a kinematic URDF preview (no PPO).

Examples::

  python3 scripts/render_hhtools_reference_video.py \\
    --reference .data/jobs/<id>/reference.json \\
    --out .data/roboparty/previews/preview.mp4

  python3 scripts/render_hhtools_reference_video.py \\
    --motion .data/jobs/<id>/motion.json \\
    --out .data/roboparty/previews/from_motion.mp4
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from q1store.roboparty.preview_video import render_reference_video  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="reference.json from a store job")
    parser.add_argument("--motion", type=Path, help="store motion.json /api/motions payload")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--mjcf", type=Path, default=None)
    args = parser.parse_args()
    if bool(args.reference) == bool(args.motion):
        parser.error("Provide exactly one of --reference or --motion")
    path = args.reference or args.motion
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = render_reference_video(
        payload,
        args.out,
        mjcf=args.mjcf,
        width=args.width,
        height=args.height,
        fps=args.fps,
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
