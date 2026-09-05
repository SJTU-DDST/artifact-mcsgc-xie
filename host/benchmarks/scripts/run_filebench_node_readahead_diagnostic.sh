#!/usr/bin/env bash

set -euo pipefail

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

# Cross the known fileserver collapse point while bounding teardown work.
export FILEBENCH_AB_PROFILE=repeat
export FILEBENCH_AB_CONFIGS=node-readahead
export FILEBENCH_AB_WORKLOADS=filebench-fileserver
export FILEBENCH_AB_REPETITIONS=1
export FILEBENCH_AB_RUNTIME=${FILEBENCH_NODE_READAHEAD_RUNTIME:-65}
export FILEBENCH_AB_REPORT_INTERVAL=${FILEBENCH_NODE_READAHEAD_REPORT_INTERVAL:-5}
export FILEBENCH_AB_STATUS_SAMPLE_INTERVAL=${FILEBENCH_NODE_READAHEAD_STATUS_INTERVAL:-5}

exec "${SCRIPT_DIR}/run_filebench_mcsgc_ab.sh" "$@"
