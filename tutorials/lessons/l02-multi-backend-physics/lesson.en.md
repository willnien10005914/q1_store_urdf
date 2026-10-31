# Multi-backend physics: PhysX vs Newton MuJoCo Warp

The biggest structural change in Isaac Lab 3.0 is that the core `isaaclab` package is no longer tied to PhysX. `Articulation`, `RigidObject`, and sensors expose **backend-neutral** APIs; factories load `isaaclab_physx` or `isaaclab_newton` implementations from the active preset.

```python
from isaaclab.assets import Articulation, ArticulationCfg
robot = Articulation(cfg=ArticulationCfg(...))   # same import; backend comes from the preset
```

## 1. Three backends at a glance

| preset | Engine | Needs Isaac Sim | Best for |
| --- | --- | --- | --- |
| `isaacsim_physx` | Isaac Sim PhysX | Yes (`--extra isaacsim`) | Final validation, RTX recording, legacy 2.x tasks |
| `newton_mjwarp` | Newton + MuJoCo Warp | No | Fast reward iteration, Kit-less training, browser viz |
| `ovphysx` | OV PhysX standalone | No (`--extra ovphysx`) | PhysX-like behavior without full Kit |

> They are **not** interchangeable black boxes: contact models, joint drives, friction, and solvers differ. The same PD gains may feel soft on Newton and stiff on PhysX.

## 2. Multi-backend envs: `PresetCfg`

2.x set `self.sim.physics = PhysxCfg(...)` directly. 3.0 lists selectable backends:

```python
from isaaclab.utils import configclass
from isaaclab_physx.physics import PhysxCfg
from isaaclab_newton.physics import NewtonCfg
from isaaclab_tasks.utils import PresetCfg

@configclass
class Q1PhysicsPresets(PresetCfg):
    isaacsim_physx: PhysxCfg = PhysxCfg(solver_type=1, max_position_iteration_count=4)
    newton_mjwarp: NewtonCfg = NewtonCfg()
    default: PhysxCfg = isaacsim_physx

self.sim.physics = Q1PhysicsPresets()
```

CLI `physics=newton_mjwarp` replaces the entire `sim.physics` section with that field; `default` is used when no preset is passed.

## 3. Where backends diverge

1. **Contact**: PhysX uses penetration + solver iterations; MJWarp uses MuJoCo soft contact (`solref` / `solimp`). Landing bounce and settle time differ.
2. **Joint drive**: PhysX implicit drives live in the solver; Newton drives use the Newton 1.6 drive API (`write_group_parameter(..., "drive")`).
3. **Data refresh**: PhysX is pull-to-refresh (stable buffers); Newton auto-refreshes and **may swap wrappers after reset**—do not cache `.torch` across reset.
4. **Performance**: Newton is often faster with many simple articulations; PhysX has richer features for complex contact / deformables (deformables and surface grippers are PhysX-only today).

## 4. Example: Q1 drop test

The `example.py` on the right drops Q1 from `drop_height`, holds stance with one PD gain set, and records:

- `settle_height_m`: mean root height over the last 0.5 s
- `max_contact_force_n`: peak normal force on feet/wheels from `ContactSensor` (3.0 field: `net_normal_forces_w`)
- `steps_per_second`: throughput including `scene.update`
- `fallen_fraction`: fraction of envs with root height below 0.5 m

**Steps**

1. Run once with `physics=newton_mjwarp`, `num_envs=64`, save result.json.
2. Switch to `isaacsim_physx` and run again.
3. Change `dt` from 0.005 to 0.01—which backend penetrates or falls first?
4. Sweep `solver_iterations` from 1 to 16 (Newton maps to
   `NewtonCfg(solver_cfg=MJWarpSolverCfg(iterations=…))`; PhysX uses
   `max_position_iteration_count`).

Ask yourself: Q1 Wheel wheels are `continuous` joints—do both backends treat unlimited rotation the same? (Hint: inspect `robot.data.joint_pos.torch` range.)

## 5. Suggested Q1 training workflow

```text
Newton MJWarp (reward design, fast curriculum trials)
      ↓ policy stands / walks
Isaac Sim PhysX (acceptance, recording, compare to 2.x history)
      ↓ pass this store’s gates
LEAPP / ONNX export (lesson 8)
```

Policy transfer between backends has an official guide (Transfer Policies Between PhysX and Newton)—treat it as **re-validation**, not free portability.
