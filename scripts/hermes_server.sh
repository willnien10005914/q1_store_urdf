#!/usr/bin/env bash
# Start Hermes 3 (GGUF) via llama-cpp-python OpenAI-compatible server on GPU.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${Q1_HERMES_VENV:-$ROOT/.venv-llm}"
MODEL="${Q1_HERMES_MODEL:-$ROOT/.data/models/NousResearch--Hermes-3-Llama-3.1-8B-GGUF/Hermes-3-Llama-3.1-8B.Q4_K_M.gguf}"
PORT="${Q1_LLM_PORT:-8877}"
CUDART="${Q1_CUDART_DIR:-$ROOT/.data/llama-cpp/cudart/cudart-llama-b12018-bin-ubuntu-cuda-12.8-x64}"
NV_ROOT="${Q1_NVIDIA_LIBS:-$HOME/.venv/myisaacsim/lib/python3.11/site-packages/nvidia}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "missing $VENV — run: bash scripts/setup_store.sh --hermes-only" >&2
  exit 1
fi
if [[ ! -f "$MODEL" ]]; then
  echo "missing model $MODEL — run: bash scripts/setup_store.sh --hermes-only" >&2
  exit 1
fi

export LD_LIBRARY_PATH="$CUDART:${NV_ROOT}/cuda_runtime/lib:${NV_ROOT}/cublas/lib:${NV_ROOT}/cuda_nvrtc/lib:${LD_LIBRARY_PATH:-}"

exec "$VENV/bin/python" -m llama_cpp.server \
  --model "$MODEL" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --n_gpu_layers "${Q1_HERMES_GPU_LAYERS:-99}" \
  --n_ctx "${Q1_HERMES_CTX:-8192}" \
  --chat_format chatml-function-calling
