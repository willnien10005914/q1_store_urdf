#!/usr/bin/env bash
# Start the Q1 Store web server (Hermes on :8877 by default). Prefer setup first:
#   bash scripts/setup_store.sh
#   bash scripts/start_store.sh --port 8878
#   bash scripts/start_store.sh --port 8878 --no-hermes   # web only
set -euo pipefail

Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${Q1_STORE_ENV_FILE:-$Q1_STORE_ROOT/.data/store.env}"
# Hermes is the default companion service; opt out with --no-hermes.
WITH_HERMES="${Q1_STORE_WITH_HERMES:-1}"
PASS_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --with-hermes) WITH_HERMES=1 ;;
    --no-hermes) WITH_HERMES=0 ;;
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

exec "$Q1_STORE_PYTHON" "$Q1_STORE_ROOT/server.py" "${PASS_ARGS[@]}"
