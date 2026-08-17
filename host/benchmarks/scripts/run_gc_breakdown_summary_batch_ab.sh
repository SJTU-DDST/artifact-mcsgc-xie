#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so repository updates cannot alter a live run.
if [ -z "${GC_BREAKDOWN_SUMMARY_AB_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_BREAKDOWN_SUMMARY_AB_SNAPSHOT=1
    export GC_BREAKDOWN_SUMMARY_AB_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_BREAKDOWN_SUMMARY_AB_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
comparator="${script_dir}/draw-xie/compare-gc-summary-batch-results.py"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-breakdown-summary-batch-ab/${batch_id}"
manifest="${batch_dir}/results.txt"
sudo_keepalive_pid=""

# Print the one-command interface without touching the test device.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}")

Builds and runs these four full diagnostics on SSD1t:
  1. summary control, bigfile
  2. summary control, smallfile
  3. batched summary, bigfile
  4. batched summary, smallfile

Run this outer script as the normal login user. It invokes sudo internally.
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
    echo "Interrupted. Completed run metadata remains in ${manifest}." >&2
    exit 130
}

# Read OpenSSD Git provenance without changing the remote server.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Build one Host configuration and record the exact module provenance.
prepare_configuration() {
    local configuration=$1
    local expected_branch=$2
    local host_tree
    local module_path

    echo
    echo "Preparing ${configuration} at $(date --iso-8601=seconds)"
    "${prepare_script}" "${configuration}"

    host_tree=$(git -C "${host_repo}" worktree list --porcelain | awk \
        -v target="refs/heads/${expected_branch}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / && substr($0, 8) == target { print path }
        ')
    if [ -z "${host_tree}" ] || [ "$(printf '%s\n' "${host_tree}" | wc -l)" -ne 1 ]; then
        echo "ERROR: cannot resolve one worktree for ${expected_branch}." >&2
        return 1
    fi
    module_path="${host_tree}/fs/f2fs/f2fs.ko"
    if [ ! -r "${module_path}" ]; then
        echo "ERROR: module is unavailable after build: ${module_path}" >&2
        return 1
    fi

    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\n' "${host_tree}"
        printf 'host_branch=%s\n' "${expected_branch}"
        printf 'host_commit=%s\n' "$(git -C "${host_tree}" rev-parse HEAD)"
        printf 'module_sha256=%s\n' "$(sha256sum "${module_path}" | awk '{print $1}')"
        printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module_path}")"
    } >> "${manifest}"
}

# Run one full workload and append its exact result path to the manifest.
run_one() {
    local configuration=$1
    local workload=$2
    local sequence=$3
    local label
    local result_path_file
    local run_dir

    label=$(printf '%02d-%s-%s' "${sequence}" "${configuration}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"

    echo
    echo "Starting ${label} at $(date --iso-8601=seconds)"
    sudo env GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${runner}" "${configuration}" "${workload}"
    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: result path was not recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    if [ ! -s "${run_dir}/gc-breakdown-diagnostic-result.txt" ]; then
        echo "ERROR: diagnostic summary is missing for ${label}." >&2
        return 1
    fi

    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'configuration=%s\n' "${configuration}"
        printf 'workload=%s\n' "${workload}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'summary=%s\n' "${run_dir}/gc-breakdown-diagnostic-result.txt"
        printf 'kernel_log=%s\n' "${run_dir}/external-dmesg.log"
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
    echo "ERROR: do not run the outer batch through sudo." >&2
    exit 1
fi
if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
    echo "ERROR: /dev/nvme0n1 is mounted; refusing to replace f2fs." >&2
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
openssd_dirty=$(awk -F= '$1 == "openssd_tracked_dirty" { print $2 }' <<< "${openssd_provenance}")
if [ "${openssd_branch}" != "${expected_openssd_branch}" ]; then
    echo "ERROR: expected OpenSSD branch ${expected_openssd_branch}, got ${openssd_branch}." >&2
    exit 1
fi
if [ "${openssd_dirty}" != "0" ]; then
    echo "ERROR: the OpenSSD source has tracked local modifications." >&2
    exit 1
fi

{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host=%s\n' "$(hostname)"
    printf 'ssd_thread_mode=ssd1t\n'
    printf '%s\n' "${openssd_provenance}"
} > "${manifest}"

prepare_configuration mcsgc8t-summary-control \
    exp/diagnostic-mcsgc8t-summary-control-20260817
run_one mcsgc8t-summary-control bigfile 1
run_one mcsgc8t-summary-control smallfile 2

prepare_configuration mcsgc8t-batch-summary \
    exp/diagnostic-mcsgc8t-batched-summary-breakdown-20260817
run_one mcsgc8t-batch-summary bigfile 3
run_one mcsgc8t-batch-summary smallfile 4

comparison="${batch_dir}/comparison.txt"
python3 "${comparator}" \
    --control-big "$(<"${batch_dir}/01-mcsgc8t-summary-control-bigfile.result-path")" \
    --control-small "$(<"${batch_dir}/02-mcsgc8t-summary-control-smallfile.result-path")" \
    --treatment-big "$(<"${batch_dir}/03-mcsgc8t-batch-summary-bigfile.result-path")" \
    --treatment-small "$(<"${batch_dir}/04-mcsgc8t-batch-summary-smallfile.result-path")" \
    --output "${comparison}"

printf '\ncompleted_at=%s\nstatus=success\n' \
    "$(date --iso-8601=seconds)" >> "${manifest}"

echo
echo "Summary batch A/B diagnostics completed successfully."
echo "Batch directory: ${batch_dir}"
echo "Manifest: ${manifest}"
echo "Comparison: ${comparison}"
