#!/usr/bin/env bash
# Start the Q1 Store web server (Hermes on :8877 by default). Prefer setup first:
#   bash scripts/setup_store.sh
#   bash scripts/start_store.sh --port 8878
#   bash scripts/start_store.sh --port 8878 --no-hermes   # web only
#   bash scripts/start_store.sh --port 8878 --with-worker # also start GPU training worker
# GPU worker auto-starts when NVIDIA + q1_leg_urdf are present unless --no-worker.
set -euo pipefail

Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${Q1_STORE_ENV_FILE:-$Q1_STORE_ROOT/.data/store.env}"
# Hermes is the default companion service; opt out with --no-hermes.
WITH_HERMES="${Q1_STORE_WITH_HERMES:-1}"
# Empty = auto (start worker when NVIDIA GPU + leg checkout look ready).
WITH_WORKER="${Q1_STORE_WITH_WORKER:-}"
PASS_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --with-hermes) WITH_HERMES=1 ;;
    --no-hermes) WITH_HERMES=0 ;;
    --with-worker) WITH_WORKER=1 ;;
    --no-worker) WITH_WORKER=0 ;;
    --setup)
      bash "$Q1_STORE_ROOT/scripts/setup_store.sh"
      ;;
    --setup-check)
      bash "$Q1_STORE_ROOT/scripts/setup_store.sh" --check || true
      ;;
    *)
      PASS_ARGS+=("$arg")
      ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo "loaded env: $ENV_FILE"
  if [[ -n "${Q1_ISAACLAB3_ROOT:-}" ]]; then
    echo "  Q1_ISAACLAB3_ROOT=$Q1_ISAACLAB3_ROOT"
  else
    echo "  Q1_ISAACLAB3_ROOT unset — Isaac Lab 3 lessons will be blocked (run scripts/setup_store.sh)"
  fi
else
  echo "no $ENV_FILE yet — run: bash scripts/setup_store.sh" >&2
fi

Q1_STORE_PYTHON="${Q1_STORE_PYTHON:-python3}"
"$Q1_STORE_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Q1 Store scripts require Python 3.11+ (set Q1_STORE_PYTHON)" >&2; exit 1; }

hermes_ready() {
  local port="${Q1_LLM_PORT:-8877}"
  curl -sf --max-time 1 "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1
}

start_hermes() {
  local port="${Q1_LLM_PORT:-8877}"
  local script="$Q1_STORE_ROOT/scripts/hermes_server.sh"
  local log="${Q1_STORE_DATA:-$Q1_STORE_ROOT/.data}/hermes.log"

  if hermes_ready; then
    echo "Hermes already running on port ${port}"
  else
    if [[ ! -x "$script" ]]; then
      echo "Hermes script missing ($script) — run bash scripts/setup_store.sh --hermes-only; continuing without Hermes" >&2
      return 0
    fi
    mkdir -p "$(dirname "$log")"
    echo "starting Hermes on :${port}…"
    nohup bash "$script" >"$log" 2>&1 &
    for _ in $(seq 1 90); do
      if hermes_ready; then
        echo "Hermes ready: ${Q1_LLM_BASE_URL:-http://127.0.0.1:${port}/v1}"
        break
      fi
      sleep 1
    done
    if ! hermes_ready; then
      echo "Hermes failed to start — see $log" >&2
      return 1
    fi
  fi
  # Ensure the web process sees the OpenAI-compatible endpoint even if store.env was old.
  export Q1_LLM_BASE_URL="${Q1_LLM_BASE_URL:-http://127.0.0.1:${port}/v1}"
  if [[ -z "${Q1_LLM_MODEL:-}" ]] && hermes_ready; then
    Q1_LLM_MODEL="$(curl -sf "http://127.0.0.1:${port}/v1/models" \
      | "$Q1_STORE_PYTHON" -c 'import sys,json; print(json.load(sys.stdin)["data"][0]["id"])')"
    export Q1_LLM_MODEL
  fi
  return 0
}

if [[ "$WITH_HERMES" -eq 1 ]]; then
  start_hermes || exit 1
else
  echo "skipping Hermes (--no-hermes)"
fi

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

worker_ready() {
  local data="${Q1_STORE_DATA:-$Q1_STORE_ROOT/.data}"
  local beat="$data/worker/heartbeat.json"
  [[ -f "$beat" ]] || return 1
  "$Q1_STORE_PYTHON" - "$beat" <<'PY'
import json, sys, time
path = sys.argv[1]
try:
    beat = json.loads(open(path).read())
except Exception:
    raise SystemExit(1)
age = time.time() - float(beat.get("updated_at") or 0)
raise SystemExit(0 if age < 45 and beat.get("state") != "stopped" else 1)
PY
}

start_worker() {
  local data="${Q1_STORE_DATA:-$Q1_STORE_ROOT/.data}"
  local log="$data/worker.log"
  local leg
  if ! leg="$(resolve_leg_root)"; then
    echo "GPU worker skipped: Q1_LEG_ROOT / q1_leg_urdf with scripts/isaac_env.sh not found" >&2
    return 0
  fi
  export Q1_LEG_ROOT="$leg"
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "GPU worker skipped: nvidia-smi not found (CPU-only / remote web host)" >&2
    return 0
  fi
  mkdir -p "$data"
  if worker_ready; then
    echo "GPU worker already online (Q1_LEG_ROOT=$Q1_LEG_ROOT)"
    return 0
  fi
  # Drop a stale heartbeat so the UI does not show a ghost worker while we boot.
  rm -f "$data/worker/heartbeat.json"
  echo "starting GPU worker (Q1_LEG_ROOT=$Q1_LEG_ROOT)…"
  nohup bash "$Q1_STORE_ROOT/scripts/worker.sh" --data-dir "$data" \
    >"$log" 2>&1 &
  echo "GPU worker pid $! — log $log"
  for _ in $(seq 1 20); do
    if worker_ready; then
      echo "GPU worker ready"
      return 0
    fi
    sleep 0.5
  done
  echo "GPU worker did not publish a heartbeat yet — see $log" >&2
  return 0
}

should_start_worker() {
  if [[ "$WITH_WORKER" == "1" ]]; then
    return 0
  fi
  if [[ "$WITH_WORKER" == "0" ]]; then
    return 1
  fi
  # Auto: NVIDIA present and leg checkout resolvable.
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  resolve_leg_root >/dev/null || return 1
  return 0
}

if should_start_worker; then
  start_worker || true
else
  echo "skipping GPU worker (pass --with-worker to force, or set Q1_STORE_WITH_WORKER=1)"
fi

# Persist discovered leg root for later restarts / worker-only shells.
if leg="$(resolve_leg_root 2>/dev/null)"; then
  export Q1_LEG_ROOT="$leg"
  if [[ -f "$ENV_FILE" ]] && ! grep -q '^Q1_LEG_ROOT=' "$ENV_FILE" 2>/dev/null; then
    printf 'Q1_LEG_ROOT=%s\n' "$leg" >>"$ENV_FILE"
    echo "appended Q1_LEG_ROOT to $ENV_FILE"
  fi
fi

exec "$Q1_STORE_PYTHON" "$Q1_STORE_ROOT/server.py" "${PASS_ARGS[@]}"
