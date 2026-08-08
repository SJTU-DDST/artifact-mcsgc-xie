#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
config_path="${script_dir}/configs/config23_fio_pipeline_partitioned_16t26336file.sh"

if [ "$#" -ne 1 ]; then
    echo "Usage: sudo $0 <mode>" >&2
    echo "  mode: any value containing the case-sensitive substring 'csgc' selects CSGC" >&2
    echo "        ori selects the ordinary F2FS GC baseline" >&2
    exit 1
fi

mode_label=$1
case "${mode_label}" in
    *csgc*)
        # Keep arbitrary CSGC labels intact so output directories can encode
        # the exact Host branch or experiment variant chosen by the user.
        export CSGC_EXPECTED_SSD_THREAD_MODE=ssd1t
        export require_pipeline_stats=1
        expected_gc_heavy_mode=""
        ;;
    ori)
        # ORI uses the same file layout and GC-heavy measurement, but it does
        # not execute the CSGC pipeline or depend on an SSD worker count.
        unset CSGC_EXPECTED_SSD_THREAD_MODE
        export require_pipeline_stats=0
        expected_gc_heavy_mode="ori"
        ;;
    *)
        echo "ERROR: unsupported mode: ${mode_label}" >&2
        echo "Expected ori or a value containing the case-sensitive substring 'csgc'." >&2
        exit 1
        ;;
esac

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: this benchmark must run through sudo." >&2
    echo "Run: sudo $0 ${mode_label}" >&2
    exit 1
fi

# fio jobs run as threads and therefore share one descriptor table. The
# workload keeps up to 128 files open in each of 16 jobs, which exceeds the
# common 1024-descriptor soft limit inherited through sudo.
raise_open_file_limit() {
    local desired_limit=65536
    local minimum_limit=4096
    local soft_limit
    local hard_limit
    local target_limit

    soft_limit=$(ulimit -Sn)
    hard_limit=$(ulimit -Hn)
    if [ "${soft_limit}" = "unlimited" ]; then
        echo "Open-file soft limit: unlimited"
        return 0
    fi

    target_limit=${desired_limit}
    if [ "${hard_limit}" != "unlimited" ] \
        && [ "${hard_limit}" -lt "${target_limit}" ]; then
        target_limit=${hard_limit}
    fi
    if [ "${target_limit}" -lt "${minimum_limit}" ]; then
        echo "ERROR: open-file hard limit ${hard_limit} is below the required ${minimum_limit}." >&2
        return 1
    fi

    if [ "${soft_limit}" -lt "${target_limit}" ]; then
        if ! ulimit -Sn "${target_limit}"; then
            echo "ERROR: failed to raise the open-file soft limit from ${soft_limit} to ${target_limit}." >&2
            return 1
        fi
    fi
    echo "Open-file soft limit: $(ulimit -Sn) (hard=${hard_limit})"
}

raise_open_file_limit

export expected_gc_heavy_mode

exec "${script_dir}/test.sh" "${mode_label}" "${config_path}"
