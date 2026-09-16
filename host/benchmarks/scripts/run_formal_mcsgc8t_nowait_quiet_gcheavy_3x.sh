#!/usr/bin/env bash

set -euo pipefail

# Execute an immutable in-memory copy so later repository edits cannot alter a run.
if [ -z "${NOWAIT_GCHEAVY_3X_SNAPSHOT:-}" ]; then
    snapshot_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    snapshot_body=$(<"${snapshot_path}")
    export NOWAIT_GCHEAVY_3X_SNAPSHOT=1
    export NOWAIT_GCHEAVY_3X_SCRIPT_PATH="${snapshot_path}"
    exec /bin/bash -c "${snapshot_body}" "${snapshot_path}" "$@"
fi

SCRIPT_PATH=${NOWAIT_GCHEAVY_3X_SCRIPT_PATH}
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)
ARTIFACT_REPO=$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)
HOST_REPO=/home/xin/work-xie/mcsgc-real/linux-cs
HOST_BRANCH=exp/formal-mcsgc8t-nowait-quiet-20260916
HOST_COMMIT=dec1964f0bf196b1929799119e93393bfa6b79fb
HOST_PREFERRED_TREE=/home/xin/work-xie/mcsgc-real/linux-cs-nowait-quiet-20260916
OPENSSD_HOST=192.168.98.31
OPENSSD_REPO=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
OPENSSD_BRANCH=exp/formal-mcsgc-quiet-20260809
OPENSSD_COMMIT=52831c159c9f7a73f9670c163a6b513750f64b47
DEVICE=/dev/nvme0n1
NVME_CLI=/home/xin/artifact-csgc/host/src/nvme-cli/nvme
NVME_CLI_SHA256=73b9a48e3a183fe14743e6f27ec0adc982d0d27e4a6c8a5a5b9d559a62528563
PREPARE_SCRIPT=${SCRIPT_DIR}/prepare_gc_breakdown_host_module.sh
RUNNER=${SCRIPT_DIR}/run_gc_breakdown_diagnostic.sh
READONLY_FSCK=${SCRIPT_DIR}/offline_fsck_readonly.sh
FSCK_F2FS=/usr/local/sbin/fsck.f2fs
RUNNER_CONFIGURATION=mcsgc8t-nowait-quiet
RESULT_BASE=${SCRIPT_DIR}/outputs-formal-mcsgc8t-nowait-quiet-gcheavy-3x
MINIMUM_FREE_BYTES=$((12 * 1024 * 1024 * 1024))
EXPECTED_RUNS=6
HOST_TREE=""
MODULE_PATH=""
MODULE_SRCVERSION=""
MODULE_SHA256=""
SUDO_KEEPALIVE_PID=""
BATCH_DIR=""
RUNS_TSV=""

# Keep detached execution non-interactive and fail instead of waiting for sudo input.
sudo() {
    command /usr/bin/sudo -n "$@"
}
export -f sudo

# Print the destructive one-command interface and status command.
usage() {
    cat <<EOF
Usage:
  ./$(basename -- "${SCRIPT_PATH}")
  ./$(basename -- "${SCRIPT_PATH}") --dry-run
  ./$(basename -- "${SCRIPT_PATH}") --preflight
  ./$(basename -- "${SCRIPT_PATH}") --status BATCH_DIR

Builds the pinned quiet NOWAIT Host revision once and runs the historical
GC-heavy small-file and big-file workloads three times each. Every run resets,
formats, and overwrites ${DEVICE}. No interactive prompt is used.

Pinned Host:
  ${HOST_BRANCH}@${HOST_COMMIT}

Pinned OpenSSD source:
  ${OPENSSD_BRANCH}@${OPENSSD_COMMIT}
EOF
}

# Emit a counterbalanced order that limits systematic time-drift bias.
print_schedule() {
    cat <<'EOF'
01 repetition=1 workload=smallfile
02 repetition=1 workload=bigfile
03 repetition=2 workload=bigfile
04 repetition=2 workload=smallfile
05 repetition=3 workload=smallfile
06 repetition=3 workload=bigfile
EOF
}

# Abort without discarding artifacts from completed runs.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Show progress using only batch metadata files.
show_status() {
    local batch=$1
    local completed=0
    local status=unknown

    [ -d "${batch}" ] || die "batch directory does not exist: ${batch}"
    if [ -f "${batch}/results.tsv" ]; then
        completed=$(awk 'END {print NR > 0 ? NR - 1 : 0}' "${batch}/results.tsv")
    fi
    if [ -f "${batch}/state.env" ]; then
        status=$(sed -n 's/^batch_status=//p' "${batch}/state.env" | tail -n 1)
    fi
    printf 'batch=%s\nstatus=%s\ncompleted_runs=%s/%s\n' \
        "${batch}" "${status}" "${completed}" "${EXPECTED_RUNS}"
    [ ! -f "${batch}/state.env" ] || sed -n '1,12p' "${batch}/state.env"
}

# Persist enough identity for low-overhead external monitoring.
write_state() {
    local status=$1
    [ -n "${BATCH_DIR}" ] || return 0
    {
        printf 'batch_status=%s\n' "${status}"
        printf 'updated_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'outer_pid=%s\n' "$$"
        printf 'outer_start_ticks=%s\n' "$(awk '{print $22}' "/proc/$$/stat")"
        printf 'expected_runs=%s\n' "${EXPECTED_RUNS}"
        printf 'batch_dir=%s\n' "${BATCH_DIR}"
    } > "${BATCH_DIR}/state.env"
}

# Stop the sudo timestamp helper without touching benchmark processes.
stop_sudo_keepalive() {
    if [ -n "${SUDO_KEEPALIVE_PID}" ] \
        && kill -0 "${SUDO_KEEPALIVE_PID}" 2>/dev/null; then
        kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
        wait "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
    fi
    SUDO_KEEPALIVE_PID=""
}

# Preserve a failed batch state when the outer script exits early.
finalize() {
    local status=$?
    trap - EXIT
    stop_sudo_keepalive
    if [ "${status}" -ne 0 ] && [ -n "${BATCH_DIR}" ] && [ -d "${BATCH_DIR}" ]; then
        write_state failed
        printf 'failed_at=%s\nexit_status=%s\n' \
            "$(date --iso-8601=seconds)" "${status}" > "${BATCH_DIR}/failure.env"
    fi
    exit "${status}"
}
trap finalize EXIT

# Read OpenSSD source identity without changing server 31.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${OPENSSD_HOST}" \
        "repo='${OPENSSD_REPO}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Require the pinned clean OpenSSD source before every destructive run.
verify_openssd_provenance() {
    local output branch commit dirty

    output=$(read_openssd_provenance) \
        || die "failed to read OpenSSD provenance from ${OPENSSD_HOST}"
    branch=$(awk -F= '$1 == "openssd_branch" {print $2}' <<< "${output}")
    commit=$(awk -F= '$1 == "openssd_commit" {print $2}' <<< "${output}")
    dirty=$(awk -F= '$1 == "openssd_tracked_dirty" {print $2}' <<< "${output}")
    [ "${branch}" = "${OPENSSD_BRANCH}" ] \
        || die "wrong OpenSSD branch: expected=${OPENSSD_BRANCH} actual=${branch}"
    [ "${commit}" = "${OPENSSD_COMMIT}" ] \
        || die "wrong OpenSSD commit: expected=${OPENSSD_COMMIT} actual=${commit}"
    [ "${dirty}" = 0 ] || die "OpenSSD source has tracked local modifications"
    printf '%s\n' "${output}"
}

# Resolve the only worktree allowed to hold the pinned NOWAIT Host branch.
resolve_host_tree() {
    local branch_ref=refs/heads/${HOST_BRANCH}
    local worktree_output path status_line
    local -a matches=() live_matches=()

    git -C "${HOST_REPO}" fetch --quiet --prune origin
    git -C "${HOST_REPO}" show-ref --verify --quiet \
        "refs/remotes/origin/${HOST_BRANCH}" \
        || die "missing remote Host branch: origin/${HOST_BRANCH}"
    [ "$(git -C "${HOST_REPO}" rev-parse "origin/${HOST_BRANCH}")" = "${HOST_COMMIT}" ] \
        || die "remote Host branch moved from pinned commit"

    if git -C "${HOST_REPO}" show-ref --verify --quiet "${branch_ref}"; then
        [ "$(git -C "${HOST_REPO}" rev-parse "${branch_ref}")" = "${HOST_COMMIT}" ] \
            || die "local Host branch moved from pinned commit"
    else
        git -C "${HOST_REPO}" branch --track "${HOST_BRANCH}" "origin/${HOST_BRANCH}"
    fi

    worktree_output=$(git -C "${HOST_REPO}" worktree list --porcelain)
    mapfile -t matches < <(awk -v target="${branch_ref}" '
        /^worktree / {path=substr($0, 10); next}
        /^branch / {if (substr($0, 8) == target) print path}
    ' <<< "${worktree_output}")
    for path in "${matches[@]}"; do
        [ ! -d "${path}" ] || live_matches+=("${path}")
    done

    if [ "${#live_matches[@]}" -eq 0 ]; then
        path=${HOST_PREFERRED_TREE}
        [ ! -e "${path}" ] || path="${path}-$(date +%s)"
        git -C "${HOST_REPO}" worktree add --quiet --force "${path}" "${HOST_BRANCH}"
    elif [ "${#live_matches[@]}" -eq 1 ]; then
        path=${live_matches[0]}
    else
        die "multiple live worktrees claim ${HOST_BRANCH}"
    fi

    [ "$(git -C "${path}" rev-parse HEAD)" = "${HOST_COMMIT}" ] \
        || die "Host worktree is not at the pinned commit: ${path}"
    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        [ "${status_line:3}" = .config ] \
            || die "unsupported tracked Host change in ${path}: ${status_line}"
    done < <(git -C "${path}" status --porcelain=v1 --untracked-files=no)
    HOST_TREE=${path}
}

# Build the pinned module once from its committed kernel configuration.
build_host_module() {
    local committed_config_sha build_config_sha

    echo "Building pinned NOWAIT quiet module at $(date --iso-8601=seconds)"
    git -C "${HOST_TREE}" show "${HOST_COMMIT}:.config" > "${HOST_TREE}/.config"
    committed_config_sha=$(sha256sum "${HOST_TREE}/.config" | awk '{print $1}')
    "${PREPARE_SCRIPT}" "${RUNNER_CONFIGURATION}"

    MODULE_PATH=${HOST_TREE}/fs/f2fs/f2fs.ko
    [ -r "${MODULE_PATH}" ] || die "module was not produced: ${MODULE_PATH}"
    build_config_sha=$(sha256sum "${HOST_TREE}/.config" | awk '{print $1}')
    MODULE_SHA256=$(sha256sum "${MODULE_PATH}" | awk '{print $1}')
    MODULE_SRCVERSION=$(modinfo -F srcversion "${MODULE_PATH}")
    [ -n "${MODULE_SRCVERSION}" ] || die "module has no srcversion"

    {
        printf '\n[host-build]\n'
        printf 'host_tree=%s\nhost_branch=%s\nhost_commit=%s\n' \
            "${HOST_TREE}" "${HOST_BRANCH}" "${HOST_COMMIT}"
        printf 'committed_config_sha256=%s\nbuild_config_sha256=%s\n' \
            "${committed_config_sha}" "${build_config_sha}"
        printf 'module_path=%s\nmodule_sha256=%s\nmodule_srcversion=%s\n' \
            "${MODULE_PATH}" "${MODULE_SHA256}" "${MODULE_SRCVERSION}"
    } >> "${BATCH_DIR}/manifest.txt"
}

# Load the prebuilt module and verify the live srcversion.
load_host_module() {
    local loaded_srcversion

    findmnt -rn -S "${DEVICE}" >/dev/null \
        && die "${DEVICE} is mounted before module replacement"
    if lsmod | awk '$1 == "f2fs" {found=1} END {exit !found}'; then
        sudo rmmod f2fs
    fi
    sudo insmod "${MODULE_PATH}"
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    [ "${loaded_srcversion^^}" = "${MODULE_SRCVERSION^^}" ] \
        || die "loaded f2fs module does not match the pinned build"
}

# Reject a completed run containing a high-confidence kernel or device failure.
check_kernel_anomalies() {
    local run_dir=$1
    local input=${run_dir}/external-dmesg.log
    local output=${run_dir}/kernel-anomalies.log
    local pattern='kernel BUG at|BUG: unable to handle|Oops:|kernel panic|NULL pointer dereference|SBI_NEED_FSCK|EUCLEAN|SIT[^[:space:]]* (mismatch|inconsistent|corrupt)|Inconsistent segment.*SSA and SIT|nvme[^[:space:]]*.*(timeout|reset controller)|I/O error|refcount_t: (underflow|saturated)|negative refcount|INFO: task .* blocked for more than|Fail to do csgc'

    [ -s "${input}" ] || die "external kernel log is missing: ${input}"
    if grep -Eai "${pattern}" "${input}" > "${output}"; then
        die "kernel or device anomaly detected; see ${output}"
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Extract aggregate write bandwidth, runtime, transferred bytes, and fio status.
extract_fio_metrics() {
    local fio_log=$1

    awk -F: '
        /"error"/ && !seen_error {
            value=$2; gsub(/[, ]/, "", value); error=value; seen_error=1
        }
        /"io_bytes"/ {
            value=$2; gsub(/[, ]/, "", value); if (value + 0 > io_bytes) io_bytes=value + 0
        }
        /"bw_bytes"/ {
            value=$2; gsub(/[, ]/, "", value); if (value + 0 > bw_bytes) bw_bytes=value + 0
        }
        /"runtime"/ {
            value=$2; gsub(/[, ]/, "", value); if (value + 0 > runtime_ms) runtime_ms=value + 0
        }
        END {
            if (!seen_error || bw_bytes <= 0 || io_bytes <= 0 || runtime_ms <= 0) exit 2
            printf "%.6f\t%.6f\t%.6f\t%s\n", bw_bytes / 1048576, \
                runtime_ms / 1000, io_bytes / 1073741824, error
        }
    ' "${fio_log}"
}

# Run a strict offline consistency check after the benchmark unmounts the device.
run_offline_fsck() {
    local label=$1
    local log_path=${BATCH_DIR}/${label}.fsck.log

    ! findmnt -rn -S "${DEVICE}" >/dev/null \
        || die "cannot run fsck while ${DEVICE} is mounted"
    FSCK_F2FS="${FSCK_F2FS}" \
        "${READONLY_FSCK}" "${DEVICE}" "${log_path}" 900 20000 8 \
        || die "read-only offline fsck failed for ${label}"
}

# Run one destructive workload and persist its metrics and provenance.
run_one() {
    local sequence=$1 repetition=$2 workload=$3
    local label result_path_file run_dir fio_log metrics
    local bw_mib_s runtime_s io_gib fio_error openssd_provenance

    label=$(printf '%02d-rep%d-%s' "${sequence}" "${repetition}" "${workload}")
    result_path_file=${BATCH_DIR}/${label}.result-path
    write_state "running-${label}"
    echo
    echo "============================================================"
    echo "Starting ${label} at $(date --iso-8601=seconds)"
    echo "============================================================"

    openssd_provenance=$(verify_openssd_provenance)
    load_host_module
    sudo env \
        GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        KERNEL_PANIC_TIMEOUT=0 \
        NVME_CLI="${NVME_CLI}" \
        "${RUNNER}" "${RUNNER_CONFIGURATION}" "${workload}"

    [ -s "${result_path_file}" ] || die "result path was not recorded for ${label}"
    run_dir=$(<"${result_path_file}")
    fio_log=${run_dir}/fio.log
    [ -s "${fio_log}" ] || die "fio log is missing for ${label}: ${fio_log}"
    [ -s "${run_dir}/gc-breakdown-diagnostic-result.txt" ] \
        || die "measured-window summary is missing for ${label}"
    check_kernel_anomalies "${run_dir}"

    metrics=$(extract_fio_metrics "${fio_log}") \
        || die "failed to extract fio metrics for ${label}"
    IFS=$'\t' read -r bw_mib_s runtime_s io_gib fio_error <<< "${metrics}"
    [ "${fio_error}" = 0 ] || die "fio reported error=${fio_error} for ${label}"
    run_offline_fsck "${label}"

    ln -s "${run_dir}" "${BATCH_DIR}/${label}.run"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${sequence}" "${repetition}" "${workload}" "${bw_mib_s}" \
        "${runtime_s}" "${io_gib}" "${fio_error}" "${run_dir}" >> "${RUNS_TSV}"
    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\nsequence=%s\nrepetition=%s\nworkload=%s\n' \
            "$(date --iso-8601=seconds)" "${sequence}" "${repetition}" "${workload}"
        printf 'host_branch=%s\nhost_commit=%s\nmodule_sha256=%s\n' \
            "${HOST_BRANCH}" "${HOST_COMMIT}" "${MODULE_SHA256}"
        printf '%s\n' "${openssd_provenance}"
        printf 'run_dir=%s\nfio_bw_mib_s=%s\nfio_runtime_s=%s\nfio_io_gib=%s\n' \
            "${run_dir}" "${bw_mib_s}" "${runtime_s}" "${io_gib}"
        printf 'fio_error=%s\nfsck=pass\n' "${fio_error}"
    } >> "${BATCH_DIR}/manifest.txt"
    echo "Completed ${label}: ${bw_mib_s} MiB/s; fsck=pass"
}

# Generate per-workload statistics and compare against the old quiet candidates.
generate_summary() {
    python3 - "${RUNS_TSV}" "${BATCH_DIR}/summary.tsv" "${BATCH_DIR}/summary.md" <<'PY'
import csv
import statistics
import sys
from collections import defaultdict

runs_path, tsv_path, markdown_path = sys.argv[1:]
old_references = {
    "bigfile": (401.324, "Rolling-final quiet"),
    "smallfile": (427.130, "Conflict-aware quiet"),
}
groups = defaultdict(list)

with open(runs_path, newline="", encoding="utf-8") as source:
    rows = list(csv.DictReader(source, delimiter="\t"))
if len(rows) != 6:
    raise SystemExit(f"expected 6 completed runs, found {len(rows)}")
for row in rows:
    groups[row["workload"]].append(float(row["bw_mib_s"]))
if set(groups) != {"bigfile", "smallfile"} or any(len(values) != 3 for values in groups.values()):
    raise SystemExit("completed runs do not form two groups of three")

summary = []
for workload in ("bigfile", "smallfile"):
    values = groups[workload]
    reference, reference_name = old_references[workload]
    summary.append({
        "workload": workload,
        "runs": len(values),
        "mean_mib_s": statistics.mean(values),
        "median_mib_s": statistics.median(values),
        "min_mib_s": min(values),
        "max_mib_s": max(values),
        "stdev_mib_s": statistics.stdev(values),
        "old_reference_mib_s": reference,
        "old_reference": reference_name,
        "mean_vs_old": statistics.mean(values) / reference,
    })

with open(tsv_path, "w", newline="", encoding="utf-8") as output:
    writer = csv.DictWriter(output, fieldnames=list(summary[0]), delimiter="\t")
    writer.writeheader()
    writer.writerows(summary)

with open(markdown_path, "w", encoding="utf-8") as output:
    output.write("# NOWAIT Quiet GC-heavy 3x Results\n\n")
    output.write("| Workload | Runs | Mean MiB/s | Median | Min | Max | Stddev | Old quiet reference | Mean ratio |\n")
    output.write("|---|---:|---:|---:|---:|---:|---:|---|---:|\n")
    for row in summary:
        output.write(
            f"| {row['workload']} | {row['runs']} | {row['mean_mib_s']:.3f} "
            f"| {row['median_mib_s']:.3f} | {row['min_mib_s']:.3f} "
            f"| {row['max_mib_s']:.3f} | {row['stdev_mib_s']:.3f} "
            f"| {row['old_reference']} {row['old_reference_mib_s']:.3f} MiB/s "
            f"| {row['mean_vs_old']:.4f}x |\n"
        )
PY
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    --dry-run)
        print_schedule
        exit 0
        ;;
    --preflight)
        PREFLIGHT_ONLY=1
        ;;
    --status)
        [ "$#" -eq 2 ] || { usage; exit 2; }
        show_status "$(realpath "$2")"
        exit 0
        ;;
    '')
        PREFLIGHT_ONLY=0
        ;;
    *)
        usage
        exit 2
        ;;
esac

[ "${EUID}" -ne 0 ] || die "run this outer script as the login user"
command -v flock >/dev/null || die "flock is unavailable"
[ -x "${FSCK_F2FS}" ] || die "project fsck.f2fs is unavailable: ${FSCK_F2FS}"
[ -x "${READONLY_FSCK}" ] || die "missing read-only fsck helper"
[ -x "${NVME_CLI}" ] || die "pinned nvme-cli is unavailable: ${NVME_CLI}"
[ "$(sha256sum "${NVME_CLI}" | awk '{print $1}')" = "${NVME_CLI_SHA256}" ] \
    || die "pinned nvme-cli hash does not match"
[ -x "${PREPARE_SCRIPT}" ] || die "missing Host preparation script"
[ -x "${RUNNER}" ] || die "missing benchmark runner"
[ -b "${DEVICE}" ] || die "block device is unavailable: ${DEVICE}"
findmnt -rn -S "${DEVICE}" >/dev/null && die "${DEVICE} is currently mounted"
if [ -r /sys/module/f2fs/refcnt ] && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
    die "f2fs still has active references"
fi
sudo -n true || die "passwordless sudo is unavailable"
verify_openssd_provenance >/dev/null
resolve_host_tree

if [ "${PREFLIGHT_ONLY}" -eq 1 ]; then
    echo "Preflight passed. Host and OpenSSD source revisions match the pinned versions."
    echo "Running firmware identity remains limited to source and build-input provenance."
    exit 0
fi

mkdir -p "${RESULT_BASE}"
exec 9>"${RESULT_BASE}/matrix.lock"
flock -n 9 || die "another NOWAIT GC-heavy matrix is already running"
available_bytes=$(df -B1 --output=avail "${RESULT_BASE}" | tail -n 1 | tr -d ' ')
[ "${available_bytes}" -ge "${MINIMUM_FREE_BYTES}" ] \
    || die "at least 12 GiB of free result space is required"

BATCH_DIR=${RESULT_BASE}/$(date +%Y%m%d_%H%M%S)
RUNS_TSV=${BATCH_DIR}/results.tsv
mkdir -p "${BATCH_DIR}"
printf '%s\n' "${BATCH_DIR}" > "${RESULT_BASE}/latest-batch.txt"
printf 'sequence\trepetition\tworkload\tbw_mib_s\truntime_s\tio_gib\tfio_error\trun_dir\n' \
    > "${RUNS_TSV}"
exec > >(tee -a "${BATCH_DIR}/runner.log") 2>&1

openssd_provenance=$(verify_openssd_provenance)
{
    printf 'batch_id=%s\nstarted_at=%s\n' \
        "$(basename "${BATCH_DIR}")" "$(date --iso-8601=seconds)"
    printf 'artifact_branch=%s\nartifact_commit=%s\nartifact_tracked_dirty=%s\n' \
        "$(git -C "${ARTIFACT_REPO}" branch --show-current)" \
        "$(git -C "${ARTIFACT_REPO}" rev-parse HEAD)" \
        "$([ -n "$(git -C "${ARTIFACT_REPO}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf 'script_sha256=%s\nrun_count=%s\nssd_thread_mode=ssd1t\n' \
        "$(sha256sum "${SCRIPT_PATH}" | awk '{print $1}')" "${EXPECTED_RUNS}"
    printf 'kernel_panic_timeout_s=0\nfsck_after_each_run=1\n'
    printf 'fsck_mode=force-full-dry-run\nfsck_timeout_s=900\nfsck_max_log_lines=20000\n'
    printf 'nvme_cli=%s\nnvme_cli_sha256=%s\n' \
        "${NVME_CLI}" "${NVME_CLI_SHA256}"
    printf 'smallfile_config=%s\n' \
        "${SCRIPT_DIR}/configs/config24_fio_formal_performance_16t26336file.sh"
    printf 'bigfile_config=%s\n' \
        "${SCRIPT_DIR}/configs/config25_fio_formal_performance_bigfile_randwrite.sh"
    printf 'old_bigfile_reference=Rolling-final quiet 401.324 MiB/s\n'
    printf 'old_smallfile_reference=Conflict-aware quiet 427.130 MiB/s\n'
    printf '%s\n' "${openssd_provenance}"
    printf 'execution_schedule_begin\n'
    print_schedule
    printf 'execution_schedule_end\n'
} > "${BATCH_DIR}/manifest.txt"

echo "DESTRUCTIVE WARNING: this batch resets and overwrites ${DEVICE} six times."
echo "Starting without an interactive confirmation prompt."
echo "Batch directory: ${BATCH_DIR}"
echo "Expected duration: approximately 80-100 minutes, including build and fsck."
write_state preparing

sudo -v
(
    while sleep 45; do
        sudo -n -v >/dev/null 2>&1 || exit
    done
) &
SUDO_KEEPALIVE_PID=$!

build_host_module

schedule=(
    "1 1 smallfile"
    "2 1 bigfile"
    "3 2 bigfile"
    "4 2 smallfile"
    "5 3 smallfile"
    "6 3 bigfile"
)
for entry in "${schedule[@]}"; do
    read -r sequence repetition workload <<< "${entry}"
    run_one "${sequence}" "${repetition}" "${workload}"
done

generate_summary
write_state completed
printf 'completed_at=%s\nstatus=success\nsummary=%s\n' \
    "$(date --iso-8601=seconds)" "${BATCH_DIR}/summary.md" >> "${BATCH_DIR}/manifest.txt"
echo
echo "All six NOWAIT quiet GC-heavy benchmarks completed successfully."
echo "Summary: ${BATCH_DIR}/summary.md"
