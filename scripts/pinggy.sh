#!/usr/bin/env bash
# Free Pinggy reverse tunnel to the local Q1 Store web server.
#
# Manual one-liner (same options):
#   ssh -4 -p 443 \
#     -o StrictHostKeyChecking=accept-new \
#     -o ServerAliveInterval=30 \
#     -o ServerAliveCountMax=3 \
#     -o ExitOnForwardFailure=yes \
#     -o ConnectTimeout=20 \
#     -R0:127.0.0.1:8878 a.pinggy.io
#
# Usage:
#   bash scripts/pinggy.sh              # tunnel to 127.0.0.1:8878
#   bash scripts/pinggy.sh 8876         # other local port
#   Q1_STORE_PORT=8878 bash scripts/pinggy.sh
#
# Free tunnels expire in ~60 minutes and may show an interstitial
# ("Enter site"). Keep this process in the foreground; Ctrl-C stops it.
# Prefer -4: IPv6 to a.pinggy.io often fails on this Spark.
set -euo pipefail

PORT="${1:-${Q1_STORE_PORT:-8878}}"
HOST="${Q1_STORE_TUNNEL_HOST:-127.0.0.1}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "usage: $0 [local-port]" >&2
  exit 2
fi

if ! command -v ssh >/dev/null; then
  echo "ssh is required" >&2
  exit 1
fi

echo "Q1 Store Pinggy: forwarding public URL → ${HOST}:${PORT}"
echo "Free tunnel ~60 min. Press Ctrl-C to stop."
echo

exec ssh -4 -p 443 \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -o ConnectTimeout=20 \
  -R0:"${HOST}:${PORT}" \
  a.pinggy.io
