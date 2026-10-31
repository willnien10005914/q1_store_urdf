# 多後端物理：PhysX vs Newton MuJoCo Warp

Isaac Lab 3.0 最大的結構變化，是 `isaaclab` 核心套件不再綁死 PhysX。`Articulation`、`RigidObject`、感測器都變成「後端中立」的介面，由工廠依目前後端載入 `isaaclab_physx` 或 `isaaclab_newton` 的實作。

```python
from isaaclab.assets import Articulation, ArticulationCfg
robot = Articulation(cfg=ArticulationCfg(...))   # 不用改 import，後端由 preset 決定
```

## 1. 三個後端一覽

| preset | 引擎 | 需要 Isaac Sim | 適合 |
| --- | --- | --- | --- |
| `isaacsim_physx` | Isaac Sim PhysX | 是（`--extra isaacsim`） | 最終驗收、RTX 錄影、既有 2.x 任務 |
| `newton_mjwarp` | Newton + MuJoCo Warp | 否 | 快速迭代 reward、Kit-less 訓練、瀏覽器視覺化 |
| `ovphysx` | OV PhysX standalone | 否（`--extra ovphysx`） | 不裝 Kit 但要 PhysX 行為 |

> 三者**不是**可互換的黑盒：接觸模型、關節 drive、摩擦與 solver 都不同。同一組 PD 增益在 Newton 可能偏軟，在 PhysX 偏硬。

## 2. 讓環境支援多後端：`PresetCfg`

2.x 直接寫 `self.sim.physics = PhysxCfg(...)`。3.0 改成列出可選後端：

```python
from isaaclab.utils import configclass
from isaaclab_physx.physics import PhysxCfg
from isaaclab_newton.physics import NewtonCfg
from isaaclab_tasks.utils import PresetCfg

@configclass
class Q1PhysicsPresets(PresetCfg):
    isaacsim_physx: PhysxCfg = PhysxCfg(solver_type=1, max_position_iteration_count=4)
    newton_mjwarp: NewtonCfg = NewtonCfg()
    default: PhysxCfg = isaacsim_physx

self.sim.physics = Q1PhysicsPresets()
```

CLI 的 `physics=newton_mjwarp` 會把整個 `sim.physics` 區段換成該欄位；`default` 決定不帶參數時的行為。

## 3. 後端差異會從哪裡冒出來

1. **接觸**：PhysX 用 penetration + solver iterations；MJWarp 用 MuJoCo 的 soft contact（`solref`／`solimp`）。落地時的彈跳與穩定時間會不同。
2. **關節 drive**：PhysX implicit drive 直接在 solver 內；Newton 的 drive 走 Newton 1.6 drive API（`write_group_parameter(..., "drive")`）。
3. **資料刷新**：PhysX 是 pull-to-refresh（buffer 穩定）；Newton 是 auto-refresh，**reset 後 wrapper 會換**，不要跨 reset 快取 `.torch`。
4. **效能**：Newton 在大量簡單 articulation 上常較快；PhysX 在複雜接觸／deformable 上功能較完整（deformable、surface gripper 目前 PhysX-only）。

## 4. 範例：Q1 掉落測試

右側 `example.py` 會把 Q1 從 `drop_height` 放下，用同一組 PD 增益撐住站姿，然後記錄：

- `settle_height_m`：最後 0.5 秒的 root 平均高度
- `max_contact_force_n`：足部／輪子 `ContactSensor` 的最大法向力（3.0 的欄位是 `net_normal_forces_w`）
- `steps_per_second`：含 `scene.update` 的實際吞吐
- `fallen_fraction`：root 高度低於 0.5 m 的環境比例

**實作步驟**

1. 先用 `physics=newton_mjwarp`、`num_envs=64` 跑一次，記下 result.json。
2. 改 `isaacsim_physx` 再跑一次。
3. 把 `dt` 從 0.005 調到 0.01，看哪個後端先出現穿透或翻倒。
4. 把 `solver_iterations` 調到 1，再調到 16。

問自己：Q1 Wheel 的輪子是 `continuous` joint，兩個後端對「無限旋轉關節」的處理一樣嗎？（提示：檢查 `robot.data.joint_pos.torch` 的範圍。）

## 5. 給 Q1 訓練的建議流程

```text
Newton MJWarp（reward 設計、curriculum 快速試）
      ↓ 策略可站／可走
Isaac Sim PhysX（驗收、錄影、與 2.x 歷史結果對照）
      ↓ 通過本站 gate
LEAPP / ONNX 匯出（第九課）
```

兩個後端之間的策略轉移有官方指南（Transfer Policies Between PhysX and Newton）；請把它當成「要重新驗證」而不是「免費互通」。
