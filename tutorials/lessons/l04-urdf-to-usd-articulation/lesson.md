# URDF Importer 3.0：Q1 URDF → USD → ArticulationCfg

Q1 的原生模型是 URDF（本站 `assets/robots/{leg,wheel}/urdf/q1_inertia/*.urdf`，含 inertial、limit、mimic）。Isaac Lab 3.0 搭配 Isaac Sim 6 的 **URDF importer 3.0**，底層換成 `urdf-usd-converter` + `isaacsim.asset.transformer.rules`，產出結構化 USD。

## 1. 什麼變了

| 項目 | 2.x | 3.0 |
| --- | --- | --- |
| 底層 | C++ binding、Kit 指令 `URDFParseFile`／`URDFImportRobot` | Python pipeline `URDFImporter.import_urdf()` |
| 輸出路徑 | `usd_dir/usd_file_name` | `{usd_dir}/{robot_name}/{robot_name}.usda`（自動命名） |
| `merge_fixed_joints` | importer 選項 | URDF **XML 前處理**（visual／collision／inertial 併入父 link） |
| `convert_mimic_joints_to_normal_joints` | 支援 | **不支援**（警告） |
| `replace_cylinders_with_capsules`、`root_link_name` | 支援 | **不支援** |
| `make_instanceable` | 可選 | 忽略，預設 instanceable |
| `NaturalFrequencyGainsCfg` | 支援 | deprecated → 用 `PDGainsCfg` |
| 版本鎖定 | 鎖 `isaacsim.asset.importer.urdf-2.4.31` | 用 Isaac Sim 內建版本 |

CLI：

```bash
uv run --extra importers python scripts/tools/convert_urdf.py \
  assets/robots/leg/urdf/q1_inertia/quanta_robot_legged.urdf /tmp/q1_usd \
  --merge-joints --joint-stiffness 80 --joint-damping 4 --viz kit
```

## 2. 程式 API

```python
from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg

cfg = UrdfConverterCfg(
    asset_path="quanta_robot_legged.urdf",
    usd_dir="/tmp/q1_usd",            # 檔名自動：/tmp/q1_usd/quanta_robot_legged/quanta_robot_legged.usda
    fix_base=False,
    merge_fixed_joints=True,
    joint_drive=UrdfConverterCfg.JointDriveCfg(
        gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0),
    ),
)
usd_path = UrdfConverter(cfg).usd_path
```

多數情況不用自己呼叫 converter：`sim_utils.UrdfFileCfg` 繼承 `UrdfConverterCfg`，放進 `ArticulationCfg.spawn` 時會自動轉換並快取。

## 3. Q1 要注意的四件事

1. **23 個控制軸**：manifest 的 `control_joints` 是 PPO action 順序。Leg 另有 6 個鎖定關節（merge_fixed_joints 會併掉），Wheel 另有 4 個 mimic 從屬關節（不要放進致動器 `joint_names_expr`）。
2. **mimic**：importer 保留 mimic；3.0 無法再「轉成一般關節」。設計 action 時只對主關節下指令。
3. **輪子是 `continuous`**：`joint_pos_limits` 會是 ±inf 或很大；reward／observation 用 `joint_vel` 而不是 `joint_pos`。
4. **四元數 XYZW**：`InitialStateCfg.rot=(0,0,0,1)`；本站 manifest 的 `default_joint_positions` 可直接餵 `joint_pos`。

## 4. 3.0 的 `ArticulationCfg`

```python
from isaaclab.assets import ArticulationCfg
from isaaclab.actuators import ImplicitActuatorCfg
import isaaclab.sim as sim_utils

Q1_LEG_CFG = ArticulationCfg(
    prim_path="{ENV_REGEX_NS}/Robot",
    spawn=sim_utils.UrdfFileCfg(asset_path=URDF, fix_base=False, merge_fixed_joints=True,
                                joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                    gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0))),
    init_state=ArticulationCfg.InitialStateCfg(pos=(0, 0, 0.95), rot=(0, 0, 0, 1), joint_pos=DEFAULT_Q),
    actuators={
        "legs": ImplicitActuatorCfg(joint_names_expr=[".*hip.*", ".*knee.*", ".*ankle.*"], stiffness=120.0, damping=6.0,
                                    actuator_effort_limit=150.0, joint_effort_limit=150.0),
        "upper": ImplicitActuatorCfg(joint_names_expr=[".*waist.*", ".*shoulder.*", ".*elbow.*", ".*wrist.*"],
                                     stiffness=60.0, damping=3.0, actuator_effort_limit=60.0, joint_effort_limit=60.0),
    },
)
```

`actuator_effort_limit`（致動器模型的額定限制）與 `joint_effort_limit`（solver 的關節限制）在 3.0 是兩個欄位；第五課詳談。

## 5. 範例

右側 `example.py`：

1. 以 `UrdfConverterCfg` 轉換所選 Q1（參數 `fix_base`、`merge_fixed_joints`、`stiffness`、`damping`、`link_density`）。
2. 生成 `Articulation`，輸出每個關節的 `type`、`lower/upper`（`joint_pos_limits.torch`）、`effort/velocity limit`、是否在 `control_joints` 內。
3. 對照本站 `manifest.json` 的 `control_joints` 與 URDF SHA-256，列出缺少或多出的關節。
4. 輸出總質量（body masses 加總）與 URDF inertial 總和比較。

練習：把 `merge_fixed_joints` 關掉，看 Leg 多出哪 6 個關節；再把 `stiffness` 設成 0，看 Articulation 生成後的 `joint_stiffness` 是否沿用 URDF 值。

> 本頁上方的 3D 預覽是 Three.js 幾何顯示（URDFLoader），不是 Isaac Sim 畫面；它用來確認關節名稱與軸向。
