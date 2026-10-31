# Visualizers and recording

In 2.x, “watching the sim” meant opening an Isaac Sim window. 3.0 splits that into three independent choices: **physics backend** (`physics=`), **renderer** (`renderer=`), and **visualizer** (`--viz`). Train with Newton headless while streaming Q1 pose to a browser on port 8081 via viser—even through Pinggy—something 2.x could not do cleanly.

## 1. Removed / renamed pieces

| 2.x | 3.0 |
| --- | --- |
| `--headless` | Removed; use `HEADLESS=1` or omit `--viz kit` |
| `AppLauncher(args)` | `launch_simulation(sim_cfg)` context manager |
| `ViewerCfg(eye=..., lookat=...)` | Concrete visualizer cfgs (`ViserVisualizerCfg`, etc.) on `sim_cfg.visualizer_cfgs` |
| `gym.wrappers.RecordVideo` + `render_mode="rgb_array"` | `VideoRecorderCfg(source="visualizer:kit", ...)` on `env_cfg.video_recorders` |
| `--video --video_length N` | `VideoRecorderCfg(video_length=N)` or CLI `--video` (still exists, new backend) |
| `--enable_cameras` | Driven by `renderer=` and sensor cfg |

> Base `VisualizerCfg` has **no** `kind=` argument. Pick viser/rerun/newton_gl/kit by importing the concrete cfg (`isaaclab_visualizers.*`). `VideoRecorderCfg` has no `.create()`—attach it to ManagerBased `video_recorders`.

```python
from isaaclab.envs import VideoRecorderCfg
from isaaclab_visualizers.viser import ViserVisualizerCfg
from isaaclab_visualizers.kit import KitVisualizerCfg

sim_cfg.visualizer_cfgs = [
    ViserVisualizerCfg(port=8081, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6), open_browser=False),
    # or KitVisualizerCfg(eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)),
]
# ManagerBasedRLEnvCfg only — InteractiveScene demos use rollout.json → store ffmpeg instead:
env_cfg.video_recorders = [
    VideoRecorderCfg(source="visualizer:kit", fps=30, video_length=600, output_dir="videos"),
]
```

## 2. Five visualizers

| `--viz` | Requires | Where you watch | Use case |
| --- | --- | --- | --- |
| `kit` | Isaac Sim | Kit window / Omniverse WebRTC | RTX quality, mp4 recording |
| `newton_rtx` | Isaac Sim (RTX) | Kit window | Newton physics + RTX rendering |
| `newton_gl` | Local OpenGL | Local window | Fastest debug window |
| `viser` | `pip install viser` | Browser `http://host:8081` | Remote, Pinggy, multi-viewer |
| `rerun` | `pip install rerun-sdk` | Rerun viewer / browser | Timeline of sensors and scalars |

You can attach several at once: `--viz viser rerun`.

## 3. Recording contract on this store

The worker runs on GPU; users sit behind Pinggy in a browser. We define two layers:

1. **rollout.json** (always): `{"fps": 30, "joint_names": [...], "quaternion_order": "xyzw", "frames": [{"t":0.0, "root_pos":[x,y,z], "root_quat":[x,y,z,w], "q":[...]}, ...]}`. The frontend replays with the existing Three.js URDF viewer—no GPU required.
2. **result.mp4** (when possible): ManagerBased + `viz=kit` can emit via `VideoRecorderCfg`; this lesson’s InteractiveScene demo and other viz modes use store-side `ffmpeg` on rollout (`scripts/rollout_to_mp4.py`; skipped if ffmpeg missing).

> Why not stream Isaac Sim WebRTC directly? Omniverse streaming needs TURN/STUN and extra ports—hard through a single Pinggy tunnel. viser needs one HTTP+WebSocket port; the store reverse-proxies `/viz/<run_id>/`. For RTX quality, record mp4.

## 4. What the example does

`example.py`:

1. Builds concrete visualizer cfgs from `viz`, assigns `sim_cfg.visualizer_cfgs` (`none` → headless / `HEADLESS=1`).
2. Animates Q1 with `motion` (`squat`: hip/knee sine; `wave_joints`: joint sweep; `wheel_spin`: constant wheel speed).
3. Writes root pose and joint angles each frame to `rollout.json` (mp4 for this demo comes from store ffmpeg).
4. Sets `viz_url` in `progress.json` so the store knows which port to proxy.

**Exercises**

- Set `viz` to `rerun` and explore the timeline.
- Change `fps` to 60—how large is rollout.json? Does Three.js replay stutter?
- Move camera `eye` to `(0, -3, 1)` for a side view.
- Advanced: in a ManagerBased env, `VideoRecorderCfg(video_length=...)` to record only the first 5 s.

## 5. Without Isaac Lab 3

If `Q1_ISAACLAB3_ROOT` is missing, the store runs the example’s **fallback**: kinematic rollout only (no physics). You can still learn the rollout/recording contract and frontend replay before the GPU worker is ready.
