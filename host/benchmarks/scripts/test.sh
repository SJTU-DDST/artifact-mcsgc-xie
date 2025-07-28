#!/bin/bash

# gc_mode="ori"      # vanilla f2fs
# gc_mode="iplfs"    # iplfs
# gc_mode="cs"       # csgc
light_evaluation=0
#gc_mode=$1
mcsgc_mode=$1
# determine gc_mode based on mcsgc_mode
if [[ "$mcsgc_mode" == "mcsgc" || "$mcsgc_mode" == "csgc" || "$mcsgc_mode" == "mcsgcdebug" || "$mcsgc_mode" == "csgcdebug" || "$mcsgc_mode" == "mcsgcmakefile" ]]; then
  gc_mode="cs"
elif [[ "$mcsgc_mode" == "ori" || "$mcsgc_mode" == "iplfs" ]]; then
  gc_mode="$mcsgc_mode"
else
  echo "Error: unsupported mode '$mcsgc_mode'."
  exit 1
fi
echo "gc_mode=$gc_mode"

source $2

pushd $(dirname $0) > /dev/null

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
    output_path_base="./outputs-${mcsgc_mode}/${current_time}"
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

        export gc_mode workload_type bmname random_distribution segs_per_sec output_path_base \
        prefill_ratio use_cgroup host_mem_usage nr_cs_cores csgc_sync fio_timebased\
        ssd_enable_l2p ssd_enable_nand_lat ssd_enable_dsm fsck_after_run light_evaluation
        
        case "${workload_type}" in 
            "filebench")
                echo "Running filebench: ${bmname}..."
                ./run_filebench.sh
                ;;
            "fio")
                echo "Running fio: ${bmname}..."
                ./run_fio.sh
                ;;
            "ycsb")
                echo "Running ycsb: ${bmname}..."
                ./run_ycsb.sh
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
