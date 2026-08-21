#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so repository updates cannot alter a live run.
if [ -z "${GC_PARALLEL_UNSAFE_FAST_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_PARALLEL_UNSAFE_FAST_SNAPSHOT=1
    export GC_PARALLEL_UNSAFE_FAST_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_PARALLEL_UNSAFE_FAST_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
host_branch=exp/diagnostic-mcsgc8t-parallel-gc-unsafe-fast-20260821
configuration=mcsgc8t-parallel-gc-unsafe-fast
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
expected_openssd_commit=52831c159c9f7a73f9670c163a6b513750f64b47
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
comparator="${script_dir}/draw-xie/compare-parallel-unsafe-fast.py"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-parallel-unsafe-fast/${batch_id}"
manifest="${batch_dir}/results.txt"
sudo_keepalive_pid=""

# Describe the destructive experiment without changing any state.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}")

Builds and loads the unsafe-fast two-way CSGC module, then runs one full
big-file and one full small-file experiment. Both runs reset and overwrite
/dev/nvme0n1. Run this outer script as the login user.
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

# Preserve all completed artifacts when interrupted.
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

# Resolve the unique worktree that owns the unsafe-fast Host branch.
resolve_host_tree() {
    git -C "${host_repo}" worktree list --porcelain | awk \
        -v target="refs/heads/${host_branch}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / && substr($0, 8) == target { print path }
        '
}

# Replace the unloaded F2FS module with the freshly built module.
load_module() {
    local module_path=$1
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
    echo "Loaded unsafe-fast module: srcversion=${loaded_srcversion}"
}

# Stop only on failures that invalidate the performance experiment itself.
check_fatal_kernel_anomalies() {
    local run_dir=$1
    local output="${run_dir}/kernel-fatal-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|Oops:|general protection fault|watchdog: BUG|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount'

    if grep -Eai "${pattern}" "${run_dir}/external-dmesg.log" > "${output}"; then
        echo "ERROR: fatal kernel anomaly detected; see ${output}." >&2
        return 1
    fi
    printf 'No configured fatal kernel anomaly patterns matched.\n' > "${output}"
    grep -Eai 'SBI_NEED_FSCK|EUCLEAN|SIT[^[:space:]]* (mismatch|inconsistent|corrupt)' \
        "${run_dir}/external-dmesg.log" > "${run_dir}/kernel-integrity-warnings.log" || true
}

# Run one full workload and record its exact output directory.
run_one() {
    local workload=$1
    local sequence=$2
    local label
    local result_path_file
    local run_dir

    label=$(printf '%02d-%s' "${sequence}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"
    echo "Starting ${label} at $(date --iso-8601=seconds)"
    sudo env GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${runner}" "${configuration}" "${workload}"
    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: no result path was recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    check_fatal_kernel_anomalies "${run_dir}"
    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'workload=%s\nrun_dir=%s\n' "${workload}" "${run_dir}"
    } >> "${manifest}"
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

"${prepare_script}" "${configuration}"
host_tree=$(resolve_host_tree)
if [ -z "${host_tree}" ] || [ "$(printf '%s\n' "${host_tree}" | wc -l)" -ne 1 ]; then
    echo "ERROR: cannot resolve one worktree for ${host_branch}." >&2
    exit 1
fi
module_path="${host_tree}/fs/f2fs/f2fs.ko"
{
    printf 'host_tree=%s\n' "${host_tree}"
    printf 'host_branch=%s\n' "$(git -C "${host_tree}" branch --show-current)"
    printf 'host_commit=%s\n' "$(git -C "${host_tree}" rev-parse HEAD)"
    printf 'host_tracked_dirty=%s\n' "$([ -n "$(git -C "${host_tree}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf 'module_sha256=%s\n' "$(sha256sum "${module_path}" | awk '{print $1}')"
    printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module_path}")"
} >> "${manifest}"

load_module "${module_path}"
run_one bigfile 0
run_one smallfile 1

comparison="${batch_dir}/comparison.txt"
python3 "${comparator}" \
    --big "$(<"${batch_dir}/00-bigfile.result-path")" \
    --small "$(<"${batch_dir}/01-smallfile.result-path")" \
    --output "${comparison}"

printf '\ncompleted_at=%s\nstatus=success\ncomparison=%s\n' \
    "$(date --iso-8601=seconds)" "${comparison}" >> "${manifest}"
echo "Unsafe-fast parallel CSGC experiment completed: ${batch_dir}"
