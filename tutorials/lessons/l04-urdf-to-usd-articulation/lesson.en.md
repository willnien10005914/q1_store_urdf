# URDF Importer 3.0: Q1 URDF → USD → ArticulationCfg

Q1’s native models are URDF (this store: `assets/robots/{leg,wheel}/urdf/q1_inertia/*.urdf`, with inertial, limits, mimic). Isaac Lab 3.0 with Isaac Sim 6 **URDF importer 3.0** uses `urdf-usd-converter` + `isaacsim.asset.transformer.rules` to produce structured USD.

## 1. What changed

| Item | 2.x | 3.0 |
| --- | --- | --- |
| Pipeline | C++ binding, Kit `URDFParseFile` / `URDFImportRobot` | Python `URDFImporter.import_urdf()` |
| Output path | `usd_dir/usd_file_name` | `{usd_dir}/{robot_name}/{robot_name}.usda` (auto naming) |
| `merge_fixed_joints` | Importer flag | URDF **XML preprocessing** (merge visual/collision/inertial into parent) |
| `convert_mimic_joints_to_normal_joints` | Supported | **Not supported** (warning) |
| `replace_cylinders_with_capsules`, `root_link_name` | Supported | **Not supported** |
| `make_instanceable` | Optional | Ignored; instanceable by default |
| `NaturalFrequencyGainsCfg` | Supported | Deprecated → use `PDGainsCfg` |
| Version pin | Pinned `isaacsim.asset.importer.urdf-2.4.31` | Bundled Isaac Sim version |

CLI:

```bash
uv run --extra importers python scripts/tools/convert_urdf.py \
  assets/robots/leg/urdf/q1_inertia/quanta_robot_legged.urdf /tmp/q1_usd \
  --merge-joints --joint-stiffness 80 --joint-damping 4 --viz kit
```

## 2. Program API

```python
from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg

cfg = UrdfConverterCfg(
    asset_path="quanta_robot_legged.urdf",
    usd_dir="/tmp/q1_usd",            # auto path: /tmp/q1_usd/quanta_robot_legged/quanta_robot_legged.usda
    fix_base=False,
    merge_fixed_joints=True,
    joint_drive=UrdfConverterCfg.JointDriveCfg(
        gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0),
    ),
)
usd_path = UrdfConverter(cfg).usd_path
```

Often you skip manual conversion: `sim_utils.UrdfFileCfg` extends `UrdfConverterCfg` and auto-converts on `ArticulationCfg.spawn` with caching.

## 3. Four Q1-specific notes

1. **23 control axes**: manifest `control_joints` is PPO action order. Leg has six locked joints (merged with `merge_fixed_joints`); Wheel has four mimic slaves—exclude them from actuator `joint_names_expr`.
2. **Mimic**: importer keeps mimic; 3.0 cannot “convert mimic to normal joints”. Command only master joints in actions.
3. **Wheels are `continuous`**: `joint_pos_limits` may be ±inf or huge; use `joint_vel` in reward/obs, not `joint_pos`.
4. **Quaternions XYZW**: `InitialStateCfg.rot=(0,0,0,1)`; manifest `default_joint_positions` can feed `joint_pos` directly.

## 4. `ArticulationCfg` in 3.0

```python
from isaaclab.assets import ArticulationCfg
from isaaclab.actuators import ImplicitActuatorCfg
import isaaclab.sim as sim_utils

Q1_LEG_CFG = ArticulationCfg(
    prim_path="{ENV_REGEX_NS}/Robot",
    spawn=sim_utils.UrdfFileCfg(asset_path=URDF, fix_base=False, merge_fixed_joints=True,
                                joint_drive=sim_utils.UrdfFileCfg.JointDriveCfg(
                                    gains=sim_utils.UrdfFileCfg.JointDriveCfg.PDGainsCfg(stiffness=80.0, damping=4.0))),
    init_state=ArticulationCfg.InitialStateCfg(pos=(0, 0, 0.95), rot=(0, 0, 0, 1), joint_pos=DEFAULT_Q),
    actuators={
        "legs": ImplicitActuatorCfg(joint_names_expr=[".*hip.*", ".*knee.*", ".*ankle.*"], stiffness=120.0, damping=6.0,
                                    actuator_effort_limit=150.0, joint_effort_limit=150.0),
        "upper": ImplicitActuatorCfg(joint_names_expr=[".*waist.*", ".*shoulder.*", ".*elbow.*", ".*wrist.*"],
                                     stiffness=60.0, damping=3.0, actuator_effort_limit=60.0, joint_effort_limit=60.0),
    },
)
```

`actuator_effort_limit` (actuator model rating) and `joint_effort_limit` (solver cap) are separate in 3.0—lesson 5 goes deeper.

## 5. Example

`example.py` (default `physics=newton_mjwarp`, **no** full Isaac Sim required):

1. **Kitless path**: `NewtonCfg` + `sim_utils.UrdfFileCfg` (extends `UrdfConverterCfg`, converts on spawn). Needs Lab `--extra importers` only.
2. **PhysX / Kit path** (`physics=isaacsim_physx`): explicit `UrdfConverter` → `UsdFileCfg` to see `{usd_dir}/{robot}/{robot}.usda` rules; needs full Isaac Sim. Hard-coded `PhysxCfg()` without Kit yields `Isaac Sim is not installed or not found on PYTHONPATH`.
3. Spawns `Articulation`, prints each joint’s `type`, `lower/upper` (`joint_pos_limits.torch`), effort/velocity limits, and whether it is in `control_joints`.
4. Compares manifest `control_joints` for missing/extra joints and URDF inertial total mass vs articulation mass.

Exercises: disable `merge_fixed_joints` and see which six Leg joints appear; set `stiffness` to 0 and check spawned `joint_stiffness`. With Kit, switch to `isaacsim_physx` and compare `spawn_mode` / `usd_path` in result.json.

> The 3D preview above is Three.js geometry (URDFLoader), not Isaac Sim—it confirms joint names and axes.
