#!/usr/bin/env bash
# Run the Spark GPU worker that trains queued Q1 Store jobs in Isaac Lab.
#   Q1_LEG_ROOT=~/will/q1_leg_urdf Q1_WORKER_NUM_ENVS=2048 scripts/worker.sh
set -euo pipefail
Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
Q1_STORE_PYTHON="${Q1_STORE_PYTHON:-python3}"
export Q1_LEG_ROOT="${Q1_LEG_ROOT:-$HOME/will/q1_leg_urdf}"
[[ -f "$Q1_LEG_ROOT/scripts/isaac_env.sh" ]] || { echo "Q1_LEG_ROOT ($Q1_LEG_ROOT) has no scripts/isaac_env.sh" >&2; exit 1; }
cd "$Q1_STORE_ROOT"
exec "$Q1_STORE_PYTHON" -u -m q1store.worker "$@"
