#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python}"
MODEL="${GLANCE_ANNOTATION_MODEL:-gemma3:4b}"
OLLAMA_URL="${GLANCE_OLLAMA_URL:-http://127.0.0.1:11434}"
RECORDS="${GLANCE_ANNOTATION_RECORDS:-data/processed/openimages_unannotated_with_boxes.jsonl}"
OUTPUT="${GLANCE_ANNOTATION_OUTPUT:-data/processed/openimages_gemma3_v5_annotated.jsonl}"
REQUIRE_GPU="${GLANCE_REQUIRE_OLLAMA_GPU:-1}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "${REQUIRE_GPU}" == "1" ]]; then
  nvidia-smi -L >/dev/null
elif [[ "${REQUIRE_GPU}" != "0" ]]; then
  echo "GLANCE_REQUIRE_OLLAMA_GPU must be 0 or 1" >&2
  exit 2
fi

while read -r loaded_model; do
  if [[ -n "${loaded_model}" && "${loaded_model}" != "${MODEL}" ]]; then
    echo "Refusing to share the GPU with loaded Ollama model: ${loaded_model}" >&2
    echo "Run 'ollama stop ${loaded_model}' first, then retry." >&2
    exit 1
  fi
done < <(OLLAMA_HOST="${OLLAMA_URL}" ollama ps | awk 'NR > 1 && NF {print $1}')

if [[ ! -f "${RECORDS}" ]]; then
  echo "Missing annotation records: ${RECORDS}" >&2
  exit 1
fi
TOTAL_RECORDS="$(awk 'NF { count += 1 } END { print count + 0 }' "${RECORDS}")"
LIMIT="${GLANCE_ANNOTATION_LIMIT:-${TOTAL_RECORDS}}"

if [[ "${REQUIRE_GPU}" == "1" ]]; then
  RUNTIME_LABEL="GPU-required"
  GPU_ARGS=(--require-ollama-gpu)
else
  RUNTIME_LABEL="CPU-allowed"
  GPU_ARGS=()
fi

echo "Resuming ${RUNTIME_LABEL} context annotation: ${OUTPUT} (${LIMIT}/${TOTAL_RECORDS} records)"
exec "${PYTHON_BIN}" -u -m glance_retrieval.cli annotate \
  --records-path "${RECORDS}" \
  --output "${OUTPUT}" \
  --ollama \
  --ollama-model "${MODEL}" \
  --ollama-url "${OLLAMA_URL}" \
  --limit "${LIMIT}" \
  --checkpoint-every 5 \
  --retries 3 \
  --retry-backoff 2 \
  --resume \
  "${GPU_ARGS[@]}"
