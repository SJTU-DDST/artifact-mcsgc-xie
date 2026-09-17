#!/usr/bin/env bash

set -euo pipefail

# Collect corrected section-size-16 Host migration metrics only.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

export PAPER_METRICS_PROFILE=section16
exec "${SCRIPT_DIR}/run_europar25_paper_metrics.sh" "$@"
