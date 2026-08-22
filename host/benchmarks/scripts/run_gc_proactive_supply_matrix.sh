#!/usr/bin/env bash

set -euo pipefail

# Execute an in-memory snapshot so repository updates cannot alter a live run.
if [ -z "${GC_PROACTIVE_SUPPLY_MATRIX_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_PROACTIVE_SUPPLY_MATRIX_SNAPSHOT=1
    export GC_PROACTIVE_SUPPLY_MATRIX_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_PROACTIVE_SUPPLY_MATRIX_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
host_branch=exp/diagnostic-mcsgc8t-proactive-supply-20260822
configuration=mcsgc8t-proactive-supply
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
expected_openssd_commit=52831c159c9f7a73f9670c163a6b513750f64b47
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
comparator="${script_dir}/draw-xie/compare-proactive-supply-results.py"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-proactive-supply-matrix/${batch_id}"
manifest="${batch_dir}/results.txt"
sudo_keepalive_pid=""
host_tree=""
module_path=""

# Describe the destructive experiment matrix without touching the namespace.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}")

Builds the proactive-supply Host module, runs aggressive bigfile and smallfile
smoke tests, then runs six full SSD1t diagnostics:
  off/moderate/aggressive x bigfile/smallfile

Every benchmark resets and reformats /dev/nvme0n1. Run this outer script as
the normal login user; it invokes sudo internally.
EOF
}

# Stop the sudo timestamp refresher on every exit path.
stop_sudo_keepalive() {
    if [ -n "${sudo_keepalive_pid}" ] \
        && kill -0 "${sudo_keepalive_pid}" 2>/dev/null; then
        kill "${sudo_keepalive_pid}" 2>/dev/null || true
        wait "${sudo_keepalive_pid}" 2>/dev/null || true
    fi
    sudo_keepalive_pid=""
}

# Preserve all completed metadata when the matrix is interrupted.
handle_signal() {
    stop_sudo_keepalive
    echo "Interrupted. Completed run metadata remains in ${manifest}." >&2
    exit 130
}

# Read OpenSSD source provenance without modifying the remote server.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Resolve the unique worktree that owns the proactive Host branch.
resolve_host_tree() {
    git -C "${host_repo}" worktree list --porcelain | awk \
        -v target="refs/heads/${host_branch}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / && substr($0, 8) == target { print path }
        '
}

# Build the single runtime-selectable module and record exact provenance.
build_configuration() {
    local tracked_dirty

    echo "Building ${configuration} at $(date --iso-8601=seconds)"
    "${prepare_script}" "${configuration}"
    host_tree=$(resolve_host_tree)
    if [ -z "${host_tree}" ] \
        || [ "$(printf '%s\n' "${host_tree}" | wc -l)" -ne 1 ]; then
        echo "ERROR: cannot resolve one worktree for ${host_branch}." >&2
        return 1
    fi
    module_path="${host_tree}/fs/f2fs/f2fs.ko"
    if [ ! -r "${module_path}" ]; then
        echo "ERROR: module is unavailable after build: ${module_path}" >&2
        return 1
    fi
    tracked_dirty=0
    if [ -n "$(git -C "${host_tree}" status --porcelain=v1 --untracked-files=no)" ]; then
        tracked_dirty=1
    fi
    {
        printf '\n[build]\n'
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\n' "${host_tree}"
        printf 'host_branch=%s\n' "$(git -C "${host_tree}" branch --show-current)"
        printf 'host_commit=%s\n' "$(git -C "${host_tree}" rev-parse HEAD)"
        printf 'host_tracked_dirty=%s\n' "${tracked_dirty}"
        printf 'module_sha256=%s\n' "$(sha256sum "${module_path}" | awk '{print $1}')"
        printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module_path}")"
    } >> "${manifest}"
}

# Replace the unloaded F2FS module with the proactive experiment module.
load_configuration() {
    local expected_srcversion
    local loaded_srcversion

    if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
        echo "ERROR: /dev/nvme0n1 is mounted before module replacement." >&2
        return 1
    fi
    if lsmod | awk '$1 == "f2fs" { found = 1 } END { exit !found }'; then
        sudo rmmod f2fs
    fi
    sudo insmod "${module_path}"
    expected_srcversion=$(modinfo -F srcversion "${module_path}")
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    if [ "${loaded_srcversion^^}" != "${expected_srcversion^^}" ]; then
        echo "ERROR: loaded f2fs srcversion does not match the built module." >&2
        return 1
    fi
    echo "Loaded ${configuration}: srcversion=${loaded_srcversion}"
}

# Fail closed when a completed run contains a high-confidence kernel failure.
check_kernel_anomalies() {
    local run_dir=$1
    local output="${run_dir}/kernel-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|BUG: Bad page state|page dumped because|Oops:|general protection fault|slab-out-of-bounds|slab-use-after-free|use-after-free|list_del corruption|corrupted double-linked list|RCU:.*stall|rcu: INFO:.*stall|watchdog: BUG: soft lockup|SBI_NEED_FSCK|EUCLEAN|Structure needs cleaning|Inconsistent error blkaddr|something went wrong during csgc|SIT.*(bitmap|mismatch|inconsistent|corrupt)|SSA.*(mismatch|inconsistent|corrupt)|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount|CSGC_PAGE_REF_BUG|CSGC_CSI_POOL_BUG|CSGC_DEFER_HANDOFF_BUG|CSGC_DEFER_WAIT_BUG|CSGC_QUIESCE_STAT.*drained=0|CSGC_CSI_POOL_STAT.*mismatch=1|CSGC_PAGE_REF_STAT.*mismatch=1'

    if grep -Eai "${pattern}" "${run_dir}/external-dmesg.log" > "${output}"; then
        echo "ERROR: kernel anomaly detected; see ${output}." >&2
        return 1
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Run one workload/profile pair and record its exact output directory.
run_one() {
    local profile=$1
    local workload=$2
    local sequence=$3
    local smoke=${4:-0}
    local label
    local result_path_file
    local run_dir
    local -a environment

    label=$(printf '%02d-%s-%s' "${sequence}" "${profile}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"
    environment=(
        "CSGC_PROACTIVE_PROFILE=${profile}"
        "GC_BREAKDOWN_RESULT_PATH_FILE=${result_path_file}"
    )
    if [ "${smoke}" -eq 1 ]; then
        environment+=(
            "GC_BREAKDOWN_SMOKE=1"
            "GC_BREAKDOWN_SMOKE_IO_SIZE_PER_THREAD=1G"
            "GC_BREAKDOWN_SMOKE_RUNTIME=30"
        )
    fi

    echo "Starting ${label} at $(date --iso-8601=seconds)"
    if ! sudo env "${environment[@]}" \
        "${runner}" "${configuration}" "${workload}"; then
        echo "ERROR: ${label} failed." >&2
        return 1
    fi
    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: result path was not recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    if [ ! -s "${run_dir}/gc-breakdown-diagnostic-result.txt" ] \
        || [ ! -s "${run_dir}/fio.log" ] \
        || [ ! -s "${run_dir}/ssd-workload-stat.log" ]; then
        echo "ERROR: required result artifacts are missing for ${label}." >&2
        return 1
    fi
    check_kernel_anomalies "${run_dir}"

    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'profile=%s\nworkload=%s\nsmoke=%s\n' \
            "${profile}" "${workload}" "${smoke}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'summary=%s\n' "${run_dir}/gc-breakdown-diagnostic-result.txt"
        printf 'kernel_log=%s\n' "${run_dir}/external-dmesg.log"
        printf 'ssd_stats=%s\n' "${run_dir}/ssd-workload-stat.log"
    } >> "${manifest}"

    if [ "${smoke}" -eq 1 ]; then
        local fsck_log="${run_dir}/fsck-after-smoke.log"
        local fsck_status

        set +e
        sudo bash -c "fsck.f2fs -f /dev/nvme0n1 > '${fsck_log}' 2>&1"
        fsck_status=$?
        set -e
        printf 'fsck_log=%s\nfsck_status=%s\n' \
            "${fsck_log}" "${fsck_status}" >> "${manifest}"
    fi
}

if [ "$#" -eq 1 ] && { [ "$1" = "-h" ] || [ "$1" = "--help" ]; }; then
    usage
    exit 0
fi
if [ "$#" -ne 0 ]; then
    usage >&2
    exit 1
fi
if [ "${EUID}" -eq 0 ]; then
    echo "ERROR: do not run the outer matrix through sudo." >&2
    exit 1
fi
if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
    echo "ERROR: /dev/nvme0n1 is mounted; refusing to replace f2fs." >&2
    exit 1
fi
if [ -r /sys/module/f2fs/refcnt ] \
    && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
    echo "ERROR: f2fs still has active references; refusing to run the matrix." >&2
    findmnt -rn -t f2fs -o TARGET,SOURCE,FSTYPE,OPTIONS >&2 || true
    exit 1
fi

mkdir -p "${batch_dir}"
trap stop_sudo_keepalive EXIT
trap handle_signal INT TERM
sudo -v
(
    while sleep 45; do
        sudo -n true || exit
    done
) &
sudo_keepalive_pid=$!

openssd_provenance=$(read_openssd_provenance)
openssd_branch=$(awk -F= '$1 == "openssd_branch" { print $2 }' <<< "${openssd_provenance}")
openssd_commit=$(awk -F= '$1 == "openssd_commit" { print $2 }' <<< "${openssd_provenance}")
openssd_dirty=$(awk -F= '$1 == "openssd_tracked_dirty" { print $2 }' <<< "${openssd_provenance}")
if [ "${openssd_branch}" != "${expected_openssd_branch}" ] \
    || [ "${openssd_commit}" != "${expected_openssd_commit}" ] \
    || [ "${openssd_dirty}" != "0" ]; then
    echo "ERROR: OpenSSD provenance does not match the fixed SSD1t source." >&2
    printf '%s\n' "${openssd_provenance}" >&2
    exit 1
fi

{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host=%s\nssd_thread_mode=ssd1t\n' "$(hostname)"
    printf 'artifact_branch=%s\n' "$(git -C "${artifact_repo}" branch --show-current)"
    printf 'artifact_commit=%s\n' "$(git -C "${artifact_repo}" rev-parse HEAD)"
    printf 'artifact_tracked_dirty=%s\n' \
        "$([ -n "$(git -C "${artifact_repo}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf '%s\n' "${openssd_provenance}"
} > "${manifest}"

build_configuration
load_configuration

run_one aggressive bigfile 0 1
run_one aggressive smallfile 1 1

run_one off bigfile 2
run_one moderate bigfile 3
run_one aggressive bigfile 4
run_one off smallfile 5
run_one moderate smallfile 6
run_one aggressive smallfile 7

comparison="${batch_dir}/comparison.txt"
python3 "${comparator}" \
    --off-big "$(<"${batch_dir}/02-off-bigfile.result-path")" \
    --moderate-big "$(<"${batch_dir}/03-moderate-bigfile.result-path")" \
    --aggressive-big "$(<"${batch_dir}/04-aggressive-bigfile.result-path")" \
    --off-small "$(<"${batch_dir}/05-off-smallfile.result-path")" \
    --moderate-small "$(<"${batch_dir}/06-moderate-smallfile.result-path")" \
    --aggressive-small "$(<"${batch_dir}/07-aggressive-smallfile.result-path")" \
    --output "${comparison}"

printf '\ncompleted_at=%s\nstatus=success\ncomparison=%s\n' \
    "$(date --iso-8601=seconds)" "${comparison}" >> "${manifest}"

echo "Proactive CSGC matrix completed successfully."
echo "Batch directory: ${batch_dir}"
echo "Manifest: ${manifest}"
echo "Comparison: ${comparison}"
