#!/usr/bin/env bash
# Restart Q1 Store web (+ Hermes by default via start_store.sh).
#   bash scripts/restart_store.sh --port 8878
#   bash scripts/restart_store.sh --port 8878 --no-hermes
#   bash scripts/restart_store.sh --port 8878 --with-pinggy
set -euo pipefail

Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${Q1_STORE_PORT:-8878}"
LLM_PORT="${Q1_LLM_PORT:-8877}"
WITH_PINGGY=0
START_ARGS=()

while (( $# )); do
  case "$1" in
    --with-pinggy) WITH_PINGGY=1; shift ;;
    --port)
      PORT="${2:?--port needs a value}"
      START_ARGS+=(--port "$PORT")
      shift 2
      ;;
    --port=*)
      PORT="${1#--port=}"
      START_ARGS+=(--port "$PORT")
      shift
      ;;
    *)
      START_ARGS+=("$1")
      shift
      ;;
  esac
done

# Always pass an explicit --port so start_store / server share the same listen port.
has_port=0
for a in "${START_ARGS[@]+"${START_ARGS[@]}"}"; do
  [[ "$a" == "--port" || "$a" == --port=* ]] && has_port=1 && break
done
if [[ "$has_port" -eq 0 ]]; then
  START_ARGS=(--port "$PORT" "${START_ARGS[@]+"${START_ARGS[@]}"}")
fi

echo "stopping store on :${PORT} and Hermes on :${LLM_PORT}…"
# Kill by cwd-relative or absolute argv (manual `python server.py --port N` has no full path).
pkill -f "$Q1_STORE_ROOT/server.py" 2>/dev/null || true
pkill -f "[/ ]server\\.py --port ${PORT}" 2>/dev/null || true
pkill -f "$Q1_STORE_ROOT/scripts/hermes_server.sh" 2>/dev/null || true
pkill -f "llama_cpp\\.server.*--port ${LLM_PORT}" 2>/dev/null || true
pkill -f "R0:127\\.0\\.0\\.1:${PORT}.*a\\.pinggy\\.io" 2>/dev/null || true
# Last resort: whoever still owns the listen ports.
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" 2>/dev/null || true
  fuser -k "${LLM_PORT}/tcp" 2>/dev/null || true
elif command -v lsof >/dev/null 2>&1; then
  lsof -ti "tcp:${PORT}" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null || true
  lsof -ti "tcp:${LLM_PORT}" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null || true
fi
sleep 1
# Confirm ports free before relaunch.
for _ in $(seq 1 10); do
  if ! ss -ltn "( sport = :${PORT} or sport = :${LLM_PORT} )" 2>/dev/null | grep -q LISTEN; then
    break
  fi
  sleep 0.5
done

LOG_DIR="${Q1_STORE_DATA:-$Q1_STORE_ROOT/.data}"
mkdir -p "$LOG_DIR"
echo "starting via start_store.sh (Hermes on by default)…"
nohup bash "$Q1_STORE_ROOT/scripts/start_store.sh" "${START_ARGS[@]}" \
  >"$LOG_DIR/store.log" 2>&1 &
echo "store pid $! — log $LOG_DIR/store.log"

for _ in $(seq 1 45); do
  if curl -sf --max-time 1 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    echo "web ready: http://127.0.0.1:${PORT}/"
    break
  fi
  sleep 1
done
curl -sf --max-time 1 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 \
  || { echo "web failed to listen on :${PORT} — see $LOG_DIR/store.log" >&2; exit 1; }

if curl -sf --max-time 1 "http://127.0.0.1:${LLM_PORT}/v1/models" >/dev/null 2>&1; then
  echo "Hermes ready: http://127.0.0.1:${LLM_PORT}/v1"
else
  echo "Hermes not responding yet (may still be loading) — see $LOG_DIR/hermes.log" >&2
fi

if [[ "$WITH_PINGGY" -eq 1 ]]; then
  echo "starting Pinggy → :${PORT}…"
  nohup bash "$Q1_STORE_ROOT/scripts/pinggy.sh" "$PORT" \
    >"$LOG_DIR/pinggy.log" 2>&1 &
  echo "pinggy pid $! — log $LOG_DIR/pinggy.log (URL appears in that log)"
fi
