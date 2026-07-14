#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export GLANCE_QDRANT_URL="${GLANCE_QDRANT_URL:-qdrant_storage/experiments/full_lora_final_cpu}"
export GLANCE_RECORDS_PATH="${GLANCE_RECORDS_PATH:-data/processed/corpus.jsonl}"
export GLANCE_GENERIC_MODEL="${GLANCE_GENERIC_MODEL:-models/openai-clip-vit-base-patch32}"
export GLANCE_FASHION_MODEL="${GLANCE_FASHION_MODEL:-models/marqo-fashionCLIP}"
export GLANCE_FASHION_ADAPTER="${GLANCE_FASHION_ADAPTER:-artifacts/fashionclip-lora-5000-e1}"
export GLANCE_DEVICE="${GLANCE_DEVICE:-cpu}"

CUDA_LIBRARY_DIR="${HOME}/.local/lib/python3.13/site-packages/nvidia/cu13/lib"
if [[ "${GLANCE_DEVICE}" == "cuda" && -d "${CUDA_LIBRARY_DIR}" ]]; then
  export LD_LIBRARY_PATH="${CUDA_LIBRARY_DIR}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

required_paths=(
  "${GLANCE_QDRANT_URL}"
  "${GLANCE_RECORDS_PATH}"
  "${GLANCE_GENERIC_MODEL}"
  "${GLANCE_FASHION_MODEL}"
  "${GLANCE_FASHION_ADAPTER}/adapter_model.safetensors"
)
for required_path in "${required_paths[@]}"; do
  if [[ ! -e "${required_path}" ]]; then
    echo "Missing required demo asset: ${required_path}" >&2
    exit 1
  fi
done

if [[ "${GLANCE_DEVICE}" == "cuda" ]]; then
  if ! "${PYTHON_BIN}" -c "import torch; assert torch.cuda.is_available(), 'CUDA is unavailable'; print('GPU:', torch.cuda.get_device_name(0))"; then
    echo "CUDA is not available to this process. Run with GLANCE_DEVICE=cpu or repair the NVIDIA runtime." >&2
    exit 1
  fi
fi

echo "Glance demo (${GLANCE_DEVICE}): http://${GLANCE_HOST:-127.0.0.1}:${GLANCE_PORT:-8000}"
exec "${PYTHON_BIN}" -m glance_retrieval.cli serve \
  --host "${GLANCE_HOST:-127.0.0.1}" \
  --port "${GLANCE_PORT:-8000}"
