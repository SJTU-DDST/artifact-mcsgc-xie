#!/usr/bin/env bash

set -euo pipefail

# Run the strict same-binary Control/NOWAIT Filebench performance acceptance.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)

export FILEBENCH_AB_PROFILE=repeat
export FILEBENCH_AB_CONFIGS=curseg-rollover-control,curseg-rollover-nowait
export FILEBENCH_AB_WORKLOADS=filebench-varmail,filebench-fileserver
export FILEBENCH_AB_REPETITIONS=${FILEBENCH_CURSEG_REPETITIONS:-3}
export FILEBENCH_AB_RUNTIME=${FILEBENCH_CURSEG_RUNTIME:-300}
export FILEBENCH_AB_REPORT_INTERVAL=${FILEBENCH_CURSEG_REPORT_INTERVAL:-5}
export FILEBENCH_AB_STATUS_SAMPLE_INTERVAL=${FILEBENCH_CURSEG_STATUS_INTERVAL:-5}
export FILEBENCH_AB_SCHEDULE_MODE=alternating
export FILEBENCH_AB_FSCK_AFTER_CASE=1
export FILEBENCH_AB_CONTINUE_ON_FSCK_FAILURE=0
export FILEBENCH_AB_CHECK_CHECKPOINTS=1
export FILEBENCH_TEARDOWN_DIAGNOSTICS=1
export KERNEL_PANIC_TIMEOUT=0

exec "${SCRIPT_DIR}/run_filebench_mcsgc_ab.sh" "$@"
