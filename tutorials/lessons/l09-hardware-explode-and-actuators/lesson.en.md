# Hardware explode view → Isaac Lab actuators

Inspired by the RoboParty hardware-wall explode animation (embedded webm in the Lab panel), treat **Q1 URDF** as an editable mechanism: add ToF/audio, swap skins, adjust the camera, then **Analyse/Explode** to inspect separation. The same parameters feed **Isaac Lab 3 `ImplicitActuatorCfg`**, showing how hardware load maps to PD gains and effort limits.

## Why an explode view?

Mechanism design and sensor placement are hard to read on the assembled model. Explode keeps joint topology and pushes each link away from the torso so you can check:

- ToF / mic / speaker pose and size relative to `center_torso_link`
- Visual outer skins vs added mass (control-relevant)
- Camera aim occluding critical parts

The browser uses Three.js + native URDF (same `robot-viewer` as Studio)—**no Isaac Sim** required for interaction.

## Lab panel workflow

1. Watch the reference video (RoboParty hardware wall)
2. Adjust skin, camera preset, ToF/audio XYZ/size/mass
3. **Regenerate appearance** — rebuild accessory meshes and materials
4. **Analyse/Explode** — animate to “explode separation”
5. When satisfied, **Run** — writes `params.json` and runs Isaac Lab actuator tracking

## Hardware parameters → actuators

Added mass \(m_{\text{extra}} = m_{\text{tof}} + m_{\text{audio}}\) (when enabled) relative to baseline \(m_0\):

\[
s = 1 + \frac{m_{\text{extra}}}{m_0},\quad
K_p' = s\,K_p,\quad
K_d' = s\,K_d
\]

The example builds:

```python
from isaaclab.actuators import ImplicitActuatorCfg

actuators = {
    "pd": ImplicitActuatorCfg(
        joint_names_expr=pd_expr,
        stiffness=Kp_scaled,
        damping=Kd_scaled,
        actuator_effort_limit=A_LIM,
        joint_effort_limit=J_LIM,
        armature=0.01,
    ),
}
```

In 3.0 set **`actuator_effort_limit`** (model rating) and **`joint_effort_limit`** (solver) separately; keep joint ≥ actuator to avoid double clipping.

## Run outputs

| File | Purpose |
|------|---------|
| `hardware.json` | Skin, accessory poses/masses, scaled PD |
| `rollout.json` | Explode keyframes (browser replay) |
| `result.json` | Tracking RMSE, torque peaks, mass scaling |
| `result.mp4` / `result.gif` | Store ffmpeg preview from rollout when available |

## Relation to lesson 5

Lesson 5 compares Implicit / DC / Delayed PD models. This lesson fixes Implicit and focuses on **mechanism config (mass/sensors) driving the same actuator API**. Compare both to connect BOM choices to control parameters.
