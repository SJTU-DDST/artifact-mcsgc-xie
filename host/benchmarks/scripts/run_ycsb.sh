#!/bin/bash

set -uo pipefail

source ./common.sh
YCSB_DIR=../ycsb-0.17.0

mysql_started=0

# Stop MySQL and release the benchmark mount after an early userspace failure.
cleanup_ycsb() {
    local status=$?

    if [ "${mysql_started}" -eq 1 ] || systemctl is-active --quiet mysql; then
        sudo systemctl stop mysql >/dev/null 2>&1 || true
    fi
    if [ "${status}" -ne 0 ] && [ -n "${devpath:-}" ] \
        && findmnt -rn -S "${devpath}" >/dev/null; then
        sudo umount "${devpath}" >/dev/null 2>&1 || true
    fi
    exit "${status}"
}

trap cleanup_ycsb EXIT

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
echo 20 | sudo tee /proc/sys/kernel/panic > /dev/null

# load_f2fs_module $gc_mode
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
    if ! prefill_outputs="$(prefill_storage_ycsb "${devpath}" "${mntpoint}" "${prefill_ratio}" "${gc_mode}")"; then
        echo "ERROR: YCSB prefill failed" >&2
        exit 1
    fi
    echo "${prefill_outputs}"
    prefill_size=$(echo "${prefill_outputs}" | sed -n 's/.*<\([0-9]\+\)>.*$/\1/p')
    if [ -z "${prefill_size}" ]; then
        echo "ERROR: failed to parse YCSB prefill size" >&2
        exit 1
    fi
fi

# exit 0

reset_ssd_stat "${devpath}"

echo "Initializing MySQL: copy mysql data to mntpoint"
sudo chmod 777 ${mntpoint}
sudo cp -a /var/lib/mysql ${mntpoint}/mysql
echo "Start MySQL service..."
sudo systemctl reset-failed mysql >/dev/null 2>&1 || true
sudo systemctl start mysql
for _ in $(seq 1 60); do
    if systemctl is-active --quiet mysql && mysqladmin ping --silent >/dev/null 2>&1; then
        mysql_started=1
        break
    fi
    sleep 1
done
if [ "${mysql_started}" -eq 1 ]; then
    echo "Successfully started MySQL service."
else
    echo "Failed to start MySQL service."
    sudo journalctl -u mysql -n 80 --no-pager > "${output_path}/mysql-start-failure.log" 2>&1 || true
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
ycsb_status=0
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
    2>&1 | tee -a ${output_path}/${workload_type}.log || ycsb_status=$?

sudo systemctl stop mysql
mysql_started=0
echo "======================================================="


umount_and_get_stat "${devpath}" "${gc_mode}" "${output_path}/stat.log"

if [ ${fsck_after_run} -ne 0 ]; then
    echo "run fsck"
    sudo fsck.f2fs ${devpath} > ${output_path}/fsck.log
    echo "finished fsck"
fi

chown -R "$(whoami):$(whoami)" "${output_path}"

if [ "${ycsb_status}" -ne 0 ]; then
    echo "ERROR: YCSB failed with status ${ycsb_status}" >&2
    exit "${ycsb_status}"
fi

if ! grep -q '\[OVERALL\], Throughput(ops/sec)' "${output_path}/${workload_type}.log"; then
    echo "ERROR: YCSB log has no final throughput" >&2
    exit 1
fi

