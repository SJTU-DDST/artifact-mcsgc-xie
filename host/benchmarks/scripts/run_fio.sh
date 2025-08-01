#!/bin/bash

source ./common.sh
mntpoint=${MNTPOINT}

if [ $light_evaluation -eq 1 ]; then
    io_size_per_thread="20G"
    runtime=180
else
    io_size_per_thread="20G"
    runtime=60
    echo "NOTICE: runtime=60"
    sleep 5
    echo "============================="
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

if [ $fio_timebased -eq 1 ]; then
    runtime_flag="--runtime=${runtime}"
else
    runtime_flag=""
fi

fio_flags="
--time_based=${fio_timebased}
"

# only do prefill and build fio_flags when not the special bmname
if [ "${bmname}" == "randwrite" ]; then
    # prefill step
    prefill_outputs="$(prefill_storage_fio "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")" && echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
    echo "into if"
    # build fio_flags as before
    fio_flags="
        --directory=${mntpoint}
        --alloc-size=16m
        --filesize=${prefill_size}
        --size=${io_size_per_thread}
        --numjobs=${nthreads}
        --random_distribution=${random_distribution}
        --time_based=${fio_timebased}
    "
fi

if [ "${bmname}" == "rw16t55kfile" ]; then
    echo "into if rw16t55kfile"
    prefill_outputs="$(prefill_smallfiles_filewriter "${mntpoint}")" && echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
fi

if [ "${bmname}" == "rw16t50kfile" ]; then
    echo "into if rw16t50kfile"
    prefill_outputs="$(prefill_smallfiles_filewriter "${mntpoint}")" && echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
fi

if [ "${bmname}" == "rw16t30kfile" ]; then
    echo "into if rw16t30kfile"
    prefill_outputs="$(prefill_smallfiles_filewriter "${mntpoint}")" && echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
fi


echo "================ FIO WORKLOAD SUMMARY ================"
echo "bmname:               ${bmname}"
echo "workload_path:        ${workload_path}"
echo "runtime_flag:         ${runtime_flag}"
echo "fio_flags:            ${fio_flags}"
[ -n "${prefill_size}" ] && echo "prefill_size:         ${prefill_size}"
echo "======================================================="

sudo cgexec -g memory:${CGROUP_NAME} fio \
    --parse-only \
    --showcmd \
    --debug=filesetup,parse \
    ${fio_flags} \
    ${runtime_flag} \
    ${workload_path} 2>&1 | tee -a ${output_path}/${workload_type}.log
echo "======================================================="
sleep 5

reset_ssd_stat "${devpath}"

echo "=============begin fio============="

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

