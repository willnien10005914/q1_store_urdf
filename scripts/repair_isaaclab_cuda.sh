#!/usr/bin/env bash
# Align Isaac Lab 3's torch wheel with the local NVIDIA driver CUDA capability.
# Lab 3.0.0 defaults to torch+cu130; drivers that only advertise CUDA 12.8 need cu128.
#
#   bash scripts/repair_isaaclab_cuda.sh                 # repair $Q1_ISAACLAB3_ROOT or ~/IsaacLab3
#   bash scripts/repair_isaaclab_cuda.sh /path/to/lab
#   bash scripts/repair_isaaclab_cuda.sh --check
set -euo pipefail

ISAAC_ROOT="${Q1_ISAACLAB3_ROOT:-$HOME/IsaacLab3}"
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) ISAAC_ROOT="$arg" ;;
  esac
done

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '+ %s\n' "$*"; }
ok()  { printf '  ✓ %s\n' "$*"; }

[[ -f "$ISAAC_ROOT/pyproject.toml" ]] || die "not an Isaac Lab checkout: $ISAAC_ROOT"
command -v nvidia-smi >/dev/null || die "nvidia-smi not found"
command -v uv >/dev/null || die "uv not found"

DRIVER_CUDA="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: \([0-9][0-9]*\.[0-9]\).*/\1/p' | head -1)"
[[ -n "$DRIVER_CUDA" ]] || die "could not parse CUDA Version from nvidia-smi"

want_index="cu128"
case "$DRIVER_CUDA" in
  13.*) want_index="cu130" ;;
  12.8|12.9) want_index="cu128" ;;
  12.6|12.7) want_index="cu126" ;;
  12.4|12.5) want_index="cu124" ;;
  12.*) want_index="cu124" ;;
esac

torch_ver="missing"
torch_cuda="missing"
torch_ok=0
if [[ -x "$ISAAC_ROOT/.venv/bin/python" ]]; then
  # shellcheck disable=SC2046
  eval $(
    "$ISAAC_ROOT/.venv/bin/python" - <<'PY'
import torch
print(f"torch_ver={torch.__version__!r}")
print(f"torch_cuda={torch.version.cuda!r}")
try:
    ok = bool(torch.cuda.is_available())
except Exception:
    ok = False
print(f"torch_ok={1 if ok else 0}")
PY
  )
fi

log "driver CUDA=$DRIVER_CUDA  want=$want_index  torch=$torch_ver (built=$torch_cuda, available=$torch_ok)"

needs_repair=0
[[ "$torch_ok" -eq 1 ]] || needs_repair=1
if [[ "$torch_ver" == *"+cu"* && "$torch_ver" != *"+${want_index}"* ]]; then
  needs_repair=1
fi
if grep -Eq 'pytorch-cu130|whl/cu130' "$ISAAC_ROOT/pyproject.toml" && [[ "$want_index" != "cu130" ]]; then
  needs_repair=1
fi

if [[ "$needs_repair" -eq 0 ]]; then
  ok "torch already matches driver ($torch_ver)"
  exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "NEED_REPAIR torch=$torch_ver driver_cuda=$DRIVER_CUDA want=$want_index"
  exit 1
fi

log "rewriting pyproject torch index → $want_index"
ISAAC_ROOT="$ISAAC_ROOT" WANT_INDEX="$want_index" python3 - <<'PY'
import os, re
from pathlib import Path
want = os.environ["WANT_INDEX"]
path = Path(os.environ["ISAAC_ROOT"]) / "pyproject.toml"
text = path.read_text()
text = re.sub(r'name = "pytorch-cu\d+"', f'name = "pytorch-{want}"', text)
text = re.sub(r'url = "https://download\.pytorch\.org/whl/cu\d+"',
              f'url = "https://download.pytorch.org/whl/{want}"', text)
text = re.sub(r'torch = \{ index = "pytorch-cu\d+" \}',
              f'torch = {{ index = "pytorch-{want}" }}', text)
text = re.sub(r'torchvision = \{ index = "pytorch-cu\d+" \}',
              f'torchvision = {{ index = "pytorch-{want}" }}', text)
path.write_text(text)
print("patched", path)
PY

log "uv lock + sync"
(cd "$ISAAC_ROOT" && uv lock && uv sync)
printf 'cuda=%s\n' "$want_index" >"$ISAAC_ROOT/.q1_store_torch_cuda"

"$ISAAC_ROOT/.venv/bin/python" - <<'PY'
import sys, torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
sys.exit(0 if torch.cuda.is_available() else 1)
PY
ok "Isaac Lab torch matches driver CUDA $DRIVER_CUDA ($want_index)"
