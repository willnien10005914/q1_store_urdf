# Isaac Lab 3.0 新架構總覽

Isaac Lab 3.0（2026 正式版）是從 2.x 以來最大的一次改版。它不是「多了幾個 API」，而是把 **安裝方式、啟動流程、物理後端與資料存取** 全部重做。這一課先建立整體地圖，後面每一課再各自深入。

## 1. 三件最重要的事

| 面向 | Isaac Lab 2.x | Isaac Lab 3.0 |
| --- | --- | --- |
| 安裝 | `./isaaclab.sh --install`、conda | **只支援 uv**：`uv sync --extra isaacsim` |
| 啟動 | 各 RL 函式庫自己的 `train.py` | **統一指令** `uv run isaaclab train --rl_library rsl_rl --task ...` |
| 後端 | 綁定 Isaac Sim PhysX | **preset 切換**：`physics=newton_mjwarp`、`renderer=...`、`--viz ...` |

另外兩個會立刻影響舊程式碼的改變，第三課會專門講：
資料屬性改回傳 `ProxyArray`（要加 `.torch`），四元數從 WXYZ 變成 **XYZW**。

## 2. 安裝：新環境、不要升級舊的

```bash
git clone --branch v3.0.0 --depth 1 https://github.com/isaac-sim/IsaacLab.git
cd IsaacLab
# Newton-only（不需要 Isaac Sim，可在沒有 Kit 的機器上跑）
uv run isaaclab list_envs --show_presets
# 需要 Isaac Sim PhysX / RTX 時
uv sync --extra isaacsim
```

3.0 的 release 註記 Python 3.12、Isaac Sim 6.x、PyTorch 2.1x／CUDA 13、Newton 1.6、RSL-RL 5.5。
這台 Spark 既有的 `~/IsaacLab`（2.3.2）**不要就地升級**；本站 worker 用 `Q1_ISAACLAB3_ROOT` 指向獨立的 3.0 checkout。

## 3. 一個環境、多個後端

物理、渲染、視覺化是三個獨立選擇，彼此可以搭配（受 task 支援限制）：

```text
physics=newton_mjwarp | isaacsim_physx | ovphysx
renderer=newton_renderer | ovrtx | isaacsim_rtx
--viz newton_gl | newton_rtx | viser | rerun | kit
```

```bash
uv run isaaclab train --rl_library rsl_rl --task Isaac-Cartpole physics=newton_mjwarp --viz newton_gl
uv run isaaclab train --task Isaac-Cartpole --help   # 看這個 task 允許的組合
```

為什麼重要：Q1 的站立／行走任務可以先在 **Newton MJWarp** 上快速迭代 reward（不用啟動 Kit），再切到 **Isaac Sim PhysX** 做最終驗收與 RTX 錄影。第二課會比較兩個後端的差異。

## 4. 程式裡的啟動流程

2.x 用 `AppLauncher` 直接開 Isaac Sim。3.0 改用 `launch_simulation`，由 `SimulationCfg` 決定要不要啟動 Kit：

```python
from isaaclab.app import launch_simulation
from isaaclab.sim import SimulationCfg

sim_cfg = SimulationCfg(dt=1 / 200, device="cuda:0")
with launch_simulation(sim_cfg) as launch:      # 依 physics/renderer/viz 決定是否需要 Kit
    import isaaclab.sim as sim_utils             # 之後才 import 需要 runtime 的模組
    sim = sim_utils.SimulationContext(sim_cfg)
    ...
```

重點：**需要 runtime 的 import 放在 `launch_simulation` 之後**。這和 2.x「先 AppLauncher 再 import」的規矩一樣，只是入口換了。

## 5. 右邊的範例在做什麼

`example.py` 會：

1. 讀取本站傳入的 `params.json`（physics、num_envs、viz、sim_seconds）。
2. 用 `launch_simulation` 啟動所選後端。
3. 把 Q1（雙足或雙輪，看你選的機型）以 `UrdfFileCfg` 生成到 `num_envs` 個環境。
4. 讓它用預設站姿站 `sim_seconds` 秒，期間把 `progress.json` 寫回工作目錄，網頁上的進度條就是讀它。

試著改 `physics` 從 `newton_mjwarp` 換到 `isaacsim_physx`，看啟動時間與 log 的差別；再把 `num_envs` 調到 1024，觀察 GPU 記憶體。

## 6. 常見錯誤

- `ModuleNotFoundError: isaacsim`：選了 `isaacsim_physx` 卻沒裝 `--extra isaacsim`。
- 視窗沒出現：3.0 預設 headless，要加 `--viz`；無顯示器機器加 `HEADLESS=1`。
- `Unsupported preset combination`：該 task 沒提供這個組合，用 `list_envs --show_presets` 查。

> 下一課：**多後端物理**——同一個 Q1 掉落測試在 PhysX 與 Newton 的差異。
