# Actuators: PD gains, two effort limits, and ActuatorCollection

Even perfect reward design fails if the actuator model is wrong—the policy learns a robot that does not exist. Isaac Lab 3.0 makes two structural actuator changes: **ActuatorCollection** and **split limit fields**.

## 1. Choosing an actuator model

| Cfg | Type | Behavior | Best for |
| --- | --- | --- | --- |
| `ImplicitActuatorCfg` | implicit | PD solved implicitly in the solver drive; stable and fast | First training passes; PhysX and Newton |
| `IdealPDActuatorCfg` | explicit | Python computes `Kp(q*-q)+Kd(v*-v)`, sends effort | Custom clipping or noise |
| `DCMotorCfg` | explicit | Adds speed–torque envelope (`saturation_effort`, `velocity_limit`) | Brushless motor realism |
| `DelayedPDActuatorCfg` | explicit | PD plus command delay (`min_delay` / `max_delay` steps) | Comm delay, sim2real |
| `ActuatorNetMLPCfg` / `ActuatorNetLSTMCfg` | explicit | Learned network replaces analytic model | When you have real robot logs |

```python
from isaaclab.actuators import ImplicitActuatorCfg, DCMotorCfg, DelayedPDActuatorCfg

legs = ImplicitActuatorCfg(joint_names_expr=[".*hip.*", ".*knee.*", ".*ankle.*"],
                           stiffness=120.0, damping=6.0,
                           actuator_effort_limit=150.0, joint_effort_limit=150.0, armature=0.01)
wheels = DCMotorCfg(joint_names_expr=[".*wheel_joint"], stiffness=0.0, damping=2.0,
                    actuator_effort_limit=30.0, joint_effort_limit=30.0,
                    saturation_effort=40.0, velocity_limit=40.0)
```

Q1 Wheel drives use velocity control (`stiffness=0`); leg joints use position PD.

## 2. Two effort limits (new in 3.0)

| Field | Owner | Meaning |
| --- | --- | --- |
| `actuator_effort_limit` | Actuator model | Rated limit; explicit models clip here first |
| `joint_effort_limit` | Solver (`joint_effort_limits`) | Construction-time joint property override |
| `joint_velocity_limit` | Solver (`joint_vel_limits`) | Enforcement depends on backend |

**Behavior change**: 2.x inflated solver limits to 1e9 for explicit actuators so only the model clipped; 3.0 **keeps** `joint_effort_limit`, so explicit torques can clip twice. If URDF effort is tight, set `joint_effort_limit` ≥ `actuator_effort_limit`.

Legacy names `effort_limit`, `velocity_limit`, `effort_limit_sim`, `velocity_limit_sim` still parse until 3.1 (with warnings). If old and new names disagree in one group → `ValueError`; if equal → canonical name with warning.

## 3. ActuatorCollection

```python
robot = scene["robot"]
# Commands (keyword-only)
robot.actuators.target_command.set_position_index(value=q_target, joint_ids=ids)
robot.actuators.target_command.set_velocity_index(value=v_target, joint_ids=wheel_ids)
robot.actuators.target_command.set_effort_index(value=tau, joint_ids=ids)
# Telemetry
applied  = robot.actuators.applied_effort.torch
computed = robot.actuators.computed_effort.torch
cmd_pos  = robot.actuators.target_command.position.torch
```

Groups are **construction-time** state: set `robot_cfg.actuators["gripper"] = ...` before `Articulation(robot_cfg)`; assigning to `robot.actuators` at runtime raises `TypeError`.

Runtime joint property updates:

| Goal | Read | Write |
| --- | --- | --- |
| armature | `data.joint_armature` | `write_joint_armature_to_sim_index()` |
| friction | `data.joint_friction_coeff` | `write_joint_friction_coefficient_to_sim_index()` |
| effort limit | `data.joint_effort_limits` | `write_joint_effort_limit_to_sim_index()` |
| PD randomization | — | `randomize_actuator_gains()` (replaces `write_actuator_stiffness_to_sim`) |

## 4. Example: Q1 stance tracking + perturbation

`example.py` tracks the default standing pose across `num_envs`, applies a yaw root torque `perturb_nm` at 2 s (3.0 wrench composer: stackable permanent/instantaneous `set_external_force_and_torque`), and records:

- `tracking_rmse_rad`: RMSE of `joint_pos - target`
- `peak_effort_nm`, `saturation_fraction`: fraction of steps at `actuator_effort_limit`
- `recovered_fraction`: envs with root height back above 0.8 m within 1 s after perturbation

**Suggested sweeps**

1. `implicit`, Kp=80 / Kd=4 (baseline).
2. Kp=300 / Kd=4: RMSE drops but oscillation? Add `armature=0.05`.
3. `actuator_effort_limit=40`: saturation rises, recovery drops.
4. `joint_effort_limit=40` but `actuator_effort_limit=120`: watch 3.0 double clipping—similar to (3).
5. `delayed_pd` with 2–4 step delay: does high gain destabilize?

> Sim2real practice: tune rewards with implicit PD, swap to DCMotor/DelayedPD for robustness, then consider actuator nets.
