#!/usr/bin/env bash

set -euo pipefail

# Run only the Euro-Par fio sweeps that require physical WAF measurements.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

export EUROPAR_NOWAIT_PROFILE=paper-waf
EUROPAR_NOWAIT_LAUNCHER_NAME=$(basename -- "${SCRIPT_PATH}")
export EUROPAR_NOWAIT_LAUNCHER_NAME
exec "${SCRIPT_DIR}/run_europar25_mcsgc_nowait_matrix_3x.sh" "$@"
