# Manager-based 環境與 RSL-RL PPO

前五課都在「把 Q1 放進 3.0」；從這課開始**訓練**。Isaac Lab 的 manager-based 工作流程把一個 RL 任務拆成可獨立替換的 term：observation、action、reward、termination、event（randomization）、command、curriculum。好處是：調 reward 只改 cfg，不改環境類別——也就是本頁右側滑桿在做的事。

## 1. 任務骨架

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

3.0 的 mdp 函式內部已處理 `ProxyArray`；若你自己寫 term，記得 `.torch`。

## 2. Action：雙足 vs 雙輪

```python
# Leg：23 軸位置 PD
actions = mdp.JointPositionActionCfg(asset_name="robot", joint_names=CONTROL_JOINTS, scale=0.5, use_default_offset=True)
# Wheel：腿／腰／手臂位置 PD + 兩個輪子速度
leg_actions = mdp.JointPositionActionCfg(asset_name="robot", joint_names=[j for j in CONTROL_JOINTS if "wheel" not in j], scale=0.5, use_default_offset=True)
wheel_actions = mdp.JointVelocityActionCfg(asset_name="robot", joint_names=[".*wheel_joint"], scale=10.0)
```

mimic 從屬關節**不在** action 裡（第四課）。

## 3. PPO（RSL-RL 5.x）

`policy = RslRlPpoActorCriticCfg(...)` 已棄用；5.x 要分開設 `actor`／`critic`（`RslRlMLPModelCfg`，內建 `class_name="MLPModel"`），否則 `OnPolicyRunner` 會 `KeyError: 'class_name'`。

```python
from isaaclab_rl.rsl_rl import RslRlMLPModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg

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
```

統一 CLI（任務註冊後）：

```bash
uv run isaaclab train --rl_library rsl_rl --task Q1-Leg-Balance-v0 physics=newton_mjwarp --num_envs 4096
uv run isaaclab train --rl_library rsl_rl --task Q1-Leg-Balance-v0 --checkpoint latest     # 續訓
uv run isaaclab play  --rl_library rsl_rl --task Q1-Leg-Balance-v0 --checkpoint best --video viz:kit
```

## 4. 分段訓練、進度條、續訓（本站做法）

`example.py` 不用 CLI，而是直接驅動 `OnPolicyRunner`，每 `chunk` 次迭代：

1. `runner.learn(num_learning_iterations=chunk)`
2. `runner.save(latest.pt)`
3. 寫 `progress.json`（iteration、elapsed、ETA、steps/s、mean reward）

網頁每 3 秒讀 `progress.json`。關掉瀏覽器、Pinggy 斷線，訓練都在 Spark 上繼續；回來按「續訓」會帶 `--resume`，從 `latest.pt` 載入 optimizer 與 iteration。預算到期自動進入評估（32 回合存活率、直立度、高度誤差）並錄影。

## 5. 調參建議（配合滑桿）

| 症狀 | 試 |
| --- | --- |
| 一直摔 | ↑ `w_balance`、↓ `command_vx`、關 `push_interval_s` |
| 站得住但抖 | ↑ `w_smooth`、↑ `w_energy`、↓ `learning_rate` |
| 不往前走 | ↑ `w_track`、確認 `command_vx`>0、↑ `entropy_coef` 探索 |
| reward 曲線平 | ↑ `num_envs`、↑ `max_iterations`；檢查 termination 是否太早 |

先 Newton（快）把 reward 調到會站，再切 PhysX 驗收。每次改動只動一個滑桿，讓對照有意義。

## 6. 和本站既有 worker 的關係

Studio 的「Spark 真實訓練」走 Lab 2.x 的 `train_store_job.py`（motion tracking）。本課是 **Isaac Lab 3.0 版**的站立／速度追蹤任務；兩者 checkpoint 不互通（第三課的四元數與 ProxyArray 差異）。完成的訓練會保留在「我的訓練」清單，可回來看影片、rollout 與當時的參數，並以它為起點再改。
