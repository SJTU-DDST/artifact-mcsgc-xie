#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so repository updates cannot alter a live run.
if [ -z "${GC_CONFLICT_ROLLING_QUIET_3X_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_CONFLICT_ROLLING_QUIET_3X_SNAPSHOT=1
    export GC_CONFLICT_ROLLING_QUIET_3X_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_CONFLICT_ROLLING_QUIET_3X_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
expected_openssd_branch=exp/formal-mcsgc-quiet-20260809
expected_openssd_commit=52831c159c9f7a73f9670c163a6b513750f64b47
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
repeat_count=3
minimum_free_bytes=$((15 * 1024 * 1024 * 1024))
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-conflict-rolling-quiet-3x/${batch_id}"
manifest="${batch_dir}/manifest.txt"
runs_tsv="${batch_dir}/results.tsv"
summary_tsv="${batch_dir}/summary.tsv"
summary_md="${batch_dir}/summary.md"
sudo_keepalive_pid=""

declare -A branches=(
    [conflict-aware]=exp/formal-mcsgc8t-conflict-aware-lifecycle-quiet-20260825
    [rolling-final]=exp/formal-mcsgc8t-rolling-lifecycle-quiet-20260825
)
declare -A commits=(
    [conflict-aware]=972958bd18a61516ee2cee2218a8d46bb746fa98
    [rolling-final]=9575a1f861278b91afdbf0d3e60324e571b2430e
)
declare -A base_commits=(
    [conflict-aware]=76e57c02caeafd4e27ad488b51c3385dfb9973c6
    [rolling-final]=441f1b1f6449e4f9ea2e2b35c401669a9e29b4d3
)
declare -A runner_configs=(
    [conflict-aware]=mcsgc8t-conflict-aware-lifecycle-quiet
    [rolling-final]=mcsgc8t-rolling-lifecycle-quiet
)
declare -A preferred_worktrees=(
    [conflict-aware]=/tmp/linux-cs-conflict-quiet-20260825
    [rolling-final]=/tmp/linux-cs-rolling-quiet-20260825
)
declare -A host_trees=()
declare -A module_paths=()
declare -A module_srcversions=()
declare -A committed_config_sha256s=()
declare -A build_config_sha256s=()

# Print the one-command interface without touching the SSD or repositories.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}") [--dry-run]

Builds these pinned quiet lifecycle-fixed Host revisions once:
  conflict-aware  972958bd18a61516ee2cee2218a8d46bb746fa98
  rolling-final   9575a1f861278b91afdbf0d3e60324e571b2430e

It first executes all six smallfile runs, then all six bigfile runs. Each
revision and workload is repeated three times, for 12 destructive benchmarks
in total. Each benchmark resets and reformats /dev/nvme0n1. Run this outer
script as the normal login user; it invokes sudo internally and does not ask
for an additional confirmation.

Use tmux for resilience during the approximately 2 hour 15 minute batch:
  tmux new-session -s csgc-quiet-3x './$(basename -- "${script_path}")'
EOF
}

# Print the counterbalanced execution order used by the full batch.
print_schedule() {
    cat <<'EOF'
01 repetition=1 configuration=conflict-aware workload=smallfile
02 repetition=1 configuration=rolling-final  workload=smallfile
03 repetition=2 configuration=rolling-final  workload=smallfile
04 repetition=2 configuration=conflict-aware workload=smallfile
05 repetition=3 configuration=conflict-aware workload=smallfile
06 repetition=3 configuration=rolling-final  workload=smallfile
07 repetition=1 configuration=conflict-aware workload=bigfile
08 repetition=1 configuration=rolling-final  workload=bigfile
09 repetition=2 configuration=rolling-final  workload=bigfile
10 repetition=2 configuration=conflict-aware workload=bigfile
11 repetition=3 configuration=conflict-aware workload=bigfile
12 repetition=3 configuration=rolling-final  workload=bigfile
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

# Preserve completed metadata and stop only the outer batch on a signal.
handle_signal() {
    stop_sudo_keepalive
    echo "Interrupted. Completed run metadata remains in ${batch_dir}." >&2
    exit 130
}

# Abort with a concise error while preserving all completed batch artifacts.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Read the OpenSSD source revision without modifying the remote server.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Require the same clean OpenSSD source before every destructive run.
verify_openssd_provenance() {
    local output
    local branch
    local commit
    local dirty

    output=$(read_openssd_provenance) \
        || die "failed to read OpenSSD provenance from ${openssd_host}"
    branch=$(awk -F= '$1 == "openssd_branch" { print $2 }' <<< "${output}")
    commit=$(awk -F= '$1 == "openssd_commit" { print $2 }' <<< "${output}")
    dirty=$(awk -F= '$1 == "openssd_tracked_dirty" { print $2 }' <<< "${output}")

    [ "${branch}" = "${expected_openssd_branch}" ] \
        || die "wrong OpenSSD branch: expected=${expected_openssd_branch} actual=${branch}"
    [ "${commit}" = "${expected_openssd_commit}" ] \
        || die "wrong OpenSSD commit: expected=${expected_openssd_commit} actual=${commit}"
    [ "${dirty}" = "0" ] \
        || die "OpenSSD repository has tracked local modifications"

    printf '%s\n' "${output}"
}

# Resolve or create one worktree at the exact pinned Host branch and commit.
ensure_exact_host_worktree() {
    local configuration=$1
    local branch=${branches[${configuration}]}
    local commit=${commits[${configuration}]}
    local preferred_path=${preferred_worktrees[${configuration}]}
    local branch_ref="refs/heads/${branch}"
    local worktree_output
    local -a matches
    local tree
    local status_line
    local path

    git -C "${host_repo}" fetch --quiet --prune origin
    git -C "${host_repo}" show-ref --verify --quiet \
        "refs/remotes/origin/${branch}" \
        || die "required remote Host branch is unavailable: origin/${branch}"
    [ "$(git -C "${host_repo}" rev-parse "origin/${branch}")" = "${commit}" ] \
        || die "remote Host branch no longer points to pinned commit: ${branch}"

    if git -C "${host_repo}" show-ref --verify --quiet "${branch_ref}"; then
        [ "$(git -C "${host_repo}" rev-parse "${branch_ref}")" = "${commit}" ] \
            || die "local Host branch does not point to pinned commit: ${branch}"
    else
        git -C "${host_repo}" branch --track "${branch}" "origin/${branch}"
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
        git -C "${host_repo}" worktree add --quiet "${preferred_path}" "${branch}"
        tree=${preferred_path}
    elif [ "${#matches[@]}" -eq 1 ]; then
        tree=${matches[0]}
    else
        die "more than one worktree claims Host branch ${branch}"
    fi

    [ "$(git -C "${tree}" rev-parse HEAD)" = "${commit}" ] \
        || die "Host worktree does not contain pinned commit: ${tree}"
    [ "$(git -C "${tree}" rev-parse "${commit}^")" \
        = "${base_commits[${configuration}]}" ] \
        || die "repaired Host commit is not based directly on the expected historical revision"

    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        path=${status_line:3}
        [ "${path}" = ".config" ] \
            || die "unsupported tracked Host worktree change in ${tree}: ${status_line}"
    done < <(git -C "${tree}" status --porcelain=v1 --untracked-files=no)

    host_trees[${configuration}]=${tree}
}

# Build one pinned Host module from its committed configuration.
build_configuration() {
    local configuration=$1
    local runner_configuration=${runner_configs[${configuration}]}
    local commit=${commits[${configuration}]}
    local tree=${host_trees[${configuration}]}
    local module_path="${tree}/fs/f2fs/f2fs.ko"
    local committed_config_sha256
    local config_sha256
    local module_sha256
    local module_srcversion

    echo "Building ${configuration} from ${commit} at $(date --iso-8601=seconds)"

    # Reset only the generated kernel configuration to the pinned Git version.
    git -C "${tree}" show "${commit}:.config" > "${tree}/.config"
    committed_config_sha256=$(sha256sum "${tree}/.config" | awk '{print $1}')
    "${prepare_script}" "${runner_configuration}"

    [ -r "${module_path}" ] \
        || die "compiled f2fs module is unavailable: ${module_path}"
    config_sha256=$(sha256sum "${tree}/.config" | awk '{print $1}')
    module_sha256=$(sha256sum "${module_path}" | awk '{print $1}')
    module_srcversion=$(modinfo -F srcversion "${module_path}")
    [ -n "${module_srcversion}" ] \
        || die "compiled module has no srcversion: ${module_path}"

    module_paths[${configuration}]=${module_path}
    module_srcversions[${configuration}]=${module_srcversion}
    committed_config_sha256s[${configuration}]=${committed_config_sha256}
    build_config_sha256s[${configuration}]=${config_sha256}
    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\n' "${tree}"
        printf 'host_branch=%s\n' "${branches[${configuration}]}"
        printf 'host_commit=%s\n' "${commit}"
        printf 'host_base_commit=%s\n' "${base_commits[${configuration}]}"
        printf 'committed_config_sha256=%s\n' "${committed_config_sha256}"
        printf 'build_config_sha256=%s\n' "${config_sha256}"
        printf 'module_sha256=%s\n' "${module_sha256}"
        printf 'module_srcversion=%s\n' "${module_srcversion}"
    } >> "${manifest}"
}

# Replace the unloaded F2FS module with one prebuilt configuration.
load_configuration() {
    local configuration=$1
    local module_path=${module_paths[${configuration}]}
    local expected_srcversion=${module_srcversions[${configuration}]}
    local loaded_srcversion

    findmnt -rn -S /dev/nvme0n1 >/dev/null \
        && die "/dev/nvme0n1 is mounted before module replacement"
    if lsmod | awk '$1 == "f2fs" { found = 1 } END { exit !found }'; then
        sudo rmmod f2fs
    fi
    sudo insmod "${module_path}"
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    [ "${loaded_srcversion^^}" = "${expected_srcversion^^}" ] \
        || die "loaded f2fs srcversion does not match ${configuration}"
    echo "Loaded ${configuration}: srcversion=${loaded_srcversion}"
}

# Reject a completed run containing a high-confidence kernel failure.
check_kernel_anomalies() {
    local run_dir=$1
    local input="${run_dir}/external-dmesg.log"
    local output="${run_dir}/kernel-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|Oops:|SBI_NEED_FSCK|EUCLEAN|SIT[^[:space:]]* (mismatch|inconsistent|corrupt)|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount'

    [ -s "${input}" ] || die "external kernel log is missing: ${input}"
    if grep -Eai "${pattern}" "${input}" > "${output}"; then
        die "kernel anomaly detected; see ${output}"
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Extract aggregate write metrics from fio's combined normal and JSON output.
extract_fio_metrics() {
    local fio_log=$1

    awk -F: '
        /"error"/ && !seen_error {
            value = $2
            gsub(/[, ]/, "", value)
            error = value
            seen_error = 1
        }
        /"io_bytes"/ {
            value = $2
            gsub(/[, ]/, "", value)
            if (value + 0 > io_bytes)
                io_bytes = value + 0
        }
        /"bw_bytes"/ {
            value = $2
            gsub(/[, ]/, "", value)
            if (value + 0 > bw_bytes)
                bw_bytes = value + 0
        }
        /"runtime"/ {
            value = $2
            gsub(/[, ]/, "", value)
            if (value + 0 > runtime_ms)
                runtime_ms = value + 0
        }
        END {
            if (!seen_error || bw_bytes <= 0 || io_bytes <= 0 || runtime_ms <= 0)
                exit 2
            printf "%.6f\t%.6f\t%.6f\t%s\n", \
                bw_bytes / 1048576, runtime_ms / 1000, \
                io_bytes / 1073741824, error
        }
    ' "${fio_log}"
}

# Run one destructive benchmark and append its metrics and provenance.
run_one() {
    local sequence=$1
    local repetition=$2
    local configuration=$3
    local workload=$4
    local runner_configuration=${runner_configs[${configuration}]}
    local label
    local result_path_file
    local run_dir
    local fio_log
    local metrics
    local bw_mib_s
    local runtime_s
    local io_gib
    local fio_error
    local openssd_provenance

    label=$(printf '%02d-rep%d-%s-%s' \
        "${sequence}" "${repetition}" "${configuration}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"

    echo
    echo "============================================================"
    echo "Starting ${label}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    openssd_provenance=$(verify_openssd_provenance)
    load_configuration "${configuration}"
    sudo env GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${runner}" "${runner_configuration}" "${workload}"

    [ -s "${result_path_file}" ] \
        || die "result path was not recorded for ${label}"
    run_dir=$(<"${result_path_file}")
    fio_log="${run_dir}/fio.log"
    [ -s "${fio_log}" ] || die "fio log is missing for ${label}: ${fio_log}"
    [ -s "${run_dir}/gc-breakdown-diagnostic-result.txt" ] \
        || die "measured-window marker summary is missing for ${label}"
    check_kernel_anomalies "${run_dir}"

    metrics=$(extract_fio_metrics "${fio_log}") \
        || die "failed to extract fio metrics for ${label}"
    IFS=$'\t' read -r bw_mib_s runtime_s io_gib fio_error <<< "${metrics}"
    [ "${fio_error}" = "0" ] || die "fio reported error=${fio_error} for ${label}"

    ln -s "${run_dir}" "${batch_dir}/${label}.run"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${sequence}" "${repetition}" "${configuration}" "${workload}" \
        "${bw_mib_s}" "${runtime_s}" "${io_gib}" "${fio_error}" \
        "${run_dir}" >> "${runs_tsv}"
    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'sequence=%s\nrepetition=%s\n' "${sequence}" "${repetition}"
        printf 'configuration=%s\nworkload=%s\n' "${configuration}" "${workload}"
        printf 'host_branch=%s\nhost_commit=%s\n' \
            "${branches[${configuration}]}" "${commits[${configuration}]}"
        printf 'host_base_commit=%s\n' "${base_commits[${configuration}]}"
        printf '%s\n' "${openssd_provenance}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'fio_bw_mib_s=%s\nfio_runtime_s=%s\nfio_io_gib=%s\n' \
            "${bw_mib_s}" "${runtime_s}" "${io_gib}"
        printf 'fio_error=%s\n' "${fio_error}"
    } >> "${manifest}"

    echo "Completed ${label}: ${bw_mib_s} MiB/s"
    echo "Result directory: ${run_dir}"
}

# Generate aggregate statistics and the direct Rolling/Conflict-aware ratios.
generate_summary() {
    python3 - "${runs_tsv}" "${summary_tsv}" "${summary_md}" <<'PY'
import csv
import statistics
import sys
from collections import defaultdict

runs_path, tsv_path, markdown_path = sys.argv[1:]
groups = defaultdict(list)

with open(runs_path, newline="", encoding="utf-8") as source:
    reader = csv.DictReader(source, delimiter="\t")
    rows = list(reader)

if len(rows) != 12:
    raise SystemExit(f"expected 12 completed runs, found {len(rows)}")

for row in rows:
    groups[(row["configuration"], row["workload"])].append(
        float(row["bw_mib_s"])
    )

expected = {
    (configuration, workload)
    for configuration in ("conflict-aware", "rolling-final")
    for workload in ("bigfile", "smallfile")
}
if set(groups) != expected or any(len(values) != 3 for values in groups.values()):
    raise SystemExit("completed runs do not form four groups of three")

statistics_rows = []
for configuration in ("conflict-aware", "rolling-final"):
    for workload in ("bigfile", "smallfile"):
        values = groups[(configuration, workload)]
        statistics_rows.append({
            "configuration": configuration,
            "workload": workload,
            "count": len(values),
            "mean_mib_s": statistics.mean(values),
            "median_mib_s": statistics.median(values),
            "min_mib_s": min(values),
            "max_mib_s": max(values),
            "stdev_mib_s": statistics.stdev(values),
        })

with open(tsv_path, "w", newline="", encoding="utf-8") as output:
    fieldnames = list(statistics_rows[0])
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    writer.writerows(statistics_rows)

lookup = {
    (row["configuration"], row["workload"]): row
    for row in statistics_rows
}
with open(markdown_path, "w", encoding="utf-8") as output:
    output.write("# Quiet Conflict-aware and Rolling-final 3x Results\n\n")
    output.write("| Configuration | Workload | Runs | Mean MiB/s | Median MiB/s | Min | Max | Stddev |\n")
    output.write("|---|---|---:|---:|---:|---:|---:|---:|\n")
    for row in statistics_rows:
        output.write(
            f"| {row['configuration']} | {row['workload']} | {row['count']} "
            f"| {row['mean_mib_s']:.3f} | {row['median_mib_s']:.3f} "
            f"| {row['min_mib_s']:.3f} | {row['max_mib_s']:.3f} "
            f"| {row['stdev_mib_s']:.3f} |\n"
        )

    output.write("\n## Rolling-final Relative Performance\n\n")
    output.write("| Workload | Mean ratio | Median ratio |\n")
    output.write("|---|---:|---:|\n")
    for workload in ("bigfile", "smallfile"):
        control = lookup[("conflict-aware", workload)]
        rolling = lookup[("rolling-final", workload)]
        output.write(
            f"| {workload} "
            f"| {rolling['mean_mib_s'] / control['mean_mib_s']:.4f}x "
            f"| {rolling['median_mib_s'] / control['median_mib_s']:.4f}x |\n"
        )
PY
}

if [ "$#" -eq 1 ] && { [ "$1" = "-h" ] || [ "$1" = "--help" ]; }; then
    usage
    exit 0
fi
if [ "$#" -eq 1 ] && [ "$1" = "--dry-run" ]; then
    usage
    echo
    echo "Execution schedule:"
    print_schedule
    exit 0
fi
if [ "$#" -ne 0 ]; then
    usage >&2
    exit 1
fi
if [ "${EUID}" -eq 0 ]; then
    die "run this outer script as the login user; it invokes sudo internally"
fi

command -v flock >/dev/null || die "flock is unavailable"
[ -x "${prepare_script}" ] || die "Host preparation script is unavailable: ${prepare_script}"
[ -x "${runner}" ] || die "benchmark runner is unavailable: ${runner}"
[ -d "${host_repo}/.git" ] || die "Host repository is unavailable: ${host_repo}"
findmnt -rn -S /dev/nvme0n1 >/dev/null \
    && die "/dev/nvme0n1 is currently mounted"
if [ -r /sys/module/f2fs/refcnt ] && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
    die "f2fs still has active references"
fi

exec 9>/tmp/run_gc_conflict_rolling_quiet_3x.lock
flock -n 9 || die "another quiet conflict/rolling 3x batch is already running"

available_bytes=$(df -B1 --output=avail "${script_dir}" | tail -n 1 | tr -d ' ')
[ "${available_bytes}" -ge "${minimum_free_bytes}" ] \
    || die "at least 15 GiB of free log space is required"

mkdir -p "${batch_dir}"
printf 'sequence\trepetition\tconfiguration\tworkload\tbw_mib_s\truntime_s\tio_gib\tfio_error\trun_dir\n' \
    > "${runs_tsv}"
openssd_provenance=$(verify_openssd_provenance)
{
    printf 'batch_id=%s\nstarted_at=%s\n' \
        "${batch_id}" "$(date --iso-8601=seconds)"
    printf 'host=%s\nartifact_branch=%s\nartifact_commit=%s\n' \
        "$(hostname)" "$(git -C "${artifact_repo}" branch --show-current)" \
        "$(git -C "${artifact_repo}" rev-parse HEAD)"
    printf 'artifact_tracked_dirty=%s\n' \
        "$([ -n "$(git -C "${artifact_repo}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf 'script_sha256=%s\n' "$(sha256sum "${script_path}" | awk '{print $1}')"
    printf 'repeat_count=%s\nrun_count=12\nssd_thread_mode=ssd1t\n' "${repeat_count}"
    printf '%s\n' "${openssd_provenance}"
    printf 'execution_schedule_begin\n'
    print_schedule
    printf 'execution_schedule_end\n'
} > "${manifest}"

echo "DESTRUCTIVE WARNING: this batch resets and overwrites /dev/nvme0n1 twelve times."
echo "Starting without an interactive confirmation prompt."
echo "Batch directory: ${batch_dir}"
echo "Expected duration: approximately 2 hours 15 minutes, plus module builds."

trap stop_sudo_keepalive EXIT
trap handle_signal INT TERM
sudo -v
(
    while sleep 45; do
        sudo -n true || exit
    done
) &
sudo_keepalive_pid=$!

ensure_exact_host_worktree conflict-aware
ensure_exact_host_worktree rolling-final
build_configuration conflict-aware
build_configuration rolling-final
[ "${committed_config_sha256s[conflict-aware]}" \
    = "${committed_config_sha256s[rolling-final]}" ] \
    || die "pinned Host commits contain different kernel configurations"
[ "${build_config_sha256s[conflict-aware]}" \
    = "${build_config_sha256s[rolling-final]}" ] \
    || die "Host build configurations differ after olddefconfig"

schedule=(
    "1 1 conflict-aware smallfile"
    "2 1 rolling-final smallfile"
    "3 2 rolling-final smallfile"
    "4 2 conflict-aware smallfile"
    "5 3 conflict-aware smallfile"
    "6 3 rolling-final smallfile"
    "7 1 conflict-aware bigfile"
    "8 1 rolling-final bigfile"
    "9 2 rolling-final bigfile"
    "10 2 conflict-aware bigfile"
    "11 3 conflict-aware bigfile"
    "12 3 rolling-final bigfile"
)

for entry in "${schedule[@]}"; do
    read -r sequence repetition configuration workload <<< "${entry}"
    run_one "${sequence}" "${repetition}" "${configuration}" "${workload}"
done

generate_summary
{
    printf '\ncompleted_at=%s\nstatus=success\n' "$(date --iso-8601=seconds)"
    printf 'results_tsv=%s\nsummary_tsv=%s\nsummary_md=%s\n' \
        "${runs_tsv}" "${summary_tsv}" "${summary_md}"
} >> "${manifest}"

echo
echo "All 12 quiet conflict-aware and rolling-final benchmarks completed successfully."
echo "Batch manifest: ${manifest}"
echo "Aggregate summary: ${summary_md}"
