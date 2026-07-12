#!/bin/bash

source ./common.sh
mntpoint=${MNTPOINT}

: "${fio_gc_precondition:=0}"
: "${fio_gc_precondition_size_per_job:=4G}"
: "${fio_gc_precondition_max_rounds:=4}"

if [[ ! "${fio_gc_precondition}" =~ ^[01]$ ]]; then
    echo "ERROR: fio_gc_precondition must be 0 or 1" >&2
    exit 1
fi
if [[ ! "${fio_gc_precondition_max_rounds}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: fio_gc_precondition_max_rounds must be a positive integer" >&2
    exit 1
fi

# Run fio with the configured cgroup and preserve fio's exit status through tee.
run_fio_logged() {
    local log_file=$1
    shift
    local command_status

    if [ "${use_cgroup}" -eq 1 ]; then
        sudo cgexec -g "memory:${CGROUP_NAME}" fio "$@" 2>&1 | tee -a "${log_file}"
        command_status=${PIPESTATUS[0]}
    else
        fio "$@" 2>&1 | tee -a "${log_file}"
        command_status=${PIPESTATUS[0]}
    fi

    return "${command_status}"
}

# Emit a timestamped marker to both kern.log and the kernel ring buffer.
emit_kernel_marker() {
    local message=$1
    local ts_local
    local host_local
    local ts_upt

    ts_local=$(date '+%b %e %H:%M:%S')
    host_local=$(hostname)
    ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)

    printf 'IN BASH %s %s [%s] %s\n' \
        "${ts_local}" "${host_local}" "${ts_upt}" "${message}" \
        | sudo tee -a /var/log/kern.log >/dev/null
    printf '<6>IN BASH %s %s [%s] %s\n' \
        "${ts_local}" "${host_local}" "${ts_upt}" "${message}" \
        | sudo tee /dev/kmsg >/dev/null
}

# Reject a fio run that recreated or laid out the prefilled data file.
verify_fio_reused_prefill() {
    local log_file=$1

    if grep -Fq 'Laying out IO file' "${log_file}"; then
        echo "ERROR: fio laid out the IO file instead of reusing the prefilled file" >&2
        return 1
    fi
}

# Verify that fio and remounts preserved the prefilled file identity and size.
verify_prefill_file_identity() {
    local file_path=$1
    local expected_size=$2
    local expected_inode=$3
    local actual_size
    local actual_inode

    if ! actual_size=$(stat -c '%s' -- "${file_path}"); then
        echo "ERROR: prefill file is missing: ${file_path}" >&2
        return 1
    fi
    actual_inode=$(stat -c '%i' -- "${file_path}") || return 1
    if [ "${actual_size}" -ne "${expected_size}" ]; then
        echo "ERROR: prefill file changed size: expected=${expected_size}, actual=${actual_size}" >&2
        return 1
    fi
    if [ "${actual_inode}" -ne "${expected_inode}" ]; then
        echo "ERROR: prefill file was replaced: expected_inode=${expected_inode}, actual_inode=${actual_inode}" >&2
        return 1
    fi
}

# Randomly overwrite the prefilled file until foreground GC has started.
precondition_fio_for_gc() {
    local gc_counter_path=$1
    local log_file=$2
    local workload_file=$3
    local initial_gc_calls
    local current_gc_calls
    local round

    if [ ! -r "${gc_counter_path}" ]; then
        echo "ERROR: foreground GC counter is unavailable: ${gc_counter_path}" >&2
        return 1
    fi

    initial_gc_calls=$(<"${gc_counter_path}")
    : > "${log_file}"

    for ((round = 1; round <= fio_gc_precondition_max_rounds; round++)); do
        echo "Starting GC precondition round ${round}/${fio_gc_precondition_max_rounds}, size_per_job=${fio_gc_precondition_size_per_job}"
        emit_kernel_marker "mCSGC prepare to run fio GC precondition round ${round} in bash"

        if ! run_fio_logged "${log_file}" \
            --directory="${mntpoint}" \
            --alloc-size=16m \
            --filesize="${prefill_size}" \
            --size="${fio_gc_precondition_size_per_job}" \
            --numjobs="${nthreads}" \
            --random_distribution="${random_distribution}" \
            --time_based=0 \
            --overwrite=1 \
            --allow_file_create=0 \
            --end_fsync=1 \
            --eta=never \
            "${workload_file}"; then
            echo "ERROR: fio GC precondition round ${round} failed" >&2
            return 1
        fi

        if ! verify_fio_reused_prefill "${log_file}"; then
            return 1
        fi
        if ! verify_prefill_file_identity \
            "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
            return 1
        fi

        current_gc_calls=$(<"${gc_counter_path}")
        echo "Foreground GC calls after precondition round ${round}: ${initial_gc_calls} -> ${current_gc_calls}"
        if [ "${current_gc_calls}" -gt "${initial_gc_calls}" ]; then
            emit_kernel_marker "mCSGC fio GC precondition completed in bash"
            return 0
        fi
    done

    echo "ERROR: foreground GC did not start after ${fio_gc_precondition_max_rounds} precondition rounds" >&2
    return 1
}

workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}.fio"
output_path=${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}_${prefill_ratio}_${random_distribution}
mkdir -p ${output_path}
exec > >(tee -a "${output_path}/terminal.log") 2>&1

if [ $light_evaluation -eq 1 ]; then
    io_size_per_thread="20G"
    runtime=180
else
    io_size_per_thread="20G"
    runtime=300
    echo "NOTICE: runtime=${runtime}, io_size_per_thread=${io_size_per_thread}"
    sleep 5
    echo "============================="
fi

if [ -z "${should_prefill+x}" ]; then
    should_prefill=0
fi
echo "NOTICE: should_prefill=${should_prefill}"

nthreads="4"
check_kernel $gc_mode
devpath=$(find_cs_device)

if [ "${ssd_enable_dsm}" -eq 1 ]; then 
    f2fs_enable_discard="discard"
else
    f2fs_enable_discard="nodiscard"
fi


echo 0 | sudo tee /proc/sys/kernel/randomize_va_space > /dev/null
echo 20 > /proc/sys/kernel/panic # dont panic! wait 20s before reboot if kernel panics

load_f2fs_module $gc_mode
install_f2fs_tools $gc_mode
prepare_device "${devpath}" "${output_path}"
reset_ssd_config "${devpath}" "${ssd_enable_l2p}" "${ssd_enable_nand_lat}" "${ssd_enable_dsm}"
mkfs_and_mount "${devpath}" "${mntpoint}" "${segs_per_sec}" "${f2fs_enable_discard}" "${ssd_enable_l2p}"
setup_gc_config "${gc_mode}" "${nr_cs_cores}" "${csgc_sync}"
setup_cgroup_mem "${use_cgroup}" "${host_mem_usage}"


echo "======================================================="
# exit 0

if [ $fio_timebased -eq 1 ]; then
    runtime_flags=("--runtime=${runtime}")
else
    runtime_flags=()
fi

fio_flags=(
    "--time_based=${fio_timebased}"
    "--status-interval=5"
)
emit_kernel_marker "mCSGC prepare to run prefill_storage_fio in bash"

# only do prefill and build fio_flags when not the special bmname
if [ "${bmname}" == "randwrite" ]; then
    # prefill step
    echo "NOTICE: bmname == randwrite, prepare do prefill"
    echo "========================="
    if ! prefill_outputs="$(prefill_storage_fio "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")"; then
        echo "${prefill_outputs}"
        echo "ERROR: storage prefill failed" >&2
        sudo umount "${devpath}" >/dev/null 2>&1 || true
        exit 1
    fi
    echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
    if [[ ! "${prefill_size}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: failed to parse the verified prefill size" >&2
        sudo umount "${devpath}" >/dev/null 2>&1 || true
        exit 1
    fi
    prefill_file="${mntpoint}/${DUMMY_FILE_NAME}1"
    prefill_inode=$(stat -c '%i' -- "${prefill_file}") || exit 1

    fio_flags=(
        "--directory=${mntpoint}"
        "--alloc-size=16m"
        "--filesize=${prefill_size}"
        "--size=${io_size_per_thread}"
        "--numjobs=${nthreads}"
        "--random_distribution=${random_distribution}"
        "--time_based=${fio_timebased}"
        "--status-interval=1"
        "--overwrite=1"
        "--allow_file_create=0"
    )

    if [ "${fio_gc_precondition}" -eq 1 ]; then
        f2fs_sysfs_dir="/sys/fs/f2fs/${devpath##*/}"
        gc_counter_path="${f2fs_sysfs_dir}/stat/gc_foreground_calls"
        precondition_log="${output_path}/${workload_type}.precondition.log"

        if ! precondition_fio_for_gc "${gc_counter_path}" "${precondition_log}" "${workload_path}"; then
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if ! remount_f2fs_for_measurement \
            "${devpath}" "${mntpoint}" "${f2fs_enable_discard}" "${ssd_enable_l2p}"; then
            exit 1
        fi
        setup_gc_config "${gc_mode}" "${nr_cs_cores}" "${csgc_sync}"

        if ! verify_prefill_file_identity "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if [ ! -r "${gc_counter_path}" ] || [ "$(<"${gc_counter_path}")" -ne 0 ]; then
            echo "ERROR: foreground GC statistics were not reset by remount" >&2
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if [ -r "${f2fs_sysfs_dir}/free_segments" ]; then
            echo "Free segments before measured fio: $(<"${f2fs_sysfs_dir}/free_segments")"
        fi

        sudo dmesg -c > "${output_path}/dmesg.precondition.log"
    fi
fi

echo "bmname=${bmname}"

if [[ "${bmname}" == rw*file ]]; then
    echo "Pattern matched: rw*file"

    # 1. Remove trailing 'file'
    spec="${bmname%file}"            # e.g. "rw16t50k"

    # 2. Extract numeric spec at the end: digits or digits+'k'
    #    Pattern [!0-9kK] matches any character not 0-9, k, or K.
    #    '##*[!0-9kK]' strips from the left up to and including the last such character.
    spec="${spec##*[!0-9kK]}"       # e.g. "50k" or "200"

    # 3. Validate format: must be pure digits or digits+'k'
    if [[ ! "$spec" =~ ^[0-9]+([kK])?$ ]]; then
        echo "Invalid numeric spec extracted: '${spec}'" >&2
        exit 1
    fi

    # 4. Compute the final num_files
    if [[ "${spec}" =~ [kK]$ ]]; then
        # Remove trailing k/K then multiply by 1000
        num_files=$(( ${spec%[kKk]} * 1000 ))
    else
        num_files=$(( spec ))        # Convert directly to integer
    fi

    # 5. Re-validate the value
    if (( num_files <= 0 )); then
        echo "Parsed num_files <= 0: ${num_files}" >&2
        exit 1
    fi

    echo "Extracted num_files=${num_files}"
    if [[ "${should_prefill}" -eq 1 ]]; then
    prefill_size=$(
  prefill_smallfiles_filewriter "${mntpoint}" "${num_files}" \
    | tee >(grep -vF 'writing file: /home/xin/ssd/mnt/' >&2) \
    | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p'
    ) || exit 1
    else
        echo "Prefill skipped: should_prefill=${should_prefill}"
    fi

else
    echo "NOTICE: bmname does not match 'rw*file': ${bmname}"
    echo "======================================================="
fi



echo "================ FIO WORKLOAD SUMMARY ================"
echo "bmname:               ${bmname}"
echo "workload_path:        ${workload_path}"
echo "gc_precondition:      ${fio_gc_precondition}"
echo "gc_precondition_size: ${fio_gc_precondition_size_per_job} per job"
printf 'runtime_flags:        '
if [ "${#runtime_flags[@]}" -eq 0 ]; then
    printf '(none)'
else
    printf '%q ' "${runtime_flags[@]}"
fi
printf '\n'
printf 'fio_flags:            '
printf '%q ' "${fio_flags[@]}"
printf '\n'
[ -n "${prefill_size}" ] && echo "prefill_size:         ${prefill_size}"
echo "======================================================="

reset_ssd_stat "${devpath}"

echo "=============begin fio============="

measurement_start_uptime=$(awk '{ printf "%.6f", $1 }' /proc/uptime)
emit_kernel_marker "mCSGC prepare to run filebench/fio in bash"

fio_log="${output_path}/${workload_type}.log"
run_fio_logged "${fio_log}" "${fio_flags[@]}" "${runtime_flags[@]}" "${workload_path}"
fio_status=$?

if [ "${bmname}" = "randwrite" ] && ! verify_fio_reused_prefill "${fio_log}"; then
    fio_status=1
fi
if [ "${bmname}" = "randwrite" ] \
    && ! verify_prefill_file_identity "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
    fio_status=1
fi

if [ "${bmname}" = "randwrite" ] && [ "${fio_gc_precondition}" -eq 1 ]; then
    measured_gc_calls=$(<"${gc_counter_path}")
    echo "Foreground GC calls during measured fio: ${measured_gc_calls}"
    if [ "${measured_gc_calls}" -eq 0 ]; then
        echo "ERROR: measured fio completed without foreground GC" >&2
        fio_status=1
    fi

    first_gc_uptime=$(sudo dmesg --color=never \
        | sed -n 's/^\[ *\([0-9][0-9.]*\)\].*F2FS_GC_HEAVY_TRACE .*event=GC_START.*/\1/p' \
        | head -n 1)
    if [ -n "${first_gc_uptime}" ]; then
        first_gc_delay=$(awk -v first="${first_gc_uptime}" -v start="${measurement_start_uptime}" \
            'BEGIN { printf "%.6f", first - start }')
        echo "First foreground GC delay after measured fio start: ${first_gc_delay} seconds" \
            | tee "${output_path}/gc_start_delay.log"
    else
        echo "WARNING: GC occurred, but no F2FS_GC_HEAVY_TRACE start timestamp was found" >&2
    fi
fi
echo "======================================================="

umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

emit_kernel_marker "mCSGC finish umount in bash"
echo "=============end fio============="
echo "If you want to manually run fsck later, you can use the command:"
echo "sudo bash -c \"fsck.f2fs '${devpath}' > '${output_path}/fsck.log'\""
echo "======================================================="
if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
else
    echo "do not run fsck in this bash, fsck_after_run=${fsck_after_run}"
fi

output_uid=${SUDO_UID:-$(id -u)}
output_gid=${SUDO_GID:-$(id -g)}
chown -R "${output_uid}:${output_gid}" "${output_path}"

if [ "${fio_status}" -ne 0 ]; then
    echo "ERROR: fio workload failed with status ${fio_status}" >&2
    exit "${fio_status}"
fi
