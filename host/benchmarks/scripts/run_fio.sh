#!/bin/bash

source ./common.sh
mntpoint=${MNTPOINT}

: "${fio_gc_precondition:=0}"
: "${fio_gc_precondition_size_per_job:=4G}"
: "${fio_gc_precondition_max_rounds:=4}"
: "${smallfile_layout:=flat}"
: "${smallfile_jobs:=16}"
: "${smallfile_files_per_job:=0}"
: "${smallfile_size_mb:=1}"
: "${smallfile_prefill_threads:=8}"
: "${require_pipeline_stats:=0}"
: "${expected_gc_heavy_mode:=}"
: "${fio_nofile_limit:=65536}"
: "${formal_performance_only:=0}"
: "${collect_diagnostic_workload_stats:=0}"

if [[ ! "${fio_gc_precondition}" =~ ^[01]$ ]]; then
    echo "ERROR: fio_gc_precondition must be 0 or 1" >&2
    exit 1
fi
if [[ ! "${fio_gc_precondition_max_rounds}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: fio_gc_precondition_max_rounds must be a positive integer" >&2
    exit 1
fi
if [[ ! "${require_pipeline_stats}" =~ ^[01]$ ]]; then
    echo "ERROR: require_pipeline_stats must be 0 or 1" >&2
    exit 1
fi
if [[ ! "${fio_nofile_limit}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: fio_nofile_limit must be a positive integer" >&2
    exit 1
fi
if [[ ! "${formal_performance_only}" =~ ^[01]$ ]]; then
    echo "ERROR: formal_performance_only must be 0 or 1" >&2
    exit 1
fi
if [[ ! "${collect_diagnostic_workload_stats}" =~ ^[01]$ ]]; then
    echo "ERROR: collect_diagnostic_workload_stats must be 0 or 1" >&2
    exit 1
fi

measurement_epoch_enabled=0
if [ "${formal_performance_only}" -eq 0 ] \
    || [ "${collect_diagnostic_workload_stats}" -eq 1 ]; then
    measurement_epoch_enabled=1
fi

# Run fio with the configured cgroup and preserve fio's exit status through tee.
run_fio_logged() {
    local log_file=$1
    shift
    local command_status
    local nofile_hard
    local nofile_soft
    local -a fio_command

    nofile_soft=$(ulimit -Sn)
    nofile_hard=$(ulimit -Hn)
    if [ "${nofile_soft}" != "unlimited" ] \
        && [ "${nofile_soft}" -lt "${fio_nofile_limit}" ]; then
        if ! ulimit -Sn "${fio_nofile_limit}"; then
            printf 'ERROR: cannot raise fio nofile soft limit: current=%s requested=%s hard=%s\n' \
                "${nofile_soft}" "${fio_nofile_limit}" "${nofile_hard}" \
                | tee -a "${log_file}" >&2
            return 1
        fi
        nofile_soft=$(ulimit -Sn)
        nofile_hard=$(ulimit -Hn)
    fi

    printf 'fio launch limits: nofile_soft=%s nofile_hard=%s euid=%s\n' \
        "${nofile_soft}" "${nofile_hard}" "${EUID}" \
        | tee -a "${log_file}"

    if [ "${use_cgroup}" -eq 1 ]; then
        # The benchmark wrapper already runs as root. Avoid a nested sudo here:
        # sudo resets RLIMIT_NOFILE on this host before launching fio.
        if [ "${EUID}" -eq 0 ]; then
            fio_command=(cgexec -g "memory:${CGROUP_NAME}" fio)
        else
            fio_command=(sudo cgexec -g "memory:${CGROUP_NAME}" fio)
        fi
    else
        fio_command=(fio)
    fi

    "${fio_command[@]}" "$@" 2>&1 | tee -a "${log_file}"
    command_status=${PIPESTATUS[0]}

    return "${command_status}"
}

# Emit a timestamped marker to the kernel ring buffer for dmesg collectors.
emit_kernel_marker() {
    local message=$1
    local ts_local
    local host_local
    local ts_upt

    ts_local=$(date '+%b %e %H:%M:%S')
    host_local=$(hostname)
    ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)

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

# Verify that fio and preconditioning preserved the prefilled file identity and size.
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

# Switch the kernel GC measurement epoch, retrying transient busy boundaries.
set_gc_measurement_epoch() {
    local control_path=$1
    local command=$2
    local attempt
    local output

    if [ ! -e "${control_path}" ]; then
        echo "ERROR: GC measurement control is unavailable: ${control_path}" >&2
        return 1
    fi

    for ((attempt = 1; attempt <= 100; attempt++)); do
        if output=$(printf '%s\n' "${command}" | sudo tee "${control_path}" 2>&1); then
            echo "GC measurement epoch command succeeded: ${command}"
            return 0
        fi
        sleep 0.05
    done

    echo "ERROR: GC measurement epoch command failed after 100 attempts: ${command}" >&2
    echo "${output}" >&2
    return 1
}

# Read and validate the currently active GC measurement epoch from sysfs.
read_gc_measurement_epoch() {
    local control_path=$1
    local expected_scope=$2
    local expected_active=$3
    local state
    local epoch
    local scope
    local active

    if ! state=$(<"${control_path}"); then
        echo "ERROR: failed to read GC measurement state: ${control_path}" >&2
        return 1
    fi
    if [[ ! "${state}" =~ ^epoch=([0-9]+)[[:space:]]+scope=([^[:space:]]+)[[:space:]]+workload_active=([01])[[:space:]]+epoch_start_ns=([0-9]+)$ ]]; then
        echo "ERROR: invalid GC measurement state: ${state}" >&2
        return 1
    fi

    epoch=${BASH_REMATCH[1]}
    scope=${BASH_REMATCH[2]}
    active=${BASH_REMATCH[3]}
    if [ "${scope}" != "${expected_scope}" ] || [ "${active}" != "${expected_active}" ]; then
        echo "ERROR: unexpected GC measurement state: epoch=${epoch} scope=${scope} workload_active=${active}" >&2
        return 1
    fi

    printf '%s\n' "${epoch}"
}

# Persist the just-closed epoch summaries before later printk traffic can overwrite them.
capture_gc_measurement_summary() {
    local epoch=$1
    local scope=$2
    local output_file=$3

    sudo dmesg --color=never | awk -v epoch="${epoch}" -v scope="${scope}" '
        BEGIN {
            target = "epoch=" epoch " scope=" scope " "
        }
        index($0, "F2FS_GC_VICTIM_STAT " target) {
            victim = $0
        }
        index($0, "F2FS_GC_HEAVY_STAT " target) &&
            index($0, "epoch_elapsed_us=") &&
            (index($0, "epoch_start_to_first_gc_us=") ||
             index($0, "no_first_gc=1")) {
            heavy = $0
        }
        index($0, "F2FS_CSGC_SUPPLY_STAT " target) &&
            index($0, "kind=summary") {
            supply = $0
        }
        END {
            if (victim == "" || heavy == "") {
                print "ERROR: completed GC measurement summary is missing for epoch=" \
                    epoch " scope=" scope > "/dev/stderr"
                exit 1
            }
            print victim
            print heavy
            if (supply != "")
                print supply
        }
    ' > "${output_file}"
}

# Aggregate the measured-window pipeline records still present in dmesg.
capture_pipeline_summary() {
    local output_file=$1
    local -a pipeline_status

    sudo dmesg --color=never | awk '
        index($0, "CSGC_PIPELINE_STAT ") {
            sections = seg_freed = full = wall = section_sum = ""
            for (i = 1; i <= NF; i++) {
                split($i, pair, "=")
                if (pair[1] == "sections")
                    sections = pair[2] + 0
                else if (pair[1] == "seg_freed")
                    seg_freed = pair[2] + 0
                else if (pair[1] == "full_sections")
                    full = pair[2] + 0
                else if (pair[1] == "wall_us")
                    wall = pair[2] + 0
                else if (pair[1] == "section_sum_us")
                    section_sum = pair[2] + 0
            }
            if (sections == "" || full == "" || wall == "" ||
                section_sum == "")
                next
            records++
            sections_total += sections
            full_total += full
            seg_freed_total += seg_freed
            wall_total += wall
            section_sum_total += section_sum
            if (sections >= 2 && full >= 2)
                full_pair_records++
            if (full < sections)
                partial_records++
        }
        END {
            if (records == 0) {
                print "ERROR: no CSGC_PIPELINE_STAT records found" > "/dev/stderr"
                exit 1
            }
            print "scope=dmesg_tail_sample"
            printf "pipeline_records=%d sections_total=%d full_sections_total=%d seg_freed_total=%d\n", \
                records, sections_total, full_total, seg_freed_total
            printf "full_pair_records=%d full_pair_fraction=%.6f partial_records=%d partial_fraction=%.6f\n", \
                full_pair_records, full_pair_records / records, \
                partial_records, partial_records / records
            printf "wall_us_total=%.0f section_sum_us_total=%.0f lifecycle_overlap=%.6f\n", \
                wall_total, section_sum_total, \
                wall_total ? section_sum_total / wall_total : 0
        }
    ' > "${output_file}"
    pipeline_status=("${PIPESTATUS[@]}")
    if [ "${pipeline_status[0]}" -ne 0 ]; then
        echo "ERROR: failed to read dmesg for pipeline summary" >&2
        return "${pipeline_status[0]}"
    fi
    if [ "${pipeline_status[1]}" -ne 0 ]; then
        return "${pipeline_status[1]}"
    fi

    cat "${output_file}"
}

# Validate the common victim-counter invariant before interpreting GC mode.
validate_gc_victim_invariant() {
    local phase=$1
    local total=$2
    local csgc=$3
    local origc=$4

    if [[ ! "${total}" =~ ^[0-9]+$ ]] \
        || [[ ! "${csgc}" =~ ^[0-9]+$ ]] \
        || [[ ! "${origc}" =~ ^[0-9]+$ ]]; then
        echo "ERROR: invalid ${phase} victim counters: total=${total:-missing} csgc=${csgc:-missing} origc=${origc:-missing}" >&2
        return 1
    fi
    if [ "${total}" -ne $((csgc + origc)) ]; then
        echo "ERROR: ${phase} victim counter invariant failed: total=${total} csgc=${csgc} origc=${origc}" >&2
        return 1
    fi
}

# Require the measured phase to enter the collector selected by the test mode.
validate_gc_victim_target() {
    local phase=$1
    local mode=$2
    local total=$3
    local csgc=$4
    local origc=$5

    if ! validate_gc_victim_invariant \
        "${phase}" "${total}" "${csgc}" "${origc}"; then
        return 1
    fi
    if [ "${total}" -eq 0 ]; then
        echo "ERROR: ${phase} completed without a foreground GC victim" >&2
        return 1
    fi

    case "${mode}" in
        cs)
            if [ "${csgc}" -eq 0 ]; then
                echo "ERROR: ${phase} did not enter the CSGC collector" >&2
                return 1
            fi
            ;;
        ori)
            if [ "${origc}" -eq 0 ] || [ "${csgc}" -ne 0 ]; then
                echo "ERROR: ${phase} did not stay on the ORIGC collector: csgc=${csgc} origc=${origc}" >&2
                return 1
            fi
            ;;
    esac
}

# Randomly overwrite the prefilled file until foreground GC has started.
precondition_fio_for_gc() {
    local gc_counter_path=$1
    local gc_csgc_counter_path=$2
    local gc_origc_counter_path=$3
    local mode=$4
    local log_file=$5
    local workload_file=$6
    local profile=${7:-single_file}
    local partition_jobs=${8:-0}
    local partition_files_per_job=${9:-0}
    local partition_file_size_mb=${10:-0}
    local initial_gc_calls
    local initial_csgc_calls
    local initial_origc_calls
    local current_gc_calls
    local current_csgc_calls
    local current_origc_calls
    local counter_path
    local round

    for counter_path in \
        "${gc_counter_path}" \
        "${gc_csgc_counter_path}" \
        "${gc_origc_counter_path}"; do
        if [ ! -r "${counter_path}" ]; then
            echo "ERROR: foreground GC counter is unavailable: ${counter_path}" >&2
            return 1
        fi
    done

    initial_gc_calls=$(<"${gc_counter_path}")
    initial_csgc_calls=$(<"${gc_csgc_counter_path}")
    initial_origc_calls=$(<"${gc_origc_counter_path}")
    if ! validate_gc_victim_invariant "initial precondition" \
        "${initial_gc_calls}" "${initial_csgc_calls}" "${initial_origc_calls}"; then
        return 1
    fi
    : > "${log_file}"

    for ((round = 1; round <= fio_gc_precondition_max_rounds; round++)); do
        echo "Starting GC precondition round ${round}/${fio_gc_precondition_max_rounds}, size_per_job=${fio_gc_precondition_size_per_job}"
        emit_kernel_marker "mCSGC prepare to run fio GC precondition round ${round} in bash"

        case "${profile}" in
            single_file)
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
                ;;
            partitioned_smallfiles)
                # io_size limits precondition traffic without shrinking the
                # addressable file pool defined by filesize and nrfiles.
                if ! run_fio_logged "${log_file}" \
                    --io_size="${fio_gc_precondition_size_per_job}" \
                    --time_based=0 \
                    --end_fsync=1 \
                    --eta=never \
                    "${workload_file}"; then
                    echo "ERROR: partitioned fio GC precondition round ${round} failed" >&2
                    return 1
                fi
                ;;
            *)
                echo "ERROR: unsupported fio GC precondition profile: ${profile}" >&2
                return 1
                ;;
        esac

        if ! verify_fio_reused_prefill "${log_file}"; then
            return 1
        fi
        case "${profile}" in
            single_file)
                if ! verify_prefill_file_identity \
                    "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
                    return 1
                fi
                ;;
            partitioned_smallfiles)
                if ! verify_partitioned_smallfiles \
                    "${mntpoint}" "${partition_jobs}" \
                    "${partition_files_per_job}" \
                    "${partition_file_size_mb}"; then
                    return 1
                fi
                ;;
        esac

        current_gc_calls=$(<"${gc_counter_path}")
        current_csgc_calls=$(<"${gc_csgc_counter_path}")
        current_origc_calls=$(<"${gc_origc_counter_path}")
        echo "Foreground victim starts after precondition round ${round}: total=${initial_gc_calls}->${current_gc_calls} csgc=${initial_csgc_calls}->${current_csgc_calls} origc=${initial_origc_calls}->${current_origc_calls}"
        if ! validate_gc_victim_invariant "precondition round ${round}" \
            "${current_gc_calls}" "${current_csgc_calls}" \
            "${current_origc_calls}"; then
            return 1
        fi

        if { [ "${mode}" = "cs" ] \
                && [ "${current_csgc_calls}" -gt "${initial_csgc_calls}" ]; } \
            || { [ "${mode}" = "ori" ] \
                && [ "${current_origc_calls}" -gt "${initial_origc_calls}" ] \
                && [ "${current_csgc_calls}" -eq 0 ]; } \
            || { [ "${mode}" != "cs" ] && [ "${mode}" != "ori" ] \
                && [ "${current_gc_calls}" -gt "${initial_gc_calls}" ]; }; then
            if ! validate_gc_victim_target "precondition" "${mode}" \
                "${current_gc_calls}" "${current_csgc_calls}" \
                "${current_origc_calls}"; then
                return 1
            fi
            emit_kernel_marker "mCSGC fio GC precondition completed in bash"
            return 0
        fi
    done

    echo "ERROR: target foreground GC collector did not start after ${fio_gc_precondition_max_rounds} precondition rounds" >&2
    return 1
}

# Apply one fixed amount of preconditioning traffic without consulting custom
# kernel counters. This keeps the formal comparison usable with the original
# CSGC kernel and gives every mode the same precondition traffic.
precondition_fio_fixed() {
    local log_file=$1
    local workload_file=$2
    local profile=${3:-single_file}
    local partition_jobs=${4:-0}
    local partition_files_per_job=${5:-0}
    local partition_file_size_mb=${6:-0}

    : > "${log_file}"
    echo "Starting fixed GC precondition: size_per_job=${fio_gc_precondition_size_per_job}"
    emit_kernel_marker "mCSGC prepare to run fixed fio GC precondition in bash"

    case "${profile}" in
        single_file)
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
                echo "ERROR: fixed fio GC precondition failed" >&2
                return 1
            fi
            ;;
        partitioned_smallfiles)
            if ! run_fio_logged "${log_file}" \
                --io_size="${fio_gc_precondition_size_per_job}" \
                --time_based=0 \
                --end_fsync=1 \
                --eta=never \
                "${workload_file}"; then
                echo "ERROR: fixed partitioned fio GC precondition failed" >&2
                return 1
            fi
            ;;
        *)
            echo "ERROR: unsupported fixed fio GC precondition profile: ${profile}" >&2
            return 1
            ;;
    esac

    if ! verify_fio_reused_prefill "${log_file}"; then
        return 1
    fi
    case "${profile}" in
        single_file)
            verify_prefill_file_identity \
                "${prefill_file}" "${prefill_size}" "${prefill_inode}" \
                || return 1
            ;;
        partitioned_smallfiles)
            verify_partitioned_smallfiles \
                "${mntpoint}" "${partition_jobs}" \
                "${partition_files_per_job}" "${partition_file_size_mb}" \
                || return 1
            ;;
    esac

    emit_kernel_marker "mCSGC fixed fio GC precondition completed in bash"
}

workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}.fio"
output_path=${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}_${prefill_ratio}_${random_distribution}
mkdir -p ${output_path}
exec > >(tee -a "${output_path}/terminal.log") 2>&1

if [ "${formal_performance_only}" -eq 1 ]; then
    echo "Formal Host branch: ${FORMAL_HOST_BRANCH:-unknown}"
    echo "Formal Host commit: ${FORMAL_HOST_COMMIT:-unknown}"
    echo "Formal f2fs module SHA-256: ${FORMAL_MODULE_SHA256:-unknown}"
fi

if [ $light_evaluation -eq 1 ]; then
    io_size_per_thread="20G"
    runtime=180
else
    io_size_per_thread="20G"
    runtime=300
    sleep 5
    echo "============================="
fi

io_size_per_thread=${FIO_IO_SIZE_PER_THREAD_OVERRIDE:-${io_size_per_thread}}
runtime=${FIO_RUNTIME_OVERRIDE:-${runtime}}
echo "NOTICE: runtime=${runtime}, io_size_per_thread=${io_size_per_thread}"

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

f2fs_sysfs_dir="/sys/fs/f2fs/${devpath##*/}"
gc_counter_path="${f2fs_sysfs_dir}/fg_victim_starts"
gc_csgc_counter_path="${f2fs_sysfs_dir}/fg_csgc_victim_starts"
gc_origc_counter_path="${f2fs_sysfs_dir}/fg_origc_victim_starts"
gc_measurement_control_path="${f2fs_sysfs_dir}/gc_measurement_control"

if [ "${measurement_epoch_enabled}" -eq 1 ]; then
    for gc_measurement_path in \
        "${gc_counter_path}" \
        "${gc_csgc_counter_path}" \
        "${gc_origc_counter_path}" \
        "${gc_measurement_control_path}"; do
        if [ ! -e "${gc_measurement_path}" ]; then
            echo "ERROR: required GC measurement sysfs entry is unavailable: ${gc_measurement_path}" >&2
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi
    done
fi


echo "======================================================="
# exit 0

if [ $fio_timebased -eq 1 ]; then
    runtime_flags=("--runtime=${runtime}")
else
    runtime_flags=()
fi

if [ "${formal_performance_only}" -eq 1 ]; then
    fio_flags=(
        "--time_based=${fio_timebased}"
        "--eta=always"
        "--eta-interval=5s"
        "--eta-newline=5s"
        "--output-format=normal,json"
    )
else
    fio_flags=(
        "--time_based=${fio_timebased}"
        "--status-interval=5"
    )
fi
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
        "--overwrite=1"
        "--allow_file_create=0"
    )

    if [ "${formal_performance_only}" -eq 1 ]; then
        fio_flags+=(
            "--eta=always"
            "--eta-interval=5s"
            "--eta-newline=5s"
            "--output-format=normal,json"
        )
    else
        fio_flags+=("--status-interval=1")
    fi

    if [ "${fio_gc_precondition}" -eq 1 ]; then
        precondition_log="${output_path}/${workload_type}.precondition.log"

        if [ "${formal_performance_only}" -eq 1 ]; then
            precondition_fio_fixed \
                "${precondition_log}" "${workload_path}"
            precondition_status=$?
        else
            precondition_fio_for_gc \
                "${gc_counter_path}" \
                "${gc_csgc_counter_path}" \
                "${gc_origc_counter_path}" \
                "${gc_mode}" \
                "${precondition_log}" \
                "${workload_path}"
            precondition_status=$?
        fi
        if [ "${precondition_status}" -ne 0 ]; then
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if ! verify_prefill_file_identity "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if [ "${formal_performance_only}" -eq 0 ]; then
            precondition_gc_calls=$(<"${gc_counter_path}")
            precondition_csgc_calls=$(<"${gc_csgc_counter_path}")
            precondition_origc_calls=$(<"${gc_origc_counter_path}")
            echo "Foreground victim starts after precondition: total=${precondition_gc_calls} csgc=${precondition_csgc_calls} origc=${precondition_origc_calls}"
            if ! validate_gc_victim_target "precondition" "${gc_mode}" \
                "${precondition_gc_calls}" "${precondition_csgc_calls}" \
                "${precondition_origc_calls}"; then
                sudo umount "${devpath}" >/dev/null 2>&1 || true
                exit 1
            fi
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
        case "${smallfile_layout}" in
            flat)
                prefill_size=$(
                    prefill_smallfiles_filewriter \
                        "${mntpoint}" "${num_files}" \
                    | tee >(grep -vF \
                        'writing file: /home/xin/ssd/mnt/' >&2) \
                    | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p'
                ) || exit 1
                ;;
            partitioned)
                if [[ ! "${smallfile_jobs}" =~ ^[1-9][0-9]*$ ]] \
                    || [[ ! "${smallfile_files_per_job}" =~ ^[1-9][0-9]*$ ]] \
                    || [[ ! "${smallfile_size_mb}" =~ ^[1-9][0-9]*$ ]] \
                    || [[ ! "${smallfile_prefill_threads}" =~ ^[1-9][0-9]*$ ]]; then
                    echo "ERROR: invalid partitioned small-file configuration" >&2
                    exit 1
                fi
                if [ "${num_files}" -ne \
                    $((smallfile_jobs * smallfile_files_per_job)) ]; then
                    echo "ERROR: workload file count ${num_files} does not match partitioned layout $((smallfile_jobs * smallfile_files_per_job))" >&2
                    exit 1
                fi

                partitioned_prefill_log="${output_path}/smallfile-prefill.log"
                : > "${partitioned_prefill_log}"
                prefill_partitioned_smallfiles_filewriter \
                    "${mntpoint}" "${smallfile_jobs}" \
                    "${smallfile_files_per_job}" "${smallfile_size_mb}" \
                    "${smallfile_prefill_threads}" 1M no \
                    2>&1 \
                    | tee -a "${partitioned_prefill_log}" \
                    | grep -vF 'writing file: /home/xin/ssd/mnt/'
                prefill_status=${PIPESTATUS[0]}
                if [ "${prefill_status}" -ne 0 ]; then
                    echo "ERROR: partitioned small-file prefill failed" >&2
                    exit "${prefill_status}"
                fi
                prefill_size=$(sed -n \
                    's/.*total_bytes: <\([0-9][0-9]*\)>.*/\1/p' \
                    "${partitioned_prefill_log}" | tail -n 1)
                if [[ ! "${prefill_size}" =~ ^[0-9]+$ ]]; then
                    echo "ERROR: failed to parse partitioned prefill size" >&2
                    exit 1
                fi
                ;;
            *)
                echo "ERROR: unsupported smallfile_layout=${smallfile_layout}" >&2
                exit 1
                ;;
        esac
    else
        echo "Prefill skipped: should_prefill=${should_prefill}"
    fi

    if [ "${smallfile_layout}" = "partitioned" ] \
        && [ "${fio_gc_precondition}" -eq 1 ]; then
        precondition_log="${output_path}/${workload_type}.precondition.log"
        if [ "${formal_performance_only}" -eq 1 ]; then
            precondition_fio_fixed \
                "${precondition_log}" "${workload_path}" \
                partitioned_smallfiles \
                "${smallfile_jobs}" \
                "${smallfile_files_per_job}" \
                "${smallfile_size_mb}"
            precondition_status=$?
        else
            precondition_fio_for_gc \
                "${gc_counter_path}" \
                "${gc_csgc_counter_path}" \
                "${gc_origc_counter_path}" \
                "${gc_mode}" \
                "${precondition_log}" \
                "${workload_path}" \
                partitioned_smallfiles \
                "${smallfile_jobs}" \
                "${smallfile_files_per_job}" \
                "${smallfile_size_mb}"
            precondition_status=$?
        fi
        if [ "${precondition_status}" -ne 0 ]; then
            sudo umount "${devpath}" >/dev/null 2>&1 || true
            exit 1
        fi

        if [ -r "${f2fs_sysfs_dir}/free_segments" ]; then
            echo "Free segments after small-file precondition: $(<"${f2fs_sysfs_dir}/free_segments")"
        fi
        sudo dmesg -c > "${output_path}/dmesg.precondition.log"
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

echo "=============begin fio============="
emit_kernel_marker "mCSGC prepare to run filebench/fio in bash"

fio_log="${output_path}/${workload_type}.log"
gc_measurement_summary=""
workload_epoch_closed=0

if [ "${measurement_epoch_enabled}" -eq 1 ]; then
    if ! set_gc_measurement_epoch "${gc_measurement_control_path}" start; then
        sudo umount "${devpath}" >/dev/null 2>&1 || true
        exit 1
    fi
    if ! workload_gc_epoch=$(read_gc_measurement_epoch \
        "${gc_measurement_control_path}" workload 1); then
        set_gc_measurement_epoch "${gc_measurement_control_path}" stop || true
        sudo umount "${devpath}" >/dev/null 2>&1 || true
        exit 1
    fi

    # Reset device statistics only after the Host confirms that CSGC work is idle.
    if ! reset_ssd_stat "${devpath}"; then
        echo "ERROR: failed to reset SSD statistics before measured fio" >&2
        if ! set_gc_measurement_epoch "${gc_measurement_control_path}" stop; then
            echo "WARNING: failed to close the workload epoch after SSD reset failure" >&2
        fi
        sudo umount "${devpath}" >/dev/null 2>&1 || true
        exit 1
    fi
fi

emit_kernel_marker "MEASURED_FIO_START mode=${gc_mode} workload=${bmname}"
run_fio_logged "${fio_log}" "${fio_flags[@]}" "${runtime_flags[@]}" "${workload_path}"
fio_status=$?
emit_kernel_marker "MEASURED_FIO_END mode=${gc_mode} workload=${bmname} status=${fio_status}"

if [ "${measurement_epoch_enabled}" -eq 1 ]; then
    if ! set_gc_measurement_epoch "${gc_measurement_control_path}" stop; then
        echo "ERROR: failed to close the measured workload epoch" >&2
        fio_status=1
    else
        workload_epoch_closed=1
    fi

    gc_measurement_summary="${output_path}/gc-measurement-summary.log"
    if [ "${workload_epoch_closed}" -ne 1 ] \
        || ! capture_gc_measurement_summary \
            "${workload_gc_epoch}" workload "${gc_measurement_summary}"; then
        echo "ERROR: failed to capture the measured workload GC summary" >&2
        gc_measurement_summary=""
        fio_status=1
    fi

    if [ "${require_pipeline_stats}" -eq 1 ]; then
        pipeline_summary="${output_path}/pipeline-summary.log"
        if ! capture_pipeline_summary "${pipeline_summary}"; then
            echo "ERROR: required cross-section pipeline statistics are unavailable" >&2
            fio_status=1
        fi
    fi

    ssd_workload_stat="${output_path}/ssd-workload-stat.log"
    : > "${ssd_workload_stat}"
    if ! get_ssd_stat "${devpath}" "${ssd_workload_stat}"; then
        echo "ERROR: failed to collect measured-workload SSD statistics" >&2
        fio_status=1
    fi
fi
emit_kernel_marker "mCSGC finished filebench/fio in bash"

if { [ "${bmname}" = "randwrite" ] || [[ "${bmname}" == rw*file ]]; } \
    && ! verify_fio_reused_prefill "${fio_log}"; then
    fio_status=1
fi
if [ "${bmname}" = "randwrite" ] \
    && ! verify_prefill_file_identity "${prefill_file}" "${prefill_size}" "${prefill_inode}"; then
    fio_status=1
fi
if [[ "${bmname}" == rw*file ]] \
    && [ "${smallfile_layout}" = "partitioned" ] \
    && ! verify_partitioned_smallfiles \
        "${mntpoint}" "${smallfile_jobs}" \
        "${smallfile_files_per_job}" "${smallfile_size_mb}"; then
    fio_status=1
fi

if [ -n "${gc_measurement_summary}" ]; then
    measured_gc_mode=$(sed -n \
        's/.*F2FS_GC_HEAVY_STAT .* mode=\([^ ]*\) .*/\1/p' \
        "${gc_measurement_summary:-/dev/null}" | tail -n 1)
    if [ -n "${expected_gc_heavy_mode}" ] \
        && [ "${measured_gc_mode}" != "${expected_gc_heavy_mode}" ]; then
        echo "ERROR: loaded Host mode does not match this experiment: expected=${expected_gc_heavy_mode} measured=${measured_gc_mode:-missing}" >&2
        fio_status=1
    else
        echo "Measured Host GC mode: ${measured_gc_mode:-unknown}"
    fi

    measured_gc_calls=$(sed -n 's/.*fg_victim_starts=\([0-9][0-9]*\).*/\1/p' \
        "${gc_measurement_summary:-/dev/null}" | tail -n 1)
    measured_csgc_calls=$(sed -n 's/.*fg_csgc_victim_starts=\([0-9][0-9]*\).*/\1/p' \
        "${gc_measurement_summary:-/dev/null}" | tail -n 1)
    measured_origc_calls=$(sed -n 's/.*fg_origc_victim_starts=\([0-9][0-9]*\).*/\1/p' \
        "${gc_measurement_summary:-/dev/null}" | tail -n 1)
    echo "Foreground victim starts during measured fio: total=${measured_gc_calls:-missing} csgc=${measured_csgc_calls:-missing} origc=${measured_origc_calls:-missing}"
    if ! validate_gc_victim_target "measured fio" "${gc_mode}" \
        "${measured_gc_calls}" "${measured_csgc_calls}" \
        "${measured_origc_calls}"; then
        fio_status=1
    fi

    first_gc_epoch_us=$(sed -n \
        's/.*epoch_start_to_first_gc_us=\([0-9][0-9]*\).*/\1/p' \
        "${gc_measurement_summary:-/dev/null}" | tail -n 1)
    if [[ "${first_gc_epoch_us}" =~ ^[0-9]+$ ]]; then
        first_gc_delay=$(awk -v first_us="${first_gc_epoch_us}" \
            'BEGIN { printf "%.6f", first_us / 1000000.0 }')
        echo "First foreground GC delay after workload measurement epoch start: ${first_gc_delay} seconds" \
            | tee "${output_path}/gc_start_delay.log"
    elif grep -q 'no_first_gc=1' "${gc_measurement_summary:-/dev/null}"; then
        if [[ "${measured_gc_calls}" =~ ^[0-9]+$ ]] \
            && [ "${measured_gc_calls}" -ne 0 ]; then
            echo "ERROR: GC summary reports no first GC but victim starts are nonzero: ${measured_gc_calls}" >&2
            fio_status=1
        else
            echo "No foreground GC call occurred during the workload measurement epoch" \
                | tee "${output_path}/gc_start_delay.log"
        fi
    else
        echo "ERROR: invalid first-GC field in the measured workload GC summary" >&2
        fio_status=1
    fi
fi
echo "======================================================="

if ! umount_and_get_stat \
    "${devpath}" "${gc_mode}" "${output_path}/stat.log" 0; then
    echo "ERROR: failed to unmount or collect Host statistics" >&2
    fio_status=1
fi

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
