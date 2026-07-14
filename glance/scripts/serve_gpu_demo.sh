#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GLANCE_DEVICE="${GLANCE_DEVICE:-cuda}"
exec bash "${PROJECT_ROOT}/scripts/serve_demo.sh"
