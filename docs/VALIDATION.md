# 驗證紀錄

2026-10-25，以 Chromium 與本機 Python API 實際操作。

- Python 標準函式庫後端：22 項 unittest 通過，涵蓋上傳、來源檢查、motion 限位／時間驗證、持久化、取消、HTTP 隔離、MP4 range 與零收款交易。
- 原生關節 fixture：左右肩軸、URDF 限位、遞迴 mimic、人物隔離、來源重設、單調時間與 q 一致性通過。
- 雙 viewer 同時載入：完整 Leg 30／Wheel 28 個 meshes，座標有限，原生可動關節為 29／27，各 23 個控制軸。
- 真實影片：MediaPipe 實際辨識一位人物，匯出 26 frames／29 joints／5.8735 秒，source=`mediapipe_pose_landmarker`、demo=false。動作經 API 保存／讀回，並建立維持 queued_for_worker 的工作。
- 功夫操作錄製：切換雙足／雙輪、選人物 2、RSL-RL＋Newton、6 小時預算＋巴黎、30 秒概念流程、歷史 PPO 影片播放、分享與模擬購買；無 JavaScript page errors。
- 純靜態版：完整 30 秒流程、分享、零收款模擬購買與 localStorage 重新載入保存通過；模型 30／28 meshes 完整載入，無 JavaScript page errors。
- 手機：390px viewport，document scrollWidth 為 390，無橫向溢出。
- 原生 URDF／58 個 STL 的 SHA-256 與所有引用路徑已驗證。GitHub 選定來源 refs 的最新原生模型與 snapshot 相符。

[操作 GIF](images/kungfu-workflow.gif) 來自真實瀏覽器錄影，以約 3.3 倍速度播放。
錄製腳本為 [scripts/record_demo.mjs](../scripts/record_demo.mjs)，不調快後端時鐘或偽造訓練結果。

本次沒有執行新的 PPO／Isaac Sim 地標 RTX 訓練錄影。真實 GPU worker、Q1 Lab 3 task adapter 與付款服務仍依 README 所列範圍待接入。

GitHub Actions 因現有 token 缺少 workflow scope，範本保存在 docs/github-workflows/，尚未啟用；本機驗證均已執行。

## 指定影片分段範例

- 兩支指定來源的五個短片、JPG、GIF 均已解碼／雜湊驗證，完整原片未納入 Git。
- H2-X：護架跨步 33–36 s、側身出拳 36–39 s、轉身踢腿 39–41.8 s。
- X2-N：斜坡 24–28 s、坡頂轉向 37–40.4 s。
- API 與瀏覽器：分段命名、多草稿、重載保存、機型匹配、0.5–60 s 邊界與原片時間碼通過。
- Leg `.25–2.5 s` 對應原片 `33.25–35.5 s`；Wheel `.2–3.4 s` 對應 `24.2–27.4 s`。工作保持等待 worker，沒有虛構 motion 或新模型。
- 靜態 API 亦保留時間碼、URDF hash／23 軸契約；程序化 motion 不會被標示為真實提取就緒。
- MediaPipe 實測會偵測 H2-X 背景人物；候選影格數不代表來源機器人辨識正確。指定機器人來源已改為先暫停、手動核對，未選取時不輸出關節。Fixture 驗證暫停、長時間檢視、選取後續播與 capture 邊界。
- [Leg 操作 GIF](images/leg-video-training.gif)、[Wheel 操作 GIF](images/wheel-video-training.gif) 皆由實際瀏覽器操作錄影產生，無 JavaScript page errors；呈現分段與訓練規劃 demo，成果仍為歷史 PPO 影片。
- 新分段介面於 390px 無橫向溢出；全自動機器人影片 → Q1 PPO 與新訓練權重仍未實作。

## 骨架、完整模型首幀與 Deploy

- README 操作圖改用新檔名 `leg-operation-retarget-deploy.gif`、`wheel-operation-retarget-deploy.gif`，避免沿用舊的載入畫面快取；原 `*-video-training.gif` 亦更新為同一份動畫。
- 兩份 GIF 都是 960×750、87 幀，首幀已有來源骨架與完整原生模型：Leg 30 meshes、Wheel 28 meshes，沒有模型載入提示。
- 各取 18 個來源時間碼，來源影片／骨架區域與 Q1 模型區域均各有 18 個不同畫面；不是只替換介面或靜態照片。
- 取幀略去初始下載及真實 30 秒 demo 的靜態等待，沒有修改工作時鐘或結果。骨架是有來源標示的人工校正 MediaPipe 33 點格式幾何參考，不是自動辨識驗收或新 PPO。
- 兩份操作都走完實際註冊 Q1、選取同機型目標與下載 ZIP。ZIP 包含 manifest、工作計畫、robot contract 與 motion；確認不含 `.pt`／`.onnx`／`.pth` 權重。
- 下載套件皆為 `preview_only`，`deployable=false`、`checkpoint_included=false`、`hardware_activation_allowed=false`、`device_acknowledged=false`；不會啟動實體機器人或宣稱已送達。
- 兩份完整操作皆 0 JavaScript page errors；註冊清單存在時，390px viewport 的 document scrollWidth 仍為 390。
- Python 後端回歸共 22 項通過，包含註冊／部署邊界；新增 reference、ZIP 及 registry 的 JavaScript 檢查由主流程驗證。
