# Ballet 1–4s → HHTools → Isaac (v3 plan)

Source: [YouTube Short a5R0Xf46hyg](https://youtube.com/shorts/a5R0Xf46hyg) **seconds 1–4**.

## Motion analysis (frames @ 2 Hz)

| Time | Pose requirement |
|------|------------------|
| ~1.0–2.0 s | Left single-leg stance; right foot near standing ankle (*cou-de-pied*) |
| ~2.0–3.0 s | Right thigh rises to *passé* (foot at standing knee); arms to fifth en haut |
| ~3.0–4.0 s | Lower / open toward plié or recovery; CoM must stay over support foot |

Critical DOFs for Q1: **stance hip/ankle for balance**, **swing hip Pitch + knee Pitch** (must not freeze), upright torso, mild root XY.

## Gaps found vs RoboParty HHTools → BeyondMimic

1. Human-scale `root_z` (~1.5 m) fed to Isaac → fixed by `reference_bm` re-grounding.
2. `right_hip_Pitch` stuck at 0 → single-leg ballet impossible; needs heavier hip IK / calibration / post-repair.
3. Store worker still Lab 2.x PhysX PPO (not full `RP1.3-BeyondMimic` NPZ body_* on Isaac Lab 3).
4. Web lacked YouTube-by-seconds → HHTools one-shot; only CSV paste.

## v3 plan (this iteration) — shipped to web

1. **Web**: HHTools tab → YouTube URL + start/end → `POST /api/roboparty/youtube` (cache slice or GVHMR → retarget → Store motion, `pipeline=beyondmimic_v3`).
2. **Clip**: Prefer exact **1.0–4.0 s** (shorter = easier single-leg curriculum).
3. **Reference**: Keep XY; Z = Q1 `grounded_height`; **repair frozen hip Pitch** from Roll/Knee when IK froze swing thigh.
4. **Train**: Store Isaac Lab PPO worker (`beyondmimic_v3` uses same `build_beyondmimic_reference`); School remains Isaac Lab **3** for tutorials.
5. **Next**: HHTools Web Calibrate for `q1_leg`, optional `convert_motion` NPZ, full IL3 BeyondMimic task port.

## Acceptance (smoke)

- `height_error` ≪ 0.1 m early in training  
- Swing hip/knee **ptp > 0.2 rad** in imported CSV  
- Result video on Store job page  

## Retarget QA (before PPO)

1. HHTools tab → import / YouTube v3 → **URDF 預覽影片（無 PPO）** (`POST /api/roboparty/preview`).
2. Or CLI: `python3 scripts/render_hhtools_reference_video.py --reference .data/jobs/<id>/reference.json --out preview.mp4`
3. Current 1–4s preview shows **wrong ballet geometry** (near-split / airborne) vs source passé — fix IK / hip repair before more PPO.  
