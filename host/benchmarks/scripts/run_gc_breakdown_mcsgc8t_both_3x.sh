#!/usr/bin/env bash

set -euo pipefail

# Execute an in-memory snapshot so repository updates cannot alter a live run.
if [ -z "${GC_BREAKDOWN_BOTH_3X_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_BREAKDOWN_BOTH_3X_SNAPSHOT=1
    export GC_BREAKDOWN_BOTH_3X_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_BREAKDOWN_BOTH_3X_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
openssd_sync_script="${openssd_repo}/scripts/sync_code.sh"
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
repeat_count=3
minimum_free_bytes=$((15 * 1024 * 1024 * 1024))
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-breakdown-mcsgc8t-both-3x/${batch_id}"
manifest="${batch_dir}/results.txt"
sudo_keepalive_pid=""
openssd_branch=""
openssd_commit=""
openssd_upstream=""
openssd_upstream_commit=""
openssd_ahead=""
openssd_behind=""
openssd_tracked_dirty=""
openssd_sync_sha256=""
baseline_openssd_branch=""
baseline_openssd_commit=""
baseline_openssd_tracked_dirty=""
baseline_openssd_sync_sha256=""

# Print the single-command interface without starting a destructive benchmark.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}")

Runs three bigfile+smallfile diagnostic groups for each configuration:
  1. mCSGC8t no-pipeline
  2. mCSGC8t pipeline

The script switches Host branches, rebuilds f2fs, and runs all 12 benchmarks.
Run it as the normal login user; it requests sudo itself.
EOF
}

# Stop the sudo timestamp refresher when the batch exits or is interrupted.
stop_sudo_keepalive() {
    if [ -n "${sudo_keepalive_pid}" ] \
        && kill -0 "${sudo_keepalive_pid}" 2>/dev/null; then
        kill "${sudo_keepalive_pid}" 2>/dev/null || true
        wait "${sudo_keepalive_pid}" 2>/dev/null || true
    fi
    sudo_keepalive_pid=""
}

# Stop background helpers and terminate the batch after an external signal.
handle_signal() {
    stop_sudo_keepalive
    echo "Interrupted. Completed run metadata remains in ${manifest}." >&2
    exit 130
}

# Reject Host changes that could be overwritten or contaminate branch results.
check_host_worktree() {
    local status_line
    local path

    if [ ! -d "${host_repo}/.git" ]; then
        echo "ERROR: Host repository is unavailable: ${host_repo}" >&2
        return 1
    fi

    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        path=${status_line:3}
        if [ "${path}" != ".config" ]; then
            echo "ERROR: Host worktree has an unsupported local change:" >&2
            echo "  ${status_line}" >&2
            echo "Only the expected .config modification may be present." >&2
            return 1
        fi
    done < <(git -C "${host_repo}" status --porcelain=v1 --untracked-files=normal)
}

# Read Git and sync-script provenance from the OpenSSD source repository.
read_openssd_provenance() {
    local remote_output
    local key value extra
    local -a ssh_command=(
        ssh
        -o BatchMode=yes
        -o ConnectTimeout=10
        -o ConnectionAttempts=1
        -o StrictHostKeyChecking=yes
        "${openssd_host}"
    )

    if ! remote_output=$("${ssh_command[@]}" "bash -s" <<'REMOTE_SCRIPT'
set -eu
repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
sync_script="${repo}/scripts/sync_code.sh"

if [ ! -d "${repo}/.git" ]; then
    echo "Remote error: OpenSSD repository is unavailable: ${repo}" >&2
    exit 20
fi
if [ ! -r "${sync_script}" ]; then
    echo "Remote error: OpenSSD sync script is unavailable: ${sync_script}" >&2
    exit 21
fi

branch=$(git -C "${repo}" symbolic-ref --quiet --short HEAD || printf 'DETACHED')
commit=$(git -C "${repo}" rev-parse HEAD)
upstream=$(git -C "${repo}" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || printf 'none')
if [ "${upstream}" = "none" ]; then
    upstream_commit=none
    ahead=unknown
    behind=unknown
else
    upstream_commit=$(git -C "${repo}" rev-parse '@{upstream}')
    counts=$(git -C "${repo}" rev-list --left-right --count 'HEAD...@{upstream}')
    ahead=${counts%%[[:space:]]*}
    behind=${counts##*[[:space:]]}
fi
if [ -n "$(git -C "${repo}" status --porcelain=v1 --untracked-files=no)" ]; then
    tracked_dirty=1
else
    tracked_dirty=0
fi
sync_sha256=$(sha256sum "${sync_script}" | awk '{print $1}')

printf 'branch\t%s\n' "${branch}"
printf 'commit\t%s\n' "${commit}"
printf 'upstream\t%s\n' "${upstream}"
printf 'upstream_commit\t%s\n' "${upstream_commit}"
printf 'ahead\t%s\n' "${ahead}"
printf 'behind\t%s\n' "${behind}"
printf 'tracked_dirty\t%s\n' "${tracked_dirty}"
printf 'sync_sha256\t%s\n' "${sync_sha256}"
REMOTE_SCRIPT
    ); then
        echo "ERROR: failed to read OpenSSD provenance from ${openssd_host}." >&2
        return 1
    fi

    openssd_branch=""
    openssd_commit=""
    openssd_upstream=""
    openssd_upstream_commit=""
    openssd_ahead=""
    openssd_behind=""
    openssd_tracked_dirty=""
    openssd_sync_sha256=""
    while IFS=$'\t' read -r key value extra; do
        if [ -n "${extra}" ]; then
            echo "ERROR: malformed OpenSSD provenance for key ${key}." >&2
            return 1
        fi
        case "${key}" in
            branch) openssd_branch=${value} ;;
            commit) openssd_commit=${value} ;;
            upstream) openssd_upstream=${value} ;;
            upstream_commit) openssd_upstream_commit=${value} ;;
            ahead) openssd_ahead=${value} ;;
            behind) openssd_behind=${value} ;;
            tracked_dirty) openssd_tracked_dirty=${value} ;;
            sync_sha256) openssd_sync_sha256=${value} ;;
            *)
                echo "ERROR: unexpected OpenSSD provenance key: ${key}" >&2
                return 1
                ;;
        esac
    done <<< "${remote_output}"

    if [[ ! "${openssd_commit}" =~ ^[0-9a-f]{40}$ \
        || ! "${openssd_upstream_commit}" =~ ^([0-9a-f]{40}|none)$ \
        || ! "${openssd_tracked_dirty}" =~ ^[01]$ \
        || ! "${openssd_sync_sha256}" =~ ^[0-9a-f]{64}$ ]]; then
        echo "ERROR: incomplete or invalid OpenSSD provenance." >&2
        return 1
    fi
}

# Ensure every run uses the same clean OpenSSD source and sync script.
verify_openssd_provenance() {
    read_openssd_provenance
    if [ "${openssd_branch}" != "${baseline_openssd_branch}" ] \
        || [ "${openssd_commit}" != "${baseline_openssd_commit}" ] \
        || [ "${openssd_tracked_dirty}" != "${baseline_openssd_tracked_dirty}" ] \
        || [ "${openssd_sync_sha256}" != "${baseline_openssd_sync_sha256}" ]; then
        echo "ERROR: OpenSSD source provenance changed during the batch." >&2
        echo "Expected: branch=${baseline_openssd_branch} commit=${baseline_openssd_commit} dirty=${baseline_openssd_tracked_dirty} sync=${baseline_openssd_sync_sha256}" >&2
        echo "Actual:   branch=${openssd_branch} commit=${openssd_commit} dirty=${openssd_tracked_dirty} sync=${openssd_sync_sha256}" >&2
        return 1
    fi
}

# Switch to one diagnostic branch, build its module, and verify the result.
switch_and_build() {
    local configuration=$1
    local expected_branch=$2
    local actual_branch
    local module_path="${host_repo}/fs/f2fs/f2fs.ko"

    echo
    echo "============================================================"
    echo "Preparing ${configuration}"
    echo "Target Host branch: ${expected_branch}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    check_host_worktree
    git -C "${host_repo}" show-ref --verify --quiet \
        "refs/heads/${expected_branch}" || {
        echo "ERROR: local Host branch is unavailable: ${expected_branch}" >&2
        return 1
    }
    git -C "${host_repo}" switch "${expected_branch}"

    actual_branch=$(git -C "${host_repo}" branch --show-current)
    if [ "${actual_branch}" != "${expected_branch}" ]; then
        echo "ERROR: Host branch switch failed: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
        return 1
    fi

    "${prepare_script}" "${configuration}"

    if [ ! -r "${module_path}" ]; then
        echo "ERROR: compiled f2fs module is missing: ${module_path}" >&2
        return 1
    fi

    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'branch=%s\n' "${actual_branch}"
        printf 'commit=%s\n' "$(git -C "${host_repo}" rev-parse HEAD)"
        printf 'module_sha256=%s\n' "$(sha256sum "${module_path}" | awk '{print $1}')"
        printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module_path}")"
    } >> "${manifest}"
}

# Run one destructive diagnostic benchmark and preserve its output location.
run_one() {
    local configuration=$1
    local workload=$2
    local repetition=$3
    local sequence=$4
    local label
    local result_path_file
    local run_dir
    local summary_path

    label=$(printf '%02d-%s-group%d-%s' \
        "${sequence}" "${configuration}" "${repetition}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"

    echo
    echo "============================================================"
    echo "Starting ${label}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    verify_openssd_provenance
    sudo env GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${runner}" "${configuration}" "${workload}"

    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: result path was not recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    summary_path="${run_dir}/gc-breakdown-diagnostic-result.txt"
    if [ ! -s "${summary_path}" ]; then
        echo "ERROR: diagnostic summary is missing: ${summary_path}" >&2
        return 1
    fi

    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'configuration=%s\n' "${configuration}"
        printf 'workload=%s\n' "${workload}"
        printf 'repetition=%s\n' "${repetition}"
        printf 'openssd_branch=%s\n' "${openssd_branch}"
        printf 'openssd_commit=%s\n' "${openssd_commit}"
        printf 'openssd_tracked_dirty=%s\n' "${openssd_tracked_dirty}"
        printf 'openssd_sync_sha256=%s\n' "${openssd_sync_sha256}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'summary=%s\n' "${summary_path}"
        printf 'kernel_log=%s\n' "${run_dir}/external-dmesg.log"
    } >> "${manifest}"

    echo "Completed ${label}"
    echo "Result directory: ${run_dir}"
    echo "Summary: ${summary_path}"
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
    echo "Run: ./$(basename -- "${script_path}")" >&2
    exit 1
fi
if [ ! -x "${prepare_script}" ]; then
    echo "ERROR: Host preparation script is unavailable: ${prepare_script}" >&2
    exit 1
fi
if [ ! -x "${runner}" ]; then
    echo "ERROR: diagnostic runner is unavailable: ${runner}" >&2
    exit 1
fi
if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
    echo "ERROR: /dev/nvme0n1 is currently mounted; refusing to switch modules." >&2
    exit 1
fi

check_host_worktree
read_openssd_provenance
if [ "${openssd_branch}" != "${expected_openssd_branch}" ]; then
    echo "ERROR: wrong OpenSSD branch on ${openssd_host}." >&2
    echo "Expected: ${expected_openssd_branch}" >&2
    echo "Actual:   ${openssd_branch}" >&2
    exit 1
fi
if [ "${openssd_tracked_dirty}" != "0" ]; then
    echo "ERROR: OpenSSD repository has tracked local modifications on ${openssd_host}." >&2
    echo "Branch and commit alone would not identify the tested source." >&2
    exit 1
fi
baseline_openssd_branch=${openssd_branch}
baseline_openssd_commit=${openssd_commit}
baseline_openssd_tracked_dirty=${openssd_tracked_dirty}
baseline_openssd_sync_sha256=${openssd_sync_sha256}

nopipeline_branch=exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811
pipeline_branch=exp/diagnostic-mcsgc8t-pipeline-breakdown-20260812
nopipeline_config_blob=$(git -C "${host_repo}" rev-parse "${nopipeline_branch}:.config")
pipeline_config_blob=$(git -C "${host_repo}" rev-parse "${pipeline_branch}:.config")
if [ "${nopipeline_config_blob}" != "${pipeline_config_blob}" ] \
    && ! git -C "${host_repo}" diff --quiet -- .config; then
    echo "ERROR: target branches track different .config files while local .config is modified." >&2
    exit 1
fi

available_bytes=$(df -B1 --output=avail "${script_dir}" | tail -n 1 | tr -d ' ')
if [ "${available_bytes}" -lt "${minimum_free_bytes}" ]; then
    echo "ERROR: at least 15 GiB of free space is required for diagnostic logs." >&2
    echo "Available bytes: ${available_bytes}" >&2
    exit 1
fi

mkdir -p "${batch_dir}"
{
    printf 'batch_id=%s\n' "${batch_id}"
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host_repo=%s\n' "${host_repo}"
    printf 'repeat_count=%s\n' "${repeat_count}"
    printf 'execution_order=no-pipeline-3x-then-pipeline-3x\n'
    printf 'initial_branch=%s\n' "$(git -C "${host_repo}" branch --show-current)"
    printf 'initial_commit=%s\n' "$(git -C "${host_repo}" rev-parse HEAD)"
    printf 'openssd_host=%s\n' "${openssd_host}"
    printf 'openssd_repo=%s\n' "${openssd_repo}"
    printf 'openssd_branch=%s\n' "${openssd_branch}"
    printf 'openssd_commit=%s\n' "${openssd_commit}"
    printf 'openssd_upstream=%s\n' "${openssd_upstream}"
    printf 'openssd_upstream_commit=%s\n' "${openssd_upstream_commit}"
    printf 'openssd_ahead=%s\n' "${openssd_ahead}"
    printf 'openssd_behind=%s\n' "${openssd_behind}"
    printf 'openssd_tracked_dirty=%s\n' "${openssd_tracked_dirty}"
    printf 'openssd_sync_script=%s\n' "${openssd_sync_script}"
    printf 'openssd_sync_sha256=%s\n' "${openssd_sync_sha256}"
} > "${manifest}"

echo "This batch resets and overwrites /dev/nvme0n1 twelve times."
echo "All filesystem data on the namespace will be lost."
echo "Batch directory: ${batch_dir}"
echo "OpenSSD source: ${openssd_host}:${openssd_repo}"
echo "OpenSSD branch: ${openssd_branch}"
echo "OpenSSD commit: ${openssd_commit}"
echo "Estimated duration: about 2 hours 15 minutes, plus two module builds."
echo

sudo -v
(
    while true; do
        sudo -n -v >/dev/null 2>&1 || exit
        sleep 45
    done
) &
sudo_keepalive_pid=$!
trap stop_sudo_keepalive EXIT
trap handle_signal INT TERM

sequence=0
switch_and_build "mcsgc8t-nopipeline" "${nopipeline_branch}"
for repetition in $(seq 1 "${repeat_count}"); do
    sequence=$((sequence + 1))
    run_one "mcsgc8t-nopipeline" bigfile "${repetition}" "${sequence}"
    sequence=$((sequence + 1))
    run_one "mcsgc8t-nopipeline" smallfile "${repetition}" "${sequence}"
done

switch_and_build "mcsgc8t-pipeline" "${pipeline_branch}"
for repetition in $(seq 1 "${repeat_count}"); do
    sequence=$((sequence + 1))
    run_one "mcsgc8t-pipeline" bigfile "${repetition}" "${sequence}"
    sequence=$((sequence + 1))
    run_one "mcsgc8t-pipeline" smallfile "${repetition}" "${sequence}"
done

{
    printf '\ncompleted_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'status=success\n'
    printf 'final_branch=%s\n' "$(git -C "${host_repo}" branch --show-current)"
    printf 'final_commit=%s\n' "$(git -C "${host_repo}" rev-parse HEAD)"
} >> "${manifest}"

echo
echo "All 12 mCSGC8t diagnostic benchmarks completed successfully."
echo "The Host repository remains on ${pipeline_branch}."
echo "Batch manifest: ${manifest}"
