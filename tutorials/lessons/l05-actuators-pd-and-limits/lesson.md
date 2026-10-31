# 致動器：PD 增益、兩種 effort limit 與 ActuatorCollection

reward 設計再好，致動器模型不對，策略就學到一個不存在的機器人。Isaac Lab 3.0 在致動器上有兩個結構性改變：**ActuatorCollection** 與 **limit 欄位拆分**。

## 1. 致動器模型選擇

| Cfg | 類型 | 行為 | 適合 |
| --- | --- | --- | --- |
| `ImplicitActuatorCfg` | implicit | PD 由 solver drive 隱式求解，最穩定、最快 | 第一版訓練、PhysX／Newton 皆可 |
| `IdealPDActuatorCfg` | explicit | Python 端算 `Kp(q*-q)+Kd(v*-v)`，再送 effort | 需要自訂裁切或噪聲 |
| `DCMotorCfg` | explicit | 加上轉速-力矩包絡（`saturation_effort`、`velocity_limit`） | 接近真實無刷馬達 |
| `DelayedPDActuatorCfg` | explicit | PD 加上指令延遲（`min_delay`／`max_delay` 步） | 模擬通訊延遲、sim2real |
| `ActuatorNetMLPCfg`／`ActuatorNetLSTMCfg` | explicit | 用學到的網路取代解析模型 | 有實機資料時 |

```python
from isaaclab.actuators import ImplicitActuatorCfg, DCMotorCfg, DelayedPDActuatorCfg

legs = ImplicitActuatorCfg(joint_names_expr=[".*hip.*", ".*knee.*", ".*ankle.*"],
                           stiffness=120.0, damping=6.0,
                           actuator_effort_limit=150.0, joint_effort_limit=150.0, armature=0.01)
wheels = DCMotorCfg(joint_names_expr=[".*wheel_joint"], stiffness=0.0, damping=2.0,
                    actuator_effort_limit=30.0, joint_effort_limit=30.0,
                    saturation_effort=40.0, velocity_limit=40.0)
```

Q1 Wheel 的輪子用速度控制（`stiffness=0`），雙足關節用位置 PD。

## 2. 兩種 effort limit（3.0 新）

| 欄位 | 擁有者 | 意義 |
| --- | --- | --- |
| `actuator_effort_limit` | 致動器模型 | 額定限制；explicit 模型輸出先被它裁切 |
| `joint_effort_limit` | solver（`joint_effort_limits`） | 建構期的關節屬性覆寫 |
| `joint_velocity_limit` | solver（`joint_vel_limits`） | 是否強制依後端而定 |

**行為改變**：2.x 對 explicit 致動器會把 solver 限制放大到 1e9，只讓模型裁切；3.0 **保留** `joint_effort_limit`，所以 explicit 模型的輸出會被 solver 再裁一次。如果你的 URDF 寫了很緊的 `effort`，請把 `joint_effort_limit` 設成 ≥ `actuator_effort_limit`。

舊名稱 `effort_limit`、`velocity_limit`、`effort_limit_sim`、`velocity_limit_sim` 到 3.1 前仍接受（警告）。同一群組同時寫新舊名：相等→用新名並警告；不同→`ValueError`。

## 3. ActuatorCollection

```python
robot = scene["robot"]
# 指令（keyword-only）
robot.actuators.target_command.set_position_index(value=q_target, joint_ids=ids)
robot.actuators.target_command.set_velocity_index(value=v_target, joint_ids=wheel_ids)
robot.actuators.target_command.set_effort_index(value=tau, joint_ids=ids)
# 遙測
applied  = robot.actuators.applied_effort.torch
computed = robot.actuators.computed_effort.torch
cmd_pos  = robot.actuators.target_command.position.torch
```

群組是**建構期**狀態：`robot_cfg.actuators["gripper"] = ...` 要在 `Articulation(robot_cfg)` 之前；執行期對 `robot.actuators` 賦值會 `TypeError`。

執行期要改 joint 屬性：

| 想改 | 讀 | 寫 |
| --- | --- | --- |
| armature | `data.joint_armature` | `write_joint_armature_to_sim_index()` |
| friction | `data.joint_friction_coeff` | `write_joint_friction_coefficient_to_sim_index()` |
| effort limit | `data.joint_effort_limits` | `write_joint_effort_limit_to_sim_index()` |
| PD 增益隨機化 | — | `randomize_actuator_gains()`（取代 `write_actuator_stiffness_to_sim`） |

## 4. 範例：Q1 站姿追蹤 + 擾動

`example.py` 讓 `num_envs` 個 Q1 追蹤預設站姿，在第 2 秒對 root 施加 `perturb_nm` 的 yaw 力矩（3.0 的 wrench composer：`set_external_force_and_torque` 改為可疊加的 permanent／instantaneous 版本），記錄：

- `tracking_rmse_rad`：`joint_pos - target` 的 RMSE
- `peak_effort_nm`、`saturation_fraction`：`applied_effort` 達到 `actuator_effort_limit` 的比例
- `recovered_fraction`：擾動後 1 秒內 root 高度回到 0.8 m 以上的環境比例

**實驗建議**

1. `implicit`、Kp=80／Kd=4（基準）。
2. Kp=300／Kd=4：看 RMSE 下降但是否開始震盪；加 `armature=0.05` 再看。
3. `actuator_effort_limit=40`：飽和比例應明顯上升、recovered 下降。
4. `joint_effort_limit=40` 但 `actuator_effort_limit=120`：注意 3.0 的二次裁切——結果應與第 3 點接近。
5. `delayed_pd`：延遲 2–4 步時高增益是否失穩。

> sim2real 的經驗：先用 implicit 把 reward 調通，再換 DCMotor／DelayedPD 做 robustness，最後才考慮 actuator net。
