# Isaac Lab 3 worker 銜接設計

目前 Q1 Store 執行網站、影片儲存、瀏覽器 MediaPipe 與工作記錄。
選擇真實訓練模式只產生可下載的工作計畫；`worker_available=false`、
`task_registered=false`，工作維持 `queued_for_worker`。30 秒 demo 的成果是
歷史影片，`newly_trained=false`、`landmark_rendered=false`，不會偽造 checkpoint。

## 固定版本與執行環境

新 worker 應使用隔離的 Isaac Lab **v3.0.0** 環境，避免直接替換既有 Lab 2.x 訓練環境。
官方 release 對應 commit `e3f7d9ff1f5d7f8280de3ece9000b7117f66bcf4`，並列出
Python 3.12、Isaac Sim 6.2、PyTorch 2.11／CUDA 13、Newton 1.6.1 與 RSL-RL 5.5.1。
以實際來源 commit、套件 lock 與 GPU driver 記錄 worker，而非只用版本顯示字串。
[官方 v3.0.0 release](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0)。

以下是官方 Cartpole 的安裝後自我檢查範例，**不會訓練 Q1**：

```bash
git clone --branch v3.0.0 --depth 1 https://github.com/isaac-sim/IsaacLab.git
cd IsaacLab
uv run isaaclab list_envs --show_presets
uv run isaaclab train --rl_library rsl_rl --task Isaac-Cartpole physics=newton_mjwarp
```

Isaac Lab 3 將 physics、renderer、visualizer 分開選擇；可用組合仍取決於 task。
例如 `physics=isaacsim_physx`、`physics=newton_mjwarp`、`physics=ovphysx`，
不表示同一 Q1 checkpoint 可任意互換。先查該 task 的 presets 與 RL library 支援。
[官方後端說明與查詢指令](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0#one-environment-multiple-backends)。

## 工作資料契約

本機 API 的 `GET /api/jobs/<id>/plan` 回傳規劃 JSON，包含以下資訊；
內附 train／play command 是尚待完成 task adapter 的規劃，不應直接當作可執行交付。

| 資料 | Worker 需要保留／驗證的內容 |
| --- | --- |
| 機型 | `robot_type`，對應 manifest 原生 URDF 路徑、SHA-256 與 23 軸控制順序 |
| 來源 | YouTube canonical URL 或本機已保存的 upload ID；無法取得影片應失敗，不換成範例 |
| 人物 | `target_person` 追蹤 ID／顏色；它是 session 的追蹤識別，不是永久人物身分 |
| 動作 | 工作引用的 `motion_id`／動作 URL；關節名稱、q、時間、信心值與幾何映射範圍 |
| 設定 | RL library、physics preset、rewards、`budget_hours` 與 landmark |
| 可執行性 | task 是否完成註冊、來源與資產是否存在、動作及 checkpoint 是否相容 |

`POST /api/motions` 會驗證原生 URDF 的關節名稱、位置限位、有限數值與嚴格遞增的時間。
這只能證明資料符合幾何格式，**不證明物理可行或策略已學會**。
靜態版的工作 JSON 存在瀏覽器中，外部 worker 無法直接讀取使用者的 localStorage 或 blob URL；
交付時需另外上傳 motion JSON 與有權使用的實際影片，並建立可讀的 artifact URI。

## Q1 task adapter

目前計畫中的 `Q1-Leg-Motion-Tracking-v0` 與 `Q1-Wheel-Motion-Tracking-v0`
尚未在此 repository 註冊。真正 worker 須完成以下工作後才接受訓練：

1. 匯入對應原生模型，驗證 STL 尺度、質量／慣量、碰撞、摩擦、joint axes／limits、
   root pose 與致動器。Leg 的額外 wrist／finger 關節維持固定控制語意；Wheel 的 knee／finger
   mimic 必須依 source multiplier 執行，不能增加或重排 PPO 控制通道。
2. 由指定人物建立單調時間的 motion reference，處理缺失／低信心影格與骨架尺度。
   目前瀏覽器映射沒有可靠 root trajectory、接觸、動力學與尺度校準；Wheel 的人類足部動作
   必須轉成可學的姿態／輪速命令，不能直接當作可行輪式軌跡。
3. 固定 observation、action order、normalization、control／physics dt、扭矩／速度限制。
   Leg 與 Wheel 權重不互換；舊 wheel 24 軸權重不能載入目前 23 軸 task。
4. 將 store 的 reward ID 映射為該 task 的 reward terms 與明確權重，並固定 termination／reset。
   `energy` 應先定義為 torque／power proxy，沒有電流模型時不能稱為量測電池耗電。
5. 註冊所選 library 的 agent config；以相同模型／場景做指定 physics preset 的短程驗證。
   更新引擎後重新評估策略，不把不同版本的 checkpoint 或歷史片段混用。

Lab 2 → 3 遷移須檢查新的啟動流程、ProxyArray 存取及 quaternion 順序；
3.0 的 quaternion 使用 XYZW，舊 2.x 紀錄可能是 WXYZ。
[官方遷移摘要](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0#migrating-from-isaac-lab-2x)。

## 訓練、錄影與回報

時數應作為 wall-clock 預算，記錄已消耗時間、更新數、transitions、來源與恢復 checkpoint；
到期保存模型並以獨立 seed 評估。跑完時間不等於學會動作，應另外報告存活、motion tracking、
接觸／滑動、跌倒及 task success。候選與通過驗證的 checkpoint 分別保存。

地標需要具授權與明確尺度的 USD、碰撞／地面設定、光源及 camera。先在同一 checkpoint
的真實物理 rollout 中錄影，再保存 MP4 與 metadata；不要將 Three.js 示意場景標示為 Isaac Sim RTX。
3.0 的官方錄影使用環境 recorder 設定，實際參數需對照固定版本。
[官方錄影變更](https://github.com/isaac-sim/IsaacLab/releases/tag/v3.0.0#video-recording)。

完成時應保存 checkpoint SHA-256、URDF／motion／config／場景版本、backend／library 版本、
訓練預算、評估 seed／結果、影片 SHA-256 及實際 renderer。
**本 demo 尚未提供遠端 worker claim、heartbeat、結果上傳或驗證簽章 API。**
接入時需另建可鑑別的工作接收與結果回報，完整保存證據後才把新工作轉為成功。
市集目前只分享設定與歷史參考；真正交易仍需帳戶、權限、授權、付款及權重交付機制。
