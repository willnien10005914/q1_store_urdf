#!/usr/bin/env bash
set -euo pipefail
Q1_STORE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
Q1_STORE_PYTHON="${Q1_STORE_PYTHON:-python3}"
"$Q1_STORE_PYTHON" -c 'import sys; sys.version_info >= (3, 11) or sys.exit("Q1 Store scripts require Python 3.11+")'
exec "$Q1_STORE_PYTHON" "$Q1_STORE_ROOT/server.py" "$@"
