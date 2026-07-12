#!/bin/bash
# common.sh -- common utilities and variables shared by filebench, fio and ycsb scripts

DEBUGFS_PATH=/sys/kernel/debug/f2fs
NVME_PATH=$(realpath ../../src/nvme-cli/nvme)   # path of nvme-cli
CGROUP_NAME=host_gc
FS_MODE=lfs
BGGC_ONOFF=off
FSYNC_MODE=strict
VANILLA_F2FS_TOOLS_PATH=$(realpath ../../src/f2fs-tools-csgc)
IPLFS_F2FS_TOOLS_PATH=$(realpath ../../src/f2fs-tools-iplfs)

kernel_version=$(uname -r)

if [[ "$kernel_version" == "6.1.54-csgcmtdebug" ]]; then
    VANILLA_KERNEL_PATH=/home/xin/work-xie/mcsgc/linux-cs
elif [[ "$kernel_version" == "6.1.54-csgcmt" ]]; then
    VANILLA_KERNEL_PATH=/home/xin/work-xie/mcsgc-real/linux-cs
elif [[ "$kernel_version" == "6.1.54-csgcmt-csgcmt" ]]; then
    echo "now is csgcmt v2.0"
    echo "=================================="
    sleep 5
    VANILLA_KERNEL_PATH=/home/xin/work-xie/mcsgc-real/linux-cs
else
    echo "Unsupported kernel version: $kernel_version"
    exit 1
fi

#VANILLA_KERNEL_PATH=/home/xin/work-xie/mcsgc/linux-cs
#VANILLA_KERNEL_PATH=/home/xin/work-xie/mcsgc-real/linux-cs
IPLFS_KERNEL_PATH=$(realpath ../../src/linux-iplfs)
WORKLOAD_PATH_BASE=$(realpath ../myworkloads)
FILE_WRITER_DIR=$(realpath ../file_writer)
DUMMY_FILE_NAME=testbigfile
MNTPOINT=/home/xin/ssd/mnt

check_kernel() {
    local gc_mode=$1
    local kernel_suffix
    if [ "$gc_mode" = "iplfs" ]; then
        kernel_suffix="iplfs"
    else
        kernel_suffix="csgc"
    fi

    if ! uname -r | grep -q $kernel_suffix ; then
        echo "error: kernel not match, expect suffix: $kernel_suffix"
        exit 1
    fi
    echo "kernel version check passed"
}

# find our openssd device by size, its size must <= 64G
find_cs_device() {
    local matching_devices
    matching_devices=$(lsblk -o NAME,SIZE -n | awk '$1 ~ /nvme[0-9]n[0-9]/ && $2 ~ /[0-9]+(\.[0-9]+)?G/ {print $1}')
    local device_count
    device_count=$(echo "$matching_devices" | wc -l)
    if [ $device_count -ne 1 ]; then
        echo "error: hope only one device is has size XY[.Z]G, but found count: $device_count"
        exit 1
    fi
    local devname
    devname=$(echo "$matching_devices" | tr -d ' ')
    echo "/dev/${devname}"
}

install_f2fs_tools() {
    local gc_mode=$1
    local f2fs_tools_src_path
    if [ "$gc_mode" = "iplfs" ]; then
        f2fs_tools_src_path=$IPLFS_F2FS_TOOLS_PATH
    else
        f2fs_tools_src_path=$VANILLA_F2FS_TOOLS_PATH
    fi

    pushd "${f2fs_tools_src_path}" > /dev/null

    ls -a ./lib | grep -qE '\.libs' || (echo "build f2fs-tools for $gc_mode" && \
    ./autogen.sh && ./configure && sudo make clean && sudo make)
    
    echo "install f2fs-tools for $gc_mode"
    sudo make install > /dev/null
    sudo ldconfig
    echo "f2fs-tools installed"
    popd > /dev/null
}

# load f2fs module from given path
load_f2fs_module() {
    echo "NOTE: begin load f2fs module"
    echo "=============================================================================="
    sleep 2
    local gc_mode=$1
    local f2fs_ko_path
    if [ "$gc_mode" = "iplfs" ]; then
        f2fs_ko_path=${IPLFS_KERNEL_PATH}/fs/f2fs/f2fs.ko
    else
        f2fs_ko_path=${VANILLA_KERNEL_PATH}/fs/f2fs/f2fs.ko
        echo "NOTE: set f2fs_ko_path"
        echo "first please make sure the path is correct: ${f2fs_ko_path}"
        sleep 5
        echo "=============================================================================="
    fi
    if ! lsmod | grep -q f2fs; then
        sudo modprobe f2fs
        sudo rmmod f2fs
        echo "please make sure the path is correct: ${f2fs_ko_path}"
        sleep 5
        echo "prepare insmod"
        if ! sudo insmod "${f2fs_ko_path}"; then
            echo " fail insmod ${f2fs_ko_path}"
            exit 1
        fi
        echo "NOTE: finish insmod, the f2fs is ${f2fs_ko_path}"
        echo "=============================================================================="
        sleep 5
    fi
}

# umount device if mounted, clear dmesg
prepare_device() {
    local devpath=$1
    local output_dir=$2
    sudo umount "${devpath}" >/dev/null
    sudo dmesg -c > "${output_dir}/dmesg.old"
}

# reset ssd config: FTL will be reset with new config, all previous contents in storage will be lost
reset_ssd_config() {
    local devpath=$1
    local ssd_enable_l2p=$2
    local ssd_enable_nand_lat=$3
    local ssd_enable_dsm=$4
    echo "Reset SSD config: l2p=${ssd_enable_l2p}, nand_lat=${ssd_enable_nand_lat}, dsm=${ssd_enable_dsm}"
    sudo "${NVME_PATH}" ssd-admin "${devpath}" -o 1 --l2p "${ssd_enable_l2p}" --nand "${ssd_enable_nand_lat}" --dsm "${ssd_enable_dsm}"
}

reset_ssd_stat() {
    local devpath=$1
    sudo "${NVME_PATH}" ssd-admin "${devpath}" -o 2
}

get_ssd_stat() {
    local devpath=$1
    local output_path=$2
    sudo "${NVME_PATH}" read "${devpath}" -s 123 -c 1 -z 4096 -t -L | tee -a "${output_path}"
}

umount_and_get_stat() {
    local devpath=$1
    local gc_mode=$2
    local output_path=$3
    local wait_time=5
    
    echo "sleep ${wait_time} seconds before umount and dmesg"
    sleep ${wait_time}
    
    if [ "$gc_mode" != "iplfs" ]; then
        echo "csgc status:" `cat ${DEBUGFS_PATH}/csgc_status` | tee -a ${output_path}

        echo "umount device"
        sudo umount ${devpath}
        dmesg | grep -F 'UNMOUNT mCSGC. mCSGC time(ns)' | tee -a ${output_path}
        dmesg | grep -E '<ORIGC STAT>' | tee -a ${output_path}
        dmesg | grep -E '<CSGC STAT>' | tee -a ${output_path}
        
        dmesg | grep -oE 'f2fs csgc called [0-9]+ times'
        dmesg | grep -oE 'f2fs csgc skip count: [0-9]+' | tee -a ${output_path}
        dmesg | grep -oE 'f2fs csgc data page cached count: [0-9]+, dirty count [0-9]+, hole count: [0-9]+' | tee -a ${output_path}
        dmesg | grep -oE 'f2fs csgc get dpage time: [0-9]+ ns, grab dpage time: [0-9]+ ns' | tee -a ${output_path}
        dmesg | grep -oE 'AVG get time: [0-9]+ ns, grab time: [0-9]+ ns' | tee -a ${output_path}
        dmesg | grep -oE 'f2fs gc data page hit count: [0-9]+, total req count: [0-9]+' | tee -a ${output_path}
    else
        echo "umount device"
        sudo umount ${devpath}
    fi

    get_ssd_stat "${devpath}" "${output_path}"

    sudo dmesg > $(dirname ${output_path})/dmesg.log
}

# format storage and mount 
mkfs_and_mount() {
    local devpath=$1
    local mntpoint=$2
    local segs_per_sec=$3
    local discard_option=$4   # "discard" or "nodiscard"
    local ssd_enable_l2p=$5   # 1=>conventional, 2=>sFTL, 3=>interval-mapping 
    if [ $ssd_enable_l2p -eq 3 ]; then
        # iplfs must run with these settings, otherwise it panics
        segs_per_sec=1
        discard_option="discard"
    fi
    echo "Formatting filesystem with segs_per_sec=${segs_per_sec}"
    sudo mkfs.f2fs -f -s "${segs_per_sec}" "${devpath}"
    if [ $ssd_enable_l2p -eq 2 ]; then # csgc, need to send fs-ready signal and mkfs again
        sudo "${NVME_PATH}" fs-ready -f 1 "${devpath}"
        # need to mkfs again, since when sFTL is enabled, fs-ready will reset the device
        sudo mkfs.f2fs -f -s "${segs_per_sec}" "${devpath}"
    fi
    if ! sudo mount -t f2fs \
        -o mode="${FS_MODE}",background_gc="${BGGC_ONOFF}",fsync_mode="${FSYNC_MODE}","${discard_option}" \
        "${devpath}" "${mntpoint}"; then
        echo "ERROR: failed to mount ${devpath} at ${mntpoint}" >&2
        exit 1
    fi

    if ! mountpoint -q "${mntpoint}"; then
        echo "ERROR: ${mntpoint} is not a mount point after mount completed" >&2
        exit 1
    fi

    echo "Mounted ${devpath} at ${mntpoint}"
    echo "======================================================="
}

# Remount an existing F2FS filesystem to reset per-mount host statistics.
remount_f2fs_for_measurement() {
    local devpath=$1
    local mntpoint=$2
    local discard_option=$3
    local ssd_enable_l2p=$4

    if [ "${ssd_enable_l2p}" -eq 3 ]; then
        discard_option="discard"
    fi

    sudo sync
    if ! sudo umount "${devpath}"; then
        echo "ERROR: failed to unmount ${devpath} before measurement" >&2
        return 1
    fi

    if ! sudo mount -t f2fs \
        -o mode="${FS_MODE}",background_gc="${BGGC_ONOFF}",fsync_mode="${FSYNC_MODE}","${discard_option}" \
        "${devpath}" "${mntpoint}"; then
        echo "ERROR: failed to remount ${devpath} at ${mntpoint}" >&2
        return 1
    fi

    if ! mountpoint -q "${mntpoint}"; then
        echo "ERROR: ${mntpoint} is not a mount point after remount completed" >&2
        return 1
    fi

    echo "Remounted ${devpath} at ${mntpoint} for measurement"
}

# configure csgc settings
setup_gc_config() {
    local gc_mode=$1
    local nr_cs_cores=$2
    local csgc_sync=$3 
    local csgc_max_count
    if [ "$gc_mode" = "cs" ]; then
        csgc_max_count=4294967295
    else
        csgc_max_count=0
    fi
    if [ "$gc_mode" != "iplfs" ]; then
        echo "${csgc_max_count}" | sudo tee "${DEBUGFS_PATH}/csgc_max_count" >/dev/null
        echo "${nr_cs_cores}" | sudo tee "${DEBUGFS_PATH}/nr_cs_cores" >/dev/null
        echo "${csgc_sync}" | sudo tee "${DEBUGFS_PATH}/csgc_sync" >/dev/null
    fi
}

setup_cgroup_mem() {
    local use_cgroup=$1
    local host_mem_usage=$2
    if [ "${use_cgroup}" -eq 1 ]; then
        if [ -d "/sys/fs/cgroup/${CGROUP_NAME}" ]; then
            echo "found existing cgroup ${CGROUP_NAME}"
        else
            echo "creating cgroup ${CGROUP_NAME}"
            sudo cgcreate -g memory:"${CGROUP_NAME}"
        fi
        echo "Setting host memory usage max = ${host_mem_usage}"
        echo "${host_mem_usage}" | sudo tee /sys/fs/cgroup/"${CGROUP_NAME}"/memory.max >/dev/null
    else
        echo "No cgroup usage"
    fi
}

prefill_smallfiles_filewriter() {
    local mntpoint=$1
    local num_files=${2:-30000}
    local threads=${3:-16}          # you can tune this (e.g., 16~32)
    local io_size=${4:-1M}
    local use_fallocate=${5:-no}    # set to "yes" if you also want preallocation

    # per-file size in MB
    local size_per_file=${6:-1}

    # compute totals
    local total_bytes=$(( num_files * size_per_file * 1024 * 1024 ))
    local total_size_human="$(( num_files * size_per_file ))M"

     # echo all parameters for verification
    echo "Parameters for prefill_smallfiles_filewriter():"
    echo "  mntpoint       = ${mntpoint}"
    echo "  num_files      = ${num_files}"
    echo "  size_per_file  = ${size_per_file}M"
    echo "  threads        = ${threads}"
    echo "  io_size        = ${io_size}"
    echo "  use_fallocate  = ${use_fallocate}"
    echo "  total_size     = ${total_size_human}"
    echo "  total_bytes    = ${total_bytes}"

    echo "Prefilling small files: count=${num_files}, each=${size_per_file}M, total=${total_size_human}"
    ${FILE_WRITER_DIR}/build.sh
    ${FILE_WRITER_DIR}/file_writer "${mntpoint}" "smallfiles." "${num_files}" "${total_size_human}" "${threads}" "${io_size}" independent "${use_fallocate}" || return 1
    echo "Prefilled smallfiles, total_bytes: <${total_bytes}>"

    # rename 1..N -> 0..N-1
    for (( i=1; i<=num_files; i++ )); do
        mv "${mntpoint}/smallfiles.${i}" "${mntpoint}/smallfiles.$((i-1))"
    done
     # verify created files
    local total_created expected good_files size_bytes
    expected=$num_files
    total_created=$(find "${mntpoint}" -maxdepth 1 -type f -name 'smallfiles.*' | wc -l)
    size_bytes=$(( size_per_file * 1024 * 1024 ))
    good_files=$(find "${mntpoint}" -maxdepth 1 -type f -name 'smallfiles.*' -size "${size_bytes}c" | wc -l)
    echo "Verification: expected files=${expected}, total found=${total_created}, correct size files=${good_files}"
}


prefill_storage_fio() {
    local devpath=$1
    local mntpoint=$2
    local prefill_ratio=$3
    local gc_mode=$4
    local storage_size
    local requested_size
    local prefill_size
    local prefill_size_human
    local prefill_file="${mntpoint}/${DUMMY_FILE_NAME}1"
    local alignment=4096
    local prefill_threads=10
    local prefill_io_size=1M
    local prefill_mode=collaborate
    local prefill_use_fallocate=no

    if ! storage_size=$(blockdev --getsize64 "${devpath}"); then
        echo "ERROR: failed to read the size of ${devpath}" >&2
        return 1
    fi
    if ! requested_size=$(echo "${storage_size} * ${prefill_ratio} / 1" | bc); then
        echo "ERROR: failed to calculate the prefill size" >&2
        return 1
    fi

    prefill_size=$((requested_size / alignment * alignment))
    prefill_size_human="$(echo "${prefill_size} / 1024 / 1024 / 1024" | bc)G"

    if [ "$gc_mode" = "iplfs" ]; then
        # seems that collaborate mode does not work well with iplfs, 
        # the size of the file and the actually written bytes are not as expected
        prefill_threads=1
        prefill_mode=independent
    fi

    echo "Prefilling storage, ratio=${prefill_ratio}, requested=${requested_size}, aligned=${prefill_size}, size=${prefill_size_human}"
    if ! "${FILE_WRITER_DIR}/build.sh"; then
        echo "ERROR: failed to build file_writer" >&2
        return 1
    fi
    if ! "${FILE_WRITER_DIR}/file_writer" \
        "${mntpoint}" "${DUMMY_FILE_NAME}" 1 "${prefill_size}" \
        "${prefill_threads}" "${prefill_io_size}" "${prefill_mode}" "${prefill_use_fallocate}"; then
        echo "ERROR: file_writer failed during storage prefill" >&2
        return 1
    fi

    if ! sync -f "${prefill_file}"; then
        echo "ERROR: failed to synchronize ${prefill_file}" >&2
        return 1
    fi

    local actual_size
    local allocated_blocks
    local allocated_bytes
    if ! actual_size=$(stat -c '%s' -- "${prefill_file}"); then
        echo "ERROR: prefill file does not exist: ${prefill_file}" >&2
        return 1
    fi
    allocated_blocks=$(stat -c '%b' -- "${prefill_file}") || return 1
    allocated_bytes=$((allocated_blocks * 512))

    if [ "${actual_size}" -ne "${prefill_size}" ]; then
        echo "ERROR: prefill size mismatch: expected=${prefill_size}, actual=${actual_size}" >&2
        return 1
    fi
    if [ "${allocated_bytes}" -lt "${actual_size}" ]; then
        echo "ERROR: prefill file contains holes: size=${actual_size}, allocated=${allocated_bytes}" >&2
        return 1
    fi

    echo "Verified prefill file: path=${prefill_file}, size=${actual_size}, allocated=${allocated_bytes}"
    echo "Prefilled storage, size: <${actual_size}>"
}

prefill_storage_ycsb() {
    local devpath=$1
    local mntpoint=$2
    local prefill_ratio=$3
    local gc_mode=$4
    local storage_size=$(blockdev --getsize64 ${devpath})
    local prefill_size=$(echo "${storage_size} * ${prefill_ratio} / 1" | bc)
    local prefill_size_human="$(echo "${prefill_size} / 1024 / 1024 / 1024" | bc)G"
    local prefill_threads=16
    local prefill_numfiles=$prefill_threads
    local prefill_io_size=1M
    local prefill_mode=independent
    local prefill_use_fallocate=no

    if [ "$gc_mode" != "iplfs" ]; then
        # csgc and ori somehow fails in independent mode...
        prefill_numfiles=1
        prefill_mode=collaborate
    fi
    
    echo "Prefilling storage, ratio=${prefill_ratio}, size=${prefill_size_human}"
    ${FILE_WRITER_DIR}/build.sh
    ${FILE_WRITER_DIR}/file_writer ${mntpoint} testbigfile ${prefill_numfiles} ${prefill_size} ${prefill_threads} ${prefill_io_size} ${prefill_mode} ${prefill_use_fallocate}
    echo "Prefilled storage, size: <${prefill_size}>"
}
