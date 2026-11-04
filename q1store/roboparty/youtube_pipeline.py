"""YouTube (by seconds) → HHTools CSV → Store motion (BeyondMimic v3).

Fast path: if a prior ballet CSV already covers the requested window, slice it.
Slow path: invoke ``scripts/youtube_seconds_to_hhtools.py`` (yt-dlp + GVHMR).
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import subprocess
import urllib.parse
from pathlib import Path

from .hhtools_import import HHToolsImportError, parse_hhtools_csv, motion_payload_from_hhtools

STORE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = STORE_ROOT / ".data" / "roboparty" / "youtube_gvhmr"


def youtube_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in (parsed.netloc or ""):
        return parsed.path.strip("/").split("/")[0] or "video"
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("v"):
        return query["v"][0]
    match = re.search(r"(?:shorts|embed|live)/([^/?&#]+)", parsed.path or "")
    return match.group(1) if match else "video"


def _read_csv_rows(text: str) -> tuple[list[str], list[str], list[dict[str, str]]]:
    meta, header, rows = [], None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            meta.append(raw)
            continue
        if header is None:
            header = next(csv.reader([line]))
            continue
        values = next(csv.reader([line]))
        rows.append(dict(zip(header, values)))
    if not header or len(rows) < 2:
        raise HHToolsImportError("CSV 影格不足，無法依秒數切片。")
    return meta, header, rows


def slice_hhtools_csv(text: str, start: float, end: float) -> str:
    """Keep frames with time in [start, end], renorm time to 0."""
    if end - start < 0.4:
        raise HHToolsImportError("片段至少需要 0.4 秒。")
    meta, header, rows = _read_csv_rows(text)
    selected = [r for r in rows if start - 1e-6 <= float(r["time"]) <= end + 1e-6]
    if len(selected) < 2:
        raise HHToolsImportError(f"在 {start}–{end}s 找不到足夠影格。")
    t0 = float(selected[0]["time"])
    out = io.StringIO()
    for line in meta:
        out.write(line.rstrip() + "\n")
    out.write(f"# slice_start_s: {start}\n")
    out.write(f"# slice_end_s: {end}\n")
    out.write(f"# pipeline: beyondmimic_v3\n")
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    for row in selected:
        row = dict(row)
        row["time"] = f"{float(row['time']) - t0:.6f}"
        writer.writerow(row)
    return out.getvalue()


def qa_leg_motion(csv_text: str) -> dict:
    """Basic DOF range checks for single-leg ballet."""
    _, _, rows = _read_csv_rows(csv_text)
    report = {"frames": len(rows), "warnings": [], "ok": True, "repairs": []}

    def ptp(col: str) -> float:
        vals = [float(r[col]) for r in rows if col in r]
        return (max(vals) - min(vals)) if vals else 0.0

    for col, need in (
        ("dof_right_hip_Pitch_joint", 0.15),
        ("dof_left_hip_Pitch_joint", 0.15),
        ("dof_right_knee_Pitch_joint", 0.1),
        ("dof_left_knee_Pitch_joint", 0.1),
    ):
        span = ptp(col)
        report[col] = span
        if span < need:
            report["warnings"].append(f"{col} ptp={span:.3f} < {need} (單腳芭蕾可能失敗)")
            report["ok"] = False
    return report


def repair_frozen_hip_pitch(csv_text: str) -> tuple[str, list[str]]:
    """Synthesize swing hip Pitch when IK froze it but Roll/Knee still move.

    Ballet 1–4s needs right (or left) thigh lift for passé. HHTools sometimes
    parks Pitch at 0 and dumps lift into extreme Roll. Move a share of that
    signal into Pitch and clamp Roll so Isaac tracking can lift the foot.
    """
    meta, header, rows = _read_csv_rows(csv_text)
    repairs: list[str] = []

    def series(col: str) -> list[float]:
        return [float(r[col]) if col in r and r[col] != "" else 0.0 for r in rows]

    def ptp(vals: list[float]) -> float:
        return (max(vals) - min(vals)) if vals else 0.0

    for side in ("right", "left"):
        pitch_col = f"dof_{side}_hip_Pitch_joint"
        roll_col = f"dof_{side}_hip_Roll_joint"
        knee_col = f"dof_{side}_knee_Pitch_joint"
        if pitch_col not in header:
            continue
        pitches = series(pitch_col)
        rolls = series(roll_col) if roll_col in header else [0.0] * len(rows)
        knees = series(knee_col) if knee_col in header else [0.0] * len(rows)
        if ptp(pitches) >= 0.12:
            continue
        if ptp(rolls) < 0.2 and ptp(knees) < 0.08:
            continue
        for i, row in enumerate(rows):
            roll = rolls[i]
            knee = knees[i]
            # Passé: thigh flexion from |roll| excess + knee bend (Q1 knee often ≤0).
            excess = max(0.0, abs(roll) - 0.35)
            target = min(1.35, 0.85 * excess + 1.1 * abs(knee) + 0.15)
            # Match existing sign convention on this CSV (left pitch often negative when flexed).
            if side == "left":
                target = -target
            row[pitch_col] = f"{target:.6f}"
            if roll_col in header and abs(roll) > 0.7:
                row[roll_col] = f"{max(-0.65, min(0.65, roll)):.6f}"
        repairs.append(
            f"{side}_hip_Pitch synthesized from Roll/Knee "
            f"(was ptp={ptp(pitches):.3f}, roll_ptp={ptp(rolls):.3f})"
        )

    if not repairs:
        return csv_text, repairs

    out = io.StringIO()
    for line in meta:
        out.write(line.rstrip() + "\n")
    out.write("# repair: frozen_hip_pitch_v3\n")
    for note in repairs:
        out.write(f"# repair_note: {note}\n")
    writer = csv.DictWriter(out, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue(), repairs


def find_cached_csv(video_id: str) -> Path | None:
    root = DEFAULT_CACHE / video_id
    candidates = [
        root / "q1_leg_v2.csv",
        root / "q1_leg.csv",
        DEFAULT_CACHE / "a5R0Xf46hyg" / "q1_leg_v2.csv",
        DEFAULT_CACHE / "a5R0Xf46hyg" / "q1_leg.csv",
    ]
    for path in candidates:
        if path.is_file() and path.stat().st_size > 1000:
            return path
    return None


def run_youtube_to_motion(
    *,
    youtube_url: str,
    start: float,
    end: float,
    action_name: str,
    robot_type: str = "leg",
    allow_cache_slice: bool = True,
) -> dict:
    """Return Store-ready motion payload fields + csv + qa."""
    if robot_type != "leg":
        raise HHToolsImportError("YouTube→HHTools v3 目前僅支援 Q1 Leg。")
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise HHToolsImportError("起訖秒數無效。")
    video_id = youtube_video_id(youtube_url)
    csv_text = None
    source = "fresh"
    cached = find_cached_csv(video_id) if allow_cache_slice else None
    if cached is not None:
        # Cache clip often starts at YouTube t=0; slice absolute seconds.
        csv_text = slice_hhtools_csv(cached.read_text(encoding="utf-8"), start, end)
        source = f"cache_slice:{cached}"
    else:
        out_dir = DEFAULT_CACHE / video_id / f"v3_{start:g}_{end:g}".replace(".", "p")
        out_dir.mkdir(parents=True, exist_ok=True)
        script = STORE_ROOT / "scripts" / "youtube_seconds_to_hhtools.py"
        cmd = [
            "python3",
            str(script),
            "--youtube-url",
            youtube_url,
            "--start",
            str(start),
            "--end",
            str(end),
            "--out-dir",
            str(out_dir),
            "--action-name",
            action_name,
        ]
        # Prefer retarget-only if GVHMR already ran for overlapping clip.
        subprocess.run(cmd, cwd=STORE_ROOT, check=False)
        csv_path = out_dir / "q1_leg.csv"
        if not csv_path.is_file():
            raise HHToolsImportError(
                "YouTube→GVHMR→retarget 未產出 CSV。請確認 SMPLX / GVHMR 就緒後重試，"
                "或先用 CLI scripts/youtube_seconds_to_hhtools.py。"
            )
        csv_text = csv_path.read_text(encoding="utf-8")
        source = f"pipeline:{csv_path}"

    csv_text, repairs = repair_frozen_hip_pitch(csv_text)
    qa = qa_leg_motion(csv_text)
    if repairs:
        qa["repairs"] = repairs
        # Re-check after repair; keep warnings that remain.
        qa["ok"] = not any("hip_Pitch" in w or "knee_Pitch" in w for w in qa.get("warnings", []))
    parsed = parse_hhtools_csv(csv_text)
    motion = motion_payload_from_hhtools(parsed, robot_type=robot_type, action_name=action_name)
    mapping = dict(motion.get("mapping") or {})
    mapping.update(
        pipeline="beyondmimic_v3",
        method="hhtools_retarget_v3_youtube",
        youtube_url=youtube_url,
        youtube_video_id=video_id,
        source_start_s=start,
        source_end_s=end,
        csv_source=source,
        qa=qa,
        repairs=repairs,
        plan_doc="/docs/BALLET_V3_PLAN.md",
    )
    motion["mapping"] = mapping
    motion["pipeline"] = "beyondmimic_v3"
    motion["source"] = "hhtools"
    return {
        "csv": csv_text,
        "motion": motion,
        "qa": qa,
        "repairs": repairs,
        "video_id": video_id,
        "start": start,
        "end": end,
        "action_name": action_name,
        "plan": {
            "doc": "docs/BALLET_V3_PLAN.md",
            "clip": f"{video_id} {start}–{end}s",
            "analysis": "left stance · right passé / cou-de-pied · arms fifth",
            "train": "Store Isaac PPO worker · pipeline=beyondmimic_v3",
            "isaac_lab3": "School tutorials use Isaac Lab 3; Motion Studio PPO remains Lab 2.x PhysX until Q1 IL3 BM task lands",
        },
    }
