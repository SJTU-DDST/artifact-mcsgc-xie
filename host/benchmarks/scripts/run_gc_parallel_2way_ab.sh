#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so repository updates cannot alter a live run.
if [ -z "${GC_PARALLEL_2WAY_AB_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_PARALLEL_2WAY_AB_SNAPSHOT=1
    export GC_PARALLEL_2WAY_AB_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_PARALLEL_2WAY_AB_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
expected_openssd_commit=52831c159c9f7a73f9670c163a6b513750f64b47
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
comparator="${script_dir}/draw-xie/compare-parallel-gc-results.py"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-parallel-2way-ab/${batch_id}"
manifest="${batch_dir}/results.txt"
sudo_keepalive_pid=""

declare -A branches=(
    [mcsgc8t-parallel-gc-control]=exp/diagnostic-mcsgc8t-parallel-gc-control-20260821
    [mcsgc8t-parallel-gc-inode-share]=exp/diagnostic-mcsgc8t-parallel-gc-inode-share-20260821
)
declare -A module_paths=()

# Print the destructive workflow without touching the SSD.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}")

Builds the parallel-GC control and shared-inode modules, runs two treatment
smoke tests, then runs full big-file and small-file A/B tests. Every benchmark
resets and reformats /dev/nvme0n1. Run this outer script as the login user.
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

# Preserve completed metadata when the batch is interrupted.
handle_signal() {
    stop_sudo_keepalive
    echo "Interrupted. Completed metadata remains in ${manifest}." >&2
    exit 130
}

# Read OpenSSD source provenance without modifying the remote server.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Resolve the unique worktree that owns one expected Host branch.
resolve_host_tree() {
    local branch=$1

    git -C "${host_repo}" worktree list --porcelain | awk \
        -v target="refs/heads/${branch}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / && substr($0, 8) == target { print path }
        '
}

# Build one Host configuration and retain its exact module path.
build_configuration() {
    local configuration=$1
    local branch=${branches[${configuration}]}
    local host_tree
    local module_path

    echo "Building ${configuration} at $(date --iso-8601=seconds)"
    "${prepare_script}" "${configuration}"
    host_tree=$(resolve_host_tree "${branch}")
    if [ -z "${host_tree}" ] || [ "$(printf '%s\n' "${host_tree}" | wc -l)" -ne 1 ]; then
        echo "ERROR: cannot resolve one worktree for ${branch}." >&2
        return 1
    fi
    module_path="${host_tree}/fs/f2fs/f2fs.ko"
    if [ ! -r "${module_path}" ]; then
        echo "ERROR: module is unavailable after build: ${module_path}" >&2
        return 1
    fi
    module_paths[${configuration}]="${module_path}"
    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\n' "${host_tree}"
        printf 'host_branch=%s\n' "${branch}"
        printf 'host_commit=%s\n' "$(git -C "${host_tree}" rev-parse HEAD)"
        printf 'host_tracked_dirty=%s\n' "$([ -n "$(git -C "${host_tree}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
        printf 'module_sha256=%s\n' "$(sha256sum "${module_path}" | awk '{print $1}')"
        printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module_path}")"
    } >> "${manifest}"
}

# Replace the unloaded F2FS module with one prebuilt configuration.
load_configuration() {
    local configuration=$1
    local module_path=${module_paths[${configuration}]}
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
        echo "ERROR: loaded f2fs srcversion does not match ${configuration}." >&2
        return 1
    fi
    echo "Loaded ${configuration}: srcversion=${loaded_srcversion}"
}

# Fail closed when a completed run contains a high-confidence kernel failure.
check_kernel_anomalies() {
    local run_dir=$1
    local output="${run_dir}/kernel-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|Oops:|SBI_NEED_FSCK|EUCLEAN|SIT[^[:space:]]* (mismatch|inconsistent|corrupt)|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount|parallel CSGC (lease|victim) imbalance'

    if grep -Eai "${pattern}" "${run_dir}/external-dmesg.log" > "${output}"; then
        echo "ERROR: kernel anomaly detected; see ${output}." >&2
        return 1
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Run one workload and record its result directory.
run_one() {
    local configuration=$1
    local workload=$2
    local sequence=$3
    local smoke=${4:-0}
    local label
    local result_path_file
    local run_dir
    local -a environment

    label=$(printf '%02d-%s-%s' "${sequence}" "${configuration}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"
    environment=("GC_BREAKDOWN_RESULT_PATH_FILE=${result_path_file}")
    if [ "${smoke}" -eq 1 ]; then
        environment+=("GC_BREAKDOWN_SMOKE=1" "GC_BREAKDOWN_SMOKE_RUNTIME=30")
    fi
    echo "Starting ${label} at $(date --iso-8601=seconds)"
    sudo env "${environment[@]}" "${runner}" "${configuration}" "${workload}"
    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: no result path was recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    check_kernel_anomalies "${run_dir}"
    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'configuration=%s\nworkload=%s\nsmoke=%s\n' \
            "${configuration}" "${workload}" "${smoke}"
        printf 'run_dir=%s\n' "${run_dir}"
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
        if [ "${fsck_status}" -ne 0 ]; then
            echo "ERROR: smoke fsck reported filesystem inconsistencies; see ${fsck_log}." >&2
            return 1
        fi
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
    echo "ERROR: run this outer script as the login user." >&2
    exit 1
fi
if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
    echo "ERROR: /dev/nvme0n1 is mounted." >&2
    exit 1
fi
if [ -r /sys/module/f2fs/refcnt ] && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
    echo "ERROR: f2fs still has active references." >&2
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
    echo "ERROR: OpenSSD provenance does not match the fixed SSD1t build." >&2
    printf '%s\n' "${openssd_provenance}" >&2
    exit 1
fi

{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host=%s\nssd_thread_mode=ssd1t\n' "$(hostname)"
    printf 'artifact_branch=%s\n' "$(git -C "${artifact_repo}" branch --show-current)"
    printf 'artifact_commit=%s\n' "$(git -C "${artifact_repo}" rev-parse HEAD)"
    printf 'artifact_tracked_dirty=%s\n' "$([ -n "$(git -C "${artifact_repo}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf '%s\n' "${openssd_provenance}"
} > "${manifest}"

build_configuration mcsgc8t-parallel-gc-control
build_configuration mcsgc8t-parallel-gc-inode-share

load_configuration mcsgc8t-parallel-gc-inode-share
run_one mcsgc8t-parallel-gc-inode-share bigfile 0 1
run_one mcsgc8t-parallel-gc-inode-share smallfile 1 1

load_configuration mcsgc8t-parallel-gc-control
run_one mcsgc8t-parallel-gc-control bigfile 2
run_one mcsgc8t-parallel-gc-control smallfile 3

load_configuration mcsgc8t-parallel-gc-inode-share
run_one mcsgc8t-parallel-gc-inode-share bigfile 4
run_one mcsgc8t-parallel-gc-inode-share smallfile 5

comparison="${batch_dir}/comparison.txt"
python3 "${comparator}" \
    --control-big "$(<"${batch_dir}/02-mcsgc8t-parallel-gc-control-bigfile.result-path")" \
    --control-small "$(<"${batch_dir}/03-mcsgc8t-parallel-gc-control-smallfile.result-path")" \
    --treatment-big "$(<"${batch_dir}/04-mcsgc8t-parallel-gc-inode-share-bigfile.result-path")" \
    --treatment-small "$(<"${batch_dir}/05-mcsgc8t-parallel-gc-inode-share-smallfile.result-path")" \
    --output "${comparison}"

printf '\ncompleted_at=%s\nstatus=success\ncomparison=%s\n' \
    "$(date --iso-8601=seconds)" "${comparison}" >> "${manifest}"
echo "Two-way parallel CSGC A/B completed: ${batch_dir}"
