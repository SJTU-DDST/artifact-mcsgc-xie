#!/usr/bin/env bash

set -euo pipefail

# Collect ORI and original CSGC migration metrics and strict timelines.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

export PAPER_METRICS_PROFILE=original
exec "${SCRIPT_DIR}/run_europar25_paper_metrics.sh" "$@"
