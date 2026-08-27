#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPRO_TREE=$(cd "${SCRIPT_DIR}/../../.." && pwd)
SOURCE_COMMIT=0271b907ec00ed643fd139403b726817c9fe8c32
HOST_TREE=/tmp/linux-cs-formal-original-matrix-20260826
HOST_COMMIT=813c35f3ec81bc317c2ca82d796e9a767ad6384e
HOST_MODULE=${HOST_TREE}/fs/f2fs/f2fs.ko
HOST_MODULE_SHA256=0e4b6cb77aac59b998b9a5c0b57e5e99be4b8e0c6456fc7f9c5447dc598eb0b2
OPENSSD_HOST=192.168.98.31
OPENSSD_TREE=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
OPENSSD_BRANCH=formal-original-csgc-main-20260809
OPENSSD_COMMIT=463e8b0b13ad345ed99c2176b1f81ad34d3c986a
DEVICE=/dev/nvme0n1
RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-original-reproduction
EXPECTED_CASES=44

usage() {
    cat <<'EOF'
Usage:
  ./run_europar25_original_matrix.sh
  ./run_europar25_original_matrix.sh --resume BATCH_DIR
  ./run_europar25_original_matrix.sh --status BATCH_DIR
  ./run_europar25_original_matrix.sh --preflight
  ./run_europar25_original_matrix.sh --dry-run

The default command starts a new destructive 44-case matrix. Every case resets,
formats, and overwrites /dev/nvme0n1. No interactive confirmation is requested.
EOF
}

write_schedule() {
    local path=$1
    local mode
    local util
    local sec
    local skew

    printf 'case_id\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tfio_timebased\n' > "${path}"
    for mode in cs ori; do
        printf '%s\t%s\tfilebench\tfileserver_4t_60G_1M_54k_period\trandom\t0.86\t8\t0\n' "${mode}-filebench-period" "${mode}" >> "${path}"
        printf '%s\t%s\tfilebench\tfileserver_4t_60G_1M_54k\trandom\t0.86\t8\t0\n' "${mode}-filebench-fileserver" "${mode}" >> "${path}"
        printf '%s\t%s\tfilebench\tvarmail_4t_60G_1M_54k\trandom\t0.86\t8\t0\n' "${mode}-filebench-varmail" "${mode}" >> "${path}"
        printf '%s\t%s\tycsb\tworkloada\trandom\t0.86\t8\t0\n' "${mode}-ycsb-a" "${mode}" >> "${path}"
        printf '%s\t%s\tycsb\tworkloadf\trandom\t0.8\t8\t0\n' "${mode}-ycsb-f" "${mode}" >> "${path}"
        printf '%s\t%s\tfio\trandwrite\trandom\t0.86\t8\t0\n' "${mode}-fio-overall-uniform" "${mode}" >> "${path}"
        printf '%s\t%s\tfio\trandwrite\tzipf:1.1\t0.86\t8\t1\n' "${mode}-fio-overall-zipf11" "${mode}" >> "${path}"

        for util in 0.6 0.7 0.8 0.9 0.95; do
            printf '%s\t%s\tfio\trandwrite\trandom\t%s\t8\t0\n' "${mode}-fio-util-${util}" "${mode}" "${util}" >> "${path}"
        done
        for sec in 1 2 4 8 16; do
            printf '%s\t%s\tfio\trandwrite\trandom\t0.86\t%s\t0\n' "${mode}-fio-section-${sec}" "${mode}" "${sec}" >> "${path}"
        done
        for skew in random zipf:0.3 zipf:0.7 zipf:0.9 zipf:1.1; do
            case "${skew}" in
                random) skew_id=uniform ;;
                *) skew_id=${skew#zipf:} ;;
            esac
            printf '%s\t%s\tfio\trandwrite\t%s\t0.86\t8\t1\n' "${mode}-fio-skew-${skew_id}" "${mode}" "${skew}" >> "${path}"
        done
    done
}

write_state() {
    local status=$1
    local now
    now=$(date --iso-8601=seconds)
    {
        printf 'batch_status=%q\n' "${status}"
        printf 'updated_at=%q\n' "${now}"
        printf 'outer_pid=%q\n' "$$"
        printf 'outer_start_ticks=%q\n' "${OUTER_START_TICKS}"
        printf 'expected_cases=%q\n' "${EXPECTED_CASES}"
        printf 'batch_dir=%q\n' "${BATCH_DIR}"
    } > "${BATCH_DIR}/state.env"
}

show_status() {
    local batch=$1
    local completed=0
    local failed=0

    if [ -f "${batch}/case-results.tsv" ]; then
        completed=$(awk -F '\t' 'NR > 1 && $11 == 0 {count++} END {print count + 0}' "${batch}/case-results.tsv")
        failed=$(awk -F '\t' 'NR > 1 && $11 != 0 {count++} END {print count + 0}' "${batch}/case-results.tsv")
    fi
    printf 'batch=%s\ncompleted=%s/%s\nfailed_rows=%s\n' "${batch}" "${completed}" "${EXPECTED_CASES}" "${failed}"
    if [ -f "${batch}/state.env" ]; then
        sed -n '1,12p' "${batch}/state.env"
    fi
}

case_succeeded() {
    local case_id=$1
    awk -F '\t' -v id="${case_id}" 'NR > 1 && $1 == id && $11 == 0 {found=1} END {exit !found}' "${CASE_RESULTS}"
}

write_case_config() {
    local path=$1
    local workload_type=$2
    local bmname=$3
    local distribution=$4
    local prefill_ratio=$5
    local segs_per_sec=$6
    local fio_timebased=$7

    cat > "${path}" <<EOF
#!/bin/bash
workloads=("${workload_type}:${bmname}")
random_distributions=("${distribution}")
prefill_ratios=("${prefill_ratio}")
segs_per_sec_list=("${segs_per_sec}")
fio_timebased=${fio_timebased}
EOF
}

preflight() {
    local module_sha
    local loaded_srcversion
    local module_srcversion
    local host_head
    local openssd_state
    local openssd_branch
    local openssd_commit
    local openssd_dirty
    local source_diff

    echo "Running preflight checks..."
    if [ "${EUID}" -eq 0 ]; then
        echo "ERROR: run this launcher as the regular user; it invokes sudo internally" >&2
        return 1
    fi
    sudo -n true

    [ -b "${DEVICE}" ] || { echo "ERROR: ${DEVICE} is missing" >&2; return 1; }
    if findmnt -rn -S "${DEVICE}" >/dev/null; then
        echo "ERROR: ${DEVICE} is already mounted" >&2
        return 1
    fi
    for process in fio filebench java mysqld; do
        if pgrep -x "${process}" >/dev/null; then
            echo "ERROR: process ${process} is already running" >&2
            return 1
        fi
    done

    [ -f "${HOST_MODULE}" ] || { echo "ERROR: Host module is missing: ${HOST_MODULE}" >&2; return 1; }
    host_head=$(git -C "${HOST_TREE}" rev-parse HEAD)
    [ "${host_head}" = "${HOST_COMMIT}" ] || { echo "ERROR: Host tree commit ${host_head}, expected ${HOST_COMMIT}" >&2; return 1; }
    module_sha=$(sha256sum "${HOST_MODULE}" | awk '{print $1}')
    [ "${module_sha}" = "${HOST_MODULE_SHA256}" ] || { echo "ERROR: Host module SHA ${module_sha}, expected ${HOST_MODULE_SHA256}" >&2; return 1; }
    loaded_srcversion=$(cat /sys/module/f2fs/srcversion)
    module_srcversion=$(modinfo -F srcversion "${HOST_MODULE}")
    [ "${loaded_srcversion}" = "${module_srcversion}" ] || { echo "ERROR: loaded F2FS srcversion does not match the pinned module" >&2; return 1; }

    [ "$(git -C "${REPRO_TREE}" merge-base "${SOURCE_COMMIT}" HEAD)" = "${SOURCE_COMMIT}" ] \
        || { echo "ERROR: reproduction branch is not based on ${SOURCE_COMMIT}" >&2; return 1; }
    source_diff=$(git -C "${REPRO_TREE}" diff --name-only "${SOURCE_COMMIT}" -- \
        host/benchmarks/myworkloads host/benchmarks/scripts/configs)
    [ -z "${source_diff}" ] || { echo "ERROR: original workload/config files were modified: ${source_diff}" >&2; return 1; }

    openssd_state=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "${OPENSSD_HOST}" \
        "git -C '${OPENSSD_TREE}' branch --show-current; git -C '${OPENSSD_TREE}' rev-parse HEAD; if test -n \"\$(git -C '${OPENSSD_TREE}' status --porcelain --untracked-files=no)\"; then echo dirty; else echo clean; fi")
    openssd_branch=$(sed -n '1p' <<< "${openssd_state}")
    openssd_commit=$(sed -n '2p' <<< "${openssd_state}")
    openssd_dirty=$(sed -n '3p' <<< "${openssd_state}")
    [ "${openssd_branch}" = "${OPENSSD_BRANCH}" ] || { echo "ERROR: OpenSSD branch ${openssd_branch}, expected ${OPENSSD_BRANCH}" >&2; return 1; }
    [ "${openssd_commit}" = "${OPENSSD_COMMIT}" ] || { echo "ERROR: OpenSSD commit ${openssd_commit}, expected ${OPENSSD_COMMIT}" >&2; return 1; }
    [ "${openssd_dirty}" = clean ] || { echo "ERROR: OpenSSD tracked tree is dirty" >&2; return 1; }

    command -v fio >/dev/null
    command -v filebench >/dev/null
    command -v java >/dev/null
    command -v python2 >/dev/null
    command -v cgexec >/dev/null
    command -v bc >/dev/null
    command -v mysqladmin >/dev/null
    [ -x "${SCRIPT_DIR}/../file_writer/build.sh" ]
    [ -x "${SCRIPT_DIR}/../ycsb-0.17.0/bin/ycsb" ]
    sudo test -f /var/lib/mysql/ycsb_db/usertable.ibd \
        || { echo "ERROR: preloaded YCSB database is missing" >&2; return 1; }
    grep -Rqs '^datadir[[:space:]]*=[[:space:]]*/mnt/openssd_f2fs/mysql' /etc/mysql \
        || { echo "ERROR: MySQL datadir is not configured for /mnt/openssd_f2fs/mysql" >&2; return 1; }
    if systemctl is-active --quiet mysql; then
        echo "ERROR: MySQL must be stopped before the matrix starts" >&2
        return 1
    fi

    sudo "${REPRO_TREE}/host/src/nvme-cli/nvme" id-ctrl "${DEVICE}" >/dev/null
    echo "Preflight checks passed."
}

write_provenance() {
    local openssd_info
    local vitis_info

    openssd_info=$(ssh -o BatchMode=yes "${OPENSSD_HOST}" \
        "git -C '${OPENSSD_TREE}' branch --show-current; git -C '${OPENSSD_TREE}' rev-parse HEAD; git -C '${OPENSSD_TREE}' status --short --untracked-files=no")
    vitis_info=$(ssh -o BatchMode=yes "${OPENSSD_HOST}" \
        "find /home/xin/vitis_workspaces/xie_csgc_withjin -path '*/src/config.h' -type f -print0 | sort -z | xargs -0 sha256sum; find /home/xin/vitis_workspaces/xie_csgc_withjin -path '*/src/shared_mem.h' -type f -print0 | sort -z | xargs -0 sha256sum")

    {
        echo "operator=Codex"
        echo "outer_script=${SCRIPT_DIR}/run_europar25_original_matrix.sh"
        echo "started_at=${STARTED_AT}"
        echo "artifact_reproduction_branch=$(git -C "${REPRO_TREE}" branch --show-current)"
        echo "artifact_reproduction_commit=$(git -C "${REPRO_TREE}" rev-parse HEAD)"
        echo "artifact_workload_source_commit=${SOURCE_COMMIT}"
        echo "host_branch=$(git -C "${HOST_TREE}" branch --show-current)"
        echo "host_commit=$(git -C "${HOST_TREE}" rev-parse HEAD)"
        echo "host_module=${HOST_MODULE}"
        echo "host_module_sha256=$(sha256sum "${HOST_MODULE}" | awk '{print $1}')"
        echo "host_module_srcversion=$(modinfo -F srcversion "${HOST_MODULE}")"
        echo "loaded_f2fs_srcversion=$(cat /sys/module/f2fs/srcversion)"
        echo "kernel=$(uname -r)"
        echo "device=${DEVICE}"
        echo "fio=$(fio --version)"
        echo "filebench=$(filebench -h 2>&1 | head -1 || true)"
        echo "java=$(java -version 2>&1 | head -1)"
        echo "python2=$(python2 --version 2>&1)"
        echo "mysql=$(mysql --version)"
        echo "openssd_expected_branch=${OPENSSD_BRANCH}"
        echo "openssd_expected_commit=${OPENSSD_COMMIT}"
        echo "firmware_binary_identity_limit=source and Vitis input hashes do not prove the currently running ELF identity"
        echo
        echo "[openssd-source]"
        echo "${openssd_info}"
        echo
        echo "[vitis-input-hashes]"
        echo "${vitis_info}"
    } > "${BATCH_DIR}/provenance.txt"

    mkdir -p "${BATCH_DIR}/source-snapshot"
    git -C "${REPRO_TREE}" archive "${SOURCE_COMMIT}" \
        host/benchmarks/scripts/configs \
        host/benchmarks/scripts/plot \
        host/benchmarks/myworkloads \
        | tar -x -C "${BATCH_DIR}/source-snapshot"
    find "${BATCH_DIR}/source-snapshot" -type f -print0 | sort -z | xargs -0 sha256sum \
        > "${BATCH_DIR}/source-snapshot-sha256.txt"
}

validate_case() {
    local workload_type=$1
    local output_path=$2
    local log_path

    case "${workload_type}" in
        filebench)
            log_path="${output_path}/filebench.log"
            grep -q 'IO Summary:' "${log_path}"
            ;;
        fio)
            log_path="${output_path}/fio.log"
            grep -q 'Run status group' "${log_path}"
            ! grep -Eq '(^|[^[:alpha:]])err=[1-9][0-9]*' "${log_path}"
            ;;
        ycsb)
            log_path="${output_path}/ycsb.log"
            grep -q '\[OVERALL\], Throughput(ops/sec)' "${log_path}"
            grep -q '\[CLEANUP\], Operations, 1' "${log_path}"
            ;;
    esac

    if grep -aEiq 'BUG:|Oops:|kernel panic|NULL pointer dereference|refcount.*(underflow|saturated)|SIT.*(corrupt|inconsistent)|EUCLEAN|nvme.*(timeout|reset controller)|I/O error' \
        "${output_path}/dmesg.log"; then
        echo "ERROR: kernel or device anomaly detected in ${output_path}/dmesg.log" >&2
        return 1
    fi
}

MODE=start
BATCH_DIR=
case "${1:-}" in
    --resume)
        [ $# -eq 2 ] || { usage; exit 2; }
        MODE=resume
        BATCH_DIR=$(realpath "$2")
        ;;
    --status)
        [ $# -eq 2 ] || { usage; exit 2; }
        show_status "$(realpath "$2")"
        exit 0
        ;;
    --dry-run)
        write_schedule /dev/stdout
        exit 0
        ;;
    --preflight)
        preflight
        exit 0
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    '') ;;
    *) usage; exit 2 ;;
esac

mkdir -p "${RESULT_BASE}"
exec 9>"${RESULT_BASE}/matrix.lock"
if ! flock -n 9; then
    echo "ERROR: another Euro-Par reproduction matrix holds ${RESULT_BASE}/matrix.lock" >&2
    exit 1
fi

if [ "${MODE}" = start ]; then
    BATCH_DIR="${RESULT_BASE}/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "${BATCH_DIR}/generated-configs" "${BATCH_DIR}/raw"
    write_schedule "${BATCH_DIR}/schedule.tsv"
    printf 'case_id\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tstarted_at\tended_at\tduration_s\tstatus\toutput_path\n' \
        > "${BATCH_DIR}/case-results.tsv"
else
    [ -f "${BATCH_DIR}/schedule.tsv" ] || { echo "ERROR: resume batch has no schedule.tsv" >&2; exit 1; }
    [ -f "${BATCH_DIR}/case-results.tsv" ] || { echo "ERROR: resume batch has no case-results.tsv" >&2; exit 1; }
fi

CASE_RESULTS=${BATCH_DIR}/case-results.tsv
OUTER_START_TICKS=$(awk '{print $22}' "/proc/$$/stat")
STARTED_AT=$(date --iso-8601=seconds)
printf '%s\n' "${BATCH_DIR}" > "${RESULT_BASE}/latest-batch.txt"
exec > >(tee -a "${BATCH_DIR}/runner.log") 2>&1

finalize() {
    local status=$?
    if [ "${status}" -ne 0 ]; then
        write_state failed
        printf 'failed_at=%s\nexit_status=%s\n' "$(date --iso-8601=seconds)" "${status}" > "${BATCH_DIR}/failure.env"
    fi
    exit "${status}"
}
trap finalize EXIT

echo "Batch directory: ${BATCH_DIR}"
echo "This authorized reproduction resets and overwrites ${DEVICE} once per case."
echo "No interactive confirmation is required."
preflight
if [ "${MODE}" = start ]; then
    write_provenance
fi
write_state running

completed_before=$(awk -F '\t' 'NR > 1 && $11 == 0 {count++} END {print count + 0}' "${CASE_RESULTS}")
echo "Starting matrix with ${completed_before}/${EXPECTED_CASES} successful cases already recorded."

while IFS=$'\t' read -r case_id mode workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased; do
    [ "${case_id}" = case_id ] && continue
    if case_succeeded "${case_id}"; then
        echo "Skipping completed case ${case_id}"
        continue
    fi

    config_path="${BATCH_DIR}/generated-configs/${case_id}.sh"
    write_case_config "${config_path}" "${workload_type}" "${bmname}" "${distribution}" \
        "${prefill_ratio}" "${segs_per_sec}" "${fio_timebased}"
    chmod +x "${config_path}"

    echo "MATRIX_CASE_START id=${case_id} at=$(date --iso-8601=seconds)"
    EUROPAR_OUTPUT_ROOT="${BATCH_DIR}/raw" \
    EUROPAR_CASE_RESULTS="${CASE_RESULTS}" \
    EUROPAR_CASE_ID="${case_id}" \
        "${SCRIPT_DIR}/test.sh" "${mode}" "${config_path}"

    output_path=$(awk -F '\t' -v id="${case_id}" '$1 == id && $11 == 0 {path=$12} END {print path}' "${CASE_RESULTS}")
    [ -n "${output_path}" ] || { echo "ERROR: no successful result row for ${case_id}" >&2; exit 1; }
    validate_case "${workload_type}" "${output_path}"

    successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {count++} END {print count + 0}' "${CASE_RESULTS}")
    echo "MATRIX_CASE_END id=${case_id} progress=${successful}/${EXPECTED_CASES} at=$(date --iso-8601=seconds)"
done < "${BATCH_DIR}/schedule.tsv"

successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {count++} END {print count + 0}' "${CASE_RESULTS}")
[ "${successful}" -eq "${EXPECTED_CASES}" ] || { echo "ERROR: expected ${EXPECTED_CASES} successful cases, found ${successful}" >&2; exit 1; }

COMPLETED_AT=$(date --iso-8601=seconds)
printf 'started_at=%s\ncompleted_at=%s\nsuccessful_cases=%s\n' "${STARTED_AT}" "${COMPLETED_AT}" "${successful}" \
    > "${BATCH_DIR}/completed.env"
write_state success
trap - EXIT
echo "EUROPAR_MATRIX_COMPLETE status=success cases=${successful} completed_at=${COMPLETED_AT}"
