#!/usr/bin/env bash
# Clone / update RoboParty HHTools, roboparty_train, and RP1 next to this store.
set -euo pipefail
STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS="${Q1_PROJECTS_ROOT:-$(dirname "$STORE_ROOT")}"
mkdir -p "$PROJECTS" "$STORE_ROOT/vendor/roboparty"

clone_or_update() {
  local url="$1" dir="$2"
  if [[ -d "$dir/.git" ]]; then
    echo "update $dir"
    git -C "$dir" fetch --depth 1 origin || true
    git -C "$dir" pull --ff-only || true
  else
    echo "clone $url → $dir"
    git clone --depth 1 "$url" "$dir"
  fi
}

clone_or_update https://github.com/Roboparty/human-humanoid-tools.git "$PROJECTS/human-humanoid-tools"
clone_or_update https://github.com/Roboparty/roboparty_train.git "$PROJECTS/roboparty_train"
clone_or_update https://github.com/Roboparty/RP1.git "$PROJECTS/RP1"

if [[ -d "$PROJECTS/roboparty_train/.git" ]]; then
  git -C "$PROJECTS/roboparty_train" submodule update --init --recursive || true
fi

cat >"$STORE_ROOT/vendor/roboparty/SOURCES.txt" <<EOF
# Local clones used by the RoboParty BeyondMimic store pipeline.
# Override with Q1_HHTOOLS_ROOT / Q1_ROBOPARTY_TRAIN_ROOT / Q1_RP1_ROOT.
HHTOOLS=$PROJECTS/human-humanoid-tools
ROBOPARTY_TRAIN=$PROJECTS/roboparty_train
RP1=$PROJECTS/RP1
EOF

echo "RoboParty sources written to vendor/roboparty/SOURCES.txt"
python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "$STORE_ROOT")
from q1store.roboparty import roboparty_status
import json
print(json.dumps(roboparty_status(), indent=2, ensure_ascii=False))
PY
