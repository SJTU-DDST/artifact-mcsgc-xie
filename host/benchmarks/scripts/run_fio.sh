#!/bin/bash

set -uo pipefail

source ./common.sh
mntpoint=${MNTPOINT}
kernel_panic_timeout=${KERNEL_PANIC_TIMEOUT:-20}
gc_paper_metrics=${GC_PAPER_METRICS:-0}
gc_paper_metrics_path=""
gc_paper_metrics_started=0

# Stop only the measurement epoch started by this invocation.
stop_gc_paper_metrics() {
    local output_file=${1:-}

    if [ "${gc_paper_metrics_started}" -ne 1 ]; then
        return
    fi
    printf 'stop\n' | sudo tee "${gc_paper_metrics_path}" >/dev/null || true
    gc_paper_metrics_started=0
    if [ -n "${output_file}" ]; then
        sudo cat "${gc_paper_metrics_path}" > "${output_file}" || true
    fi
}

# Preserve the benchmark status while closing an active metrics epoch.
cleanup_fio_run() {
    local status=$?

    stop_gc_paper_metrics "${output_path:-.}/gc-paper-metrics-abort.log"
    return "${status}"
}

trap cleanup_fio_run EXIT

case "${kernel_panic_timeout}" in
    ''|*[!0-9]*)
        echo "ERROR: KERNEL_PANIC_TIMEOUT must be a non-negative integer" >&2
        exit 2
        ;;
esac
case "${gc_paper_metrics}" in
    0|1) ;;
    *)
        echo "ERROR: GC_PAPER_METRICS must be 0 or 1" >&2
        exit 2
        ;;
esac

if [ $light_evaluation -eq 1 ]; then
    io_size_per_thread="20G"
    runtime=180
else
    io_size_per_thread="20G"
    runtime=300
fi
nthreads="4"
check_kernel $gc_mode
devpath=$(find_cs_device)

if [ "${ssd_enable_dsm}" -eq 1 ]; then 
    f2fs_enable_discard="discard"
else
    f2fs_enable_discard="nodiscard"
fi
workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}.fio"
output_path=${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}_${prefill_ratio}_${random_distribution}
mkdir -p ${output_path}

echo 0 | sudo tee /proc/sys/kernel/randomize_va_space > /dev/null
printf '%s\n' "${kernel_panic_timeout}" | sudo tee /proc/sys/kernel/panic > /dev/null

# load_f2fs_module $gc_mode
install_f2fs_tools $gc_mode
prepare_device "${devpath}" "${output_path}"
reset_ssd_config "${devpath}" "${ssd_enable_l2p}" "${ssd_enable_nand_lat}" "${ssd_enable_dsm}"
mkfs_and_mount "${devpath}" "${mntpoint}" "${segs_per_sec}" "${f2fs_enable_discard}" "${ssd_enable_l2p}"
setup_gc_config "${gc_mode}" "${nr_cs_cores}" "${csgc_sync}"
setup_cgroup_mem "${use_cgroup}" "${host_mem_usage}"


echo "======================================================="
# exit 0

if ! prefill_outputs="$(prefill_storage_fio "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")"; then
    echo "ERROR: fio prefill failed" >&2
    exit 1
fi
echo "${prefill_outputs}"
prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
if [ -z "${prefill_size}" ]; then
    echo "ERROR: failed to parse fio prefill size" >&2
    exit 1
fi

fio_flags="
    --directory=${mntpoint}
    --alloc-size=16m 
    --filesize=${prefill_size} 
    --size=${io_size_per_thread}
    --numjobs=${nthreads} 
    --random_distribution=${random_distribution} 
    --time_based=${fio_timebased} 
"
if [ $fio_timebased -eq 1 ]; then
    runtime_flag="--runtime=${runtime}"
else
    runtime_flag=""
fi
    
reset_ssd_stat "${devpath}"

if [ "${gc_paper_metrics}" -eq 1 ]; then
    gc_paper_metrics_path="/sys/fs/f2fs/$(basename -- "${devpath}")/gc_paper_metrics"
    sudo test -f "${gc_paper_metrics_path}" || {
        echo "ERROR: missing GC paper metrics interface: ${gc_paper_metrics_path}" >&2
        exit 1
    }
    sudo cat "${gc_paper_metrics_path}" > "${output_path}/gc-paper-metrics-before.log"
    printf 'start\n' | sudo tee "${gc_paper_metrics_path}" >/dev/null
    gc_paper_metrics_started=1
    sudo cat "${gc_paper_metrics_path}" > "${output_path}/gc-paper-metrics-start.log"
fi

fio_status=0
if [ ${use_cgroup} -eq 1 ]; then
    sudo cgexec -g memory:${CGROUP_NAME} fio ${fio_flags} ${runtime_flag} "${workload_path}" \
        2>&1 | tee -a "${output_path}/${workload_type}.log" || fio_status=$?
else
    fio ${fio_flags} "${workload_path}" \
        2>&1 | tee -a "${output_path}/${workload_type}.log" || fio_status=$?
fi
stop_gc_paper_metrics "${output_path}/gc-paper-metrics.log"
echo "======================================================="

umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

chown -R "$(whoami):$(whoami)" "${output_path}"

if [ "${fio_status}" -ne 0 ]; then
    echo "ERROR: fio failed with status ${fio_status}" >&2
    exit "${fio_status}"
fi

if ! grep -q 'Run status group' "${output_path}/${workload_type}.log"; then
    echo "ERROR: fio log has no final run status" >&2
    exit 1
fi
