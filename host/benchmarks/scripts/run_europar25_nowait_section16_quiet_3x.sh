#!/usr/bin/env bash

set -euo pipefail

# Measure corrected section-size-16 NOWAIT throughput with the quiet Host build.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

export PAPER_METRICS_PROFILE=section16-quiet
exec "${SCRIPT_DIR}/run_europar25_paper_metrics.sh" "$@"
