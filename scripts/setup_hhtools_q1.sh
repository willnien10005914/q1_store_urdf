#!/usr/bin/env bash
# Install HHTools extras and register the Q1 Leg robot preset for retarget.
#
# After this script:
#   cd "$HHTOOLS" && uv run hhtools doctor          # robot + retarget OK
#   uv run hhtools robot list                       # shows q1_leg
#   uv run hhtools web                              # calibrate / retarget UI
#
# Still required for Video → Motion (ballet YouTube):
#   1. Licensed SMPL-X weights (https://smpl-x.is.tue.mpg.de) under
#      configs/body_models/smplx/ or HHTOOLS_BODY_MODELS
#   2. GVHMR checkout + checkpoints + its own Python env
#      export HHTOOLS_GVHMR_ROOT=... HHTOOLS_GVHMR_PYTHON=...
set -euo pipefail

STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS="${Q1_PROJECTS_ROOT:-$(dirname "$STORE_ROOT")}"
HHTOOLS="${Q1_HHTOOLS_ROOT:-$PROJECTS/human-humanoid-tools}"
Q1_LEG="${Q1_LEG_ROOT:-$PROJECTS/q1_leg_urdf}"
GVHMR_ROOT="${HHTOOLS_GVHMR_ROOT:-$PROJECTS/GVHMR}"

if [[ ! -d "$HHTOOLS/hhtools" ]]; then
  echo "HHTools not found at $HHTOOLS — run scripts/setup_roboparty.sh first" >&2
  exit 1
fi
if [[ ! -f "$Q1_LEG/urdf/q1_inertia/quanta_robot_legged.urdf" ]]; then
  echo "Q1 Leg URDF missing under $Q1_LEG" >&2
  exit 1
fi

echo "+ sync HHTools extras (web/retarget/robot/smpl/formats)"
(
  cd "$HHTOOLS"
  uv sync --locked --extra web --extra retarget --extra robot --extra smpl --extra formats
)

DROP="$(mktemp -d /tmp/q1_leg_hhtools_drop.XXXXXX)"
cleanup() { rm -rf "$DROP"; }
trap cleanup EXIT

python3 - <<PY
from pathlib import Path
src = Path("$Q1_LEG/urdf/q1_inertia/quanta_robot_legged.urdf")
dst = Path("$DROP/quanta_robot_legged.urdf")
text = src.read_text().replace("../../meshes/", "meshes/").replace("filename=\"meshes/q1_inertia/", "filename=\"q1_inertia/")
dst.write_text(text)
meshes = Path("$DROP/meshes")
meshes.mkdir(parents=True, exist_ok=True)
target = Path("$Q1_LEG/meshes/q1_inertia")
link = meshes / "q1_inertia"
if link.exists() or link.is_symlink():
    link.unlink()
link.symlink_to(target)
print("drop ready", dst)
PY

echo "+ register q1_leg preset (configs + ~/.config)"
(
  cd "$HHTOOLS"
  uv run hhtools robot add "$DROP" --name q1_leg --link-meshes --force
  uv run hhtools robot add "$DROP" --name q1_leg --link-meshes --force \
    --configs "${HOME}/.config/hhtools/robots"
)

# Prefer the hand-tuned yaml from the store vendor if present.
VENDOR_YAML="$STORE_ROOT/vendor/roboparty/q1_leg/robot.yaml"
for DEST in \
  "$HHTOOLS/configs/robots/q1_leg" \
  "${HOME}/.config/hhtools/robots/q1_leg"
do
  mkdir -p "$DEST/meshes"
  ln -sfn "$Q1_LEG/meshes/q1_inertia" "$DEST/meshes/q1_inertia"
  # Rewrite URDF mesh paths for meshdir=meshes
  python3 - <<PY
from pathlib import Path
urdf = Path("$DEST/quanta_robot_legged.urdf")
if urdf.is_file():
    text = urdf.read_text()
    text = text.replace("filename=\"meshes/meshes/", "filename=\"")
    text = text.replace("filename=\"meshes/q1_inertia/", "filename=\"q1_inertia/")
    urdf.write_text(text)
PY
  if [[ -f "$VENDOR_YAML" ]]; then
    python3 - <<PY
from pathlib import Path
import yaml
dest = Path("$DEST")
data = yaml.safe_load(Path("$VENDOR_YAML").read_text())
data["mesh_search_paths"] = [str((dest / "meshes").resolve())]
data["urdf"] = "quanta_robot_legged.urdf"
header = "# q1_leg — installed by scripts/setup_hhtools_q1.sh\\n\\n"
(dest / "robot.yaml").write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
print("installed tuned robot.yaml ->", dest)
PY
  fi
done

echo "+ bootstrap zero-pose SMPL / SMPL-X calibrations"
(
  cd "$HHTOOLS"
  HHTOOLS_DIR="$HHTOOLS" uv run python - <<'PY'
from pathlib import Path
import os
import shutil
from hhtools.robot.registry import get as get_preset, refresh
from hhtools.robot.loader import load_robot
from hhtools.retarget.calibration.calibration import (
    RobotRetargetCalibration,
    derive_calibration_params,
    save_calibration_for_preset,
)

refresh()
preset = get_preset("q1_leg")
model = load_robot(preset, compile_mjcf=True)
paths = []
for ref in ("smpl", "smplx"):
    cal = RobotRetargetCalibration(
        robot=preset.name,
        reference=ref,
        calibrated_joint_q={},
        notes=f"bootstrap zero-pose {ref} for Q1; refine in HHTools Web Calibrate",
    )
    derived = derive_calibration_params(cal, model)
    path = save_calibration_for_preset(cal, preset, derived=derived, prefer_user_overlay=True)
    paths.append(Path(path))
    print("saved", path)
bundled = Path(os.environ["HHTOOLS_DIR"]) / "configs/robots/q1_leg"
user = Path.home() / ".config/hhtools/robots/q1_leg"
for src in paths:
    for dest_dir in (bundled, user):
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / src.name)
        print("copied", src.name, "->", dest_dir)
PY
)

if [[ -d "$GVHMR_ROOT" ]]; then
  echo "+ GVHMR checkout found at $GVHMR_ROOT"
  echo "  export HHTOOLS_GVHMR_ROOT=$GVHMR_ROOT"
else
  echo "! GVHMR not found. Optional: git clone https://github.com/zju3dv/GVHMR.git $PROJECTS/GVHMR"
fi

echo "+ doctor"
(
  cd "$HHTOOLS"
  uv run hhtools doctor || true
  uv run hhtools robot validate q1_leg
  uv run hhtools robot list
)

ENV_FILE="$STORE_ROOT/.data/hhtools.env"
mkdir -p "$(dirname "$ENV_FILE")"
cat >"$ENV_FILE" <<EOF
# Generated by scripts/setup_hhtools_q1.sh
export Q1_HHTOOLS_ROOT=$HHTOOLS
export HHTOOLS_GVHMR_ROOT=$GVHMR_ROOT
# After placing SMPL-X weights:
# export HHTOOLS_BODY_MODELS=$HHTOOLS/configs/body_models
# export HHTOOLS_GVHMR_PYTHON=/path/to/gvhmr/venv/bin/python
EOF
echo "Wrote $ENV_FILE"
echo "Done. Next: place SMPL-X weights, set up GVHMR Python, then retarget ballet video."
