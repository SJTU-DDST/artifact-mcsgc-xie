#!/bin/bash

source ./common.sh
mntpoint=${MNTPOINT}

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

echo 0 | sudo tee /proc/sys/kernel/randomize_va_space > /dev/null
echo 20 > /proc/sys/kernel/panic # dont panic! wait 20s before reboot if kernel panics

# load_f2fs_module $gc_mode
install_f2fs_tools $gc_mode
prepare_device "${devpath}" "${output_path}"
reset_ssd_config "${devpath}" "${ssd_enable_l2p}" "${ssd_enable_nand_lat}" "${ssd_enable_dsm}"
mkfs_and_mount "${devpath}" "${mntpoint}" "${segs_per_sec}" "${f2fs_enable_discard}" "${ssd_enable_l2p}"
setup_gc_config "${gc_mode}" "${nr_cs_cores}" "${csgc_sync}"
setup_cgroup_mem "${use_cgroup}" "${host_mem_usage}"

echo "======================================================="

tmp_workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}_tmp.f"
cp ${workload_path} ${tmp_workload_path}
sed -i "s|__DATA_PATH_PLACEHOLDER__|${mntpoint}|g" ${tmp_workload_path}
sed -i "s|__RUNTIME_PLACEHOLDER__|${runtime}|g" ${tmp_workload_path}

reset_ssd_stat "${devpath}"
if [ ${use_cgroup} -eq 1 ]; then
    sudo cgexec -g memory:${CGROUP_NAME} filebench -f ${tmp_workload_path} \
    2>&1 | tee -a ${output_path}/${workload_type}.log
else
    filebench -f ${tmp_workload_path} \
    2>&1 | tee -a ${output_path}/${workload_type}.log
fi
echo "======================================================="

umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

rm ${tmp_workload_path}
chown -R $(whoami):$(whoami) ${output_path}


