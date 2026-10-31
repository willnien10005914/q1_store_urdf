#!/usr/bin/env python3
"""Lesson 06 — Q1 balance / velocity-tracking task (manager-based, Isaac Lab 3.0) trained with RSL-RL PPO.

    uv run [--extra isaacsim] python example.py --job-dir <dir> --params <dir>/params.json [--resume] [--phase train|record]

Train phase: chunked runner.learn → latest.pt + progress.json each chunk, then evaluation.json.
Record phase: loads latest.pt, rolls out one env, writes rollout.json (+ result.mp4 when a visualizer is available).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--job-dir", type=Path, required=True)
parser.add_argument("--params", type=Path, required=True)
parser.add_argument("--phase", choices=["train", "record"], default="train")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--chunk", type=int, default=20)
args = parser.parse_args()
P = json.loads(args.params.read_text())
JOB = args.job_dir
JOB.mkdir(parents=True, exist_ok=True)
LATEST = JOB / "latest.pt"
PROGRESS = JOB / "progress.json"
T0 = time.monotonic()
CONTROL = list(P.get("control_joints", []))
WHEELS = [j for j in CONTROL if "wheel" in j]
PD_JOINTS = [j for j in CONTROL if "wheel" not in j] or [".*"]


def write_json(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


from isaaclab.app import launch_simulation  # noqa: E402
from isaaclab.sim import SimulationCfg  # noqa: E402

sim_cfg = SimulationCfg(dt=1 / 200, render_interval=4, device="cuda:0")
if P.get("physics", "newton_mjwarp") == "isaacsim_physx":
    from isaaclab_physx.physics import PhysxCfg
    sim_cfg.physics = PhysxCfg()
else:
    # Match Lab locomotion velocity presets: Q1 has many contacts; default njmax=300 overflows
    # ("nefc overflow") and the solver then emits NaN observations into PPO.
    from isaaclab_newton.physics import (
        MJWarpSolverCfg,
        NewtonCfg,
        NewtonCollisionPipelineCfg,
        NewtonShapeCfg,
    )
    sim_cfg.physics = NewtonCfg(
        solver_cfg=MJWarpSolverCfg(
            njmax=int(P.get("njmax", 1000)),
            nconmax=int(P.get("nconmax", 300)),
            iterations=int(P.get("solver_iterations", 100)),
            ls_iterations=50,
            cone="pyramidal",
            impratio=1.0,
            integrator="implicitfast",
            use_mujoco_contacts=False,
        ),
        collision_cfg=NewtonCollisionPipelineCfg(max_triangle_pairs=2_500_000),
        num_substeps=int(P.get("num_substeps", 2)),
        default_shape_cfg=NewtonShapeCfg(margin=0.0, ke=160000.0, kd=1100.0),
    )
if args.phase == "record":
    from isaaclab.visualizers import VisualizerCfg
    os.environ.setdefault("HEADLESS", "1")
    sim_cfg.visualizer_cfgs = VisualizerCfg(eye=(3.0, -2.6, 1.9), lookat=(0.0, 0.1, 0.75))
write_json(PROGRESS, {"phase": "starting", "iteration": 0, "max_iterations": int(P.get("max_iterations", 300)), "elapsed_s": 0, "updated_at": time.time()})

with launch_simulation(sim_cfg):
    try:
        import lab3_compat
        lab3_compat.patch_urdf_importer()
    except Exception as exc:
        print(f"[q1] lab3_compat patch skipped: {exc}")
    import torch
    import isaaclab.sim as sim_utils
    import isaaclab.envs.mdp as mdp
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import ArticulationCfg, AssetBaseCfg
    from isaaclab.envs import ManagerBasedRLEnv, ManagerBasedRLEnvCfg
    from isaaclab.managers import EventTermCfg as EventTerm, ObservationGroupCfg as ObsGroup, ObservationTermCfg as ObsTerm
    from isaaclab.managers import RewardTermCfg as RewTerm, TerminationTermCfg as DoneTerm
    from isaaclab.scene import InteractiveSceneCfg
    from isaaclab.utils import configclass
    from isaaclab.utils.noise import UniformNoiseCfg as Unoise
    from isaaclab_rl.rsl_rl import (
        RslRlMLPModelCfg,
        RslRlOnPolicyRunnerCfg,
        RslRlPpoAlgorithmCfg,
        RslRlVecEnvWrapper,
        handle_deprecated_rsl_rl_cfg,
    )
    from isaaclab_rl.rsl_rl.utils import check_rsl_rl_version
    from rsl_rl.runners import OnPolicyRunner

    VX = float(P.get("command_vx", 0.0))
    actuators = {"pd": ImplicitActuatorCfg(joint_names_expr=PD_JOINTS, stiffness=float(P.get("stiffness", 100.0)), damping=float(P.get("damping", 5.0)),
                                           actuator_effort_limit=150.0, joint_effort_limit=150.0, armature=0.01)}
    if WHEELS:
        actuators["wheels"] = ImplicitActuatorCfg(joint_names_expr=WHEELS, stiffness=0.0, damping=2.0, actuator_effort_limit=30.0, joint_effort_limit=30.0)

    @configclass
    class Q1SceneCfg(InteractiveSceneCfg):
        ground = AssetBaseCfg(prim_path="/World/ground", spawn=sim_utils.GroundPlaneCfg())
        light = AssetBaseCfg(prim_path="/World/light", spawn=sim_utils.DomeLightCfg(intensity=2500.0))
        robot = ArticulationCfg(
            prim_path="{ENV_REGEX_NS}/Robot",
            spawn=sim_utils.UrdfFileCfg(asset_path=P["urdf"], fix_base=False, merge_fixed_joints=True,
                                        joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=100.0, damping=5.0))),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, float(P.get("spawn_height", 0.95))), rot=(0.0, 0.0, 0.0, 1.0), joint_pos=P.get("default_joint_positions", {})),
            actuators=actuators)

    @configclass
    class CommandsCfg:
        base_velocity = mdp.UniformVelocityCommandCfg(asset_name="robot", resampling_time_range=(6.0, 6.0), rel_standing_envs=0.0 if VX else 1.0,
                                                      heading_command=False, debug_vis=False,
                                                      ranges=mdp.UniformVelocityCommandCfg.Ranges(lin_vel_x=(VX, VX), lin_vel_y=(0.0, 0.0), ang_vel_z=(0.0, 0.0)))

    @configclass
    class ActionsCfg:
        joints = mdp.JointPositionActionCfg(asset_name="robot", joint_names=PD_JOINTS, scale=0.5, use_default_offset=True)
        if WHEELS:
            wheels = mdp.JointVelocityActionCfg(asset_name="robot", joint_names=WHEELS, scale=10.0)

    @configclass
    class ObservationsCfg:
        @configclass
        class PolicyCfg(ObsGroup):
            base_lin_vel = ObsTerm(func=mdp.base_lin_vel, noise=Unoise(n_min=-0.1, n_max=0.1))
            base_ang_vel = ObsTerm(func=mdp.base_ang_vel, noise=Unoise(n_min=-0.2, n_max=0.2))
            projected_gravity = ObsTerm(func=mdp.projected_gravity, noise=Unoise(n_min=-0.05, n_max=0.05))
            commands = ObsTerm(func=mdp.generated_commands, params={"command_name": "base_velocity"})
            joint_pos = ObsTerm(func=mdp.joint_pos_rel, noise=Unoise(n_min=-0.01, n_max=0.01))
            joint_vel = ObsTerm(func=mdp.joint_vel_rel, noise=Unoise(n_min=-1.5, n_max=1.5))
            actions = ObsTerm(func=mdp.last_action)

            def __post_init__(self):
                self.enable_corruption = True
                self.concatenate_terms = True
        policy: PolicyCfg = PolicyCfg()

    @configclass
    class RewardsCfg:
        alive = RewTerm(func=mdp.is_alive, weight=0.5)
        flat = RewTerm(func=mdp.flat_orientation_l2, weight=-float(P.get("w_balance", 2.0)))
        height = RewTerm(func=mdp.base_height_l2, weight=-float(P.get("w_balance", 2.0)), params={"target_height": float(P.get("target_height", 0.92))})
        track = RewTerm(func=mdp.track_lin_vel_xy_exp, weight=float(P.get("w_track", 1.0)), params={"command_name": "base_velocity", "std": 0.25})
        torques = RewTerm(func=mdp.joint_torques_l2, weight=-float(P.get("w_energy", 2.0)) * 1e-4)
        action_rate = RewTerm(func=mdp.action_rate_l2, weight=-float(P.get("w_smooth", 0.05)))

    @configclass
    class TerminationsCfg:
        time_out = DoneTerm(func=mdp.time_out, time_out=True)
        fell = DoneTerm(func=mdp.root_height_below_minimum, params={"minimum_height": 0.5})

    @configclass
    class EventCfg:
        reset_base = EventTerm(func=mdp.reset_root_state_uniform, mode="reset", params={"pose_range": {"yaw": (-0.3, 0.3)}, "velocity_range": {}})
        reset_joints = EventTerm(func=mdp.reset_joints_by_scale, mode="reset", params={"position_range": (0.9, 1.1), "velocity_range": (0.0, 0.0)})
        if float(P.get("push_interval_s", 5.0)) > 0:
            push = EventTerm(func=mdp.push_by_setting_velocity, mode="interval",
                             interval_range_s=(float(P["push_interval_s"]) * 0.8, float(P["push_interval_s"]) * 1.2),
                             params={"velocity_range": {"x": (-0.5, 0.5), "y": (-0.5, 0.5)}})

    @configclass
    class Q1BalanceEnvCfg(ManagerBasedRLEnvCfg):
        scene = Q1SceneCfg(num_envs=int(P.get("num_envs", 1024)), env_spacing=2.5)
        observations = ObservationsCfg()
        actions = ActionsCfg()
        commands = CommandsCfg()
        rewards = RewardsCfg()
        terminations = TerminationsCfg()
        events = EventCfg()

        def __post_init__(self):
            self.decimation = 4
            self.episode_length_s = 10.0
            self.sim = sim_cfg

    @configclass
    class Q1RunnerCfg(RslRlOnPolicyRunnerCfg):
        num_steps_per_env = 24
        max_iterations = int(P.get("max_iterations", 300))
        save_interval = 10 ** 9  # the lesson saves latest.pt itself after every chunk
        experiment_name = "q1_tutorial_balance"
        # rsl-rl >= 4/5: actor + critic MLPModel (class_name=MLPModel), not deprecated policy=ActorCritic.
        obs_groups = {"actor": ["policy"], "critic": ["policy"]}
        actor = RslRlMLPModelCfg(
            hidden_dims=[512, 256, 128], activation="elu", obs_normalization=False,
            distribution_cfg=RslRlMLPModelCfg.GaussianDistributionCfg(init_std=1.0),
        )
        critic = RslRlMLPModelCfg(hidden_dims=[512, 256, 128], activation="elu", obs_normalization=False)
        algorithm = RslRlPpoAlgorithmCfg(value_loss_coef=1.0, use_clipped_value_loss=True, clip_param=0.2, entropy_coef=float(P.get("entropy_coef", 0.005)),
                                         num_learning_epochs=5, num_mini_batches=4, learning_rate=float(P.get("learning_rate", 1e-3)), schedule="adaptive",
                                         gamma=0.99, lam=0.95, desired_kl=0.01, max_grad_norm=1.0)

    env_cfg = Q1BalanceEnvCfg()
    if args.phase == "record":
        env_cfg.scene.num_envs = 1
        env_cfg.events.push = None if hasattr(env_cfg.events, "push") else None
    env = ManagerBasedRLEnv(cfg=env_cfg)
    wrapped = RslRlVecEnvWrapper(env, clip_actions=1.0)
    # rsl-rl 5.x: to_dict() still emits legacy stochastic/init_noise_std unless we migrate first.
    runner_cfg = handle_deprecated_rsl_rl_cfg(Q1RunnerCfg(), check_rsl_rl_version())
    runner = OnPolicyRunner(wrapped, runner_cfg.to_dict(), log_dir=str(JOB), device=env.device)

    if args.phase == "train":
        iteration = 0
        previous = json.loads(PROGRESS.read_text()) if args.resume and PROGRESS.exists() else {}
        elapsed_before = float(previous.get("elapsed_s", 0.0)) if args.resume else 0.0
        if args.resume and LATEST.exists():
            runner.load(str(LATEST), load_optimizer=True)
            iteration = int(runner.current_learning_iteration)
            print(f"[lesson06] resumed from latest.pt at iteration {iteration}")
        budget = float(P.get("budget_minutes", 10)) * 60.0
        deadline = time.monotonic() + max(60.0, budget - elapsed_before)
        steps_per_iter = runner_cfg.num_steps_per_env * env.num_envs
        rate = None
        while iteration < runner_cfg.max_iterations and time.monotonic() < deadline:
            chunk = max(1, min(args.chunk, runner_cfg.max_iterations - iteration))
            t_chunk = time.monotonic()
            runner.learn(num_learning_iterations=chunk, init_at_random_ep_len=True)
            iteration = int(runner.current_learning_iteration)
            rate = chunk / max(1e-6, time.monotonic() - t_chunk)
            runner.save(str(LATEST))
            elapsed = elapsed_before + (time.monotonic() - T0)
            eta = min((runner_cfg.max_iterations - iteration) / rate, max(0.0, deadline - time.monotonic()))
            write_json(PROGRESS, {"phase": "training", "iteration": iteration, "max_iterations": runner_cfg.max_iterations, "elapsed_s": round(elapsed, 1),
                                  "eta_s": round(eta, 1), "iterations_per_second": round(rate, 3), "steps_per_second": round(steps_per_iter * rate),
                                  "num_envs": env.num_envs, "budget_s": budget, "updated_at": time.time()})
        runner.save(str(LATEST))
        # --- evaluation: deterministic policy, survival / upright / height over fixed episodes
        write_json(PROGRESS, {"phase": "evaluating", "iteration": iteration, "max_iterations": runner_cfg.max_iterations, "elapsed_s": round(elapsed_before + time.monotonic() - T0, 1), "updated_at": time.time()})
        policy = runner.get_inference_policy(device=env.device)
        # RslRlVecEnvWrapper.get_observations() returns a TensorDict (not a (obs, info) tuple).
        obs = wrapped.get_observations() if hasattr(wrapped, "get_observations") else wrapped.reset()[0]
        survived = torch.ones(env.num_envs, dtype=torch.bool, device=env.device)
        upright, heights, n = 0.0, 0.0, 0
        robot = env.scene["robot"]
        with torch.inference_mode():
            for _ in range(int(env.max_episode_length) - 1):
                obs, _, dones, _ = wrapped.step(policy(obs))
                survived &= ~(dones.bool() & env.termination_manager.terminated)
                gravity = robot.data.projected_gravity_b.torch
                upright += (-gravity[:, 2]).mean().item()
                heights += robot.data.root_pos_w.torch[:, 2].mean().item()
                n += 1
        evaluation = {"survival_rate": round(survived.float().mean().item(), 4), "upright": round(upright / max(n, 1), 4),
                      "mean_height_m": round(heights / max(n, 1), 4), "height_error": round(abs(heights / max(n, 1) - float(P.get("target_height", 0.92))), 4),
                      "iterations": iteration, "num_envs": env.num_envs, "elapsed_s": round(elapsed_before + time.monotonic() - T0, 1),
                      "physics": P.get("physics", "newton_mjwarp"), "params": {k: P[k] for k in P if k.startswith("w_") or k in ("command_vx", "learning_rate", "entropy_coef", "push_interval_s")}}
        evaluation["passed"] = evaluation["survival_rate"] >= 0.9 and evaluation["upright"] >= 0.9 and evaluation["height_error"] < 0.1
        write_json(JOB / "evaluation.json", evaluation)
        write_json(PROGRESS, {"phase": "trained", "iteration": iteration, "max_iterations": runner_cfg.max_iterations, "elapsed_s": evaluation["elapsed_s"], "eta_s": 0, "passed": evaluation["passed"], "updated_at": time.time()})
        print("[lesson06] evaluation " + json.dumps(evaluation))
    else:
        runner.load(str(LATEST), load_optimizer=False)
        policy = runner.get_inference_policy(device=env.device)
        robot = env.scene["robot"]
        obs = wrapped.reset()[0]
        frames = []
        writer = None
        video = JOB / "result.mp4"
        try:
            import imageio.v2 as imageio
            writer = imageio.get_writer(str(video), fps=50, codec="libx264", quality=8, macro_block_size=16, ffmpeg_params=["-movflags", "+faststart"])
        except Exception as error:  # video is optional: rollout.json always ships
            print(f"[lesson06] video disabled: {error}")
        with torch.inference_mode():
            for step in range(int(env.max_episode_length) - 1):
                frames.append({"time": round(step * env.step_dt, 4), "q": robot.data.joint_pos.torch[0].tolist(),
                               "root": (robot.data.root_pos_w.torch[0] - env.scene.env_origins[0]).tolist(),
                               "quaternion_xyzw": robot.data.root_quat_w.torch[0].tolist()})
                if writer is not None:
                    frame = env.render()
                    if frame is not None:
                        writer.append_data(frame)
                obs, _, dones, _ = wrapped.step(policy(obs))
                if bool(dones[0]):
                    break
        if writer is not None:
            writer.close()
        rollout = {"source": "isaaclab3_tutorial_l06", "joint_names": list(robot.joint_names), "dt": env.step_dt, "frames": frames,
                   "survived": len(frames) >= int(env.max_episode_length) - 2, "duration_s": round(len(frames) * env.step_dt, 3), "quaternion_order": "xyzw"}
        write_json(JOB / "rollout.json", rollout)
        write_json(JOB / "record.json", {"video": video.name if writer is not None and video.exists() else None, "frames": len(frames), "fps": 50, "survived": rollout["survived"]})
        print("RECORD_RESULT " + json.dumps({"frames": len(frames), "video": writer is not None}))
    env.close()
