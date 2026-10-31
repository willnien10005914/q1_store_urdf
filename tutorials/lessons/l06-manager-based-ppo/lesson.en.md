# Manager-based environments and RSL-RL PPO

Lessons 1–5 put **Q1 inside 3.0**; from here we **train**. Isaac Lab’s manager-based workflow splits an RL task into swappable terms: observation, action, reward, termination, event (randomization), command, curriculum. Tune reward by editing cfg, not the env class—the sliders on this page do exactly that.

## 0. Newton MJWarp capacity (read this)

Q1 has heavy collision geometry; default `MJWarpSolverCfg(njmax=300)` overflows (`nefc overflow`) when the robot falls, observations go NaN, and PPO `check_nan` aborts. This lesson aligns with Lab’s locomotion velocity preset:

```python
from isaaclab_newton.physics import MJWarpSolverCfg, NewtonCfg, NewtonCollisionPipelineCfg, NewtonShapeCfg

sim_cfg.physics = NewtonCfg(
    solver_cfg=MJWarpSolverCfg(
        njmax=1000, nconmax=300, iterations=100,
        integrator="implicitfast", use_mujoco_contacts=False,
    ),
    collision_cfg=NewtonCollisionPipelineCfg(max_triangle_pairs=2_500_000),
    num_substeps=2,
    default_shape_cfg=NewtonShapeCfg(margin=0.0, ke=160000.0, kd=1100.0),
)
```

If logs still say `increase njmax to N`, bump `njmax` (e.g. 1500).

## 1. Task skeleton

```python
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.managers import (ObservationGroupCfg as ObsGroup, ObservationTermCfg as ObsTerm,
                               RewardTermCfg as RewTerm, TerminationTermCfg as DoneTerm,
                               EventTermCfg as EventTerm, SceneEntityCfg)
import isaaclab.envs.mdp as mdp

@configclass
class ObservationsCfg:
    @configclass
    class PolicyCfg(ObsGroup):
        base_lin_vel = ObsTerm(func=mdp.base_lin_vel, noise=Unoise(n_min=-0.1, n_max=0.1))
        base_ang_vel = ObsTerm(func=mdp.base_ang_vel)
        projected_gravity = ObsTerm(func=mdp.projected_gravity)
        joint_pos = ObsTerm(func=mdp.joint_pos_rel)
        joint_vel = ObsTerm(func=mdp.joint_vel_rel)
        actions = ObsTerm(func=mdp.last_action)
        velocity_commands = ObsTerm(func=mdp.generated_commands, params={"command_name": "base_velocity"})
        def __post_init__(self):
            self.enable_corruption = True
            self.concatenate_terms = True
    policy: PolicyCfg = PolicyCfg()

@configclass
class RewardsCfg:
    alive = RewTerm(func=mdp.is_alive, weight=0.5)
    track_lin_vel = RewTerm(func=mdp.track_lin_vel_xy_exp, weight=1.0, params={"command_name": "base_velocity", "std": 0.25})
    flat_orientation = RewTerm(func=mdp.flat_orientation_l2, weight=-2.0)
    base_height = RewTerm(func=mdp.base_height_l2, weight=-2.0, params={"target_height": 0.92})
    joint_torques = RewTerm(func=mdp.joint_torques_l2, weight=-2e-4)
    action_rate = RewTerm(func=mdp.action_rate_l2, weight=-0.05)

@configclass
class TerminationsCfg:
    time_out = DoneTerm(func=mdp.time_out, time_out=True)
    fell = DoneTerm(func=mdp.root_height_below_minimum, params={"minimum_height": 0.5})

@configclass
class EventCfg:
    reset_base = EventTerm(func=mdp.reset_root_state_uniform, mode="reset",
                           params={"pose_range": {"yaw": (-0.3, 0.3)}, "velocity_range": {}})
    reset_joints = EventTerm(func=mdp.reset_joints_by_scale, mode="reset", params={"position_range": (0.9, 1.1), "velocity_range": (0, 0)})
    push = EventTerm(func=mdp.push_by_setting_velocity, mode="interval", interval_range_s=(4.0, 6.0),
                     params={"velocity_range": {"x": (-0.5, 0.5), "y": (-0.5, 0.5)}})
```

Built-in mdp terms handle `ProxyArray`; custom terms must use `.torch`.

## 2. Actions: biped vs wheeled

```python
# Leg: 23-axis position PD
actions = mdp.JointPositionActionCfg(asset_name="robot", joint_names=CONTROL_JOINTS, scale=0.5, use_default_offset=True)
# Wheel: leg/waist/arm position PD + wheel velocity
leg_actions = mdp.JointPositionActionCfg(asset_name="robot", joint_names=[j for j in CONTROL_JOINTS if "wheel" not in j], scale=0.5, use_default_offset=True)
wheel_actions = mdp.JointVelocityActionCfg(asset_name="robot", joint_names=[".*wheel_joint"], scale=10.0)
```

Mimic slaves are **not** in the action vector (lesson 4).

## 3. PPO (RSL-RL 5.x)

`policy = RslRlPpoActorCriticCfg(...)` is deprecated; 5.x sets separate `actor` / `critic` (`RslRlMLPModelCfg` with `class_name="MLPModel"`) or `OnPolicyRunner` raises `KeyError: 'class_name'`.

`RslRlMLPModelCfg.to_dict()` may still emit legacy `stochastic` / `init_noise_std`; passing them straight to `OnPolicyRunner` causes `TypeError: MLPModel(..., stochastic=...)`. Official entrypoints call `handle_deprecated_rsl_rl_cfg` first—this lesson does too:

```python
from isaaclab_rl.rsl_rl import (
    RslRlMLPModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg, handle_deprecated_rsl_rl_cfg,
)
from isaaclab_rl.rsl_rl.utils import check_rsl_rl_version

@configclass
class Q1PPORunnerCfg(RslRlOnPolicyRunnerCfg):
    num_steps_per_env = 24
    max_iterations = 3000
    save_interval = 50
    experiment_name = "q1_balance"
    obs_groups = {"actor": ["policy"], "critic": ["policy"]}
    actor = RslRlMLPModelCfg(
        hidden_dims=[512, 256, 128], activation="elu", obs_normalization=False,
        distribution_cfg=RslRlMLPModelCfg.GaussianDistributionCfg(init_std=1.0),
    )
    critic = RslRlMLPModelCfg(hidden_dims=[512, 256, 128], activation="elu", obs_normalization=False)
    algorithm = RslRlPpoAlgorithmCfg(value_loss_coef=1.0, use_clipped_value_loss=True, clip_param=0.2, entropy_coef=0.005,
                                     num_learning_epochs=5, num_mini_batches=4, learning_rate=1e-3, schedule="adaptive",
                                     gamma=0.99, lam=0.95, desired_kl=0.01, max_grad_norm=1.0)

runner_cfg = handle_deprecated_rsl_rl_cfg(Q1PPORunnerCfg(), check_rsl_rl_version())
runner = OnPolicyRunner(env, runner_cfg.to_dict(), log_dir=..., device=...)
```

Unified CLI (after task registration):

```bash
uv run isaaclab train --rl_library rsl_rl --task Q1-Leg-Balance-v0 physics=newton_mjwarp --num_envs 4096
uv run isaaclab train --rl_library rsl_rl --task Q1-Leg-Balance-v0 --checkpoint latest     # resume
uv run isaaclab play  --rl_library rsl_rl --task Q1-Leg-Balance-v0 --checkpoint best --video viz:kit
```

## 4. Chunked training, progress bar, resume (this store)

`example.py` skips the CLI and drives `OnPolicyRunner` directly. Every `chunk` iterations it:

1. `runner.learn(num_learning_iterations=chunk)`
2. `runner.save(latest.pt)`
3. Writes `progress.json` (iteration, elapsed, ETA, steps/s, mean reward)

The web UI polls `progress.json` every 3 s. Closing the browser or dropping Pinggy does not stop training on Spark; **Resume** loads `latest.pt` with optimizer and iteration. When the budget expires, evaluation runs (32-episode survival, upright score, height error) and records video.

## 5. Tuning tips (use the sliders)

| Symptom | Try |
| --- | --- |
| Keeps falling | ↑ `w_balance`, ↓ `command_vx`, disable `push_interval_s` |
| Stands but shakes | ↑ `w_smooth`, ↑ `w_energy`, ↓ `learning_rate` |
| Won’t walk forward | ↑ `w_track`, ensure `command_vx`>0, ↑ `entropy_coef` |
| Flat reward curve | ↑ `num_envs`, ↑ `max_iterations`; check early termination |

Tune on Newton first, then validate on PhysX. Change one slider at a time so comparisons mean something.

## 6. Relation to this store’s legacy worker

Studio “Spark real training” still uses Lab 2.x `train_store_job.py` (motion tracking). This lesson is the **Isaac Lab 3.0** balance/velocity task—checkpoints are not interchangeable (lesson 3: quaternions and ProxyArray). Finished runs appear under “My training” with video, rollout, and params for further edits.
