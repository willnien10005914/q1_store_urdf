"""Kinematic URDF/MJCF preview of HHTools / BeyondMimic references (no PPO).

Renders ``reference.json`` (or a store motion converted to the same layout) with
MuJoCo so operators can check retarget positioning before training.
"""
from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path
from typing import Any

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

STORE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MJCF = Path("/home/will/projects/q1_leg_urdf/urdf/leg_humanoid.xml")
PREVIEW_DIR = STORE_ROOT / ".data" / "roboparty" / "previews"


def _load_trajectory(payload: dict[str, Any]) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return joint_names, times, q, root xyz, root_quat xyzw."""
    if "time" in payload and "q" in payload and "joint_names" in payload:
        names = list(payload["joint_names"])
        times = np.asarray(payload["time"], dtype=np.float64)
        q = np.asarray(payload["q"], dtype=np.float64)
        root = np.asarray(payload["root"], dtype=np.float64)
        quat = np.asarray(payload.get("root_quat") or [[0, 0, 0, 1]] * len(times), dtype=np.float64)
        return names, times, q, root, quat

    frames = payload.get("frames") or []
    names = list(payload.get("joint_names") or [])
    if len(frames) < 2 or not names:
        raise ValueError("預覽需要 reference 或含 frames 的 motion。")
    times = np.asarray([float(f["time"]) for f in frames], dtype=np.float64)
    q = np.asarray([f["q"] for f in frames], dtype=np.float64)
    root = np.asarray([
        (f.get("root") or [0.0, 0.0, 0.88])[:3] for f in frames
    ], dtype=np.float64)
    # Motion CSV often keeps human-scale Z; clamp for preview standing height.
    zs = root[:, 2]
    if float(np.median(zs)) > 1.2:
        root = root.copy()
        root[:, 2] = np.clip(0.88 + (zs - np.median(zs)) * 0.15, 0.72, 1.05)
    quat = np.asarray([
        (f.get("root_quat") or f.get("quaternion") or [0.0, 0.0, 0.0, 1.0])[:4]
        for f in frames
    ], dtype=np.float64)
    return names, times, q, root, quat


def _sample_at(times: np.ndarray, rows: np.ndarray, t: float) -> np.ndarray:
    if t <= times[0]:
        return rows[0]
    if t >= times[-1]:
        return rows[-1]
    i = int(np.searchsorted(times, t, side="right") - 1)
    i = max(0, min(i, len(times) - 2))
    w = (t - times[i]) / max(times[i + 1] - times[i], 1e-9)
    return rows[i] * (1.0 - w) + rows[i + 1] * w


def render_reference_video(
    payload: dict[str, Any],
    out_path: Path,
    *,
    mjcf: Path | None = None,
    width: int = 960,
    height: int = 720,
    fps: float = 30.0,
    camera_distance: float = 2.8,
) -> dict[str, Any]:
    """Write an MP4 of kinematic playback; returns metadata."""
    names, times, qs, roots, quats = _load_trajectory(payload)
    duration = float(times[-1] - times[0])
    if duration < 0.2:
        raise ValueError("片段太短，無法預覽。")

    mjcf_path = Path(mjcf or DEFAULT_MJCF).resolve()
    if not mjcf_path.is_file():
        raise FileNotFoundError(f"找不到 MJCF：{mjcf_path}")

    cwd = Path.cwd()
    os.chdir(mjcf_path.parent)
    try:
        spec = mujoco.MjSpec.from_file(str(mjcf_path))
        model = spec.compile()
        model.vis.global_.offwidth = max(int(model.vis.global_.offwidth), int(width))
        model.vis.global_.offheight = max(int(model.vis.global_.offheight), int(height))
        data = mujoco.MjData(model)
    finally:
        os.chdir(cwd)

    name_to_qadr: dict[str, int] = {}
    for j in range(model.njnt):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j)
        if name and name != "root":
            name_to_qadr[name] = int(model.jnt_qposadr[j])
    missing = [n for n in names if n not in name_to_qadr]
    mapped = [n for n in names if n in name_to_qadr]
    if len(mapped) < 4:
        raise ValueError(f"關節對不到 MJCF（missing={missing[:8]}…）。")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = max(2, int(math.ceil(duration * fps)) + 1)
    camera = mujoco.MjvCamera()
    mujoco.mjv_defaultFreeCamera(model, camera)
    camera.distance = camera_distance
    camera.elevation = -12.0

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as error:
        raise RuntimeError("需要 ffmpeg 才能輸出預覽影片。") from error

    font = ImageFont.load_default()
    try:
        with mujoco.Renderer(model, height, width) as renderer:
            for i in range(n_frames):
                t = times[0] + i / fps
                if t > times[-1]:
                    t = times[-1]
                q_row = _sample_at(times, qs, t)
                root = _sample_at(times, roots, t)
                quat = _sample_at(times, quats, t)  # xyzw
                # MuJoCo freejoint: x y z qw qx qy qz
                data.qpos[:] = 0.0
                data.qpos[0:3] = root
                data.qpos[3] = quat[3]
                data.qpos[4:7] = quat[0:3]
                for name, value in zip(names, q_row):
                    adr = name_to_qadr.get(name)
                    if adr is not None:
                        data.qpos[adr] = float(value)
                data.qvel[:] = 0.0
                mujoco.mj_forward(model, data)

                yaw = 0.35 + 0.15 * math.sin(2 * math.pi * (t - times[0]) / max(duration, 1e-6))
                camera.lookat[:] = [float(root[0]), float(root[1]), float(np.clip(root[2], 0.7, 1.05))]
                camera.azimuth = 180.0 + math.degrees(yaw)
                renderer.update_scene(data, camera=camera)
                frame = renderer.render()
                image = Image.fromarray(frame)
                draw = ImageDraw.Draw(image)
                label = f"HHTools retarget preview (no PPO)  t={t - times[0]:.2f}s"
                draw.text((16, 16), label, fill=(240, 240, 240), font=font)
                assert proc.stdin is not None
                proc.stdin.write(image.tobytes())
        assert proc.stdin is not None
        proc.stdin.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        code = proc.wait(timeout=120)
        if code != 0:
            raise RuntimeError(f"ffmpeg 失敗：{stderr[-500:].decode(errors='ignore')}")
    except Exception:
        if proc.poll() is None:
            proc.kill()
        raise

    return {
        "path": str(out_path),
        "url_name": out_path.name,
        "fps": fps,
        "frames": n_frames,
        "duration_s": round(duration, 3),
        "width": width,
        "height": height,
        "mapped_joints": len(mapped),
        "missing_joints": missing,
        "h264": True,
        "kind": "hhtools_kinematic_preview",
    }
