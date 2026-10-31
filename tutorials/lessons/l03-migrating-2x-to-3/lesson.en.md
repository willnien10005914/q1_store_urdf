# Migrating 2.x → 3.0: ProxyArray, XYZW, and index/mask writes

This store’s biped training comes from `q1_leg_urdf` (Isaac Lab 2.3.x). Moving code like `leg_humanoid_lab/isaac_env.py` and `isaac_evaluation.py` to 3.0 follows the official migration order:

1. Create a fresh uv environment (lesson 1)—do not upgrade 2.x in place.
2. Update **backend / preset** config (lesson 2).
3. Update **data access, write APIs, quaternions** (this lesson).
4. Update **asset / actuator / sensor** names (lessons 4–5).
5. Update **RL commands and visualization / recording** (lessons 7–8).

## 1. `.data.*` returns `ProxyArray`

```python
# 2.x
root_pos = robot.data.root_pos_w                # torch.Tensor
# 3.0
root_pos = robot.data.root_pos_w.torch          # torch.Tensor (zero-copy cached view)
wp_arr   = robot.data.root_pos_w.warp           # warp.array for kernels
wp.launch(kernel, inputs=[robot.data.joint_pos])  # ProxyArray inputs also work
```

Common pitfalls:

- `torch.zeros_like(robot.data.joint_pos)` → use `torch.zeros_like(robot.data.joint_pos.torch)`.
- `tensor.clone()` → `.torch.clone()`.
- **Do not cache `.torch` across reset** on Newton—buffers may rebuild.

## 2. Quaternions: WXYZ → XYZW

| | 2.x | 3.0 |
| --- | --- | --- |
| Order | `(w, x, y, z)` | `(x, y, z, w)` |
| Identity | `(1, 0, 0, 0)` | `(0, 0, 0, 1)` |

Affected areas: `ArticulationCfg.InitialStateCfg.rot`, reset states, target poses, controller targets, `root_quat_w` in observations, and **all** `isaaclab.utils.math.quat_*` helpers. `convert_quat()` is gone.

Historical rollout JSON under `.data/jobs/*/rollout.json` uses 2.x WXYZ in `quaternion`; replay in 3.0 requires conversion—do not mix orders.

## 3. Writes split into `_index` and `_mask`

```python
# 2.x: one method; env_ids could be indices or a mask → ambiguous
robot.write_root_pose_to_sim(pose, env_ids)

# 3.0
robot.write_root_pose_to_sim_index(root_pose=pose, env_ids=env_ids)   # partial data + indices
robot.write_root_pose_to_sim_mask(root_pose=full_pose, env_mask=mask)  # full data + boolean mask
```

Same pattern for `write_joint_state_to_sim_index/_mask`, `write_root_velocity_to_sim_index/_mask`.

## 4. Actuator commands move to `actuators`

```python
# 2.x
robot.set_joint_position_target(q, joint_ids=ids)
applied = robot.data.applied_torque

# 3.0 (keyword-only)
robot.actuators.target_command.set_position_index(value=q, joint_ids=ids)
applied = robot.actuators.applied_effort.torch
cmd     = robot.actuators.target_command.position.torch
```

`data.joint_pos_target`, `data.applied_torque`, and `data.computed_torque` are deprecated forwarders.
This store’s `train_store_job.py` recording path reads `data.applied_torque[0, env.jids]` for mechanical work—migrate to `actuators.applied_effort.torch[0, env.jids]`.

## 5. `--headless` removed; sensor renames

- No `--headless` in 3.0: omit `--viz` for headless. Set `HEADLESS=1` for offscreen Kit / recording on display-less machines.
- Old full-state `Imu` → `Pva`; new `Imu` is gyro + linear accel only.
- `XformPrimView` → `FrameView`; `root_physx_view` → `root_view`.
- `ContactSensor` forces use explicit fields: `net_normal_forces_w`, `net_friction_forces_w`, `net_forces_w` (total).

## 6. Example: migration scanner

`example.py` is **pure Python** (no Isaac). It scans your path and flags:

- `.data.xxx` used as tensors (missing `.torch`)
- Suspected WXYZ literals (e.g. `(1.0, 0.0, 0.0, 0.0)`)
- Legacy single-method `write_*_to_sim(`
- `set_joint_*_target(` and moved APIs like `data.applied_torque`
- `--headless`, `AppLauncher`, `gym.wrappers.RecordVideo`

Default scan target is this store’s `scripts/train_store_job.py` (2.x style). Point `scan_path` at `q1_leg_urdf/source/leg_humanoid_lab` on the worker to see scope. Official `tools/` include quaternion scanners you can compare against.

> The scanner is heuristic string matching; rely on runtime `DeprecationWarning` and the official guide for the real migration.
