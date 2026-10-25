# Q1 Store · 影片到動作的創作工作室

Q1 Store is an interactive concept demo for native Q1 biped and wheeled URDFs:
video pose tracking, geometric retargeting, training plans and a simulated motion
marketplace. It does not train a new PPO policy or charge payments.

沿用 [Q1 Leg](https://github.com/willnien10005914/q1_leg_urdf) 的 store 視覺，
整合 [Q1 Wheel](https://github.com/willnien10005914/q1_wheel_urdf)，在同一個工作室
選擇雙足／雙輪、影片、人物目標、RL／物理／reward、訓練時數與世界地標。

![Q1 Store 雙機型創作工作室](docs/images/store-home.png)

![功夫範例：選機型、彩色人物追蹤、關節映射、訓練設計與成果分享](docs/images/kungfu-workflow.gif)

[實際驗證紀錄](docs/VALIDATION.md) · [工作室截圖](docs/images/store-studio.png) · [成果頁截圖](docs/images/store-result.png)

## 現在能做什麼

| 功能 | 狀態與實際行為 |
| --- | --- |
| 雙足／雙輪原生 URDF | 可操作。載入各自 CAD meshes、關節名稱、限位與 mimic。可拖曳旋轉。 |
| 功夫範例 | 可操作。兩位程序化骨架示範彩色人物選取與即時關節映射；不是影片辨識，也不是新 PPO。 |
| 使用者影片 | 可操作。MP4／WebM／MOV 等瀏覽器可解碼影片，MediaPipe 在瀏覽器辨識最多 4 位人物；依顏色選取目標。 |
| 真實 retargeting | 可操作。人物姿態映射到所選原生關節，限制角度並匯出 JSON；是幾何參考，未做逆動力學、接觸或平衡驗收。 |
| YouTube | 可嵌入預覽。本機選裝 yt-dlp 可取得影片後辨識；iframe 本身不能提供跨來源影格。受限影片下載失敗會明示，不以範例替代辨識。 |
| RL／physics／rewards／時數 | 可選擇並保存工作規劃；Q1 的 Isaac Lab 3 task adapter 尚未接入。 |
| 地標舞台 | 可選台北 101、艾菲爾鐵塔、時代廣場、澀谷、萬里長城。3D 畫面是概念輪廓；尚無地標 USD／RTX 實錄。 |
| 30 秒 demo 流程 | 可操作。展示 retargeting → 訓練示意 → rendering 示意，完成後播放已標示來源的歷史 PPO 影片。 |
| 真實訓練模式 | 匯出工作 JSON，狀態維持等待外部 worker；不會自動開始訓練或變成成功。 |
| 分享／價格／收藏／購買 | 可操作的模擬市集。保存分享記錄與零收款收據；沒有真實付款或交付新權重。 |

**這次輸入的功夫／使用者影片不會在 30 秒內訓練成 PPO。** 示範成果也不是
所選地標的 Isaac Sim 新錄影。真正 worker 需要的銜接列於
[Isaac Lab 3 worker 設計](docs/ISAACLAB3_WORKER.md)。

## 本機啟動

需要 **Python 3.11+** 與支援 WebGL 的現代瀏覽器。基本網站／SQLite API 只用標準函式庫，
不需要安裝 Isaac Sim、CUDA 或 Python ML 套件。

```bash
git clone git@github.com:willnien10005914/q1_store_urdf.git
cd q1_store_urdf
bash scripts/start_store.sh --port 8876
# 或：python3 server.py --port 8876
```

開啟 **http://127.0.0.1:8876/**。服務預設只綁本機；要在區網展示可自行指定
`--host 0.0.0.0`。啟動腳本只執行網站服務，不啟動 GPU 訓練。
上傳影片、動作參考與工作／市集記錄保存在忽略追蹤的 `.data/`，
可用 `--data-dir /your/path` 或 `Q1_STORE_DATA` 指定其他位置。

第一次執行真實影片辨識需要網路下載 MediaPipe WASM 與 Pose Landmarker 模型；
Three.js、URDFLoader 與機器人 meshes 已包含在 repository。

### 選裝 YouTube 下載

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-motion.txt
Q1_YTDLP="$PWD/.venv/bin/yt-dlp" Q1_STORE_PYTHON=.venv/bin/python bash scripts/start_store.sh --port 8876
```

`yt-dlp` 必須在服務的 PATH，可另外設定 `Q1_YTDLP=/absolute/path/to/yt-dlp`。
依來源格式可能需要主機安裝 `ffmpeg`；YouTube extractor 也可能要求可用的 JS runtime。
下載限制、登入／地區與網站變更仍可能造成失敗，此時可上傳有權使用的影片。
YouTube 嵌入播放器只能預覽；要執行 MediaPipe，必須取得可讀影格的實際影片檔。

## 功夫示範操作

1. 在 Motion Studio 選擇 **雙足 Biped** 或 **雙輪 Wheeled**。
2. 載入 **功夫範例**，點選綠色或紫色人物 chip，觀察右側原生模型即時映射。
   要處理真實影片，改選 **上傳影片**，播放後按 **開始辨識**，再依顏色選取人物。
3. 可下載關節 JSON；資料包含機型、URDF 雜湊、人物與影格。
4. 選擇 **RSL-RL、skrl、RL-Games、Stable-Baselines3 或 TorchRL**，以及
   **Isaac Sim PhysX、Newton MuJoCo Warp 或 OV PhysX**。這些是規劃選項，
   實際支援組合需由註冊後的 Q1 task 驗證。
5. 選擇追蹤、位置、平衡、能耗、平滑等 reward。雙足可選足部接觸，雙輪可選輪速追蹤。
   它們是 Q1 任務設計預設，不是 Isaac Lab 所有任務共用的現成 reward 清單。
6. 設定 **1–24 小時**訓練預算、選擇地標，再開始 **30 秒概念 demo**。
   時數是預算設定，不是成果保證；**匯出真實訓練計畫**只建立待 worker 的工作。
7. 完成後觀看對應機型的歷史參考影片、下載工作 JSON、分享至市集並模擬收藏／購買。

單鏡頭映射沒有真實尺度與完整遮擋資訊；人物交叉、遮擋或離開畫面可能造成追蹤中斷。
此處的色彩是追蹤 ID，不代表衣服辨識。幾何預覽不可直接當作實機控制策略。

## 已有的真實訓練影片

| 機型 | 包含的歷史成果 | 證據與相容性 |
| --- | --- | --- |
| Leg | [原生閃身步 PPO，7.78 秒](assets/examples/leg/sidestep.mp4)；6,144 envs × 8,000 updates，選定 model_7800 | [來源證據](assets/examples/leg/evidence.json)。獨立模擬評估 31/32 回合存活；目前 URDF 雜湊相符，尚未實機驗證。 |
| Wheel | [歷史滑行 PPO，3,800 updates](assets/examples/wheel/skate.mp4) | [來源證據](assets/examples/wheel/evidence.json)。舊版 **24-action／92-observation**，與目前原生 **23-action** wheel 模型**不相容**；保留作歷史商品範例。 |

影片、MediaPipe 關節參考與 PPO 是不同資料。Leg 的
[MediaPipe 參考](assets/examples/leg/sidestep_motion.json) 來源為
[原始示範前 7 秒](https://youtu.be/KKhF516rSyo)，包含 306/306 有效 source frames 與 20 Hz 原生關節映射。
原始人物影片及 MediaPipe 模型未納入 Git；本 store 也未把這段參考宣稱為功夫新策略。

## URDF 來源與同步

| 機型 | 來源 snapshot | 原生 URDF SHA-256 |
| --- | --- | --- |
| Leg | [568b332](https://github.com/willnien10005914/q1_leg_urdf/tree/568b33229e31cc503b6729d1185c625d35a711a4)，`spark/isaaclab3-balance-6144-15000` | `2a9a5fce0cd4cf116ba22fcc5c2dfc90747e16c7c1c1ccd0914beec61ce7b3ac` |
| Wheel | [358c668](https://github.com/willnien10005914/q1_wheel_urdf/tree/358c668e27a790b572fc18ca0bab63ce5f8ad9fa) | `0a6c9219bf0d7ade77c3a52b1d7334232ee5adc23528f8afe86679bfc2e794e9` |

[manifest.json](assets/robots/manifest.json) 是實際檔案的版本依據：記錄 source commit、
URDF／各 STL 雜湊、關節軸向與限位、mimic、控制順序、模型質量與同步時間。
兩機型各 23 個控制軸；Leg 另有 6 個鎖定關節，Wheel 另有 4 個 mimic 關節。
2026-10-25 檢查 GitHub wheel `main` 的 `207d7cb4` 時，其原生 URDF 與 28 個
引用 STL 均與上述 snapshot 相同。Store 保留原始來源 snapshot，不修改 `~/will` 的訓練工作。

```bash
# 預設：只讀取本機來源，更新 store 內的 snapshot。
python3 scripts/sync_robots.py --source-root ~/will

# 明確選用：先更新兩來源各自已設定的 upstream，僅允許乾淨工作樹及 fast-forward。
python3 scripts/sync_robots.py --source-root ~/will --pull
```

`--pull` 跟隨來源 checkout 的 upstream，並非固定 `main`；任一來源含未追蹤或修改檔案就會拒絕。
目前 wheel 來源的本機 checkpoint 不應為了同步而刪除，可改用獨立乾淨 clone。
更新模型後仍需重新驗證 policy 的關節與 observation／action 契約。

[GitHub 同步 workflow 範本](docs/github-workflows/sync-robots.yml) 設計為啟用後每日 **03:23 UTC** 執行，
也可手動指定 `leg_ref`／`wheel_ref`（branch、tag 或 commit）。預設 Leg 使用上述 Spark 分支，
Wheel 使用 `main`。它在 runner 的暫存目錄建立乾淨 sparse checkouts，將當次解析的
**確切 commit 與雜湊**寫入 manifest，只在內容或來源 commit 改變時提交 store 的資產快照。
它不執行來源的 Python 訓練程式，不修改原始專案，也不自動發布新 policy。
目前 GitHub 登入 token 缺少 `workflow` scope，因此 Actions 範本保留在 `docs/github-workflows/`，**尚未啟用每日排程或 GitHub CI**。
要啟用時，使用可管理 workflows 的登入，將兩個 YAML 複製到 `.github/workflows/` 後提交推送。
本機同步指令與本次原生資產 snapshot 均可直接使用。
工作流程執行時需要 repository 的 Actions `contents: write`；branch protection 若禁止 bot 推送，
同步會失敗並保留 log，不會略過保護。只改 `synced_at` 不會產生每日空更新。

詳細資產及授權資訊：[ROBOT_SOURCES.md](docs/ROBOT_SOURCES.md)。

## 靜態展示與檢查

```bash
python3 scripts/build_static.py
python3 -m http.server 8877 --bind 127.0.0.1 --directory dist
# http://127.0.0.1:8877/

python3 -m unittest discover -s tests -v
```

靜態版會清楚顯示 **瀏覽器 Demo**：工作、關節與市集記錄保存在該瀏覽器 localStorage，
本機影片使用 blob URL 預覽，不上傳伺服器，重新整理後需重新選檔；YouTube 只有嵌入預覽。
多人辨識首次仍需載入 MediaPipe 模型，且大量 motion frames 可能受到瀏覽器儲存配額限制。
Python 版使用 SQLite 與實際影片上傳，適合完整本機展示。
`dist/` 僅包含 `public/` 與公開範例／模型，不包含 `.data/`、source checkout 或上傳影片。
前端使用 `/assets/...` 等根路徑，靜態內容應部署在網域根目錄；未設定 GitHub Pages 專案子路徑。

[CI 範本](docs/github-workflows/ci.yml) 啟用後執行標準函式庫 unittest、Node 的瀏覽器模組語法檢查、
URDF／mesh 雜湊檢查與靜態建置。原生關節映射另有 `node tests/test_retarget.mjs`；完整瀏覽器流程可在安裝 Playwright 後執行 `npm run test:browser`。
真正 Isaac Lab 訓練、跨引擎品質、地標 RTX 錄影及
付款服務仍是外部 worker／場景／支付整合工作。
