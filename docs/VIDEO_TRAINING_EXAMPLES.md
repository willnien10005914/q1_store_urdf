# 用指定影片分段建立 Q1 Leg／Wheel 訓練計畫

本教學把一支來源影片切成多個可獨立命名與設定的動作。
網站能播放實際來源短片、預覽分段、嘗試姿態辨識、保存工作與匯出設定。
30 秒 demo 用來走完流程，成果頁播放既有 PPO 參考影片；不會在這 30 秒訓練出新的功夫或輪式策略。
全自動機器人影片 → Q1 PPO 尚未實作，本次沒有產生新的權重。

## 來源與已準備的片段

| 目標機型 | 來源影片／發布者 | 原片長度 |
| --- | --- | --- |
| Q1 Leg 雙足 | [Unitree Introducing: H2-X Kung Fu Legend Guardian](https://youtu.be/Y3zb4o47yqA)／Unitree Robotics | 61 秒 |
| Q1 Wheel 雙輪 | [Meet China's AgiBot X2-N, a shape-shifting robot](https://youtu.be/TV6ctYnA9Io)／China SCIO | 59 秒 |

這些是他款機器人的來源畫面，並非 Q1 的實機或模擬成果。
repository 只包含人工選取的靜音短片、縮圖與 GIF；完整原片和下載器 metadata 不包含在 Git。
[來源 catalog](../assets/video-examples/catalog.json) 記錄原 URL、片段 ID、起訖時間與影片 SHA-256。

| 片段 ID | 動作參考 | 原片時間 | 播放短片時間 | GIF |
| --- | --- | --- | --- | --- |
| `h2x-guard` | 護架與跨步 | 33–36 s | 0–3 s | [GIF](../assets/video-examples/leg/h2x-guard.gif) |
| `h2x-punch` | 側身出拳 | 36–39 s | 0–3 s | [GIF](../assets/video-examples/leg/h2x-punch.gif) |
| `h2x-turn-kick` | 轉身踢腿 | 39–41.8 s | 0–2.8 s | [GIF](../assets/video-examples/leg/h2x-turn-kick.gif) |
| `x2-slope` | 斜坡通行 | 24–28 s | 0–4 s | [GIF](../assets/video-examples/wheel/x2-slope.gif) |
| `x2-turn` | 坡頂轉向 | 37–40.4 s | 0–3.4 s | [GIF](../assets/video-examples/wheel/x2-turn.gif) |

## Q1 Leg：把功夫拆成三個動作

![H2-X 影片到 Q1 Leg 分段工作操作](images/leg-video-training.gif)

1. 啟動 `python3 server.py --port 8876`，開啟 `http://127.0.0.1:8876/`。
2. 在 Motion Studio 選 **雙足 Biped → 影片範例 → H2-X · 護架與跨步**。
   來源影片可以直接播放，這個已附短片不需再次下載 YouTube。
3. 起點／終點預設為短片 `0–3 s`。可再選 `0.4–2.6 s`；畫面會顯示原片
   `33.4–35.6 s`。也可將播放器目前畫面設成起點或終點。
4. 按 **預覽這一段**，必要時勾選 **循環預覽**。確認動作開始、結束與轉場，
   將動作名稱改成 `Q1 Leg · 護架跨步 v1`，按 **加入動作清單**。
5. 分別載入 **側身出拳**、**轉身踢腿**，各自調整片段、命名與保存。
   這樣同一支功夫影片就有三個可分開處理的參考動作。
6. 從 **我的動作片段** 選一個草稿，按 **繼續設定**。需要實際 capture 時先回到
   **影片與目標**，按 **開始辨識**。這兩支機器人來源會先顯示彩色**候選**，不自動選取，
   也不會在未選取時輸出關節。確認候選確實對應來源機器人後才點選；若只有背景人物，
   請保留未選取並建立參考工作，待機器人 tracker 或人工標記補足。
   取得可用資料後可按 **下載關節 JSON**；換來源、時間範圍或人物會清除舊 capture，需重新辨識。
7. 設定 RL library、physics preset、reward 與時數。例如先規劃
   `RSL-RL / Newton MuJoCo Warp / 動作追蹤＋平衡＋平滑 / 4 小時`。
   這些是工作設定；實際 Q1 task 的支援組合與 reward 權重仍需 worker 驗證。
8. 選擇地標，然後選 **30 秒概念 demo** 或 **匯出真實訓練計畫**，開始片段流程。
   Demo 完成後的閃身影片來自既有 Leg PPO，並非這次功夫片段的新訓練結果。

草稿保存的是機型、來源、時間與名稱；它不保存訓練好的模型，也不自動執行批次訓練。
可對不同片段建立各自的工作與 reward／引擎／時數設定。

## Q1 Wheel：用 X2-N 規劃斜坡與轉向

![X2-N 影片到 Q1 Wheel 分段工作操作](images/wheel-video-training.gif)

1. 選 **雙輪 Wheeled → 影片範例 → X2-N · 斜坡通行**。
2. 預設短片 `0–4 s` 對應原片 `24–28 s`。例如再選 `0.5–3.5 s`，
   原片時間會是 `24.5–27.5 s`；命名 `Q1 Wheel · 斜坡通行 v1`，加入動作清單。
3. 再選 **坡頂轉向**，預覽原片 `37–40.4 s` 的短片，另存為 `Q1 Wheel · 坡頂轉向 v1`。
4. 針對各動作設定規劃，例如輪速追蹤、平衡與平滑 reward，再選 physics、時數與地標。
5. 需要真實訓練時匯出工作 JSON。worker 必須先補足輪速／車身運動／接觸 reference，
   註冊並驗證目前原生 23 軸 Q1 Wheel task，再接受 PPO 訓練。

人體姿態模型無法直接給出輪速、滑動、接觸力或可靠世界座標。
不能將 X2-N 的動作或人類足部軌跡直接載入 Q1 輪式控制器，也不能沿用不相容的舊 24 軸 wheel PPO。
目前結果頁的 wheel 滑行影片保留「歷史 24 軸版本」來源標示。

## 時間、動作名稱與工作 JSON

時間欄位採目前可播放短片的相對秒數，必須滿足 `0 <= start_s < end_s`，
片段長度為 `0.5–60 s`，且不能超出影片。內附短片會再保存其原 YouTube 的絕對秒數。

例如對 `h2x-guard` 再切 `0.4–2.6 s`，Python API 工作會保存：

```json
{
  "robot_type": "leg",
  "action_name": "Q1 Leg · 護架跨步 v1",
  "source": {
    "type": "video_example",
    "id": "h2x-guard",
    "source_video_id": "Y3zb4o47yqA",
    "source_start_s": 33,
    "source_end_s": 36
  },
  "segment": {
    "start_s": 0.4,
    "end_s": 2.6,
    "source_start_s": 33.4,
    "source_end_s": 35.6,
    "timebase": "playable_video_seconds",
    "source_timebase": "original_video_seconds"
  }
}
```

這是欄位節錄，完整工作還包含 source URL、選定人物、URDF 版本、RL／physics／reward、預算與地標。
本機工作、motion 與市集記錄存在忽略追蹤的 `.data/` SQLite；動作草稿存在此瀏覽器的 localStorage。
純靜態模式的工作也存在 localStorage，worker 無法直接讀取它或使用者的 blob URL。

計畫的 `motion_extraction_ready=false` 表示尚無非程序化 capture，可保留來源與片段交給 worker 提取。
為 `true` 只表示已附姿態資料，不證明選對機器人、遮擋處理正確或物理可行。
實際可用 reference、Q1 task adapter 與驗收條件仍需依
[Isaac Lab 3 worker 設計](ISAACLAB3_WORKER.md) 完成。

## 機器人影片的姿態核對

MediaPipe Pose Landmarker 是人體姿態模型，H2-X 實測曾將背景人物列為候選。
候選數或 capture 影格數不能當作機器人辨識準確率；看到骨架也不表示目標是 H2-X／X2-N。
兩支機器人來源啟用 `requireTargetSelection=true`：預設不選任何候選，未選取時不輸出關節。
使用者必須先核對來源機器人，再明確點選候選；若只有背景人物，請保持未選取。
此時右側 Q1 保持站姿幾何展示，來源片段仍可建立待提取的參考工作，交由機器人專用 tracker 或人工標記處理。

來源範例均保留 `requires_manual_review=true`、`mediapipe_human_model_validated=false`。
當辨識不到目標時，網站不以程序化功夫骨架替代，也不把背景人物的動作視為機器人參考。
可先建立有原片時間碼的參考工作，待 worker 補完提取與驗證。

## 重新產生來源短片與 GIF

需要 Python 3.11+、ffmpeg；重新下載另需 `requirements-motion.txt` 的 yt-dlp 與可用的 Node runtime。
下載原片的 cache 應在 Git 之外，預設 `runtime/` 已忽略追蹤。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-motion.txt

# 缺少原片時才下載；只輸出選定的五段靜音 MP4、JPG、GIF 與最小來源資料。
python3 scripts/prepare_video_examples.py --download \
  --yt-dlp "$PWD/.venv/bin/yt-dlp" \
  --cache-dir /tmp/q1-store-youtube-examples

# 已有原片快取：不重新下載。
python3 scripts/prepare_video_examples.py --cache-dir /tmp/q1-store-youtube-examples

# 全部曾建置後，可只重建其中一段。
python3 scripts/prepare_video_examples.py --cache-dir /tmp/q1-store-youtube-examples --only h2x-punch
```

下載器只接受這兩個指定來源並保留來源連結；完整下載器 metadata 留在 cache，
repository 中的 catalog 不含臨時串流網址或下載憑證。原片水印與來源標示保留，剪輯沒有原始音訊。
