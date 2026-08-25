#!/usr/bin/env bash

set -euo pipefail

# Execute an in-memory snapshot so repository updates cannot alter a live run.
if [ -z "${GC_BREAKDOWN_DIAGNOSTIC_SNAPSHOT:-}" ]; then
    diagnostic_script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    diagnostic_script_body=$(<"${diagnostic_script_path}")
    export GC_BREAKDOWN_DIAGNOSTIC_SNAPSHOT=1
    export GC_BREAKDOWN_DIAGNOSTIC_SCRIPT_PATH="${diagnostic_script_path}"
    exec /bin/bash -c "${diagnostic_script_body}" "${diagnostic_script_path}" "$@"
fi

diagnostic_script_path=${GC_BREAKDOWN_DIAGNOSTIC_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${diagnostic_script_path}")" && pwd)
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
  mcsgc8t-pipeline
  mcsgc8t-batch-dnode
  mcsgc8t-summary-control
  mcsgc8t-batch-summary
  mcsgc8t-prealloc-control
  mcsgc8t-prealloc-dirty-batch
  mcsgc8t-section-control
  mcsgc8t-section-dirty-batch
  mcsgc8t-gc-gap-control
  mcsgc8t-unsafe-prefree-reclaim
  mcsgc8t-unsafe-prefree-refill
  mcsgc8t-continuous-supply
  mcsgc8t-conflict-aware-supply
  mcsgc8t-rolling-supply
  mcsgc8t-conflict-aware-lifecycle-fast
  mcsgc8t-rolling-lifecycle-fast
  mcsgc8t-conflict-aware-lifecycle-quiet
  mcsgc8t-rolling-lifecycle-quiet
  mcsgc8t-parallel-gc-control
  mcsgc8t-parallel-gc-inode-share
  mcsgc8t-parallel-gc-dnode-safe
  mcsgc8t-parallel-gc-unsafe-fast
  mcsgc8t-proactive-supply

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
diagnostic_workload_stats=1

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
    mcsgc8t-pipeline)
        expected_branch=exp/diagnostic-mcsgc8t-pipeline-breakdown-20260812
        prepare_configuration=mcsgc8t-pipeline
        test_mode=diagnostic-mcsgc8t-pipeline-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-batch-dnode)
        expected_branch=exp/diagnostic-mcsgc8t-batched-dnode-breakdown-20260817
        prepare_configuration=mcsgc8t-batch-dnode
        test_mode=diagnostic-mcsgc8t-batch-dnode-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-summary-control)
        expected_branch=exp/diagnostic-mcsgc8t-summary-control-20260817
        prepare_configuration=mcsgc8t-summary-control
        test_mode=diagnostic-mcsgc8t-summary-control-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-batch-summary)
        expected_branch=exp/diagnostic-mcsgc8t-batched-summary-breakdown-20260817
        prepare_configuration=mcsgc8t-batch-summary
        test_mode=diagnostic-mcsgc8t-batch-summary-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-prealloc-control)
        expected_branch=exp/diagnostic-mcsgc8t-prealloc-control-20260818
        prepare_configuration=mcsgc8t-prealloc-control
        test_mode=diagnostic-mcsgc8t-prealloc-control-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-prealloc-dirty-batch)
        expected_branch=exp/diagnostic-mcsgc8t-prealloc-dirty-batch-20260818
        prepare_configuration=mcsgc8t-prealloc-dirty-batch
        test_mode=diagnostic-mcsgc8t-prealloc-dirty-batch-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-section-control)
        expected_branch=exp/diagnostic-mcsgc8t-section-critical-control-20260818
        prepare_configuration=mcsgc8t-section-control
        test_mode=diagnostic-mcsgc8t-section-control-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-section-dirty-batch)
        expected_branch=exp/diagnostic-mcsgc8t-section-critical-dirty-batch-20260818
        prepare_configuration=mcsgc8t-section-dirty-batch
        test_mode=diagnostic-mcsgc8t-section-dirty-batch-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-gc-gap-control)
        expected_branch=exp/diagnostic-mcsgc8t-gc-gap-control-20260818
        prepare_configuration=mcsgc8t-gc-gap-control
        test_mode=diagnostic-mcsgc8t-gc-gap-control-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-unsafe-prefree-reclaim)
        expected_branch=exp/diagnostic-mcsgc8t-unsafe-prefree-reclaim-20260818
        prepare_configuration=mcsgc8t-unsafe-prefree-reclaim
        test_mode=diagnostic-mcsgc8t-unsafe-prefree-reclaim-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-unsafe-prefree-refill)
        expected_branch=exp/diagnostic-mcsgc8t-unsafe-prefree-refill-20260818
        prepare_configuration=mcsgc8t-unsafe-prefree-refill
        test_mode=diagnostic-mcsgc8t-unsafe-prefree-refill-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-continuous-supply)
        expected_branch=exp/diagnostic-mcsgc8t-continuous-supply-20260819
        prepare_configuration=mcsgc8t-continuous-supply
        test_mode=diagnostic-mcsgc8t-continuous-supply-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-conflict-aware-supply)
        expected_branch=exp/diagnostic-mcsgc8t-conflict-aware-supply-20260819
        prepare_configuration=mcsgc8t-conflict-aware-supply
        test_mode=diagnostic-mcsgc8t-conflict-aware-supply-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-rolling-supply)
        expected_branch=exp/diagnostic-mcsgc8t-rolling-supply-20260819
        prepare_configuration=mcsgc8t-rolling-supply
        test_mode=diagnostic-mcsgc8t-rolling-supply-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-conflict-aware-lifecycle-fast)
        expected_branch=exp/diagnostic-mcsgc8t-conflict-aware-lifecycle-fast-20260825
        prepare_configuration=mcsgc8t-conflict-aware-lifecycle-fast
        test_mode=diagnostic-mcsgc8t-conflict-aware-lifecycle-fast-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-rolling-lifecycle-fast)
        expected_branch=exp/diagnostic-mcsgc8t-rolling-lifecycle-fast-20260825
        prepare_configuration=mcsgc8t-rolling-lifecycle-fast
        test_mode=diagnostic-mcsgc8t-rolling-lifecycle-fast-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-conflict-aware-lifecycle-quiet)
        expected_branch=exp/formal-mcsgc8t-conflict-aware-lifecycle-quiet-20260825
        prepare_configuration=mcsgc8t-conflict-aware-lifecycle-quiet
        test_mode=formal-mcsgc8t-conflict-aware-lifecycle-quiet-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        diagnostic_workload_stats=0
        ;;
    mcsgc8t-rolling-lifecycle-quiet)
        expected_branch=exp/formal-mcsgc8t-rolling-lifecycle-quiet-20260825
        prepare_configuration=mcsgc8t-rolling-lifecycle-quiet
        test_mode=formal-mcsgc8t-rolling-lifecycle-quiet-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        diagnostic_workload_stats=0
        ;;
    mcsgc8t-parallel-gc-control)
        expected_branch=exp/diagnostic-mcsgc8t-parallel-gc-control-20260821
        prepare_configuration=mcsgc8t-parallel-gc-control
        test_mode=diagnostic-mcsgc8t-parallel-gc-control-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-parallel-gc-inode-share)
        expected_branch=exp/diagnostic-mcsgc8t-parallel-gc-inode-share-20260821
        prepare_configuration=mcsgc8t-parallel-gc-inode-share
        test_mode=diagnostic-mcsgc8t-parallel-gc-inode-share-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-parallel-gc-dnode-safe)
        expected_branch=exp/diagnostic-mcsgc8t-parallel-gc-dnode-safe-20260821
        prepare_configuration=mcsgc8t-parallel-gc-dnode-safe
        test_mode=diagnostic-mcsgc8t-parallel-gc-dnode-safe-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-parallel-gc-unsafe-fast)
        expected_branch=exp/diagnostic-mcsgc8t-parallel-gc-unsafe-fast-20260821
        prepare_configuration=mcsgc8t-parallel-gc-unsafe-fast
        test_mode=diagnostic-mcsgc8t-parallel-gc-unsafe-fast-csgc
        expected_production=1
        expected_move_plan=1
        expected_fast_unsafe=1
        run_breakdown_parser=0
        ;;
    mcsgc8t-proactive-supply)
        proactive_profile=${CSGC_PROACTIVE_PROFILE:-}
        case "${proactive_profile}" in
            off|moderate|aggressive)
                ;;
            *)
                echo "ERROR: CSGC_PROACTIVE_PROFILE must be off, moderate, or aggressive." >&2
                exit 1
                ;;
        esac
        expected_branch=exp/diagnostic-mcsgc8t-proactive-supply-20260822
        prepare_configuration=mcsgc8t-proactive-supply
        test_mode=diagnostic-mcsgc8t-proactive-${proactive_profile}-csgc
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
export collect_diagnostic_workload_stats=${diagnostic_workload_stats}
export csgc_proactive_profile=${proactive_profile:-none}
export FORMAL_HOST_BRANCH="${actual_branch}"
export FORMAL_HOST_COMMIT="${host_commit}"
export FORMAL_MODULE_SHA256="${module_sha256}"

if [ "${GC_BREAKDOWN_SMOKE:-0}" -eq 1 ]; then
    export FIO_IO_SIZE_PER_THREAD_OVERRIDE=${GC_BREAKDOWN_SMOKE_IO_SIZE_PER_THREAD:-1G}
    export FIO_RUNTIME_OVERRIDE=${GC_BREAKDOWN_SMOKE_RUNTIME:-30}
    echo "Diagnostic smoke mode: io_size_per_thread=${FIO_IO_SIZE_PER_THREAD_OVERRIDE} runtime=${FIO_RUNTIME_OVERRIDE}s"
fi

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
# The outer matrix runner executes as the login user and must inspect this log.
chmod 0644 "${saved_external_log}"

summary_path="${run_dir}/gc-breakdown-diagnostic-result.txt"
crop_path="${run_dir}/measured-fio-dmesg.log"
python3 "${script_dir}/draw-xie/analyze-gc-breakdown-diagnostic.py" \
    "${saved_external_log}" "${summary_path}" --crop-output "${crop_path}"

if [ -n "${GC_BREAKDOWN_RESULT_PATH_FILE:-}" ]; then
    result_path_parent=$(dirname -- "${GC_BREAKDOWN_RESULT_PATH_FILE}")
    if [ ! -d "${result_path_parent}" ]; then
        echo "ERROR: result path directory is unavailable: ${result_path_parent}" >&2
        exit 1
    fi
    printf '%s\n' "${run_dir}" > "${GC_BREAKDOWN_RESULT_PATH_FILE}"
fi

if [ "${run_breakdown_parser}" -eq 1 ]; then
    python3 "${script_dir}/draw-xie/breakdown.py" "${crop_path}"
fi

echo "Diagnostic output directory: ${run_dir}"
echo "Primary summary: ${summary_path}"

if [ "${test_status}" -ne 0 ]; then
    echo "ERROR: benchmark exited with status ${test_status}." >&2
    exit "${test_status}"
fi
