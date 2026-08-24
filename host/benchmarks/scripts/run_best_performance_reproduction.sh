#!/usr/bin/env bash

set -euo pipefail

# Reproduce the four runs used by the current headline comparison. The SSD
# firmware must be rebuilt between phases, so the batch is deliberately
# resumable across a Host reboot.

script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
output_root="${script_dir}/outputs-best-performance-reproduction"
active_batch_file="${output_root}/active-batch"
latest_batch_file="${output_root}/latest-batch"
minimum_free_bytes=$((15 * 1024 * 1024 * 1024))

original_host_branch=exp/formal-csgc-original-quiet-20260809
original_host_commit=813c35f3ec81bc317c2ca82d796e9a767ad6384e
original_host_worktree=/tmp/linux-cs-reproduce-original-csgc-20260824
original_ssd_branch=formal-original-csgc-main-20260809
original_ssd_commit=463e8b0b13ad345ed99c2176b1f81ad34d3c986a

best_host_branch=exp/diagnostic-mcsgc8t-conflict-aware-supply-20260819
best_host_commit=62f0a68a891bf39e14398e5d08a083ee79fe73fe
best_host_worktree=/tmp/linux-cs-reproduce-best-mcsgc8t-20260824
best_ssd_branch=exp/formal-mcsgc-quiet-20260809
best_ssd_commit=52831c159c9f7a73f9670c163a6b513750f64b47

formal_prepare="${script_dir}/prepare_formal_host_module.sh"
formal_runner="${script_dir}/run_formal_performance_test.sh"
diagnostic_prepare="${script_dir}/prepare_gc_breakdown_host_module.sh"
diagnostic_runner="${script_dir}/run_gc_breakdown_diagnostic.sh"

sudo_keepalive_pid=""
batch_dir=""
manifest=""

usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}") <command>

Commands:
  original   Create a new batch and run original CSGC bigfile + smallfile.
  best       Resume the active batch and run current-best bigfile + smallfile.
  status     Show the active or most recently completed batch.

Run this script as the normal login user, not through sudo. It requests sudo
itself. Invoking original or best authorizes that phase to reset, format, and
overwrite /dev/nvme0n1 twice without an additional confirmation prompt.
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

stop_sudo_keepalive() {
    if [ -n "${sudo_keepalive_pid}" ] \
        && kill -0 "${sudo_keepalive_pid}" 2>/dev/null; then
        kill "${sudo_keepalive_pid}" 2>/dev/null || true
        wait "${sudo_keepalive_pid}" 2>/dev/null || true
    fi
    sudo_keepalive_pid=""
}

handle_signal() {
    stop_sudo_keepalive
    if [ -n "${manifest}" ]; then
        printf '\ninterrupted_at=%s\n' "$(date --iso-8601=seconds)" \
            >> "${manifest}" 2>/dev/null || true
    fi
    echo "Interrupted. Existing results remain in ${batch_dir:-${output_root}}." >&2
    exit 130
}

trap stop_sudo_keepalive EXIT
trap handle_signal INT TERM

read_state() {
    local state_file=$1
    [ -r "${state_file}" ] || die "state file is missing: ${state_file}"
    tr -d '\r\n' < "${state_file}"
}

show_status() {
    local selected=""

    if [ -s "${active_batch_file}" ]; then
        selected=$(<"${active_batch_file}")
        echo "Active batch: ${selected}"
    elif [ -s "${latest_batch_file}" ]; then
        selected=$(<"${latest_batch_file}")
        echo "Latest batch: ${selected}"
    else
        echo "No reproduction batch has been recorded."
        return 0
    fi

    if [ -d "${selected}" ]; then
        echo "State: $(read_state "${selected}/state")"
        echo "Manifest: ${selected}/manifest.txt"
        [ ! -r "${selected}/comparison.tsv" ] \
            || echo "Comparison: ${selected}/comparison.tsv"
        [ ! -r "${selected}/summary.md" ] \
            || echo "Summary: ${selected}/summary.md"
    else
        die "recorded batch directory no longer exists: ${selected}"
    fi
}

check_common_prerequisites() {
    local available_bytes

    [ "${EUID}" -ne 0 ] \
        || die "run the outer script as the normal login user, without sudo"
    [ -d "${host_repo}/.git" ] || die "Host repository is unavailable: ${host_repo}"
    [ -d "${artifact_repo}/.git" ] \
        || die "Artifact repository is unavailable: ${artifact_repo}"
    [ -x "${formal_prepare}" ] || die "missing script: ${formal_prepare}"
    [ -x "${formal_runner}" ] || die "missing script: ${formal_runner}"
    [ -x "${diagnostic_prepare}" ] || die "missing script: ${diagnostic_prepare}"
    [ -x "${diagnostic_runner}" ] || die "missing script: ${diagnostic_runner}"
    command -v ssh >/dev/null || die "ssh is unavailable"
    command -v fio >/dev/null || die "fio is unavailable"
    command -v modinfo >/dev/null || die "modinfo is unavailable"

    [ -b /dev/nvme0n1 ] || die "/dev/nvme0n1 is not available"
    if findmnt -rn -o SOURCE \
        | awk '$0 ~ "^/dev/nvme0n1(p[0-9]+)?$" { found = 1 } END { exit !found }'; then
        die "/dev/nvme0n1 or one of its partitions is mounted"
    fi
    if pgrep -x fio >/dev/null; then
        die "another fio process is running"
    fi

    mkdir -p "${output_root}"
    available_bytes=$(df -B1 --output=avail "${script_dir}" | tail -n 1 | tr -d ' ')
    [ "${available_bytes}" -ge "${minimum_free_bytes}" ] \
        || die "at least 15 GiB free space is required for logs"
}

announce_destructive_phase() {
    local phase=$1

    echo
    echo "DESTRUCTIVE WARNING"
    echo "Phase '${phase}' resets and overwrites /dev/nvme0n1 twice."
    echo "All filesystem data on this namespace will be lost."
    echo "Starting immediately without an interactive confirmation prompt."
}

start_sudo_keepalive() {
    sudo -v
    (
        while true; do
            sudo -n -v >/dev/null 2>&1 || exit
            sleep 45
        done
    ) &
    sudo_keepalive_pid=$!
}

verify_openssd_source() {
    local expected_branch=$1
    local expected_commit=$2
    local phase=$3
    local output
    local branch=""
    local commit=""
    local tracked_dirty=""
    local sync_sha256=""
    local key value extra

    if ! output=$(ssh \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes \
        "${openssd_host}" bash -s -- "${openssd_repo}" <<'REMOTE'
set -eu
repo=$1
sync_script="${repo}/scripts/sync_code.sh"

[ -d "${repo}/.git" ] || {
    echo "Remote error: repository is unavailable: ${repo}" >&2
    exit 20
}
[ -r "${sync_script}" ] || {
    echo "Remote error: sync script is unavailable: ${sync_script}" >&2
    exit 21
}

branch=$(git -C "${repo}" symbolic-ref --quiet --short HEAD || printf DETACHED)
commit=$(git -C "${repo}" rev-parse HEAD)
if [ -n "$(git -C "${repo}" status --porcelain=v1 --untracked-files=no)" ]; then
    tracked_dirty=1
else
    tracked_dirty=0
fi
sync_sha256=$(sha256sum "${sync_script}" | awk '{print $1}')

printf 'branch\t%s\n' "${branch}"
printf 'commit\t%s\n' "${commit}"
printf 'tracked_dirty\t%s\n' "${tracked_dirty}"
printf 'sync_sha256\t%s\n' "${sync_sha256}"
REMOTE
    ); then
        die "cannot read OpenSSD provenance from ${openssd_host}"
    fi

    while IFS=$'\t' read -r key value extra; do
        [ -z "${extra}" ] || die "malformed OpenSSD provenance for ${key}"
        case "${key}" in
            branch) branch=${value} ;;
            commit) commit=${value} ;;
            tracked_dirty) tracked_dirty=${value} ;;
            sync_sha256) sync_sha256=${value} ;;
            *) die "unexpected OpenSSD provenance key: ${key}" ;;
        esac
    done <<< "${output}"

    [ "${branch}" = "${expected_branch}" ] || die \
        "wrong OpenSSD branch on server 31: expected=${expected_branch} actual=${branch}"
    [ "${commit}" = "${expected_commit}" ] || die \
        "wrong OpenSSD commit on server 31: expected=${expected_commit} actual=${commit}"
    [ "${tracked_dirty}" = "0" ] || die \
        "OpenSSD repository on server 31 has tracked local modifications"

    {
        printf '\n[openssd-%s]\n' "${phase}"
        printf 'checked_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host=%s\n' "${openssd_host}"
        printf 'repo=%s\n' "${openssd_repo}"
        printf 'branch=%s\n' "${branch}"
        printf 'commit=%s\n' "${commit}"
        printf 'tracked_dirty=%s\n' "${tracked_dirty}"
        printf 'sync_script_sha256=%s\n' "${sync_sha256}"
    } >> "${manifest}"

    echo "Validated OpenSSD source: ${branch}@${commit}"
    echo "Important: source validation cannot prove which firmware binary is running."
}

ensure_exact_host_worktree() {
    local branch=$1
    local commit=$2
    local preferred_path=$3
    local branch_ref="refs/heads/${branch}"
    local worktree_output
    local -a matches
    local tree
    local status_line path

    # Callers capture stdout as the resolved path, so all Git diagnostics must
    # stay off stdout.
    git -C "${host_repo}" fetch --quiet --prune origin >&2
    git -C "${host_repo}" show-ref --verify --quiet \
        "refs/remotes/origin/${branch}" \
        || die "required remote Host branch is unavailable: origin/${branch}"
    [ "$(git -C "${host_repo}" rev-parse "origin/${branch}")" = "${commit}" ] \
        || die "remote Host branch no longer points to the pinned commit: ${branch}"
    git -C "${host_repo}" cat-file -e "${commit}^{commit}" 2>/dev/null \
        || die "required Host commit is unavailable locally: ${commit}"

    if git -C "${host_repo}" show-ref --verify --quiet "${branch_ref}"; then
        [ "$(git -C "${host_repo}" rev-parse "${branch_ref}")" = "${commit}" ] \
            || die "local Host branch does not point to pinned commit: ${branch}"
    else
        git -C "${host_repo}" branch --track "${branch}" "origin/${branch}" >&2 \
            || die "failed to create local Host branch ${branch}"
        [ "$(git -C "${host_repo}" rev-parse "${branch_ref}")" = "${commit}" ] \
            || die "remote Host branch does not point to pinned commit: ${branch}"
    fi

    worktree_output=$(git -C "${host_repo}" worktree list --porcelain)
    mapfile -t matches < <(
        awk -v target="${branch_ref}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / {
                branch = substr($0, 8)
                if (branch == target)
                    print path
            }
        ' <<< "${worktree_output}"
    )

    if [ "${#matches[@]}" -eq 0 ]; then
        [ ! -e "${preferred_path}" ] \
            || die "preferred Host worktree path already exists: ${preferred_path}"
        git -C "${host_repo}" worktree add --quiet \
            "${preferred_path}" "${branch}" >&2
        tree=${preferred_path}
    elif [ "${#matches[@]}" -eq 1 ]; then
        tree=${matches[0]}
    else
        die "more than one worktree claims Host branch ${branch}"
    fi

    [ "$(git -C "${tree}" rev-parse HEAD)" = "${commit}" ] \
        || die "Host worktree does not contain pinned commit: ${tree}"

    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        path=${status_line:3}
        [ "${path}" = ".config" ] || die \
            "unsupported tracked Host worktree change in ${tree}: ${status_line}"
    done < <(git -C "${tree}" status --porcelain=v1 --untracked-files=no)

    printf '%s\n' "${tree}"
}

seed_generated_headers() {
    local tree=$1
    local source_config

    if [ ! -r "${tree}/.config" ]; then
        source_config="${host_repo}/.config"
        [ -r "${source_config}" ] || source_config="/boot/config-$(uname -r)"
        [ -r "${source_config}" ] \
            || die "no kernel configuration is available for ${tree}"
        cp "${source_config}" "${tree}/.config"
    fi

    (
        cd "${tree}"
        ./scripts/config --enable F2FS_STAT_FS
        make -s olddefconfig LOCALVERSION=-csgcmt
        make -s prepare modules_prepare LOCALVERSION=-csgcmt
    )
}

record_host_build() {
    local phase=$1
    local tree=$2
    local module="${tree}/fs/f2fs/f2fs.ko"

    [ -r "${module}" ] || die "Host module was not produced: ${module}"
    {
        printf '\n[host-%s]\n' "${phase}"
        printf 'built_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'tree=%s\n' "${tree}"
        printf 'branch=%s\n' "$(git -C "${tree}" branch --show-current)"
        printf 'commit=%s\n' "$(git -C "${tree}" rev-parse HEAD)"
        printf 'config_sha256=%s\n' "$(sha256sum "${tree}/.config" | awk '{print $1}')"
        printf 'module_sha256=%s\n' "$(sha256sum "${module}" | awk '{print $1}')"
        printf 'module_srcversion=%s\n' "$(modinfo -F srcversion "${module}")"
    } >> "${manifest}"
}

prepare_original_host() {
    local tree
    tree=$(ensure_exact_host_worktree \
        "${original_host_branch}" "${original_host_commit}" \
        "${original_host_worktree}")
    seed_generated_headers "${tree}"
    "${formal_prepare}" original-csgc
    record_host_build original "${tree}"
}

prepare_best_host() {
    local tree
    tree=$(ensure_exact_host_worktree \
        "${best_host_branch}" "${best_host_commit}" \
        "${best_host_worktree}")
    seed_generated_headers "${tree}"
    "${diagnostic_prepare}" mcsgc8t-conflict-aware-supply
    record_host_build best "${tree}"
}

find_new_formal_run() {
    local started_epoch=$1
    local root="${script_dir}/outputs-csgc-original-formal-ssd1t"
    local top
    local run_dir

    top=$(
        find "${root}" -mindepth 1 -maxdepth 1 -type d \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -nr | head -n 1 | cut -d' ' -f2- || true
    )
    [ -n "${top}" ] || die "failed to locate original CSGC output"
    [ "$(stat -c %Y "${top}")" -ge "${started_epoch}" ] \
        || die "new original CSGC output directory was not created"
    run_dir=$(find "${top}" -mindepth 1 -maxdepth 1 -type d \
        -name 'fio_*' -print -quit)
    [ -n "${run_dir}" ] || die "fio result directory is missing under ${top}"
    printf '%s\n' "${run_dir}"
}

extract_fio_metrics() {
    local run_dir=$1
    local output_file=$2

    python3 - "${run_dir}/fio.log" "${output_file}" <<'PY'
import json
import sys
from pathlib import Path

fio_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
text = fio_path.read_text(errors="replace")
json_start = text.find("{")
if json_start < 0:
    raise SystemExit(f"fio JSON object is missing: {fio_path}")
try:
    document, _ = json.JSONDecoder().raw_decode(text[json_start:])
except json.JSONDecodeError as error:
    raise SystemExit(f"invalid fio JSON in {fio_path}: {error}") from error

jobs = document.get("jobs", [])
if not jobs:
    raise SystemExit(f"fio jobs are missing: {fio_path}")
if any(int(job.get("error", 0)) != 0 for job in jobs):
    raise SystemExit(f"fio did not report a clean error=0 result in {fio_path}")

bw_bytes_s = sum(float(job.get("write", {}).get("bw_bytes", 0)) for job in jobs)
iops = sum(float(job.get("write", {}).get("iops", 0)) for job in jobs)
io_bytes = sum(int(job.get("write", {}).get("io_bytes", 0)) for job in jobs)
runtime_ms = max(int(job.get("write", {}).get("runtime", 0)) for job in jobs)
if bw_bytes_s <= 0:
    raise SystemExit(f"no nonzero write bandwidth found in {fio_path}")

output_path.write_text(
    f"fio_bw_mib_s={bw_bytes_s / (1024 * 1024):.3f}\n"
    f"fio_iops={iops:.3f}\n"
    f"fio_gib_written={io_bytes / (1024 ** 3):.3f}\n"
    f"fio_runtime_s={runtime_ms / 1000.0:.3f}\n"
    "fio_error=0\n"
)
PY
}

record_run() {
    local label=$1
    local configuration=$2
    local workload=$3
    local run_dir=$4
    local metrics_file="${batch_dir}/${label}.metrics"
    local stable_link="${batch_dir}/${label}"

    [ -s "${run_dir}/fio.log" ] || die "fio log is missing: ${run_dir}/fio.log"
    [ -s "${run_dir}/terminal.log" ] \
        || die "terminal log is missing: ${run_dir}/terminal.log"
    [ -s "${run_dir}/dmesg.log" ] || die "kernel log is missing: ${run_dir}/dmesg.log"
    extract_fio_metrics "${run_dir}" "${metrics_file}"

    if [ -e "${stable_link}" ] || [ -L "${stable_link}" ]; then
        die "stable result link already exists: ${stable_link}"
    fi
    ln -s "${run_dir}" "${stable_link}"
    {
        printf '\n[run-%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'configuration=%s\n' "${configuration}"
        printf 'workload=%s\n' "${workload}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'fio_log=%s\n' "${run_dir}/fio.log"
        printf 'terminal_log=%s\n' "${run_dir}/terminal.log"
        printf 'kernel_log=%s\n' "${run_dir}/dmesg.log"
        cat "${metrics_file}"
    } >> "${manifest}"

    echo "Completed ${label}: $(tr '\n' ' ' < "${metrics_file}")"
    echo "Result directory: ${run_dir}"
}

run_is_complete() {
    local label=$1
    local metrics_file="${batch_dir}/${label}.metrics"
    local stable_link="${batch_dir}/${label}"

    if [ -s "${metrics_file}" ] && [ -L "${stable_link}" ] \
        && [ -d "${stable_link}" ]; then
        return 0
    fi
    if [ -e "${metrics_file}" ] || [ -L "${stable_link}" ]; then
        die "incomplete saved result for ${label}; inspect ${batch_dir}"
    fi
    return 1
}

run_original_one() {
    local workload=$1
    local label="original-csgc-${workload}"
    local started_epoch
    local run_dir

    if run_is_complete "${label}"; then
        echo "Skipping completed run: ${label}"
        return 0
    fi
    verify_openssd_source "${original_ssd_branch}" "${original_ssd_commit}" \
        "${label}"
    started_epoch=$(date +%s)
    sudo "${formal_runner}" original-csgc "${workload}"
    run_dir=$(find_new_formal_run "${started_epoch}")
    record_run "${label}" original-csgc "${workload}" "${run_dir}"
}

run_best_one() {
    local workload=$1
    local label="best-conflict-aware-${workload}"
    local result_path_file="${batch_dir}/${label}.result-path"
    local run_dir

    if run_is_complete "${label}"; then
        echo "Skipping completed run: ${label}"
        return 0
    fi
    verify_openssd_source "${best_ssd_branch}" "${best_ssd_commit}" "${label}"
    sudo env GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${diagnostic_runner}" mcsgc8t-conflict-aware-supply "${workload}"
    [ -s "${result_path_file}" ] \
        || die "diagnostic runner did not record its result path"
    run_dir=$(<"${result_path_file}")
    [ -s "${run_dir}/external-dmesg.log" ] \
        || die "external diagnostic kernel log is missing: ${run_dir}"
    [ -s "${run_dir}/gc-breakdown-diagnostic-result.txt" ] \
        || die "diagnostic summary is missing: ${run_dir}"
    record_run "${label}" best-conflict-aware "${workload}" "${run_dir}"
}

generate_comparison() {
    local original_big original_small best_big best_small

    original_big=$(awk -F= '$1 == "fio_bw_mib_s" { print $2 }' \
        "${batch_dir}/original-csgc-bigfile.metrics")
    original_small=$(awk -F= '$1 == "fio_bw_mib_s" { print $2 }' \
        "${batch_dir}/original-csgc-smallfile.metrics")
    best_big=$(awk -F= '$1 == "fio_bw_mib_s" { print $2 }' \
        "${batch_dir}/best-conflict-aware-bigfile.metrics")
    best_small=$(awk -F= '$1 == "fio_bw_mib_s" { print $2 }' \
        "${batch_dir}/best-conflict-aware-smallfile.metrics")

    {
        printf 'configuration\tworkload\tbw_mib_s\trelative_to_original\n'
        printf 'original-csgc\tbigfile\t%s\t1.000\n' "${original_big}"
        printf 'original-csgc\tsmallfile\t%s\t1.000\n' "${original_small}"
        awk -v bw="${best_big}" -v base="${original_big}" \
            'BEGIN { printf "best-conflict-aware\tbigfile\t%s\t%.3f\n", bw, bw / base }'
        awk -v bw="${best_small}" -v base="${original_small}" \
            'BEGIN { printf "best-conflict-aware\tsmallfile\t%s\t%.3f\n", bw, bw / base }'
    } > "${batch_dir}/comparison.tsv"

    cat > "${batch_dir}/summary.md" <<EOF
# CSGC best-performance reproduction

| Configuration | Workload | fio bandwidth (MiB/s) | Relative to original CSGC |
|---|---|---:|---:|
| Original CSGC | Big file | ${original_big} | 1.000x |
| Original CSGC | Small files | ${original_small} | 1.000x |
| Current best conflict-aware | Big file | ${best_big} | $(awk -v a="${best_big}" -v b="${original_big}" 'BEGIN { printf "%.3f", a / b }')x |
| Current best conflict-aware | Small files | ${best_small} | $(awk -v a="${best_small}" -v b="${original_small}" 'BEGIN { printf "%.3f", a / b }')x |

The original baseline is the quiet formal build, while the current-best run is
the exact diagnostic build that produced the reported 423.723/426.853 MiB/s
results. This reproduces the reported configurations; it is not a same-overhead
causal A/B experiment.
EOF
}

initialize_batch() {
    local batch_id

    if [ -s "${active_batch_file}" ]; then
        local existing
        existing=$(<"${active_batch_file}")
        [ -d "${existing}" ] \
            || die "active batch directory is missing: ${existing}"
        [ "$(read_state "${existing}/state")" = "running-original" ] \
            || die "active batch is not in the original phase: ${existing}"
        batch_dir=${existing}
        manifest="${batch_dir}/manifest.txt"
        [ -s "${manifest}" ] || die "active batch manifest is missing: ${manifest}"
        {
            printf '\noriginal_phase_resumed_at=%s\n' "$(date --iso-8601=seconds)"
            printf 'resumed_artifact_branch=%s\n' \
                "$(git -C "${artifact_repo}" branch --show-current)"
            printf 'resumed_artifact_commit=%s\n' \
                "$(git -C "${artifact_repo}" rev-parse HEAD)"
            printf 'resumed_script_sha256=%s\n' \
                "$(sha256sum "${script_path}" | awk '{print $1}')"
        } >> "${manifest}"
        return 0
    fi

    batch_id=$(date +"%Y%m%d_%H%M%S")
    batch_dir="${output_root}/${batch_id}"
    mkdir -p "${batch_dir}"
    printf '%s\n' "${batch_dir}" > "${active_batch_file}"
    printf '%s\n' "${batch_dir}" > "${latest_batch_file}"
    printf 'running-original\n' > "${batch_dir}/state"
    manifest="${batch_dir}/manifest.txt"
    {
        printf 'batch_id=%s\n' "${batch_id}"
        printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'artifact_repo=%s\n' "${artifact_repo}"
        printf 'artifact_branch=%s\n' "$(git -C "${artifact_repo}" branch --show-current)"
        printf 'artifact_commit=%s\n' "$(git -C "${artifact_repo}" rev-parse HEAD)"
        printf 'script_sha256=%s\n' "$(sha256sum "${script_path}" | awk '{print $1}')"
        printf 'execution_order=original-bigfile,original-smallfile,best-bigfile,best-smallfile\n'
    } > "${manifest}"
}

resume_batch_for_best() {
    local state

    [ -s "${active_batch_file}" ] \
        || die "no active original phase exists; run the original phase first"
    batch_dir=$(<"${active_batch_file}")
    [ -d "${batch_dir}" ] || die "active batch directory is missing: ${batch_dir}"
    manifest="${batch_dir}/manifest.txt"
    state=$(read_state "${batch_dir}/state")
    case "${state}" in
        awaiting-best-firmware|running-best)
            ;;
        *)
            die "batch is not ready for the best phase: ${state}"
            ;;
    esac
    for required in \
        original-csgc-bigfile.metrics \
        original-csgc-smallfile.metrics; do
        [ -s "${batch_dir}/${required}" ] \
            || die "original phase output is incomplete: ${required}"
    done
    printf 'running-best\n' > "${batch_dir}/state"
}

run_original_phase() {
    initialize_batch
    exec > >(tee -a "${batch_dir}/original-phase-console.log") 2>&1
    echo "Batch directory: ${batch_dir}"
    announce_destructive_phase original
    start_sudo_keepalive
    verify_openssd_source "${original_ssd_branch}" "${original_ssd_commit}" \
        original-preflight
    prepare_original_host
    run_original_one bigfile
    run_original_one smallfile
    printf 'awaiting-best-firmware\n' > "${batch_dir}/state"
    {
        printf '\noriginal_phase_completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'next_action=switch_and_rebuild_best_openssd_then_run_best_phase\n'
    } >> "${manifest}"
    echo
    echo "Original phase completed."
    echo "Switch and rebuild OpenSSD at ${best_ssd_branch}@${best_ssd_commit},"
    echo "restart the Host if needed, then run:"
    echo "  ./$(basename -- "${script_path}") best"
}

run_best_phase() {
    resume_batch_for_best
    exec > >(tee -a "${batch_dir}/best-phase-console.log") 2>&1
    echo "Resuming batch: ${batch_dir}"
    announce_destructive_phase best
    start_sudo_keepalive
    verify_openssd_source "${best_ssd_branch}" "${best_ssd_commit}" best-preflight
    prepare_best_host
    run_best_one bigfile
    run_best_one smallfile
    generate_comparison
    printf 'complete\n' > "${batch_dir}/state"
    {
        printf '\ncompleted_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'status=success\n'
        printf 'comparison=%s\n' "${batch_dir}/comparison.tsv"
        printf 'summary=%s\n' "${batch_dir}/summary.md"
    } >> "${manifest}"
    : > "${active_batch_file}"
    echo
    echo "All four reproduction runs completed."
    echo "Comparison: ${batch_dir}/comparison.tsv"
    echo "Summary: ${batch_dir}/summary.md"
    echo "Manifest: ${manifest}"
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    status)
        [ "$#" -eq 1 ] || die "status does not accept additional arguments"
        show_status
        ;;
    original)
        [ "$#" -eq 1 ] || die "original does not accept additional arguments"
        check_common_prerequisites
        run_original_phase
        ;;
    best)
        [ "$#" -eq 1 ] || die "best does not accept additional arguments"
        check_common_prerequisites
        run_best_phase
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
