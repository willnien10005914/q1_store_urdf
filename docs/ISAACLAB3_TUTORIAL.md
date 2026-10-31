# Isaac Lab 3 Tutorial（網頁課程）

`isaac_v3` 分支在 Q1 Store 上加了一套可互動的 Isaac Lab 3.0 課程：課文、可改的 `example.py`、參數滑桿、測驗、本機 Hermes 助教、課程提案走馬燈，以及可透過 Pinggy 續訓的執行紀錄。

## 入口

| 路徑 | 說明 |
| --- | --- |
| `/tutorial` | 課程主頁（`public/tutorial.html`） |
| 首頁 nav「Isaac Lab 3」 | hover mega-menu；右下角續課卡片 |
| 首頁上方 Hermes ticker | 待審提案與掃描結果 |

## 課程內容（`tutorials/`）

```
tutorials/
  categories.json          # 分類與等級門檻
  lessons/<id>/
    lesson.json            # 標題、params、quiz、references、run.kind
    lesson.md              # 課文
    example.py             # 練習（寫 progress.json / result.json）
  proposals/{pending,approved,rejected}/
    <id>.md                # Hermes 或手動提案（front matter + body）
```

目前八堂課對應 3.0 最大變格：

1. `l01` 架構／`launch_simulation`／preset  
2. `l02` PhysX vs Newton  
3. `l03` 2.x→3.0 遷移掃描器（純 Python）  
4. `l04` URDF importer 3.0  
5. `l05` 致動器 limits／PD  
6. `l06` Manager-based + RSL-RL PPO（可續訓）  
7. `l07` visualizers／VideoRecorderCfg／viser（無 Lab 3 時 kinematic fallback）  
8. `l08` ONNX／TorchScript 匯出與 `policy_meta.json`

`run.kind`：

- `any` — 本機 `python3`（l03、l08）  
- `isaac_v3` — 需要 `Q1_ISAACLAB3_ROOT` + `uv`；`fallback: true`（l07）在缺 Lab 3 時改跑 example 內 fallback 分支  
- `trainable` / `resumable` — l06 寫 `latest.pt`，可 `--resume`

## 後端模組

| 模組 | 職責 |
| --- | --- |
| `q1store/tutorial.py` | catalog、使用者進度、測驗、runs、提案、discovery、ticker |
| `q1store/llm.py` | GPU／runtime 偵測、Hermes 型號目錄、下載進度、伺服器啟動、chat stream |
| `q1store/agent.py` | Hermes tool loop（搜尋／改碼／開跑／提案）+ 離線 `/` 指令 |
| `q1store/research.py` | DuckDuckGo HTML、arXiv Atom、URL→text |

資料目錄（預設 `.data/`，已 gitignore）：

- `tutorial.sqlite3` — users / progress / runs / messages / memory / settings / ticker  
- `tutorial/runs/<id>/` — `params.json`、`example.py`、`progress.json`、`result.json`、`latest.pt`、`rollout.json`、`result.mp4`…  
- `models/` — 下載的 GGUF／HF 權重  
- `llm-server.json` — 本機 LLM 伺服器狀態  

## API 摘要

```
GET  /api/tutorial/catalog
GET  /api/tutorial/lessons/<id>?robot=&user=
POST /api/tutorial/users                     {name}
POST /api/tutorial/users/<uid>/lessons/<id>/{start,draft,quiz}
POST /api/tutorial/runs                      {user,lesson,robot,params,code}
POST /api/tutorial/runs/<id>/{cancel,resume}
GET  /api/tutorial/proposals
POST /api/tutorial/proposals/<id>/{approve,reject,reopen,mark-created}
POST /api/tutorial/discovery                 {enabled, run_now, …}
GET  /api/llm/status
POST /api/llm/download                       {model_id}
POST /api/llm/server                         {model_id} | {action:stop}
POST /api/agent/chat                         SSE
GET  /media/tutorial/<run_id>/<file>
```

維護指令：

```bash
python3 -m q1store.tutorial list-lessons
python3 -m q1store.tutorial list-proposals
python3 -m q1store.tutorial discover-once
python3 -m q1store.tutorial cursor-prompt <proposal_id>
python3 -m q1store.tutorial mark-created <proposal_id> <lesson_id>
```

## 環境變數

| 變數 | 用途 |
| --- | --- |
| `Q1_ISAACLAB3_ROOT` | Isaac Lab **3.0** checkout（需 `pyproject.toml` + `uv`） |
| `Q1_TUTORIAL_PYTHON` | 沙箱 python（預設 `sys.executable`） |
| `Q1_LLM_BASE_URL` / `Q1_LLM_MODEL` | 外部 OpenAI-compatible endpoint（跳過本機下載） |
| `Q1_LLM_PORT` | 本機 LLM 埠（預設 8877） |
| `Q1_LLAMA_SERVER` | `llama-server` / `scripts/hermes_server.sh` 路徑 |
| `Q1_STORE_DATA` | 覆寫 `.data` |
| `Q1_STORE_ENV_FILE` | 覆寫 `.data/store.env`（setup 產出、start 會 source） |

沒有 Lab 3 時，需要 GPU 的課程會標 `blocked` 並提示跑 `setup_store.sh`；**不會假裝訓練成功**。l07 仍可用 kinematic fallback 練習 rollout 契約。

## 建議啟動流程（setup → store）

啟動 web 前先跑 idempotent setup，把 Hermes 與 Isaac Lab 3 一次備齊（已安裝會 skip）：

```bash
bash scripts/setup_store.sh              # 寫入 .data/store.env
bash scripts/setup_store.sh --check      # 只報告，缺件 exit 1
bash scripts/start_store.sh --port 8878 --with-hermes
# 等價：bash scripts/start_store.sh --setup --port 8878 --with-hermes
```

`setup_store.sh` 會：

1. **Hermes**：`.venv-llm` + CUDA `llama-cpp-python[server]` + cudart + Hermes-3 GGUF（已有則跳過）
2. **Isaac Lab 3**：clone `isaac-sim/IsaacLab` @ `v3.0.0` → `~/IsaacLab3`（可用 `Q1_ISAACLAB3_ROOT` 覆寫；**不要**覆寫既有 `~/IsaacLab` 2.x），再 `uv sync`（可加 `--with-isaacsim`）
3. 寫 `.data/store.env`；`start_store.sh` 自動 source，並可 `--with-hermes` 拉起 `:8877`

## 本機 LLM（Hermes）

手動路徑（通常不必；prefer `setup_store.sh`）：

```bash
bash scripts/setup_store.sh --hermes-only
bash scripts/hermes_server.sh          # http://127.0.0.1:8877/v1
bash scripts/start_store.sh --port 8878 --with-hermes
```

Tutorial 頁右上角應顯示 **Hermes**；reasoning 視窗會出現 `<think>`。

沒有 LLM 時離線助教仍可用：`/search`、`/arxiv`、`/params`、`/run`、`/propose`…

## Hermes 進修提案流程

1. 勾「Hermes 搜尋進修課程」（或按「立刻掃描」）  
2. ticker 與側欄出現 pending 數  
3. 人工「同意」→ 提案移到 `approved/`  
4. 「複製 Cursor prompt」貼到 Cursor，依 md 建新課並 push `isaac_v3`  
5. `python3 -m q1store.tutorial mark-created <id> <lesson_id>` 標記建立時間  

## 測試

```bash
python3 -m unittest discover -s tests -v
npm run check
```

`tests/test_tutorial.py` 涵蓋 catalog、進度、測驗、l03／l07 runs、提案、LLM status、離線 agent、HTTP 路由。
