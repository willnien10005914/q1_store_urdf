#!/usr/bin/env python3
"""YouTube (by seconds) → GVHMR/SMPL-X → HHTools retarget → Q1 CSV → Store import.

Example (ballet job source):

  python3 scripts/youtube_seconds_to_hhtools.py \\
    --youtube-url 'https://www.youtube.com/watch?v=a5R0Xf46hyg' \\
    --start 0 --end 6.5 \\
    --action-name 'Ballet · HHTools formal' \\
    --import-store

Requires:
  * yt-dlp / ffmpeg
  * GVHMR env (HHTOOLS_GVHMR_ROOT + HHTOOLS_GVHMR_PYTHON) with public ckpts
  * Licensed SMPLX_NEUTRAL.npz under
      $GVHMR/inputs/checkpoints/body_models/smplx/
    (or pass --place-smplx /path/to/SMPLX_NEUTRAL.npz once)
  * q1_leg preset (scripts/setup_hhtools_q1.sh)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

STORE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = STORE_ROOT / ".data" / "roboparty" / "youtube_gvhmr"
ENV_FILE = STORE_ROOT / ".data" / "hhtools.env"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _uv_bin() -> str:
    return shutil.which("uv") or "uv"


def youtube_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/") or "video"
    query = urllib.parse.parse_qs(parsed.query)
    if "v" in query and query["v"]:
        return query["v"][0]
    match = re.search(r"(?:shorts|embed|live)/([^/?&#]+)", parsed.path)
    if match:
        return match.group(1)
    return "video"


def download_youtube_segment(url: str, start: float, end: float, out_mp4: Path) -> Path:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if out_mp4.is_file() and out_mp4.stat().st_size > 1024:
        print(f"reuse existing clip {out_mp4}")
        return out_mp4
    yt_dlp = shutil.which("yt-dlp") or "yt-dlp"
    # Prefer native section download; fall back to full + ffmpeg cut.
    section = f"*{start}-{end}"
    raw = out_mp4.with_suffix(".raw.mp4")
    try:
        _run([
            yt_dlp, "-f", "mp4/best",
            "--download-sections", section,
            "--force-keyframes-at-cuts",
            "-o", str(out_mp4),
            url,
        ])
        if out_mp4.is_file() and out_mp4.stat().st_size > 1024:
            return out_mp4
    except subprocess.CalledProcessError:
        print("section download failed; falling back to full+ffmpeg", flush=True)
    _run([yt_dlp, "-f", "mp4/best", "-o", str(raw), url])
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    duration = max(0.1, end - start)
    _run([
        ffmpeg, "-y", "-ss", str(start), "-i", str(raw),
        "-t", str(duration), "-c", "copy", str(out_mp4),
    ])
    return out_mp4


def smplx_target() -> Path:
    root = Path(os.environ.get("HHTOOLS_GVHMR_ROOT", Path.home() / "projects" / "GVHMR")).expanduser()
    body = Path(
        os.environ.get(
            "HHTOOLS_GVHMR_BODY_MODELS",
            root / "inputs" / "checkpoints" / "body_models",
        )
    ).expanduser()
    return body / "smplx" / "SMPLX_NEUTRAL.npz"


def place_smplx(src: Path) -> Path:
    src = src.expanduser().resolve()
    if not src.is_file():
        raise SystemExit(f"--place-smplx file not found: {src}")
    dest = smplx_target()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    try:
        os.symlink(src, dest)
        print(f"linked SMPLX → {dest} -> {src}")
    except OSError:
        shutil.copy2(src, dest)
        print(f"copied SMPLX → {dest}")
    return dest


def gvhmr_ready() -> dict:
    env = os.environ.copy()
    env.setdefault("HHTOOLS_GVHMR_ROOT", str(Path.home() / "projects" / "GVHMR"))
    root = Path(env["HHTOOLS_GVHMR_ROOT"]).expanduser()
    py = env.get("HHTOOLS_GVHMR_PYTHON") or str(root / ".venv/bin/python")
    env["HHTOOLS_GVHMR_PYTHON"] = py
    sys.path.insert(0, str(Path(os.environ.get("Q1_HHTOOLS_ROOT", Path.home() / "projects/human-humanoid-tools"))))
    from hhtools.integrations.gvhmr import gvhmr_status  # noqa: WPS433

    status = gvhmr_status(probe_runtime=True)
    status["_env"] = {"HHTOOLS_GVHMR_ROOT": str(root), "HHTOOLS_GVHMR_PYTHON": py}
    status["smplx_target"] = str(smplx_target())
    return status


def run_gvhmr(video: Path, out_dir: Path, *, static_cam: bool = True) -> Path:
    status = gvhmr_ready()
    if not status.get("ready"):
        missing = status.get("missing") or []
        raise SystemExit(
            "GVHMR not ready:\n- " + "\n- ".join(missing)
            + "\nPlace SMPLX_NEUTRAL.npz after accepting https://smpl-x.is.tue.mpg.de/"
            + f"\nExpected path: {smplx_target()}"
            + "\nOr: python3 scripts/youtube_seconds_to_hhtools.py --place-smplx ~/Downloads/SMPLX_NEUTRAL.npz --status-only"
        )
    env = os.environ.copy()
    env.update(status["_env"])
    sys.path.insert(0, str(Path(env.get("Q1_HHTOOLS_ROOT", Path.home() / "projects/human-humanoid-tools"))))
    from hhtools.integrations.gvhmr import run_gvhmr as _run_gvhmr  # noqa: WPS433

    out_dir.mkdir(parents=True, exist_ok=True)
    result = _run_gvhmr(video, out_dir, static_cam=static_cam)
    print("GVHMR result", result)
    return Path(result)


def import_and_retarget(gvhmr_out: Path, csv_out: Path, *, robot: str = "q1_leg") -> Path:
    hhtools = Path(os.environ.get("Q1_HHTOOLS_ROOT", Path.home() / "projects/human-humanoid-tools"))
    motions = csv_out.parent / "motions"
    motions.mkdir(parents=True, exist_ok=True)
    uv = _uv_bin()
    # Convert GVHMR output folder → unified NPZ
    _run([
        uv, "run", "hhtools", "import", "run",
        "--dataset", "gvhmr",
        "--root", str(gvhmr_out if gvhmr_out.is_dir() else gvhmr_out.parent),
        "--out", str(motions),
    ], cwd=hhtools)
    npzs = sorted(motions.rglob("*.npz"))
    if not npzs:
        raise SystemExit(f"No NPZ produced under {motions}")
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    _run([
        uv, "run", "hhtools", "retarget", "run",
        str(npzs[0]),
        "--robot", robot,
        "-o", str(csv_out),
        "--calibration-reference", "smplx",
    ], cwd=hhtools)
    return csv_out


def import_store(csv_path: Path, *, action_name: str, store_url: str) -> dict:
    payload = {
        "csv": csv_path.read_text(encoding="utf-8"),
        "robot_type": "leg",
        "action_name": action_name,
        "title": action_name,
    }
    req = urllib.request.Request(
        store_url.rstrip("/") + "/api/roboparty/hhtools",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as response:
        return json.load(response)


def write_manifest(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("manifest", path)


def main() -> None:
    _load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser()
    parser.add_argument("--youtube-url", default=None)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    parser.add_argument("--action-name", default="YouTube · HHTools")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--video", type=Path, default=None, help="Skip download; use this mp4")
    parser.add_argument("--static-cam", action="store_true", default=True)
    parser.add_argument("--no-gvhmr", action="store_true", help="Only download/cut the clip")
    parser.add_argument("--import-store", action="store_true")
    parser.add_argument("--store-url", default="http://127.0.0.1:8878")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument(
        "--place-smplx",
        type=Path,
        default=None,
        help="Copy/symlink licensed SMPLX_NEUTRAL.npz into the GVHMR body_models path",
    )
    args = parser.parse_args()

    os.environ.setdefault("HHTOOLS_GVHMR_ROOT", str(Path.home() / "projects" / "GVHMR"))
    os.environ.setdefault(
        "HHTOOLS_GVHMR_PYTHON",
        str(Path(os.environ["HHTOOLS_GVHMR_ROOT"]) / ".venv/bin/python"),
    )
    os.environ.setdefault("Q1_HHTOOLS_ROOT", str(Path.home() / "projects" / "human-humanoid-tools"))

    if args.place_smplx is not None:
        place_smplx(args.place_smplx)

    if args.status_only:
        print(json.dumps(gvhmr_ready(), indent=2, default=str))
        return

    if args.youtube_url is None and args.video is None:
        raise SystemExit("--youtube-url or --video is required")
    if args.end is None and args.video is None:
        raise SystemExit("--end is required when downloading from YouTube")

    video_id = youtube_video_id(args.youtube_url) if args.youtube_url else "local"
    out = args.out_dir or (DEFAULT_OUT / video_id)
    out.mkdir(parents=True, exist_ok=True)

    if args.video:
        video = args.video
    else:
        assert args.youtube_url is not None and args.end is not None
        stem = f"clip_{args.start:g}_{args.end:g}".replace(".", "p")
        # Prefer previously staged ballet clip path if present.
        legacy = DEFAULT_OUT / "ballet" / f"{stem}.mp4"
        target = out / f"{stem}.mp4"
        if legacy.is_file() and not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            print(f"staged legacy clip {legacy} → {target}")
        video = download_youtube_segment(args.youtube_url, args.start, args.end, target)
    print("video", video)

    write_manifest(out / "manifest.json", {
        "youtube_url": args.youtube_url,
        "video_id": video_id,
        "start": args.start,
        "end": args.end,
        "video": str(video),
        "action_name": args.action_name,
        "smplx_target": str(smplx_target()),
        "gvhmr": gvhmr_ready(),
    })

    if args.no_gvhmr:
        print("stopped before GVHMR (--no-gvhmr). Status:")
        print(json.dumps(gvhmr_ready(), indent=2, default=str))
        return

    gvhmr_dir = out / "gvhmr_out"
    result = run_gvhmr(video, gvhmr_dir, static_cam=args.static_cam)
    csv_path = out / "q1_leg.csv"
    import_and_retarget(result if result.is_dir() else result.parent, csv_path)
    print("CSV", csv_path)
    write_manifest(out / "manifest.json", {
        "youtube_url": args.youtube_url,
        "video_id": video_id,
        "start": args.start,
        "end": args.end,
        "video": str(video),
        "gvhmr_out": str(result),
        "csv": str(csv_path),
        "action_name": args.action_name,
    })
    if args.import_store:
        motion = import_store(csv_path, action_name=args.action_name, store_url=args.store_url)
        print("STORE", json.dumps(motion, ensure_ascii=False))


if __name__ == "__main__":
    main()
