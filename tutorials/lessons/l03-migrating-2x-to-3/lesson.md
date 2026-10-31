# 2.x → 3.0 遷移：ProxyArray、XYZW 與 index/mask

本站的雙足訓練來自 `q1_leg_urdf`（Isaac Lab 2.3.x）。要把 `leg_humanoid_lab/isaac_env.py`、`isaac_evaluation.py` 這類程式搬到 3.0，官方遷移指南建議的順序是：

1. 先新建 uv 環境（第一課），不要升級 2.x 環境。
2. 改 **backend／preset** 設定（第二課）。
3. 改 **資料存取、寫入方法、四元數**（本課）。
4. 改 **asset／actuator／sensor** 名稱（第四、五課）。
5. 最後改 **RL 指令與視覺化／錄影**（第七、八課）。

## 1. `.data.*` 回傳 `ProxyArray`

```python
# 2.x
root_pos = robot.data.root_pos_w                # torch.Tensor
# 3.0
root_pos = robot.data.root_pos_w.torch          # torch.Tensor（零拷貝 cached view）
wp_arr   = robot.data.root_pos_w.warp           # warp.array，給 kernel 用
wp.launch(kernel, inputs=[robot.data.joint_pos])  # 直接傳 ProxyArray 也可以
```

常見陷阱：

- `torch.zeros_like(robot.data.joint_pos)` → 要寫 `torch.zeros_like(robot.data.joint_pos.torch)`。
- `tensor.clone()` → `.torch.clone()`。
- **不要跨 reset 快取 `.torch`**：Newton 後端 reset 後 buffer 可能重建。

## 2. 四元數：WXYZ → XYZW

| | 2.x | 3.0 |
| --- | --- | --- |
| 順序 | `(w, x, y, z)` | `(x, y, z, w)` |
| 單位 | `(1, 0, 0, 0)` | `(0, 0, 0, 1)` |

受影響的地方：`ArticulationCfg.InitialStateCfg.rot`、reset state、目標姿態、controller target、observation 裡的 `root_quat_w`，以及 **所有** `isaaclab.utils.math.quat_*` 函式。`convert_quat()` 不再需要。

本站的歷史 rollout JSON（`.data/jobs/*/rollout.json`）裡的 `quaternion` 欄位是 2.x 的 WXYZ；若要用 3.0 重播，請先轉換，不要混用。

## 3. 寫入方法分成 `_index` 與 `_mask`

```python
# 2.x：一個方法，env_ids 可以是索引也可以是 mask → 語意模糊
robot.write_root_pose_to_sim(pose, env_ids)

# 3.0
robot.write_root_pose_to_sim_index(root_pose=pose, env_ids=env_ids)   # 部分資料 + 索引
robot.write_root_pose_to_sim_mask(root_pose=full_pose, env_mask=mask)  # 完整資料 + 布林遮罩
```

同理 `write_joint_state_to_sim_index/_mask`、`write_root_velocity_to_sim_index/_mask`。

## 4. 致動器指令移到 `actuators`

```python
# 2.x
robot.set_joint_position_target(q, joint_ids=ids)
applied = robot.data.applied_torque

# 3.0（keyword-only）
robot.actuators.target_command.set_position_index(value=q, joint_ids=ids)
applied = robot.actuators.applied_effort.torch
cmd     = robot.actuators.target_command.position.torch
```

`data.joint_pos_target`、`data.applied_torque`、`data.computed_torque` 都變成 deprecated forwarder。
本站 `train_store_job.py` 的錄影段讀 `data.applied_torque[0, env.jids]` 算機械功，遷移時要改成 `actuators.applied_effort.torch[0, env.jids]`。

## 5. `--headless` 移除、感測器改名

- 3.0 沒有 `--headless`：不選 `--viz` 就是 headless。無顯示器機器要開 kit／錄影時設 `HEADLESS=1`。
- 舊 `Imu`（全狀態）改名 `Pva`；新 `Imu` 只有角速度＋線加速度。
- `XformPrimView` → `FrameView`；`root_physx_view` → `root_view`。
- `ContactSensor` 的力改用明確欄位：`net_normal_forces_w`、`net_friction_forces_w`、`net_forces_w`（total）。

## 6. 範例：遷移掃描器

右側 `example.py` 是**純 Python**，不用 Isaac：它掃描你指定的路徑，列出

- 直接把 `.data.xxx` 當 tensor 用的位置（缺 `.torch`）
- 疑似 WXYZ 的硬編碼四元數（例如 `(1.0, 0.0, 0.0, 0.0)`）
- 舊的 `write_*_to_sim(` 單一方法
- `set_joint_*_target(` 與 `data.applied_torque` 等已搬移 API
- `--headless`、`AppLauncher`、`gym.wrappers.RecordVideo`

預設路徑是本站的 `scripts/train_store_job.py`（2.x 風格）。把 `scan_path` 換成 worker 機器上 `q1_leg_urdf/source/leg_humanoid_lab`，看看要改多少處。官方也附了 `tools/` 下的四元數掃描與修正工具，可對照使用。

> 掃描器只做字串層級的啟發式比對；真正的遷移要以執行期 `DeprecationWarning` 與官方指南為準。
