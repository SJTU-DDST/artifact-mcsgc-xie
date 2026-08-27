#!/bin/bash

set -u

# gc_mode="ori"      # vanilla f2fs
# gc_mode="iplfs"    # iplfs
# gc_mode="cs"       # csgc
light_evaluation=0
gc_mode=$1
config_path=$(realpath "$2")
source "${config_path}"
fio_timebased=${fio_timebased:-0}

pushd "$(dirname "$0")" > /dev/null || exit 1

host_mem_usage="8G"
use_cgroup=1
nr_cs_cores="1"
csgc_sync=0
fsck_after_run=0
ssd_enable_l2p=0    # 0=>no-FTL 1=>conventional, 2=>sFTL, 3=>interval-mapping
ssd_enable_nand_lat=0
ssd_enable_dsm=1

current_time=$(date +"%Y%m%d_%H%M%S")

echo "Running evaluation for $gc_mode..."

# for gc_mode in "${gc_modes[@]}"; do
    output_root=${EUROPAR_OUTPUT_ROOT:-.}
    output_path_base="${output_root}/outputs-${gc_mode}/${current_time}"
    mkdir -p "${output_path_base}"
    case "${gc_mode}" in 
        "ori")
            ssd_enable_l2p=1
            ;;
        "cs")
            ssd_enable_l2p=2
            ;;
        "iplfs")
            segs_per_sec_list=("1") # iplfs crashes for other section size
            ssd_enable_l2p=3
            fsck_after_run=0
            ;;
        *)
            echo "workload_type not supported"
            exit 1
            ;;
    esac

    for workload in "${workloads[@]}"; do
        workload_type=$(echo "$workload" | cut -d':' -f1)
        bmname=$(echo "$workload" | cut -d':' -f2)
    for random_distribution in "${random_distributions[@]}"; do
    for prefill_ratio in "${prefill_ratios[@]}"; do
    for segs_per_sec in "${segs_per_sec_list[@]}"; do

        export gc_mode workload_type bmname random_distribution segs_per_sec output_path_base \
        prefill_ratio use_cgroup host_mem_usage nr_cs_cores csgc_sync fio_timebased\
        ssd_enable_l2p ssd_enable_nand_lat ssd_enable_dsm fsck_after_run light_evaluation
        
        case_id=${EUROPAR_CASE_ID:-${gc_mode}-${workload_type}-${bmname}-s${segs_per_sec}-${prefill_ratio}-${random_distribution}}
        case_started_epoch=$(date +%s)
        case_started_at=$(date --iso-8601=seconds)
        case_status=0

        echo "EUROPAR_CASE_START id=${case_id} mode=${gc_mode} output=${output_path_base}"
        case "${workload_type}" in 
            "filebench")
                echo "Running filebench: ${bmname}..."
                ./run_filebench.sh || case_status=$?
                ;;
            "fio")
                echo "Running fio: ${bmname}..."
                ./run_fio.sh || case_status=$?
                ;;
            "ycsb")
                echo "Running ycsb: ${bmname}..."
                ./run_ycsb.sh || case_status=$?
                ;;
            *)
                echo "workload_type not supported"
                exit 1
                ;;
        esac

        case_ended_epoch=$(date +%s)
        case_ended_at=$(date --iso-8601=seconds)
        case_duration=$((case_ended_epoch - case_started_epoch))
        case_output="${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}"
        if [ "${workload_type}" = "ycsb" ]; then
            case_output="${case_output}_${prefill_ratio}"
        elif [ "${workload_type}" = "fio" ]; then
            case_output="${case_output}_${prefill_ratio}_${random_distribution}"
        fi

        if [ -n "${EUROPAR_CASE_RESULTS:-}" ]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                "${case_id}" "${gc_mode}" "${workload_type}" "${bmname}" \
                "${random_distribution}" "${prefill_ratio}" "${segs_per_sec}" \
                "${case_started_at}" "${case_ended_at}" "${case_duration}" \
                "${case_status}" "${case_output}" >> "${EUROPAR_CASE_RESULTS}"
        fi
        echo "EUROPAR_CASE_END id=${case_id} status=${case_status} duration_s=${case_duration} output=${case_output}"
        if [ "${case_status}" -ne 0 ]; then
            exit "${case_status}"
        fi

    done
    done
    done
    done
# done

popd > /dev/null || exit 1
