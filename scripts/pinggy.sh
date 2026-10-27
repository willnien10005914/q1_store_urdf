#!/usr/bin/env bash
# Free Pinggy reverse tunnel to the local Q1 Store web server (HTTPS only).
#
# Manual one-liner (same options):
#   ssh -4 -t -p 443 \
#     -o StrictHostKeyChecking=accept-new \
#     -o ServerAliveInterval=30 \
#     -o ServerAliveCountMax=3 \
#     -o ExitOnForwardFailure=yes \
#     -o ConnectTimeout=20 \
#     -R0:127.0.0.1:8878 \
#     "x:https@a.pinggy.io"
#
# Usage:
#   bash scripts/pinggy.sh              # tunnel to 127.0.0.1:8878
#   bash scripts/pinggy.sh 8876         # other local port
#   Q1_STORE_PORT=8878 bash scripts/pinggy.sh
#
# The public URL is https://….free.pinggy.net (HTTP redirects to HTTPS).
# Free tunnels expire in ~60 minutes and may show an interstitial
# ("Enter site"). Keep this process in the foreground; Ctrl-C stops it.
# Prefer -4: IPv6 to a.pinggy.io often fails on this Spark.
#
# If a corporate SWG still blocks *.pinggy.net / *.pinggy.link, that is a
# domain category block — force HTTPS will not unblock it; ask IT to allow
# the domain, or use a Pinggy Pro / Cloudflare custom domain they approve.
set -euo pipefail

PORT="${1:-${Q1_STORE_PORT:-8878}}"
HOST="${Q1_STORE_TUNNEL_HOST:-127.0.0.1}"
# x:https = redirect HTTP → HTTPS at the Pinggy edge (local store stays HTTP).
PINGGY_USER="${Q1_PINGGY_USER:-x:https}"

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "usage: $0 [local-port]" >&2
  exit 2
fi

if ! command -v ssh >/dev/null; then
  echo "ssh is required" >&2
  exit 1
fi

echo "Q1 Store Pinggy: HTTPS public URL → http://${HOST}:${PORT} (local)"
echo "Use only the https://….free.pinggy.net link (HTTP redirects)."
echo "Free tunnel ~60 min. Press Ctrl-C to stop."
echo

exec ssh -4 -t -p 443 \
  -o StrictHostKeyChecking=accept-new \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -o ConnectTimeout=20 \
  -R0:"${HOST}:${PORT}" \
  "${PINGGY_USER}@a.pinggy.io"
