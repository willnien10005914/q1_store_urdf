#!/usr/bin/env python3
"""Build short, attributed reference excerpts from the two user-selected videos.

Original videos and full yt-dlp metadata remain in a separate cache directory.
Only selected silent excerpts, posters, GIFs and minimal provenance are shipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/video-examples"
SOURCES = [
    {"id": "Y3zb4o47yqA", "robot_type": "leg", "url": "https://youtu.be/Y3zb4o47yqA",
     "title": "Unitree Introducing: H2-X Kung Fu Legend Guardian", "duration_s": 61,
     "uploader": "Unitree Robotics"},
    {"id": "TV6ctYnA9Io", "robot_type": "wheel", "url": "https://youtu.be/TV6ctYnA9Io",
     "title": "Meet China's AgiBot X2-N, a shape-shifting robot", "duration_s": 59,
     "uploader": "China SCIO"},
]
SEGMENTS = [
    {"id": "h2x-guard", "source_video_id": "Y3zb4o47yqA", "title": "H2-X · 護架與跨步",
     "start_s": 33, "end_s": 36,
     "description": "原片 00:33–00:36 的護架、抬腿與跨步參考；用於分段建立雙足動作工作。"},
    {"id": "h2x-punch", "source_video_id": "Y3zb4o47yqA", "title": "H2-X · 側身出拳",
     "start_s": 36, "end_s": 39,
     "description": "原片 00:36–00:39 的側身與手臂動作參考；須檢查背景人物與前景遮擋。"},
    {"id": "h2x-turn-kick", "source_video_id": "Y3zb4o47yqA", "title": "H2-X · 轉身踢腿",
     "start_s": 39, "end_s": 41.8,
     "description": "原片 00:39–00:41.8 的轉身踢腿參考；分段在日景轉場前結束。"},
    {"id": "x2-slope", "source_video_id": "TV6ctYnA9Io", "title": "X2-N · 斜坡通行",
     "start_s": 24, "end_s": 28,
     "description": "原片 00:24–00:28 的輪式斜坡通行參考；輪速與接觸需另行重建。"},
    {"id": "x2-turn", "source_video_id": "TV6ctYnA9Io", "title": "X2-N · 坡頂轉向",
     "start_s": 37, "end_s": 40.4,
     "description": "原片 00:37–00:40.4 的坡頂轉向參考；不等同 Q1 wheel 已學會此動作。"},
]


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def media_path(cache: Path, identifier: str) -> Path | None:
    return next((path for suffix in ("mp4", "webm", "mkv")
                 if (path := cache / f"{identifier}.{suffix}").is_file()), None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/video-example-sources")
    parser.add_argument("--download", action="store_true", help="Download missing originals using yt-dlp")
    parser.add_argument("--yt-dlp", help="Path to yt-dlp (otherwise use PATH)")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--only", choices=[segment["id"] for segment in SEGMENTS], help="Rebuild one excerpt")
    args = parser.parse_args()
    cache = args.cache_dir.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    downloader = args.yt_dlp or shutil.which("yt-dlp")
    metadata = {}
    for source in SOURCES:
        identifier = source["id"]
        if media_path(cache, identifier) is None and args.download:
            if not downloader:
                parser.error("yt-dlp is not on PATH; install requirements-motion.txt or pass --yt-dlp")
            command = [downloader, "--ignore-config", "--no-exec", "--no-playlist", "--no-cache-dir",
                       "--no-warnings", "--socket-timeout", "20", "--retries", "1", "--js-runtimes", "node",
                       "--format", "best[height<=720][ext=mp4]/best[height<=720]", "--write-info-json",
                       "--no-write-playlist-metafiles", "--output", str(cache / "%(id)s.%(ext)s"), source["url"]]
            run(command)
        video = media_path(cache, identifier)
        if video is None:
            parser.error(f"Missing source {identifier}; add --download or use an existing --cache-dir")
        information = cache / f"{identifier}.info.json"
        raw = json.loads(information.read_text()) if information.is_file() else {}
        metadata[identifier] = {**source, "title": raw.get("title", source["title"]),
                                "duration_s": raw.get("duration", source["duration_s"]),
                                "uploader": raw.get("uploader", source["uploader"]),
                                "available": True, "source_sha256": sha256(video),
                                "original_in_repository": False, "scope": "robot_video_reference"}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    examples = []
    for segment in SEGMENTS:
        identifier = segment["id"]
        source = metadata[segment["source_video_id"]]
        start, end = segment["start_s"], segment["end_s"]
        folder = OUTPUT / source["robot_type"]
        folder.mkdir(exist_ok=True)
        video, poster, gif = (folder / f"{identifier}.{suffix}" for suffix in ("mp4", "jpg", "gif"))
        if args.only in (None, identifier):
            original = media_path(cache, source["id"])
            run([args.ffmpeg, "-y", "-ss", str(start), "-i", str(original), "-t", str(end-start),
                 "-an", "-vf", "scale=640:-2,fps=25", "-c:v", "libx264", "-preset", "medium", "-crf", "25",
                 "-movflags", "+faststart", str(video), "-loglevel", "error"])
            run([args.ffmpeg, "-y", "-ss", "0.6", "-i", str(video), "-frames:v", "1", str(poster), "-loglevel", "error"])
            run([args.ffmpeg, "-y", "-i", str(video), "-vf",
                 "fps=8,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
                 "-loop", "0", str(gif), "-loglevel", "error"])
        if not all(path.is_file() for path in (video, poster, gif)):
            parser.error(f"Missing prepared assets for {identifier}; rebuild without --only first")
        public = "/assets/video-examples/" + source["robot_type"] + "/" + identifier
        examples.append({**segment, "robot_type": source["robot_type"], "source_url": source["url"],
                         "source_title": source["title"], "duration_s": end-start,
                         "video_url": public + ".mp4", "poster_url": public + ".jpg", "gif_url": public + ".gif",
                         "tracking_scope": "robot_video_reference", "requires_manual_review": True,
                         "mediapipe_human_model_validated": False, "newly_trained": False,
                         "audio_removed": True, "video_sha256": sha256(video),
                         "note": "來源為他款機器人的剪輯影片；人體姿態模型可能漏檢或誤選背景人物。此片段不是 Q1 訓練成果或實機驗證。"})
        print(f"{identifier}: {start:g}–{end:g}s → {video.relative_to(ROOT)}")
    catalog = {"schema": 1, "sources": list(metadata.values()), "examples": examples,
               "scope": "Attributed source excerpts for segment selection and Q1 training plans; not newly trained Q1 policies."}
    (OUTPUT / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    print(f"Built {len(examples)} reference excerpts in {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
