#!/usr/bin/env bash
# Run the local GPU worker that trains queued Q1 Store jobs in Isaac Lab.
# Works on DGX Spark, x86 Intel hosts with NVIDIA GPUs, and similar setups.
# Env count follows the job budget: 4 h = 1024, each extra hour adds 1024.
#   Q1_LEG_ROOT=~/projects/q1_leg_urdf scripts/worker.sh
#   bash scripts/worker.sh --data-dir .data
set -euo pipefail
Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
Q1_STORE_PYTHON="${Q1_STORE_PYTHON:-python3}"

resolve_leg_root() {
  local cand
  for cand in \
    "${Q1_LEG_ROOT:-}" \
    "$HOME/projects/q1_leg_urdf" \
    "$HOME/will/q1_leg_urdf" \
    "$HOME/q1_leg_urdf" \
    "$Q1_STORE_ROOT/../q1_leg_urdf"
  do
    [[ -n "$cand" && -f "$cand/scripts/isaac_env.sh" ]] || continue
    printf '%s\n' "$(cd -- "$cand" && pwd)"
    return 0
  done
  return 1
}

if [[ -z "${Q1_LEG_ROOT:-}" ]] || [[ ! -f "${Q1_LEG_ROOT}/scripts/isaac_env.sh" ]]; then
  Q1_LEG_ROOT="$(resolve_leg_root)" || {
    echo "Q1_LEG_ROOT not found. Set Q1_LEG_ROOT to a q1_leg_urdf checkout with scripts/isaac_env.sh" >&2
    echo "  tried: \$Q1_LEG_ROOT, ~/projects/q1_leg_urdf, ~/will/q1_leg_urdf, ~/q1_leg_urdf, $Q1_STORE_ROOT/../q1_leg_urdf" >&2
    exit 1
  }
fi
export Q1_LEG_ROOT
echo "Q1 Store worker using Q1_LEG_ROOT=$Q1_LEG_ROOT"
cd "$Q1_STORE_ROOT"
exec "$Q1_STORE_PYTHON" -u -m q1store.worker "$@"
