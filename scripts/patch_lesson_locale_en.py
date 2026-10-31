#!/usr/bin/env python3
"""Add English locale fields to tutorials/categories.json and lessons/*/lesson.json."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LESSONS_DIR = ROOT / "tutorials" / "lessons"
CATEGORIES_PATH = ROOT / "tutorials" / "categories.json"

CATEGORY_DESCRIPTION_EN = {
    "architecture": (
        "uv install for Isaac Lab 3, the isaaclab CLI, presets, and launch_simulation."
    ),
    "physics": (
        "Switching PhysX, Newton MuJoCo Warp, and OV PhysX; dt/solver settings and consistency comparisons."
    ),
    "migration": (
        "ProxyArray, XYZW quaternions, index/mask writes, actuator collection, and the removed --headless flag."
    ),
    "urdf": (
        "URDF importer 3.0, UrdfConverterCfg, ArticulationCfg, joint limits, and Q1 mimic joints."
    ),
    "actuator": (
        "Implicit, DC motor, and Delayed PD actuators; actuator_effort_limit vs joint_effort_limit; PD gains."
    ),
    "training": (
        "ManagerBased environments, reward/termination/event terms, RSL-RL PPO, checkpoints, and resume."
    ),
    "recording": (
        "--viz kit/newton_gl/viser/rerun, VideoRecorderCfg, HEADLESS=1, and browser streaming."
    ),
    "deployment": (
        "LEAPP/ONNX export, PhysX↔Newton policy transfer, and the Q1 deployment contract."
    ),
    "hardware": (
        "Q1 explode view, sensor/audio layout, skins and mass, mapped to Isaac Lab actuator parameters."
    ),
}

LESSON_PATCHES: dict[str, dict] = {
    "l01-isaaclab3-overview": {
        "params": {
            "physics": {
                "label_en": "Physics preset",
                "help_en": (
                    "physics=newton_mjwarp does not need full Isaac Sim, but still requires "
                    "uv sync --extra importers (URDF→USD). isaacsim_physx needs --extra isaacsim."
                ),
            },
            "num_envs": {
                "label_en": "Parallel env count",
                "help_en": "Use a small count on first launch to verify the scene and assets, then scale up.",
            },
            "viz": {
                "label_en": "Visualizer",
                "help_en": (
                    "3.0 defaults to headless; pick a viz option to open a window. "
                    "Set HEADLESS=1 on machines without a display."
                ),
            },
            "sim_seconds": {
                "label_en": "Simulation duration (s)",
                "help_en": "How long the standing scene runs; confirms the pipeline, not training.",
            },
        },
        "quiz": {
            "q1": {
                "question_en": "What is the only supported installation method in Isaac Lab 3.0?",
                "choices_en": [
                    "./isaaclab.sh --install",
                    "conda env create",
                    "uv sync (optionally with --extra isaacsim)",
                    "pip install isaaclab into an existing 2.x environment",
                ],
                "explain_en": (
                    "3.0 removed isaaclab.sh / isaaclab.bat and --install/--conda/--uv; "
                    "the official path is uv only, in a fresh environment—do not upgrade a 2.x env."
                ),
            },
            "q2": {
                "question_en": "What do you pass on the CLI to run the same task with Newton MuJoCo Warp physics?",
                "choices_en": [
                    "--physics newton",
                    "physics=newton_mjwarp",
                    "--backend mjwarp",
                    "--engine newton",
                ],
                "explain_en": (
                    "Presets use key=value: physics=newton_mjwarp, renderer=..., and --viz ... "
                    "are three independent choices."
                ),
            },
            "q3": {
                "question_en": "Which command lists supported physics/renderer/visualizer combinations for a task?",
                "choices_en": [
                    "uv run isaaclab list_envs --show_presets",
                    "cat pyproject.toml",
                    "uv run isaaclab train --all",
                    "python -c 'import isaaclab; print(isaaclab.presets)'",
                ],
                "explain_en": (
                    "list_envs --show_presets lists presets per task; train --task X --help also works. "
                    "Combinations are task-specific, not global."
                ),
            },
            "q4": {
                "question_en": "If you do not pass --viz in 3.0, what happens?",
                "choices_en": [
                    "Opens a Kit window",
                    "Errors until you specify one",
                    "Runs headless with no window",
                    "Automatically opens newton_gl",
                ],
                "explain_en": (
                    "3.0 removed the --headless flag: no visualizer means headless; "
                    "pick --viz to open a window."
                ),
            },
        },
        "run": {
            "note_en": (
                "This example only starts simulation and holds Q1 upright for a few seconds "
                "to verify the uv environment, preset, and asset paths; it does not train."
            ),
        },
    },
    "l02-multi-backend-physics": {
        "params": {
            "physics": {
                "label_en": "Physics preset",
                "help_en": "Run twice—once with Newton, once with PhysX—then compare result.json.",
            },
            "dt": {
                "label_en": "Physics dt (s)",
                "help_en": "1/200 s is a common starting point. Large dt causes penetration; small dt is slower.",
            },
            "drop_height": {
                "label_en": "Initial height (m)",
                "help_en": "Slightly above the Q1 standing height so you can watch landing and balance.",
            },
            "num_envs": {"label_en": "Parallel env count", "help_en": ""},
            "sim_seconds": {"label_en": "Simulation duration (s)", "help_en": ""},
            "solver_iterations": {
                "label_en": "Solver iterations",
                "help_en": (
                    "PhysX maps to solver_position_iteration_count; "
                    "Newton MJWarp maps to MuJoCo solver iterations."
                ),
            },
        },
        "quiz": {
            "q1": {
                "question_en": "In 3.0, how do you make a task support multiple physics backends in the env config?",
                "choices_en": [
                    "Two separate env cfgs with different imports",
                    "sim.physics = a PresetCfg subclass listing isaacsim_physx / newton_mjwarp / default",
                    "if/else imports in __init__",
                    "CLI only; code cannot express it",
                ],
                "explain_en": (
                    "PresetCfg lists selectable backends and default; CLI physics=NAME replaces the whole sim.physics block."
                ),
            },
            "q2": {
                "question_en": "What is special about ProxyArray on the Newton backend?",
                "choices_en": [
                    "Cannot use .torch",
                    "After reset, buffers may be rebuilt—do not cache .torch across reset",
                    "CPU only",
                    "No contact sensors",
                ],
                "explain_en": (
                    "Newton auto-refreshes and may swap wrappers on reset; PhysX is pull-to-refresh with stable buffers. "
                    "Re-read attributes each step on both."
                ),
            },
            "q3": {
                "question_en": "Which backend can train without installing Isaac Sim?",
                "choices_en": ["isaacsim_physx", "isaacsim_rtx", "newton_mjwarp", "All of them"],
                "explain_en": (
                    "Kit-less workflow: Newton physics plus Newton/GL visualization needs no Isaac Sim. "
                    "PhysX and RTX need --extra isaacsim."
                ),
            },
            "q4": {
                "question_en": "Can a policy trained in PhysX run unchanged on Newton?",
                "choices_en": [
                    "Always, because the API is the same",
                    "Never; must retrain from scratch",
                    "Usually needs the official policy-transfer guide, contact/actuator tuning, and re-validation",
                    "Same dynamics if dt matches",
                ],
                "explain_en": (
                    "Same API does not mean same dynamics; the official PhysX↔Newton transfer guide expects re-evaluation, not free portability."
                ),
            },
        },
        "run": {
            "note_en": "Each run uses one backend; compare the two result.json files side by side.",
        },
    },
    "l03-migrating-2x-to-3": {
        "params": {
            "scan_path": {
                "label_en": "File or folder to scan",
                "help_en": (
                    "e.g. q1_leg_urdf leg_humanoid_lab/isaac_env.py paths on the worker machine (absolute path)."
                ),
            },
            "show_identity_only": {
                "label_en": "List identity quaternions only",
                "help_en": "Only flag hard-coded (1,0,0,0) that must change.",
            },
            "max_findings": {"label_en": "Max findings to list", "help_en": ""},
        },
        "quiz": {
            "q1": {
                "question_en": "In 3.0, robot.data.joint_pos returns what?",
                "choices_en": [
                    "torch.Tensor",
                    "numpy.ndarray",
                    "ProxyArray (use .torch for tensor, .warp for warp array)",
                    "list",
                ],
                "explain_en": "All asset/sensor .data.* return ProxyArray; .torch is a zero-copy cached view.",
            },
            "q2": {
                "question_en": "How do you write the identity quaternion in 3.0?",
                "choices_en": ["(1, 0, 0, 0)", "(0, 0, 0, 1)", "(0, 1, 0, 0)", "Either works"],
                "explain_en": (
                    "3.0 uses XYZW everywhere; 2.x WXYZ identity (1,0,0,0) is a 180° rotation about x in 3.0."
                ),
            },
            "q3": {
                "question_en": "What replaces robot.set_joint_position_target(q) from 2.x?",
                "choices_en": [
                    "robot.data.joint_pos_target = q",
                    "robot.actuators.target_command.set_position_index(value=q)",
                    "robot.write_joint_target(q)",
                    "Unchanged",
                ],
                "explain_en": (
                    "Actuator commands and telemetry moved to ActuatorCollection; setters are keyword-only; "
                    "old APIs still work with DeprecationWarning."
                ),
            },
            "q4": {
                "question_en": "What happened to --headless from 2.x in 3.0?",
                "choices_en": [
                    "Still supported",
                    "Renamed to --no-viz",
                    "Removed: omit --viz for headless; set HEADLESS=1 for offscreen rendering",
                    "Replaced by --viz none",
                ],
                "explain_en": (
                    "Visualization is chosen with --viz; kit uses Replicator offscreen when HEADLESS=1 "
                    "(recording needs --enable_cameras); newton_gl uses EGL automatically."
                ),
            },
            "q5": {
                "question_en": "How should write_root_pose_to_sim(pose, env_ids) from 2.x be updated?",
                "choices_en": [
                    "write_root_pose_to_sim_index(root_pose=pose, env_ids=env_ids)",
                    "write_root_pose(pose)",
                    "set_root_pose(pose, env_ids)",
                    "No change",
                ],
                "explain_en": (
                    "3.0 splits index (partial data + indices) from mask (full data + boolean mask) "
                    "to avoid 2.x dual semantics on one parameter."
                ),
            },
        },
        "run": {
            "note_en": "The scanner is pure Python and needs no Isaac; runs in the sandbox or any Lab 3 env.",
        },
    },
    "l04-urdf-to-usd-articulation": {
        "params": {
            "physics": {
                "label_en": "Physics preset",
                "help_en": (
                    "newton_mjwarp: kitless UrdfFileCfg (default). "
                    "isaacsim_physx: explicit UrdfConverter→USD; needs full Isaac Sim."
                ),
            },
            "fix_base": {
                "label_en": "Fix base",
                "help_en": "Fixing the base makes joint inspection easier; turn off for training.",
            },
            "merge_fixed_joints": {
                "label_en": "Merge fixed joints",
                "help_en": (
                    "Implemented as URDF XML preprocessing in 3.0; Q1 Leg’s six locked joints merge into parent links."
                ),
            },
            "stiffness": {"label_en": "Default drive stiffness", "help_en": ""},
            "damping": {"label_en": "Default drive damping", "help_en": ""},
            "link_density": {
                "label_en": "Link density when inertia missing (kg/m³)",
                "help_en": "0 uses existing URDF inertial; Q1 URDF already has inertial data.",
            },
            "num_envs": {"label_en": "Parallel env count", "help_en": ""},
        },
        "quiz": {
            "q1": {
                "question_en": "Which UrdfConverterCfg option is still supported in 3.0?",
                "choices_en": [
                    "convert_mimic_joints_to_normal_joints",
                    "replace_cylinders_with_capsules",
                    "merge_fixed_joints",
                    "root_link_name",
                ],
                "explain_en": (
                    "The other three are unsupported in the new importer (warnings); "
                    "merge_fixed_joints is done via XML preprocessing."
                ),
            },
            "q2": {
                "question_en": "How is the output USD path chosen in 3.0?",
                "choices_en": [
                    "usd_file_name parameter",
                    "Auto: {usd_dir}/{robot_name}/{robot_name}.usda",
                    "Always robot.usd",
                    "--output flag",
                ],
                "explain_en": "usd_file_name can no longer override; make_instanceable is ignored (instanceable by default).",
            },
            "q3": {
                "question_en": "What is the status of NaturalFrequencyGainsCfg in 3.0?",
                "choices_en": [
                    "Recommended",
                    "Deprecated; use PDGainsCfg with stiffness/damping directly",
                    "Renamed to ResonanceGainsCfg",
                    "Wheel-only",
                ],
                "explain_en": (
                    "compute_natural_stiffness was removed from the importer; "
                    "use PDGainsCfg (stiffness=None keeps URDF values)."
                ),
            },
            "q4": {
                "question_en": "How should Q1 Wheel knee/finger mimic joints be handled in 3.0?",
                "choices_en": [
                    "Use convert_mimic_joints_to_normal_joints",
                    "Importer keeps mimic; PPO’s 23 control axes must exclude mimic slaves—use manifest control_joints in joint_names_expr",
                    "Treat all as independent actuators",
                    "Delete mimic tags",
                ],
                "explain_en": (
                    "Mimic slaves are not control channels; manifest control_joints defines the 23-axis order; "
                    "ActuatorCfg joint_names_expr lists only those."
                ),
            },
        },
        "run": {
            "note_en": "Converts URDF→USD, spawns Articulation, writes joint/limit/mass table to result.json.",
        },
    },
    "l05-actuators-pd-and-limits": {
        "params": {
            "actuator_type": {
                "label_en": "Actuator model",
                "help_en": (
                    "implicit: PD inside the solver; dc_motor: speed–torque envelope; "
                    "delayed_pd: command delay."
                ),
            },
            "stiffness": {"label_en": "Stiffness Kp", "help_en": ""},
            "damping": {"label_en": "Damping Kd", "help_en": ""},
            "actuator_effort_limit": {
                "label_en": "actuator_effort_limit (N·m)",
                "help_en": "Rated limit of the actuator model; explicit models clip output to this first.",
            },
            "joint_effort_limit": {
                "label_en": "joint_effort_limit (N·m)",
                "help_en": (
                    "Solver joint limit; 3.0 no longer inflates to 1e9—set ≥ actuator_effort_limit to avoid double clipping."
                ),
            },
            "armature": {
                "label_en": "Armature (kg·m²)",
                "help_en": "Motor rotor inertia; stabilizes high-gain PD.",
            },
            "perturb_nm": {
                "label_en": "External perturbation (N·m, at step 2 s)",
                "help_en": "",
            },
            "num_envs": {"label_en": "Parallel env count", "help_en": ""},
            "sim_seconds": {"label_en": "Simulation duration (s)", "help_en": ""},
        },
        "quiz": {
            "q1": {
                "question_en": "What is the difference between actuator_effort_limit and joint_effort_limit in 3.0?",
                "choices_en": [
                    "Same meaning",
                    "Former is the actuator model rated limit, latter is the solver joint limit; explicit outputs clip through both",
                    "Former for PhysX, latter for Newton",
                    "Former is velocity limit",
                ],
                "explain_en": (
                    "2.x inflated explicit solver limits to 1e9; 3.0 keeps configured values—"
                    "set joint_effort_limit ≥ actuator_effort_limit."
                ),
            },
            "q2": {
                "question_en": "In 3.0, how should you read applied joint torque?",
                "choices_en": [
                    "robot.data.applied_torque",
                    "robot.actuators.applied_effort.torch",
                    "robot.get_torque()",
                    "env.torque",
                ],
                "explain_en": "data.applied_torque is a deprecated forwarder; telemetry lives on ActuatorCollection.",
            },
            "q3": {
                "question_en": "What is the main difference between ImplicitActuatorCfg and DCMotorCfg?",
                "choices_en": [
                    "Implicit is PhysX-only",
                    "Implicit PD is solved inside the solver (stable); DCMotor is explicit with speed–torque saturation",
                    "DCMotor ignores stiffness",
                    "No difference",
                ],
                "explain_en": (
                    "Explicit models (DCMotor, DelayedPD, ActuatorNet) compute torque in Python then send effort; "
                    "Implicit uses solver drives."
                ),
            },
            "q4": {
                "question_en": "How do you change armature for some envs at runtime in 3.0?",
                "choices_en": [
                    "robot.actuators['legs'].armature = x",
                    "robot.write_joint_armature_to_sim_index(armature, env_ids=..., joint_ids=...)",
                    "Edit URDF and reload",
                    "Not possible",
                ],
                "explain_en": (
                    "Runtime group armature property was removed; read data.joint_armature, "
                    "write with write_joint_armature_to_sim_index()."
                ),
            },
            "q5": {
                "question_en": "If you set effort_limit=100 and actuator_effort_limit=150 in the same actuator group?",
                "choices_en": [
                    "Average them",
                    "Take the minimum",
                    "Conflicting values raise ValueError; equal values use canonical names with a warning",
                    "Ignore the new name",
                ],
                "explain_en": (
                    "Deprecated and canonical names together: equal→warning and canonical; "
                    "different→ValueError. Prefer canonical names only."
                ),
            },
        },
        "run": {
            "note_en": "Outputs tracking RMSE, torque peaks, and saturation fraction; rerun with different gains and limits.",
        },
    },
    "l06-manager-based-ppo": {
        "params": {
            "physics": {"label_en": "Physics preset", "help_en": ""},
            "njmax": {
                "label_en": "MJWarp njmax (constraint cap per env)",
                "help_en": "Too small causes nefc overflow → NaN observations. Q1 needs ≥1000 (Lab locomotion preset).",
            },
            "nconmax": {"label_en": "MJWarp nconmax (contact cap per env)", "help_en": ""},
            "solver_iterations": {"label_en": "MJWarp solver iterations", "help_en": ""},
            "num_envs": {"label_en": "Parallel env count", "help_en": ""},
            "max_iterations": {"label_en": "PPO iteration cap", "help_en": ""},
            "budget_minutes": {
                "label_en": "Training budget (minutes)",
                "help_en": "Saves and evaluates when time is up; use Resume to continue from latest.pt.",
            },
            "command_vx": {
                "label_en": "Target forward velocity (m/s)",
                "help_en": "0 = balance only; >0 enables velocity tracking.",
            },
            "w_balance": {"label_en": "Reward: upright / height", "help_en": ""},
            "w_track": {"label_en": "Reward: velocity tracking", "help_en": ""},
            "w_energy": {"label_en": "Reward: torque penalty (×1e-4)", "help_en": ""},
            "w_smooth": {"label_en": "Reward: action smoothness", "help_en": ""},
            "learning_rate": {"label_en": "PPO learning rate", "help_en": ""},
            "entropy_coef": {"label_en": "PPO entropy coef", "help_en": ""},
            "push_interval_s": {
                "label_en": "Event: random push interval (s)",
                "help_en": "0 disables pushes.",
            },
        },
        "quiz": {
            "q1": {
                "question_en": "In a manager-based env, where are reward weights configured?",
                "choices_en": [
                    "Hard-coded in env.step()",
                    "RewardTermCfg(func=..., weight=..., params=...)",
                    "PPO cfg only",
                    "URDF",
                ],
                "explain_en": (
                    "Each term is func + weight + params; tune rewards without changing the env class—"
                    "what the sliders on this page do."
                ),
            },
            "q2": {
                "question_en": "What is the unified 3.0 flag to resume RSL-RL training?",
                "choices_en": [
                    "--resume --load_run RUN",
                    "--checkpoint <path|latest|best>",
                    "--continue",
                    "--init_from",
                ],
                "explain_en": (
                    "--resume/--load_run and --use_pretrained_checkpoint were replaced by --checkpoint; "
                    "latest/best pick from the newest compatible run."
                ),
            },
            "q3": {
                "question_en": "If num_envs goes from 1024 to 4096, PPO transitions per iteration?",
                "choices_en": [
                    "Unchanged",
                    "×4 (num_steps_per_env × num_envs)",
                    "÷4",
                    "Depends on learning rate only",
                ],
                "explain_en": (
                    "RSL-RL collects num_steps_per_env × num_envs steps per iteration; "
                    "env count is the main throughput knob."
                ),
            },
            "q4": {
                "question_en": "How should Q1 Wheel wheels appear in the action space?",
                "choices_en": [
                    "JointPositionActionCfg",
                    "JointVelocityActionCfg (or effort)—continuous joint position is not meaningful",
                    "Omit from actions",
                    "BinaryJointAction",
                ],
                "explain_en": (
                    "Continuous joints have unbounded angle; use velocity (or torque) actions with stiffness=0 actuators."
                ),
            },
            "q5": {
                "question_en": "What does EventTermCfg(mode='interval') push_by_setting_velocity do?",
                "choices_en": [
                    "Reset joints",
                    "Periodic random root velocity pushes for robustness",
                    "Change reward",
                    "Record video",
                ],
                "explain_en": (
                    "Events have startup/reset/interval modes; interval pushes are common domain randomization for legged robots."
                ),
            },
        },
        "run": {
            "note_en": (
                "Trains for real. Progress bar reads progress.json; latest.pt updates each chunk; "
                "resume via Pinggy after disconnect. Record phase produces result.mp4 and rollout.json."
            ),
        },
    },
    "l07-visualizers-and-recording": {
        "params": {
            "viz": {
                "label_en": "Visualizer",
                "help_en": "viser/rerun are Python services in the browser; kit needs Isaac Sim.",
            },
            "record_video": {
                "label_en": "Record result.mp4",
                "help_en": (
                    "ManagerBased+kit: VideoRecorderCfg(source='visualizer:kit'); "
                    "this demo/other viz: rollout.json → Three.js replay, mp4 via store ffmpeg."
                ),
            },
            "fps": {"label_en": "Recording FPS", "help_en": ""},
            "sim_seconds": {"label_en": "Simulation duration (s)", "help_en": ""},
            "motion": {"label_en": "Demo motion", "help_en": ""},
            "viser_port": {"label_en": "viser / rerun port", "help_en": ""},
        },
        "quiz": {
            "q1": {
                "question_en": "How do you run training headless in 3.0?",
                "choices_en": [
                    "--headless",
                    "HEADLESS=1 env var (or omit --viz)",
                    "--no-gui",
                    "--viz none plus --headless",
                ],
                "explain_en": (
                    "The --headless flag was removed; Kit windows depend on --viz kit and HEADLESS."
                ),
            },
            "q2": {
                "question_en": "What replaces gym.wrappers.RecordVideo from 2.x in 3.0?",
                "choices_en": [
                    "--video alone",
                    "VideoRecorderCfg(source='visualizer:kit') on env_cfg.video_recorders",
                    "Hand-written cv2",
                    "Recording not supported",
                ],
                "explain_en": (
                    "Recording is part of env cfg, sourced from a visualizer—not gym wrappers or render_mode."
                ),
            },
            "q3": {
                "question_en": "Which visualizer shows Newton simulation in a browser without Isaac Sim/Kit?",
                "choices_en": ["kit", "viser", "newton_rtx", "Omniverse streaming"],
                "explain_en": (
                    "viser and rerun are standalone Python services; newton_gl needs a local OpenGL window; "
                    "kit/newton_rtx need Isaac Sim."
                ),
            },
            "q4": {
                "question_en": "How do you attach a viser camera to SimulationCfg?",
                "choices_en": [
                    "VisualizerCfg(kind='viser', ...)",
                    "sim_cfg.visualizer_cfgs = [ViserVisualizerCfg(port=..., eye=..., lookat=...)]",
                    "ViewerCfg(eye, lookat)",
                    "CLI --viz only",
                ],
                "explain_en": (
                    "Base VisualizerCfg has no kind=; use concrete cfgs (ViserVisualizerCfg, etc.) on sim.visualizer_cfgs."
                ),
            },
        },
        "run": {
            "note_en": (
                "With viz=viser the store proxies the port to /viz/<run_id>/ for Pinggy browser viewing. "
                "Without Lab 3, only the rollout generator runs (pure Python) but Three.js replay still works."
            ),
        },
    },
    "l08-policy-export-deployment": {
        "params": {
            "checkpoint": {
                "label_en": "Checkpoint",
                "help_en": (
                    "latest/best or a .pt path; latest picks the newest run from your lesson 6 training on this store."
                ),
            },
            "export_format": {"label_en": "Export format", "help_en": ""},
            "opset": {"label_en": "ONNX opset", "help_en": ""},
            "verify_steps": {"label_en": "Numerical verify steps", "help_en": ""},
            "tolerance": {"label_en": "Tolerance (max abs error)", "help_en": ""},
            "control_hz": {"label_en": "Deploy control rate (Hz)", "help_en": ""},
            "cross_backend_check": {
                "label_en": "PhysX↔Newton cross-check",
                "help_en": (
                    "Needs Isaac Lab 3 + Isaac Sim; runs the same policy 10 s on the other backend and reports survival."
                ),
            },
        },
        "quiz": {
            "q1": {
                "question_en": "What is most often skipped in ONNX export but breaks deployment?",
                "choices_en": [
                    "Opset version",
                    "Observation order and EmpiricalNormalization statistics",
                    "File name",
                    "GPU model",
                ],
                "explain_en": (
                    "RSL-RL normalizer runs before the actor; trace it with the actor or the robot gets unnormalized obs."
                ),
            },
            "q2": {
                "question_en": "Mindset when running a Newton-trained policy on PhysX?",
                "choices_en": [
                    "It will always work",
                    "Re-validate (contacts/drives differ); fine-tune on PhysX if needed",
                    "Impossible",
                    "Identical if dt matches",
                ],
                "explain_en": (
                    "Official cross-backend guides mean verify and possibly fine-tune—not free portability."
                ),
            },
            "q3": {
                "question_en": "What are action_scale and default_joint_positions in policy_meta.json for?",
                "choices_en": [
                    "Cosmetics",
                    "Map network output to joint targets: target = default + scale × action",
                    "Author metadata",
                    "Pick physics backend",
                ],
                "explain_en": (
                    "Deploy code does not know training JointPositionActionCfg(scale=..., use_default_offset=True)—"
                    "meta must record it."
                ),
            },
            "q4": {
                "question_en": "What should wheel actions represent at deploy time for Q1 Wheel?",
                "choices_en": [
                    "Position targets",
                    "Velocity targets (matching JointVelocityActionCfg in training)",
                    "No output",
                    "Motor current",
                ],
                "explain_en": (
                    "Action semantics must match training; action_kinds marks position vs velocity per joint."
                ),
            },
        },
        "run": {
            "note_en": (
                "Export and numeric verify need torch only (no Isaac Lab). "
                "Without a lesson 6 checkpoint, a random same-shape actor demos the flow (demo=true in result.json)."
            ),
        },
    },
    "l09-hardware-explode-and-actuators": {
        "params": {
            "explode_amount": {
                "label_en": "Explode separation",
                "help_en": "0 = assembled; 1 = fully exploded. Lab Analyse/Explode animates to this value.",
            },
            "skin": {
                "label_en": "Outer skin",
                "help_en": "Browser preview material; written to hardware.json on run.",
            },
            "camera_preset": {
                "label_en": "Camera preset",
                "help_en": "Orbit camera preset angles.",
            },
            "tof_enabled": {"label_en": "Add ToF sensor", "help_en": ""},
            "tof_x": {"label_en": "ToF X (m)", "help_en": ""},
            "tof_y": {"label_en": "ToF Y (m)", "help_en": ""},
            "tof_z": {"label_en": "ToF Z (m)", "help_en": ""},
            "tof_size": {"label_en": "ToF size (m)", "help_en": ""},
            "tof_mass": {
                "label_en": "ToF mass (kg)",
                "help_en": "Added mass scales PD stiffness to simulate heavier load.",
            },
            "audio_enabled": {"label_en": "Add audio module", "help_en": ""},
            "audio_x": {"label_en": "Audio X (m)", "help_en": ""},
            "audio_y": {"label_en": "Audio Y (m)", "help_en": ""},
            "audio_z": {"label_en": "Audio Z (m)", "help_en": ""},
            "audio_size": {"label_en": "Audio size (m)", "help_en": ""},
            "audio_mass": {"label_en": "Audio mass (kg)", "help_en": ""},
            "stiffness": {
                "label_en": "Base stiffness Kp",
                "help_en": "ImplicitActuatorCfg.stiffness; scaled up with added mass.",
            },
            "damping": {"label_en": "Base damping Kd", "help_en": ""},
            "actuator_effort_limit": {"label_en": "actuator_effort_limit (N·m)", "help_en": ""},
            "joint_effort_limit": {"label_en": "joint_effort_limit (N·m)", "help_en": ""},
            "sim_seconds": {"label_en": "Simulation duration (s)", "help_en": ""},
            "num_envs": {"label_en": "Parallel env count", "help_en": ""},
            "fps": {"label_en": "Explode animation FPS", "help_en": ""},
        },
        "quiz": {
            "q1": {
                "question_en": "How does the browser explode view separate links?",
                "choices_en": [
                    "Edit URDF and re-export USD",
                    "Offset each link along link→root direction in local frames, animated on a timeline",
                    "Rotate camera only",
                    "Delete fixed joints",
                ],
                "explain_en": (
                    "Explode is visual: topology unchanged, links pushed away from torso/hip."
                ),
            },
            "q2": {
                "question_en": "Effect of ToF/audio mass parameters on the Isaac Lab run in this lesson?",
                "choices_en": [
                    "Browser color only",
                    "Increases load ratio; example scales ImplicitActuatorCfg stiffness/damping for tracking",
                    "Disables Newton",
                    "Forces PhysX",
                ],
                "explain_en": (
                    "Sensor mass is extra payload; the example scales PD by mass ratio—hardware config → actuator params."
                ),
            },
            "q3": {
                "question_en": "How should torque limits be set in 3.0?",
                "choices_en": [
                    "effort_limit alone is enough",
                    "actuator_effort_limit (model) and joint_effort_limit (solver)",
                    "torque_max only",
                    "URDF alone is enough",
                ],
                "explain_en": "3.0 splits limits; explicit model output clips through both.",
            },
            "q4": {
                "question_en": "Difference between Analyse/Explode and Run?",
                "choices_en": [
                    "Identical",
                    "Former: browser explode/rebuild; latter: same hardware+actuator params sent to Isaac Lab",
                    "Former only downloads webm",
                    "Run only changes skin",
                ],
                "explain_en": (
                    "Lab panel preview needs no GPU; Run executes physics+actuator tracking and writes hardware.json/rollout."
                ),
            },
            "q5": {
                "question_en": "Which parameters mount a sensor on the front of the torso?",
                "choices_en": [
                    "stiffness",
                    "tof_x / tof_y / tof_z (relative to center_torso_link)",
                    "num_envs",
                    "fps alone",
                ],
                "explain_en": (
                    "Accessories attach to center_torso_link; XYZ is relative offset, size/mass change shape and load."
                ),
            },
        },
        "run": {
            "note_en": (
                "Writes hardware.json, explode keyframes/rollout.json, and runs stance tracking with scaled ImplicitActuatorCfg. "
                "Without Lab, explode artifacts are still produced."
            ),
        },
    },
}


def patch_categories() -> None:
    data = json.loads(CATEGORIES_PATH.read_text(encoding="utf-8"))
    for cat in data.get("categories", []):
        cid = cat["id"]
        if cid in CATEGORY_DESCRIPTION_EN:
            cat["description_en"] = CATEGORY_DESCRIPTION_EN[cid]
    CATEGORIES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_lesson(lesson_id: str, patch: dict) -> None:
    path = LESSONS_DIR / lesson_id / "lesson.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    param_patch = patch.get("params", {})
    for p in data.get("params", []):
        key = p["key"]
        if key not in param_patch:
            continue
        en = param_patch[key]
        p["label_en"] = en["label_en"]
        p["help_en"] = en.get("help_en", "")

    quiz_patch = patch.get("quiz", {})
    for q in data.get("quiz", []):
        qid = q["id"]
        if qid not in quiz_patch:
            continue
        en = quiz_patch[qid]
        q["question_en"] = en["question_en"]
        q["choices_en"] = en["choices_en"]
        q["explain_en"] = en["explain_en"]
        if len(q["choices_en"]) != len(q["choices"]):
            raise ValueError(f"{lesson_id} quiz {qid}: choices_en length mismatch")

    run_patch = patch.get("run")
    if run_patch and data.get("run") and "note" in data["run"]:
        data["run"]["note_en"] = run_patch["note_en"]

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    patch_categories()
    for lesson_id, patch in LESSON_PATCHES.items():
        patch_lesson(lesson_id, patch)
    print(f"Patched categories.json and {len(LESSON_PATCHES)} lesson.json files.")


if __name__ == "__main__":
    main()
