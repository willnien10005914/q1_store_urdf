# 驗證紀錄

2026-10-25，以 Chromium 與本機 Python API 實際操作。

- Python 標準函式庫後端：13 項 unittest 通過，涵蓋上傳、來源檢查、motion 限位／時間驗證、持久化、取消、HTTP 隔離、MP4 range 與零收款交易。
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
