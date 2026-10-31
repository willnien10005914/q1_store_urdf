#!/usr/bin/env bash
# Restart Q1 Store web (+ Hermes by default via start_store.sh) and Pinggy.
#   bash scripts/restart_store.sh --port 8878
#   bash scripts/restart_store.sh --port 8878 --no-hermes
#   bash scripts/restart_store.sh --port 8878 --no-pinggy   # skip public tunnel
set -euo pipefail

Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${Q1_STORE_PORT:-8878}"
LLM_PORT="${Q1_LLM_PORT:-8877}"
# Default ON: after code changes we always want a fresh public URL.
WITH_PINGGY="${Q1_RESTART_WITH_PINGGY:-1}"
START_ARGS=()

while (( $# )); do
  case "$1" in
    --with-pinggy) WITH_PINGGY=1; shift ;;
    --no-pinggy) WITH_PINGGY=0; shift ;;
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
kill_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  elif command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)"
  fi
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.4
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}
# Match absolute or cwd-relative argv forms.
pkill -f "${Q1_STORE_ROOT}/server.py" >/dev/null 2>&1 || true
pkill -f "server.py --port ${PORT}" >/dev/null 2>&1 || true
pkill -f "${Q1_STORE_ROOT}/scripts/hermes_server.sh" >/dev/null 2>&1 || true
pkill -f "llama_cpp.server .*--port ${LLM_PORT}" >/dev/null 2>&1 || true
pkill -f "a.pinggy.io" >/dev/null 2>&1 || true
pkill -f "${Q1_STORE_ROOT}/scripts/pinggy.sh" >/dev/null 2>&1 || true
kill_port "$PORT"
kill_port "$LLM_PORT"
sleep 1

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
  : >"$LOG_DIR/pinggy.log"
  nohup bash "$Q1_STORE_ROOT/scripts/pinggy.sh" "$PORT" \
    >>"$LOG_DIR/pinggy.log" 2>&1 &
  echo "pinggy pid $! — log $LOG_DIR/pinggy.log"
  PRIMARY=""
  for _ in $(seq 1 15); do
    PRIMARY="$(grep -Eo 'https://[a-z0-9.-]+\.free\.pinggy\.net' "$LOG_DIR/pinggy.log" 2>/dev/null | tail -1 || true)"
    [[ -n "$PRIMARY" ]] && break
    sleep 1
  done
  ALT="$(grep -Eo 'https://[a-z0-9.-]+\.run\.pinggy-free\.link' "$LOG_DIR/pinggy.log" 2>/dev/null | tail -1 || true)"
  if [[ -n "$PRIMARY" ]]; then
    echo "Pinggy ready: $PRIMARY"
    [[ -n "$ALT" ]] && echo "Pinggy alt:   $ALT"
  else
    echo "Pinggy URL not printed yet — see $LOG_DIR/pinggy.log" >&2
  fi
fi
