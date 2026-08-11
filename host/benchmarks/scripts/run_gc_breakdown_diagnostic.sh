#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
source "${script_dir}/formal_host_worktree.sh"

# Print supported diagnostic configurations and workloads.
usage() {
    cat <<'EOF'
Usage: sudo ./run_gc_breakdown_diagnostic.sh <configuration> [workload]

Configurations:
  original-ori
  original-csgc
  mcsgc8t-nopipeline

Workloads:
  bigfile     4-job single-big-file workload (default)
  smallfile   16-job partitioned small-file workload
EOF
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage
    exit 1
fi

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: run this diagnostic through sudo." >&2
    exit 1
fi

configuration=$1
workload=${2:-bigfile}

case "${workload}" in
    bigfile)
        config_path="configs/config25_fio_formal_performance_bigfile_randwrite.sh"
        ;;
    smallfile)
        config_path="configs/config24_fio_formal_performance_16t26336file.sh"
        ;;
    *)
        usage
        exit 1
        ;;
esac

case "${configuration}" in
    original-ori)
        expected_branch=exp/diagnostic-original-gc-breakdown-20260811
        prepare_configuration=original
        test_mode=ori
        expected_production=undefined
        expected_move_plan=undefined
        expected_fast_unsafe=undefined
        run_breakdown_parser=0
        ;;
    original-csgc)
        expected_branch=exp/diagnostic-original-gc-breakdown-20260811
        prepare_configuration=original
        test_mode=diagnostic-original-csgc
        expected_production=undefined
        expected_move_plan=undefined
        expected_fast_unsafe=undefined
        run_breakdown_parser=0
        ;;
    mcsgc8t-nopipeline)
        expected_branch=exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811
        prepare_configuration=mcsgc8t-nopipeline
        test_mode=diagnostic-mcsgc8t-nopipeline-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    *)
        usage
        exit 1
        ;;
esac

host_tree=$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")
actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    echo "ERROR: wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
    exit 1
fi

module_path="${host_tree}/fs/f2fs/f2fs.ko"
if [ ! -r "${module_path}" ]; then
    echo "ERROR: diagnostic Host module is unavailable: ${module_path}" >&2
    echo "Action: run './prepare_gc_breakdown_host_module.sh ${prepare_configuration}' first." >&2
    exit 1
fi

module_srcversion=$(modinfo -F srcversion "${module_path}")
if [ -z "${module_srcversion}" ]; then
    echo "ERROR: diagnostic f2fs module has no srcversion." >&2
    exit 1
fi
if [ -r /sys/module/f2fs/srcversion ]; then
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    if [ "${loaded_srcversion^^}" != "${module_srcversion^^}" ]; then
        echo "ERROR: loaded f2fs module does not match the diagnostic module." >&2
        echo "Loaded srcversion: ${loaded_srcversion}" >&2
        echo "Expected srcversion: ${module_srcversion}" >&2
        exit 1
    fi
fi

host_commit=$(git -C "${host_tree}" rev-parse HEAD)
module_sha256=$(sha256sum "${module_path}" | awk '{print $1}')
output_root="${script_dir}/outputs-${test_mode}-ssd1t"
run_started_epoch=$(date +%s)
external_log=$(mktemp --tmpdir "gc-breakdown-${configuration}.XXXXXX.log")
collector_pid=""

# Stop the external dmesg collector without affecting the benchmark process.
stop_collector() {
    if [ -n "${collector_pid}" ] && kill -0 "${collector_pid}" 2>/dev/null; then
        kill "${collector_pid}" 2>/dev/null || true
        wait "${collector_pid}" 2>/dev/null || true
    fi
    collector_pid=""
}

# Preserve the temporary log path when the runner is interrupted.
handle_signal() {
    stop_collector
    echo "Interrupted. External dmesg remains at ${external_log}" >&2
    exit 130
}

trap stop_collector EXIT
trap handle_signal INT TERM

echo "Diagnostic configuration: ${configuration}"
echo "Diagnostic workload: ${workload}"
echo "Host tree: ${host_tree}"
echo "Host branch: ${actual_branch}"
echo "Host commit: ${host_commit}"
echo "Module SHA-256: ${module_sha256}"
echo "External dmesg temporary path: ${external_log}"

dmesg --follow --color=never > "${external_log}" 2>&1 &
collector_pid=$!

export F2FS_KERNEL_PATH_OVERRIDE="${host_tree}"
export CSGC_EXPECTED_SSD_THREAD_MODE=ssd1t
export CSGC_EXPECTED_OPENSSD_PRODUCTION_PERFORMANCE="${expected_production}"
export CSGC_EXPECTED_MOVE_PLAN_V2="${expected_move_plan}"
export CSGC_EXPECTED_MOVE_PLAN_FAST_UNSAFE="${expected_fast_unsafe}"
export FORMAL_HOST_BRANCH="${actual_branch}"
export FORMAL_HOST_COMMIT="${host_commit}"
export FORMAL_MODULE_SHA256="${module_sha256}"

set +e
(
    cd "${script_dir}"
    ./test.sh "${test_mode}" "${config_path}"
)
test_status=$?
set -e

stop_collector

latest_output=$(
    find "${output_root}" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | head -n 1 | cut -d' ' -f2- || true
)
if [ -z "${latest_output}" ] || [ "$(stat -c %Y "${latest_output}")" -lt "${run_started_epoch}" ]; then
    echo "ERROR: failed to locate this diagnostic output directory." >&2
    echo "External dmesg remains at ${external_log}" >&2
    exit 1
fi

run_dir=$(find "${latest_output}" -mindepth 1 -maxdepth 1 -type d -name 'fio_*' -print -quit || true)
if [ -z "${run_dir}" ]; then
    echo "ERROR: fio result directory is missing under ${latest_output}." >&2
    echo "External dmesg remains at ${external_log}" >&2
    exit 1
fi

saved_external_log="${run_dir}/external-dmesg.log"
mv "${external_log}" "${saved_external_log}"
external_log="${saved_external_log}"

summary_path="${run_dir}/gc-breakdown-diagnostic-result.txt"
crop_path="${run_dir}/measured-fio-dmesg.log"
python3 "${script_dir}/draw-xie/analyze-gc-breakdown-diagnostic.py" \
    "${saved_external_log}" "${summary_path}" --crop-output "${crop_path}"

if [ "${run_breakdown_parser}" -eq 1 ]; then
    python3 "${script_dir}/draw-xie/breakdown.py" "${crop_path}"
fi

echo "Diagnostic output directory: ${run_dir}"
echo "Primary summary: ${summary_path}"

if [ "${test_status}" -ne 0 ]; then
    echo "ERROR: benchmark exited with status ${test_status}." >&2
    exit "${test_status}"
fi
