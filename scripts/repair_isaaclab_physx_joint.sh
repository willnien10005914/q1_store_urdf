#!/usr/bin/env bash
# Patch kitless Isaac Lab 3 URDF importer so PhysxJointAPI ApplyAPI failures
# do not abort newton_mjwarp tutorial runs.
#
#   bash scripts/repair_isaaclab_physx_joint.sh [ISAACLAB3_ROOT]
#   bash scripts/repair_isaaclab_physx_joint.sh --check [ISAACLAB3_ROOT]
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    -h|--help)
      sed -n '2,8p' "$0"
      exit 0
      ;;
    *) ARGS+=("$arg") ;;
  esac
done

ISAAC="${ARGS[0]:-${Q1_ISAACLAB3_ROOT:-$HOME/IsaacLab3}}"
MARKER="PhysxJointAPI unavailable; skipping"
STAMP="$ISAAC/.q1_store_physx_joint_patch"

find_importer() {
  local py
  if [[ -x "$ISAAC/.venv/bin/python" ]]; then
    py="$ISAAC/.venv/bin/python"
  else
    py="$(command -v python3 || true)"
  fi
  [[ -n "$py" ]] || return 1
  "$py" - <<'PY' "$ISAAC"
import sys
from pathlib import Path
root = Path(sys.argv[1])
candidates = list((root / ".venv").glob("lib/python*/site-packages/isaacsim/asset/importer/utils/impl/importer_utils.py"))
if not candidates:
    # uv may place the package outside .venv in editable layouts
    candidates = list(root.glob("**/site-packages/isaacsim/asset/importer/utils/impl/importer_utils.py"))
print(candidates[0] if candidates else "")
PY
}

TARGET="$(find_importer || true)"
if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
  echo "importer_utils.py not found under $ISAAC (install with: uv sync --extra importers)" >&2
  exit 1
fi

if grep -qF "$MARKER" "$TARGET"; then
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "ok: PhysxJointAPI guard present in $TARGET"
    exit 0
  fi
  echo "skip: already patched $TARGET"
  printf 'try_except_add_joint_schemas\n' >"$STAMP"
  exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "need: patch $TARGET for kitless PhysxJointAPI" >&2
  exit 1
fi

python3 - "$TARGET" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = '''        if not prim.HasAPI(PhysxSchema.JOINT_API):
            prim.ApplyAPI(PhysxSchema.JOINT_API)
        instance_name = "angular" if prim.IsA(UsdPhysics.RevoluteJoint) else "linear"

        if not prim.HasAPI(UsdPhysics.DriveAPI, instance_name):
            UsdPhysics.DriveAPI.Apply(prim, instance_name)

        if not prim.HasAPI(PhysxSchema.JOINT_STATE_API, instance_name):
            prim.ApplyAPI(PhysxSchema.JOINT_STATE_API, instance_name)
'''
new = '''        # Kitless / usd-exchange-only envs may lack a registered PhysxJointAPI schema.
        try:
            if not prim.HasAPI(PhysxSchema.JOINT_API):
                prim.ApplyAPI(PhysxSchema.JOINT_API)
        except Exception:
            _logger.debug("PhysxJointAPI unavailable; skipping on %s", prim.GetPath())
        instance_name = "angular" if prim.IsA(UsdPhysics.RevoluteJoint) else "linear"

        if not prim.HasAPI(UsdPhysics.DriveAPI, instance_name):
            UsdPhysics.DriveAPI.Apply(prim, instance_name)

        try:
            if not prim.HasAPI(PhysxSchema.JOINT_STATE_API, instance_name):
                prim.ApplyAPI(PhysxSchema.JOINT_STATE_API, instance_name)
        except Exception:
            _logger.debug("PhysicsJointStateAPI unavailable; skipping on %s", prim.GetPath())
'''
if old not in text:
    raise SystemExit(f"pattern not found in {path}; package layout may have changed")
path.write_text(text.replace(old, new, 1))
print(f"patched {path}")
PY

printf 'try_except_add_joint_schemas\n' >"$STAMP"
echo "ok: wrote $STAMP"
