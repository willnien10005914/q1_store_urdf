# Isaac Lab 3.0 architecture overview

Isaac Lab 3.0 (2026 release) is the largest change since 2.x. It is not “a few new APIs”—**installation, launch flow, physics backends, and data access** were redesigned. This lesson builds the map; later lessons go deeper on each topic.

## 1. Three things that matter most

| Area | Isaac Lab 2.x | Isaac Lab 3.0 |
| --- | --- | --- |
| Install | `./isaaclab.sh --install`, conda | **uv only**: `uv sync --extra isaacsim` |
| Launch | Each RL library’s own `train.py` | **Unified** `uv run isaaclab train --rl_library rsl_rl --task ...` |
| Backend | Tied to Isaac Sim PhysX | **Preset switching**: `physics=newton_mjwarp`, `renderer=...`, `--viz ...` |

Two other changes hit old code immediately (lesson 3 covers them in detail):
data properties return `ProxyArray` (add `.torch`), and quaternions move from WXYZ to **XYZW**.

## 2. Install: new environment, do not upgrade in place

```bash
git clone --branch v3.0.0 --depth 1 https://github.com/isaac-sim/IsaacLab.git
cd IsaacLab
# Newton-only (no Isaac Sim; runs on machines without Kit)
uv run isaaclab list_envs --show_presets
# When you need Isaac Sim PhysX / RTX
uv sync --extra isaacsim
```

Release notes for 3.0 list Python 3.12, Isaac Sim 6.x, PyTorch 2.1x / CUDA 13, Newton 1.6, RSL-RL 5.5.
Do **not** upgrade an existing `~/IsaacLab` (2.3.2) in place; this store’s worker uses `Q1_ISAACLAB3_ROOT` for a separate 3.0 checkout.

## 3. One environment, many backends

Physics, rendering, and visualization are three independent choices (within task support):

```text
physics=newton_mjwarp | isaacsim_physx | ovphysx
renderer=newton_renderer | ovrtx | isaacsim_rtx
--viz newton_gl | newton_rtx | viser | rerun | kit
```

```bash
uv run isaaclab train --rl_library rsl_rl --task Isaac-Cartpole physics=newton_mjwarp --viz newton_gl
uv run isaaclab train --task Isaac-Cartpole --help   # allowed combinations for this task
```

Why it matters: Q1 stand/walk tasks can iterate rewards quickly on **Newton MJWarp** (no Kit), then switch to **Isaac Sim PhysX** for final validation and RTX recording. Lesson 2 compares the two backends.

## 4. Launch flow in code

2.x used `AppLauncher` to open Isaac Sim. 3.0 uses `launch_simulation`; `SimulationCfg` decides whether Kit starts:

```python
from isaaclab.app import launch_simulation
from isaaclab.sim import SimulationCfg

sim_cfg = SimulationCfg(dt=1 / 200, device="cuda:0")
with launch_simulation(sim_cfg) as launch:      # Kit or not depends on physics/renderer/viz
    import isaaclab.sim as sim_utils             # import runtime modules after launch
    sim = sim_utils.SimulationContext(sim_cfg)
    ...
```

Rule: **import runtime-dependent modules after `launch_simulation`**. Same idea as 2.x “AppLauncher first, then import”—only the entry point changed.

## 5. What the example on the right does

`example.py` will:

1. Read `params.json` from this store (physics, num_envs, viz, sim_seconds).
2. Start the selected backend with `launch_simulation`.
3. Spawn Q1 (biped or wheeled, per robot choice) into `num_envs` environments via `UrdfFileCfg`.
4. Hold the default standing pose for `sim_seconds`, writing `progress.json` to the job directory for the web progress bar.

Try switching `physics` from `newton_mjwarp` to `isaacsim_physx` and compare startup time and logs; then set `num_envs` to 1024 and watch GPU memory.

## 6. Common errors

- `ModuleNotFoundError: isaacsim`: you chose `isaacsim_physx` without `uv sync --extra isaacsim`.
- No window: 3.0 defaults to headless—add `--viz`; on headless servers set `HEADLESS=1`.
- `Unsupported preset combination`: the task does not offer that combo—check with `list_envs --show_presets`.

> Next lesson: **multi-backend physics**—the same Q1 drop test on PhysX vs Newton.
