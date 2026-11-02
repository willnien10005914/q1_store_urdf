# RoboParty / HHTools / BeyondMimic training on Q1 Store

Ballet-like motion tracking fails when the reference zeroes root XY (MediaPipe
geometric mimic). RoboParty’s published path keeps **root trajectory + joints**
from HHTools retarget, then trains a BeyondMimic-style tracker.

## Upstream

| Project | Role |
|---------|------|
| [RP1](https://github.com/Roboparty/RP1) | Platform overview + training docs |
| [human-humanoid-tools](https://github.com/Roboparty/human-humanoid-tools) | Retarget → robot CSV/PKL |
| [roboparty_train](https://github.com/Roboparty/roboparty_train) | Isaac Lab BeyondMimic tasks + `convert_motion.py` |
| Docs | https://roboparty.com/documents/roboto-01?doc=training%2Findex&lang=en |

Local clones are discovered via `vendor/roboparty/SOURCES.txt` or
`Q1_HHTOOLS_ROOT` / `Q1_ROBOPARTY_TRAIN_ROOT` / `Q1_RP1_ROOT`.

```bash
bash scripts/setup_roboparty.sh   # clone/update the three repos next to this store
bash scripts/setup_hhtools_q1.sh  # uv extras + register q1_leg preset + SMPL calibrations
```

### HHTools → Q1 (formal retarget)

`setup_hhtools_q1.sh` registers robot `q1_leg` (URDF + tuned `ik_map` + zero-pose
`retarget_calibration_smpl.yaml` / `_smplx.yaml`). Smoke-tested:

```bash
cd "$Q1_HHTOOLS_ROOT"   # e.g. ~/projects/human-humanoid-tools
uv run hhtools robot list
uv run hhtools retarget run path/to/clip.npz --robot q1_leg -o out.csv \
  --calibration-reference smpl
```

**Ballet YouTube → CSV** still needs:

1. Licensed SMPL-X weights under `configs/body_models/smplx/` (or `HHTOOLS_BODY_MODELS`)
2. GVHMR checkout + checkpoints + Python (`HHTOOLS_GVHMR_ROOT` / `HHTOOLS_GVHMR_PYTHON`)
3. Video → Motion in HHTools Web, then Motion → Robot (`q1_leg`) → export CSV → Store HHTools tab

Vendor copy of the tuned preset: `vendor/roboparty/q1_leg/`.

## Store pipeline (Q1 Leg)

1. **Retarget in HHTools** onto a robot whose DOF names map to Q1 (or use RP1
   aliases — the importer remaps `left_hip_pitch_joint` → `left_hip_Pitch_joint`,
   etc.). Export CSV with `# sample_rate` and columns
   `time,root_x,root_y,root_z,root_qx,root_qy,root_qz,root_qw,dof_*`.
2. **Motion Studio → HHTools tab** → import CSV (`POST /api/roboparty/hhtools`).
3. Design training → **真實訓練 · Isaac Lab PPO**. Job `pipeline=beyondmimic`.
4. GPU worker builds reference with `build_beyondmimic_reference` (preserves root
   XY/Z + quat) instead of MediaPipe’s zero-XY grounded height.

Full RP1.3-BeyondMimic Isaac task IDs stay on `roboparty_train`. This store
adapts the **reference contract** for the existing Q1 Leg Isaac worker.

## API

- `GET /api/roboparty` — discovery status
- `POST /api/roboparty/hhtools` — `{ "csv": "...", "robot_type": "leg", "action_name": "…" }`

## Seed / convert

```bash
# Rebuild a BeyondMimic CSV from a prior MediaPipe store motion (adds FK root).
python3 scripts/seed_beyondmimic_from_motion.py \
  --motion .data/jobs/<id>/motion.json \
  --out .data/roboparty/ballet_beyondmimic.csv
```
