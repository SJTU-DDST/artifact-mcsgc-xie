#!/bin/bash

source ./common.sh
YCSB_DIR=../ycsb-0.17.0

if [ $light_evaluation -eq 1 ]; then
    operationcount=1000000  # 1M
else
    operationcount=2000000  # 2M
fi

check_kernel $gc_mode
devpath=$(find_cs_device)

mntpoint=${MNTPOINT} # if in user's directory, mysql cannot initialize db due to permission issues
if [ "${ssd_enable_dsm}" -eq 1 ]; then 
    f2fs_enable_discard="discard"
else
    f2fs_enable_discard="nodiscard"
fi
workload_path="${WORKLOAD_PATH_BASE}/${workload_type}/${bmname}"
output_path=${output_path_base}/${workload_type}_${bmname}_s${segs_per_sec}_${prefill_ratio}
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
if systemctl is-active --quiet mysql; then
    echo "Detected running MySQL service, shouldn't exist."
    exit 1
fi

prefill=1
if [ $prefill -eq 1 ]; then
    prefill_outputs="$(prefill_storage_ycsb "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")" && echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
fi

# exit 0

reset_ssd_stat "${devpath}"

echo "Initializing MySQL: copy mysql data to mntpoint"
sudo chmod 777 ${mntpoint}
sudo cp -a /var/lib/mysql ${mntpoint}/mysql
echo "Start MySQL service..."
sudo systemctl start mysql
if systemctl is-active --quiet mysql; then
    echo "Successfully started MySQL service."
else
    echo "Failed to start MySQL service."
    exit 1
fi


# 1M records, each with 1KB size
# 2M operations
workload_property_flags="
    -p recordcount=1000000
    -p fieldcount=16
    -p fieldlength=64
    -p minfieldlength=16

    -p operationcount=${operationcount}

    -p readallfields=true
    -p writeallfields=false
"
USER_NAME="ycsb_user"
USER_DB_NAME="ycsb_db"
USER_PASSWORD="1111"

echo "Start running ycsb ${bmname}" | tee -a ${output_path}/${workload_type}.log
${YCSB_DIR}/bin/ycsb run jdbc    \
    -P ${workload_path}  \
    -P ${YCSB_DIR}/jdbc-binding/conf/db.properties  \
    -p db.driver=com.mysql.jdbc.Driver  \
    -p db.url=jdbc:mysql://localhost:3306/${USER_DB_NAME} \
    -p db.user=${USER_NAME}  \
    -p db.passwd=${USER_PASSWORD} \
    ${workload_property_flags} \
    -threads 36 \
    -s \
    2>&1 | tee ${output_path}/${workload_type}.log

sudo systemctl stop mysql
echo "======================================================="


umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

chown -R $(whoami):$(whoami) ${output_path}


