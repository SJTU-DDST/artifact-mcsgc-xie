#!/bin/bash

set -uo pipefail

source ./common.sh
mntpoint=${MNTPOINT}
filebench_report_interval=${FILEBENCH_REPORT_INTERVAL:-0}
f2fs_status_sample_interval=${F2FS_STATUS_SAMPLE_INTERVAL:-0}
status_sampler_pid=""
phase_timing_file=""

# Record coarse benchmark boundaries outside the measured request path.
record_phase_time() {
    local phase=$1

    printf '%s\t%s\t%s\n' "${phase}" "$(date +%s%N)" \
        "$(date --iso-8601=ns)" >> "${phase_timing_file}"
}

# Stop only the sampler started by this invocation.
stop_f2fs_status_sampler() {
    if [ -n "${status_sampler_pid}" ] && kill -0 "${status_sampler_pid}" 2>/dev/null; then
        kill "${status_sampler_pid}" 2>/dev/null || true
        wait "${status_sampler_pid}" 2>/dev/null || true
    fi
    status_sampler_pid=""
}

# Save low-frequency F2FS state snapshots without adding kernel instrumentation.
sample_f2fs_status() {
    local output_file=$1
    local interval=$2

    while findmnt -rn -S "${devpath}" >/dev/null; do
        printf '=== F2FS_STATUS_SAMPLE wall=%s realtime_ns=%s ===\n' \
            "$(date --iso-8601=ns)" "$(date +%s%N)" >> "${output_file}"
        if /usr/bin/sudo -n test -r "${DEBUGFS_PATH}/status"; then
            /usr/bin/sudo -n cat "${DEBUGFS_PATH}/status" >> "${output_file}" || true
        else
            printf 'status_unavailable=1\n' >> "${output_file}"
        fi
        printf '=== F2FS_STATUS_SAMPLE_END ===\n' >> "${output_file}"
        sleep "${interval}"
    done
}

trap stop_f2fs_status_sampler EXIT

case "${filebench_report_interval}" in
    ''|*[!0-9]*)
        echo "ERROR: FILEBENCH_REPORT_INTERVAL must be a non-negative integer" >&2
        exit 2
        ;;
esac
case "${f2fs_status_sample_interval}" in
    ''|*[!0-9]*)
        echo "ERROR: F2FS_STATUS_SAMPLE_INTERVAL must be a non-negative integer" >&2
        exit 2
        ;;
esac

if [ $light_evaluation -eq 1 ]; then
    runtime=60
else
    runtime=300
fi
check_kernel $gc_mode
devpath=$(find_cs_device)

if [ "${ssd_enable_dsm}" -eq 1 ]; then 
    f2fs_enable_discard="discard"
else
    f2fs_enable_discard="nodiscard"
fi
workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}.f"
output_path=${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}
mkdir -p ${output_path}
phase_timing_file="${output_path}/filebench-phase-times.tsv"
printf 'phase\trealtime_ns\twall_time\n' > "${phase_timing_file}"

echo 0 | sudo tee /proc/sys/kernel/randomize_va_space > /dev/null
echo 20 | sudo tee /proc/sys/kernel/panic > /dev/null

# load_f2fs_module $gc_mode
install_f2fs_tools $gc_mode
prepare_device "${devpath}" "${output_path}"
reset_ssd_config "${devpath}" "${ssd_enable_l2p}" "${ssd_enable_nand_lat}" "${ssd_enable_dsm}"
mkfs_and_mount "${devpath}" "${mntpoint}" "${segs_per_sec}" "${f2fs_enable_discard}" "${ssd_enable_l2p}"
setup_gc_config "${gc_mode}" "${nr_cs_cores}" "${csgc_sync}"
setup_cgroup_mem "${use_cgroup}" "${host_mem_usage}"

echo "======================================================="

tmp_workload_path="${output_path}/${bmname}.resolved.f"
cp "${workload_path}" "${tmp_workload_path}"
sed -i "s|__DATA_PATH_PLACEHOLDER__|${mntpoint}|g" "${tmp_workload_path}"
sed -i "s|__RUNTIME_PLACEHOLDER__|${runtime}|g" "${tmp_workload_path}"

if [ "${filebench_report_interval}" -gt 0 ]; then
    tmp_periodic_path="${tmp_workload_path}.periodic"
    awk -v interval="${filebench_report_interval}" -v runtime="${runtime}" '
        ($1 == "run" && $2 == "$runtime") ||
        ($1 == "psrun" && $3 == "$runtime") {
            print "psrun -" interval " " runtime
            converted++
            next
        }
        { print }
        END { if (converted != 1) exit 42 }
    ' "${tmp_workload_path}" > "${tmp_periodic_path}" || {
        echo "ERROR: failed to convert Filebench workload to periodic output" >&2
        echo "Failed generated workload retained at ${tmp_periodic_path}" >&2
        exit 1
    }
    mv "${tmp_periodic_path}" "${tmp_workload_path}"
fi

reset_ssd_stat "${devpath}"
if [ "${f2fs_status_sample_interval}" -gt 0 ]; then
    sample_f2fs_status "${output_path}/f2fs-status-timeline.log" \
        "${f2fs_status_sample_interval}" &
    status_sampler_pid=$!
fi
filebench_status=0
record_phase_time filebench_start
if [ ${use_cgroup} -eq 1 ]; then
    sudo cgexec -g memory:${CGROUP_NAME} filebench -f "${tmp_workload_path}" \
    2>&1 | tee -a "${output_path}/${workload_type}.log" || filebench_status=$?
else
    filebench -f "${tmp_workload_path}" \
    2>&1 | tee -a "${output_path}/${workload_type}.log" || filebench_status=$?
fi

# Filebench can return zero after a flowop abort, so reject its fatal markers.
if grep -Eq 'NO VALID RESULTS|Failed to open file|flowop .* failed|Input/output error' \
        "${output_path}/${workload_type}.log"; then
    filebench_status=1
fi
record_phase_time filebench_end
echo "======================================================="

record_phase_time teardown_start
umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"
record_phase_time teardown_end
stop_f2fs_status_sampler

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

chown -R "$(whoami):$(whoami)" "${output_path}"

if [ "${filebench_status}" -ne 0 ]; then
    echo "ERROR: filebench failed with status ${filebench_status}" >&2
    exit "${filebench_status}"
fi

if ! grep -q 'IO Summary:' "${output_path}/${workload_type}.log"; then
    echo "ERROR: filebench log has no IO Summary" >&2
    exit 1
fi
