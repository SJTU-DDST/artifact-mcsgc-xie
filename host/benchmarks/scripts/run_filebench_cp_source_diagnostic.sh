#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

# One short fileserver run crosses the known collapse point while limiting the
# amount of delayed writeback that teardown must drain.
export FILEBENCH_AB_PROFILE=repeat
export FILEBENCH_AB_CONFIGS=cp-source
export FILEBENCH_AB_WORKLOADS=filebench-fileserver
export FILEBENCH_AB_REPETITIONS=1
export FILEBENCH_AB_RUNTIME=${FILEBENCH_CP_SOURCE_RUNTIME:-120}
export FILEBENCH_AB_REPORT_INTERVAL=${FILEBENCH_CP_SOURCE_REPORT_INTERVAL:-5}
export FILEBENCH_AB_STATUS_SAMPLE_INTERVAL=${FILEBENCH_CP_SOURCE_STATUS_INTERVAL:-5}

exec "${SCRIPT_DIR}/run_filebench_mcsgc_ab.sh" "$@"
