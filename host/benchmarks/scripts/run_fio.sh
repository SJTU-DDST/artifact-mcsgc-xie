#!/bin/bash

source ./common.sh
mntpoint=${MNTPOINT}

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

prefill_outputs="$(prefill_storage_fio "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")" && echo "${prefill_outputs}"
prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')

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

if [ ${use_cgroup} -eq 1 ]; then
    sudo cgexec -g memory:${CGROUP_NAME} fio ${fio_flags} ${runtime_flag} ${workload_path} 2>&1 | tee -a ${output_path}/${workload_type}.log
else
    fio ${fio_flags} ${workload_path} 2>&1 | tee -a ${output_path}/${workload_type}.log
fi
echo "======================================================="

umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

chown -R $(whoami):$(whoami) ${output_path}

