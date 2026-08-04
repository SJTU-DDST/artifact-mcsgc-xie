#!/bin/bash

str_debug="test begin in bash"
ts_local=$(date '+%b %e %H:%M:%S')
host_local=$(hostname)
ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)
# send to /dev/kmsg so any running `dmesg -w >> kernel_log.txt` will capture it
printf '<6>IN BASH %s %s [%s] %s\n' \
  "$ts_local" "$host_local" "$ts_upt" "$str_debug" | sudo tee /dev/kmsg >/dev/null

# gc_mode="ori"      # vanilla f2fs
# gc_mode="iplfs"    # iplfs
# gc_mode="cs"       # csgc
light_evaluation=0

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "Usage: $0 <mode> <ssd1t|ssd2t> <config>"
  echo "  mode: any value containing the case-sensitive substring 'csgc' selects CSGC"
  echo "        ori and iplfs select the existing baseline paths"
}

if [ $# -ne 3 ]; then
  usage
  exit 1
fi

mcsgc_mode=$1
ssd_thread_mode=$2
config_path=$3

case "${ssd_thread_mode}" in
    "ssd1t"|"ssd2t")
        ;;
    *)
        echo "Error: unsupported SSD thread mode '${ssd_thread_mode}', expected 'ssd1t' or 'ssd2t'."
        exit 1
        ;;
esac

if [ ! -f "${config_path}" ]; then
    if [ -f "${script_dir}/${config_path}" ]; then
        config_path="${script_dir}/${config_path}"
    else
        echo "Error: config file not found: ${config_path}"
        exit 1
    fi
fi

# Keep the user-provided mode as an output label while normalizing CSGC variants.
case "${mcsgc_mode}" in
    *csgc*)
        gc_mode="cs"
        ;;
    "ori"|"iplfs")
        gc_mode="${mcsgc_mode}"
        ;;
    *)
        echo "Error: unsupported mode '${mcsgc_mode}'."
        exit 1
        ;;
esac
echo "gc_mode=$gc_mode"
echo "ssd_thread_mode=$ssd_thread_mode"

source "${config_path}"

pushd "${script_dir}" > /dev/null

: "${fio_gc_precondition:=0}"
: "${fio_gc_precondition_size_per_job:=4G}"
: "${fio_gc_precondition_max_rounds:=4}"

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
    output_path_base="./outputs-${mcsgc_mode}-${ssd_thread_mode}/${current_time}"
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
        workload_type=$(echo $workload | cut -d':' -f1)
        bmname=$(echo $workload | cut -d':' -f2)
    for random_distribution in "${random_distributions[@]}"; do
    for prefill_ratio in "${prefill_ratios[@]}"; do
    for segs_per_sec in "${segs_per_sec_list[@]}"; do

        export gc_mode ssd_thread_mode workload_type bmname random_distribution segs_per_sec output_path_base \
        prefill_ratio use_cgroup host_mem_usage nr_cs_cores csgc_sync fio_timebased\
        ssd_enable_l2p ssd_enable_nand_lat ssd_enable_dsm fsck_after_run light_evaluation \
        fio_gc_precondition fio_gc_precondition_size_per_job fio_gc_precondition_max_rounds
        
        case "${workload_type}" in 
            "filebench")
                echo "Running filebench: ${bmname}..."
                if ! ./run_filebench.sh; then
                    echo "Error: filebench runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            "fio")
                echo "Running fio: ${bmname}..."
                if ! ./run_fio.sh; then
                    echo "Error: fio runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            "ycsb")
                echo "Running ycsb: ${bmname}..."
                if ! ./run_ycsb.sh; then
                    echo "Error: YCSB runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            *)
                echo "workload_type not supported"
                exit 1
                ;;
        esac

    done
    done
    done
    done
# done

popd > /dev/null

str_debug="test finish in bash"
ts_local=$(date '+%b %e %H:%M:%S')
host_local=$(hostname)
ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)
# send to /dev/kmsg so any running `dmesg -w >> kernel_log.txt` will capture it
printf '<6>IN BASH %s %s [%s] %s\n' \
  "$ts_local" "$host_local" "$ts_upt" "$str_debug" | sudo tee /dev/kmsg >/dev/null

sudo "${script_dir}/flush_dmesg_buffer.sh"
echo "Test script completed."
