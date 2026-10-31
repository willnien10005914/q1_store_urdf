# 視覺化器與錄影

2.x 的「看模擬」等於「開 Isaac Sim 視窗」。3.0 把它拆成三件獨立的事：**物理後端**（`physics=`）、**渲染器**（`renderer=`）、**視覺化器**（`--viz`）。訓練用 Newton 在無頭下跑，同時用 viser 開一個 8081 埠讓你在瀏覽器（甚至透過 Pinggy）看 Q1 的姿態，這在 2.x 是做不到的。

## 1. 被移除／改名的東西

| 2.x | 3.0 |
| --- | --- |
| `--headless` | 移除；用 `HEADLESS=1` 或不要帶 `--viz kit` |
| `AppLauncher(args)` | `launch_simulation(sim_cfg)` context manager |
| `ViewerCfg(eye=..., lookat=...)` | 具體 visualizer cfg（`ViserVisualizerCfg` 等）掛在 `sim_cfg.visualizer_cfgs` |
| `gym.wrappers.RecordVideo` + `render_mode="rgb_array"` | `VideoRecorderCfg(source="visualizer:kit", ...)` 掛在 `env_cfg.video_recorders` |
| `--video --video_length N` | `VideoRecorderCfg(video_length=N)` 或 CLI `--video`（仍存在，但底層已換） |
| `--enable_cameras` | 由 `renderer=` 與 sensor cfg 決定 |

> 注意：基底 `VisualizerCfg` **沒有** `kind=` 參數。要選 viser／rerun／newton_gl／kit 時，直接 import 對應的 concrete cfg（`isaaclab_visualizers.*`）。`VideoRecorderCfg` 也沒有 `.create()`，而是掛在 ManagerBased 環境的 `video_recorders`。

```python
from isaaclab.envs import VideoRecorderCfg
from isaaclab_visualizers.viser import ViserVisualizerCfg
from isaaclab_visualizers.kit import KitVisualizerCfg

sim_cfg.visualizer_cfgs = [
    ViserVisualizerCfg(port=8081, eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6), open_browser=False),
    # 或 KitVisualizerCfg(eye=(2.5, 2.5, 1.5), lookat=(0.0, 0.0, 0.6)),
]
# ManagerBasedRLEnvCfg only — InteractiveScene demos use rollout.json → store ffmpeg instead:
env_cfg.video_recorders = [
    VideoRecorderCfg(source="visualizer:kit", fps=30, video_length=600, output_dir="videos"),
]
```

## 2. 五種 visualizer

| `--viz` | 需要 | 在哪裡看 | 用途 |
| --- | --- | --- | --- |
| `kit` | Isaac Sim | Kit 視窗／Omniverse WebRTC | RTX 畫質、錄 mp4 |
| `newton_rtx` | Isaac Sim（RTX） | Kit 視窗 | Newton 物理 + RTX 渲染 |
| `newton_gl` | 本機 OpenGL | 本機視窗 | 最快的 debug 視窗 |
| `viser` | `pip install viser` | 瀏覽器 `http://host:8081` | 遠端、Pinggy、多人同時看 |
| `rerun` | `pip install rerun-sdk` | Rerun viewer／瀏覽器 | 時間軸、感測器與數值一起看 |

一次可以掛多個：`--viz viser rerun`。

## 3. 本站的錄影契約

Q1 store 的 worker 在 GPU 機器上跑，使用者在 Pinggy 另一端的瀏覽器。我們定義兩層輸出：

1. **rollout.json**（一定有）：`{"fps": 30, "joint_names": [...], "quaternion_order": "xyzw", "frames": [{"t":0.0, "root_pos":[x,y,z], "root_quat":[x,y,z,w], "q":[...]}, ...]}`。前端用既有的 Three.js URDF viewer 重播，不需要 GPU。
2. **result.mp4**（盡量有）：ManagerBased + `viz=kit` 時可由 `VideoRecorderCfg` 直出；本課 InteractiveScene demo 與其他 viz 由 store 端 `ffmpeg` 把 rollout 以離屏方式合成（`scripts/rollout_to_mp4.py`，若無 ffmpeg 則略過）。

> 為什麼不直接把 Isaac Sim 的 WebRTC 串出去？Omniverse 串流需要 TURN／STUN 與額外埠，Pinggy 單埠隧道不易通。viser 只要一個 HTTP+WebSocket 埠，store 會把 `/viz/<run_id>/` 反向代理到它，所以 Pinggy 下可直接看。要 RTX 畫質就錄 mp4。

## 4. 範例要做的事

右側 `example.py`：

1. 根據 `viz` 建立 concrete visualizer cfg 列表，寫入 `sim_cfg.visualizer_cfgs`（`none` 代表純無頭／`HEADLESS=1`）。
2. 讓 Q1 做 `motion`（`squat`：髖膝正弦；`wave_joints`：逐關節掃描；`wheel_spin`：輪子等速）。
3. 每幀把 root pose 與關節角寫進 `rollout.json`（本課 demo 的 mp4 由 store ffmpeg 合成）。
4. `progress.json` 裡的 `viz_url` 欄位告訴 store 要代理哪個埠。

**練習**

- 把 `viz` 改成 `rerun`，觀察 Rerun 瀏覽器時間軸。
- 把 `fps` 改成 60，rollout.json 大小變多少？Three.js 重播會掉幀嗎？
- 把相機 `eye` 改到 `(0, -3, 1)` 看側面。
- 進階：在 ManagerBased 環境用 `VideoRecorderCfg(video_length=...)` 只錄前 5 秒。

## 5. 沒有 Isaac Lab 3 時

store 若偵測不到 `Q1_ISAACLAB3_ROOT`，會以 `python3` 直接執行本範例的 **fallback 分支**：以純運動學的方式產生 rollout.json（沒有物理，只有關節軌跡）。這讓你在任何機器都能先學會 rollout／錄影契約與前端重播，等 GPU 機器就緒再切回真實模擬。
