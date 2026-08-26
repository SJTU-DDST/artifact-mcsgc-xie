#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so repository changes cannot alter a live
# twelve-run batch.
if [ -z "${FORMAL_ORIGINAL_MATRIX_3X_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export FORMAL_ORIGINAL_MATRIX_3X_SNAPSHOT=1
    export FORMAL_ORIGINAL_MATRIX_3X_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${FORMAL_ORIGINAL_MATRIX_3X_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
host_branch=exp/formal-csgc-original-quiet-20260809
host_commit=813c35f3ec81bc317c2ca82d796e9a767ad6384e
preferred_host_worktree=/tmp/linux-cs-formal-original-matrix-20260826
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
openssd_branch=formal-original-csgc-main-20260809
openssd_commit=463e8b0b13ad345ed99c2176b1f81ad34d3c986a
prepare_script="${script_dir}/prepare_formal_host_module.sh"
runner="${script_dir}/run_formal_performance_test.sh"
repeat_count=3
minimum_free_bytes=$((15 * 1024 * 1024 * 1024))
batch_id=$(date +"%Y%m%d_%H%M%S")
output_root="${script_dir}/outputs-formal-original-matrix-3x"
batch_dir="${output_root}/${batch_id}"
latest_batch_file="${output_root}/latest-batch"
manifest="${batch_dir}/manifest.txt"
runs_tsv="${batch_dir}/results.tsv"
summary_tsv="${batch_dir}/summary.tsv"
summary_md="${batch_dir}/summary.md"
state_file="${batch_dir}/state"
host_tree=""
module_path=""
module_sha256=""
module_srcversion=""
sudo_keepalive_pid=""
batch_initialized=0
batch_complete=0

# Print the non-interactive command interface without changing repositories or
# touching the SSD.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}") [--dry-run | --status]

The default command builds the pinned original Host module once and runs:
  original CSGC smallfile x3
  original CSGC bigfile   x3
  original ORI  smallfile x3
  original ORI  bigfile   x3

All twelve runs reset, format, and overwrite /dev/nvme0n1. Run this outer
script as the normal login user. It uses passwordless sudo internally and does
not display an interactive confirmation prompt.
EOF
}

# Keep the failure-prone CSGC small-file workload first so a lifecycle problem
# is detected before spending time on the remaining configurations.
print_schedule() {
    cat <<'EOF'
01 repetition=1 configuration=original-csgc workload=smallfile
02 repetition=2 configuration=original-csgc workload=smallfile
03 repetition=3 configuration=original-csgc workload=smallfile
04 repetition=1 configuration=original-csgc workload=bigfile
05 repetition=2 configuration=original-csgc workload=bigfile
06 repetition=3 configuration=original-csgc workload=bigfile
07 repetition=1 configuration=original-ori  workload=smallfile
08 repetition=2 configuration=original-ori  workload=smallfile
09 repetition=3 configuration=original-ori  workload=smallfile
10 repetition=1 configuration=original-ori  workload=bigfile
11 repetition=2 configuration=original-ori  workload=bigfile
12 repetition=3 configuration=original-ori  workload=bigfile
EOF
}

# Stop the sudo timestamp refresher without touching benchmark processes.
stop_sudo_keepalive() {
    if [ -n "${sudo_keepalive_pid}" ] \
        && kill -0 "${sudo_keepalive_pid}" 2>/dev/null; then
        kill "${sudo_keepalive_pid}" 2>/dev/null || true
        wait "${sudo_keepalive_pid}" 2>/dev/null || true
    fi
    sudo_keepalive_pid=""
}

# Preserve completed artifacts and mark an incomplete batch on every exit.
handle_exit() {
    local status=$?

    stop_sudo_keepalive
    if [ "${batch_initialized}" -eq 1 ] && [ "${batch_complete}" -eq 0 ]; then
        {
            printf 'status=failed-or-interrupted\n'
            printf 'exit_status=%s\n' "${status}"
            printf 'ended_at=%s\n' "$(date --iso-8601=seconds)"
        } > "${state_file}" 2>/dev/null || true
        {
            printf '\nended_at=%s\n' "$(date --iso-8601=seconds)"
            printf 'status=failed-or-interrupted\n'
            printf 'exit_status=%s\n' "${status}"
        } >> "${manifest}" 2>/dev/null || true
    fi
}

# Abort while retaining every completed result in the batch directory.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Display progress from the most recently started batch.
show_status() {
    local latest

    if [ ! -s "${latest_batch_file}" ]; then
        echo "No original ORI/CSGC matrix batch has been recorded."
        return 0
    fi
    latest=$(<"${latest_batch_file}")
    [ -d "${latest}" ] || die "recorded batch directory is unavailable: ${latest}"
    echo "Batch directory: ${latest}"
    if [ -r "${latest}/state" ]; then
        echo "State:"
        sed 's/^/  /' "${latest}/state"
    fi
    if [ -r "${latest}/results.tsv" ]; then
        echo "Completed runs: $(( $(wc -l < "${latest}/results.tsv") - 1 ))/12"
    fi
    [ ! -r "${latest}/summary.md" ] \
        || echo "Summary: ${latest}/summary.md"
}

# Read and validate the original OpenSSD source tree without changing server
# 31. This records source provenance but cannot prove the loaded ELF hash.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; printf 'openssd_branch=%s\\nopenssd_commit=%s\\nopenssd_tracked_dirty=%s\\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\""
}

# Fail closed if the server-31 source revision changes during the matrix.
verify_openssd_provenance() {
    local output
    local actual_branch
    local actual_commit
    local tracked_dirty

    output=$(read_openssd_provenance) \
        || die "failed to read OpenSSD provenance from ${openssd_host}"
    actual_branch=$(awk -F= '$1 == "openssd_branch" { print $2 }' <<< "${output}")
    actual_commit=$(awk -F= '$1 == "openssd_commit" { print $2 }' <<< "${output}")
    tracked_dirty=$(awk -F= '$1 == "openssd_tracked_dirty" { print $2 }' <<< "${output}")

    [ "${actual_branch}" = "${openssd_branch}" ] \
        || die "wrong OpenSSD branch: expected=${openssd_branch} actual=${actual_branch}"
    [ "${actual_commit}" = "${openssd_commit}" ] \
        || die "wrong OpenSSD commit: expected=${openssd_commit} actual=${actual_commit}"
    [ "${tracked_dirty}" = "0" ] \
        || die "OpenSSD source tree has tracked local modifications"
    printf '%s\n' "${output}"
}

# Resolve or create exactly one live worktree at the pinned Host revision.
ensure_exact_host_worktree() {
    local branch_ref="refs/heads/${host_branch}"
    local worktree_output
    local -a matches
    local -a live_matches=()
    local path

    git -C "${host_repo}" fetch --quiet --prune origin
    git -C "${host_repo}" show-ref --verify --quiet \
        "refs/remotes/origin/${host_branch}" \
        || die "required remote Host branch is unavailable: origin/${host_branch}"
    [ "$(git -C "${host_repo}" rev-parse "origin/${host_branch}")" \
        = "${host_commit}" ] \
        || die "remote Host branch no longer points to the pinned commit"

    if git -C "${host_repo}" show-ref --verify --quiet "${branch_ref}"; then
        [ "$(git -C "${host_repo}" rev-parse "${branch_ref}")" \
            = "${host_commit}" ] \
            || die "local Host branch does not point to the pinned commit"
    else
        git -C "${host_repo}" branch --track "${host_branch}" \
            "origin/${host_branch}"
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
    for path in "${matches[@]}"; do
        [ ! -d "${path}" ] || live_matches+=("${path}")
    done

    if [ "${#live_matches[@]}" -eq 0 ]; then
        path=${preferred_host_worktree}
        if [ -e "${path}" ]; then
            path="${path}-${batch_id}"
        fi
        git -C "${host_repo}" worktree add --quiet --force \
            "${path}" "${host_branch}"
        host_tree=${path}
    elif [ "${#live_matches[@]}" -eq 1 ]; then
        host_tree=${live_matches[0]}
    else
        die "more than one live worktree claims Host branch ${host_branch}"
    fi

    [ "$(git -C "${host_tree}" rev-parse HEAD)" = "${host_commit}" ] \
        || die "resolved Host worktree is not at the pinned commit"
    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        [ "${status_line:3}" = ".config" ] \
            || die "unsupported tracked Host change: ${status_line}"
    done < <(git -C "${host_tree}" status --porcelain=v1 --untracked-files=no)
}

# Build and load the original module once. ORI and original CSGC use the same
# Host binary; only the runtime mode and device L2P mode differ.
build_original_host_module() {
    local source_config

    if [ ! -r "${host_tree}/.config" ]; then
        source_config="${host_repo}/.config"
        [ -r "${source_config}" ] || source_config="/boot/config-$(uname -r)"
        [ -r "${source_config}" ] \
            || die "no running-kernel configuration is available"
        cp "${source_config}" "${host_tree}/.config"
    fi

    "${prepare_script}" original-csgc
    module_path="${host_tree}/fs/f2fs/f2fs.ko"
    [ -r "${module_path}" ] || die "Host module was not produced: ${module_path}"
    module_sha256=$(sha256sum "${module_path}" | awk '{print $1}')
    module_srcversion=$(modinfo -F srcversion "${module_path}")
    [ -n "${module_srcversion}" ] \
        || die "compiled Host module has no srcversion"

    {
        printf '\n[host-build]\n'
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\n' "${host_tree}"
        printf 'host_branch=%s\n' "${host_branch}"
        printf 'host_commit=%s\n' "${host_commit}"
        printf 'config_sha256=%s\n' \
            "$(sha256sum "${host_tree}/.config" | awk '{print $1}')"
        printf 'module_sha256=%s\n' "${module_sha256}"
        printf 'module_srcversion=%s\n' "${module_srcversion}"
    } >> "${manifest}"
}

# Return the formal output root selected by test.sh for one configuration.
formal_output_root() {
    case "$1" in
        original-csgc)
            printf '%s\n' "${script_dir}/outputs-csgc-original-formal-ssd1t"
            ;;
        original-ori)
            printf '%s\n' "${script_dir}/outputs-ori-ssd1t"
            ;;
        *)
            return 1
            ;;
    esac
}

# Return the result-directory pattern generated for one formal workload.
formal_run_dir_pattern() {
    case "$1" in
        smallfile)
            printf '%s\n' 'fio_rw16t26336file_*'
            ;;
        bigfile)
            printf '%s\n' 'fio_randwrite_*'
            ;;
        *)
            return 1
            ;;
    esac
}

# Return the fio job name recorded in the aggregate JSON result.
formal_fio_job_name() {
    case "$1" in
        smallfile)
            printf '%s\n' 'pipeline_partitioned_randwrite'
            ;;
        bigfile)
            printf '%s\n' 'randwrite'
            ;;
        *)
            return 1
            ;;
    esac
}

# Return the measured-window identifiers emitted to the kernel log.
formal_kernel_marker_fields() {
    local configuration=$1
    local workload=$2
    local mode
    local workload_name

    case "${configuration}" in
        original-csgc)
            mode=cs
            ;;
        original-ori)
            mode=ori
            ;;
        *)
            return 1
            ;;
    esac
    case "${workload}" in
        smallfile)
            workload_name=rw16t26336file
            ;;
        bigfile)
            workload_name=randwrite
            ;;
        *)
            return 1
            ;;
    esac
    printf '%s\t%s\n' "${mode}" "${workload_name}"
}

# Find the only result directory created by the just-completed formal run.
find_new_formal_run() {
    local configuration=$1
    local workload=$2
    local start_marker=$3
    local root
    local run_pattern
    local top
    local -a top_dirs
    local -a run_dirs

    root=$(formal_output_root "${configuration}") \
        || die "unsupported formal configuration: ${configuration}"
    run_pattern=$(formal_run_dir_pattern "${workload}") \
        || die "unsupported formal workload: ${workload}"
    [ -e "${start_marker}" ] \
        || die "run start marker is missing: ${start_marker}"
    mapfile -t top_dirs < <(
        find "${root}" -mindepth 1 -maxdepth 1 -type d \
            -newer "${start_marker}" -print 2>/dev/null
    )
    [ "${#top_dirs[@]}" -eq 1 ] \
        || die "expected one new output for ${configuration}, found ${#top_dirs[@]}"
    top=${top_dirs[0]}
    mapfile -t run_dirs < <(
        find "${top}" -mindepth 1 -maxdepth 1 -type d \
            -name "${run_pattern}" -print
    )
    [ "${#run_dirs[@]}" -eq 1 ] \
        || die "expected one ${workload} result under ${top}, found ${#run_dirs[@]}"
    printf '%s\n' "${run_dirs[0]}"
}

# Require exactly one fixed marker in a completed run log.
require_single_marker() {
    local input=$1
    local marker=$2
    local description=$3
    local count

    count=$(grep -Fc -- "${marker}" "${input}" || true)
    [ "${count}" -eq 1 ] \
        || die "expected one ${description} in ${input}, found ${count}"
}

# Validate the local artifacts that prove one synchronous test.sh invocation
# reached a successful measured-fio boundary and returned normally.
validate_completed_run() {
    local run_dir=$1
    local configuration=$2
    local workload=$3
    local terminal_log="${run_dir}/terminal.log"
    local fio_log="${run_dir}/fio.log"
    local dmesg_log="${run_dir}/dmesg.log"
    local marker_fields
    local kernel_mode
    local kernel_workload

    [ -s "${terminal_log}" ] \
        || die "terminal log is missing: ${terminal_log}"
    [ -s "${fio_log}" ] || die "fio log is missing: ${fio_log}"
    [ -s "${dmesg_log}" ] || die "kernel log is missing: ${dmesg_log}"

    require_single_marker "${terminal_log}" \
        "Formal Host commit: ${host_commit}" "pinned Host commit record"
    require_single_marker "${terminal_log}" \
        "Formal f2fs module SHA-256: ${module_sha256}" \
        "compiled module hash record"
    require_single_marker "${terminal_log}" \
        '=============end fio=============' "terminal completion marker"

    marker_fields=$(formal_kernel_marker_fields \
        "${configuration}" "${workload}") \
        || die "failed to resolve measured-fio marker fields"
    IFS=$'\t' read -r kernel_mode kernel_workload <<< "${marker_fields}"
    require_single_marker "${dmesg_log}" \
        "MEASURED_FIO_START mode=${kernel_mode} workload=${kernel_workload}" \
        "measured-fio start marker"
    require_single_marker "${dmesg_log}" \
        "MEASURED_FIO_END mode=${kernel_mode} workload=${kernel_workload} status=0" \
        "successful measured-fio end marker"
}

# Reject a completed run containing a high-confidence kernel failure.
check_kernel_anomalies() {
    local run_dir=$1
    local input="${run_dir}/dmesg.log"
    local output="${run_dir}/formal-kernel-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|Oops:|SBI_NEED_FSCK|EUCLEAN|SIT[^[:space:]]* (mismatch|inconsistent|corrupt)|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount'

    [ -s "${input}" ] || die "kernel log is missing: ${input}"
    if grep -Eai "${pattern}" "${input}" > "${output}"; then
        die "kernel anomaly detected; see ${output}"
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Parse fio's mixed normal/JSON output with the JSON decoder rather than text
# pattern matching.
extract_fio_metrics() {
    local run_dir=$1
    local expected_job_name=$2

    python3 - "${run_dir}/fio.log" "${expected_job_name}" <<'PY'
import json
import sys
from pathlib import Path

fio_path = Path(sys.argv[1])
expected_job_name = sys.argv[2]
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
job_names = {str(job.get("jobname", "")) for job in jobs}
if job_names != {expected_job_name}:
    raise SystemExit(
        f"unexpected fio jobs {sorted(job_names)}; "
        f"expected {expected_job_name}: {fio_path}"
    )
errors = [int(job.get("error", 0)) for job in jobs]
if any(errors):
    raise SystemExit(f"fio reported job errors {errors}: {fio_path}")

bw_bytes_s = sum(float(job.get("write", {}).get("bw_bytes", 0)) for job in jobs)
iops = sum(float(job.get("write", {}).get("iops", 0)) for job in jobs)
io_bytes = sum(int(job.get("write", {}).get("io_bytes", 0)) for job in jobs)
runtime_ms = max(int(job.get("write", {}).get("runtime", 0)) for job in jobs)
if bw_bytes_s <= 0 or io_bytes <= 0 or runtime_ms <= 0:
    raise SystemExit(f"fio write metrics are incomplete: {fio_path}")

print(
    f"{bw_bytes_s / (1024 * 1024):.6f}\t"
    f"{iops:.6f}\t"
    f"{runtime_ms / 1000.0:.6f}\t"
    f"{io_bytes / (1024 ** 3):.6f}\t0"
)
PY
}

# Run one destructive formal benchmark and record its provenance and metrics.
run_one() {
    local sequence=$1
    local repetition=$2
    local configuration=$3
    local workload=$4
    local label
    local start_marker
    local run_dir
    local expected_job_name
    local metrics
    local bw_mib_s
    local iops
    local runtime_s
    local io_gib
    local fio_error
    local openssd_provenance

    label=$(printf '%02d-rep%d-%s-%s' \
        "${sequence}" "${repetition}" "${configuration}" "${workload}")
    {
        printf 'status=running\n'
        printf 'sequence=%s\n' "${sequence}"
        printf 'completed_runs=%s\n' "$((sequence - 1))"
        printf 'current_run=%s\n' "${label}"
        printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    } > "${state_file}"

    echo
    echo "============================================================"
    echo "Starting ${label}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    openssd_provenance=$(verify_openssd_provenance)
    start_marker="${batch_dir}/${label}.start-marker"
    : > "${start_marker}"
    sudo -n "${runner}" "${configuration}" "${workload}"
    run_dir=$(find_new_formal_run \
        "${configuration}" "${workload}" "${start_marker}")

    validate_completed_run "${run_dir}" "${configuration}" "${workload}"
    check_kernel_anomalies "${run_dir}"

    expected_job_name=$(formal_fio_job_name "${workload}") \
        || die "unsupported formal workload: ${workload}"
    metrics=$(extract_fio_metrics "${run_dir}" "${expected_job_name}") \
        || die "failed to extract fio metrics for ${label}"
    IFS=$'\t' read -r bw_mib_s iops runtime_s io_gib fio_error <<< "${metrics}"
    [ "${fio_error}" = "0" ] || die "fio reported error=${fio_error}"

    ln -s "${run_dir}" "${batch_dir}/${label}.run"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${sequence}" "${repetition}" "${configuration}" "${workload}" \
        "${bw_mib_s}" "${iops}" "${runtime_s}" "${io_gib}" \
        "${fio_error}" "${run_dir}" >> "${runs_tsv}"
    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'sequence=%s\nrepetition=%s\n' "${sequence}" "${repetition}"
        printf 'configuration=%s\nworkload=%s\n' "${configuration}" "${workload}"
        printf 'host_branch=%s\nhost_commit=%s\n' "${host_branch}" "${host_commit}"
        printf 'module_sha256=%s\n' "${module_sha256}"
        printf '%s\n' "${openssd_provenance}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'fio_bw_mib_s=%s\nfio_iops=%s\n' "${bw_mib_s}" "${iops}"
        printf 'fio_runtime_s=%s\nfio_io_gib=%s\n' "${runtime_s}" "${io_gib}"
        printf 'fio_error=%s\n' "${fio_error}"
    } >> "${manifest}"
    {
        printf 'status=between-runs\n'
        printf 'completed_runs=%s\n' "${sequence}"
        printf 'last_run=%s\n' "${label}"
        printf 'last_bandwidth_mib_s=%s\n' "${bw_mib_s}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
    } > "${state_file}"

    echo "Completed ${label}: ${bw_mib_s} MiB/s"
    echo "Result directory: ${run_dir}"
}

# Summarize the three repetitions and compare original CSGC directly with ORI.
generate_summary() {
    python3 - "${runs_tsv}" "${summary_tsv}" "${summary_md}" <<'PY'
import csv
import statistics
import sys
from collections import defaultdict

runs_path, tsv_path, markdown_path = sys.argv[1:]
groups = defaultdict(list)
with open(runs_path, newline="", encoding="utf-8") as source:
    rows = list(csv.DictReader(source, delimiter="\t"))
if len(rows) != 12:
    raise SystemExit(f"expected 12 completed runs, found {len(rows)}")

for row in rows:
    groups[(row["configuration"], row["workload"])].append(
        float(row["bw_mib_s"])
    )
expected = {
    (configuration, workload)
    for configuration in ("original-csgc", "original-ori")
    for workload in ("smallfile", "bigfile")
}
if set(groups) != expected or any(len(values) != 3 for values in groups.values()):
    raise SystemExit("completed runs do not form four groups of three")

summary_rows = []
for configuration in ("original-csgc", "original-ori"):
    for workload in ("smallfile", "bigfile"):
        values = groups[(configuration, workload)]
        summary_rows.append({
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
    writer = csv.DictWriter(
        output, fieldnames=list(summary_rows[0]), delimiter="\t"
    )
    writer.writeheader()
    writer.writerows(summary_rows)

lookup = {
    (row["configuration"], row["workload"]): row for row in summary_rows
}
with open(markdown_path, "w", encoding="utf-8") as output:
    output.write("# Original ORI and CSGC Formal Matrix\n\n")
    output.write(
        "| Configuration | Workload | Runs | Mean MiB/s | Median MiB/s "
        "| Min | Max | Stddev |\n"
    )
    output.write("|---|---|---:|---:|---:|---:|---:|---:|\n")
    for row in summary_rows:
        output.write(
            f"| {row['configuration']} | {row['workload']} | {row['count']} "
            f"| {row['mean_mib_s']:.3f} | {row['median_mib_s']:.3f} "
            f"| {row['min_mib_s']:.3f} | {row['max_mib_s']:.3f} "
            f"| {row['stdev_mib_s']:.3f} |\n"
        )

    output.write("\n## Original CSGC Relative to ORI\n\n")
    output.write("| Workload | Mean ratio | Median ratio |\n")
    output.write("|---|---:|---:|\n")
    for workload in ("smallfile", "bigfile"):
        csgc = lookup[("original-csgc", workload)]
        ori = lookup[("original-ori", workload)]
        output.write(
            f"| {workload} "
            f"| {csgc['mean_mib_s'] / ori['mean_mib_s']:.4f}x "
            f"| {csgc['median_mib_s'] / ori['median_mib_s']:.4f}x |\n"
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
if [ "$#" -eq 1 ] && [ "$1" = "--status" ]; then
    show_status
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
command -v fio >/dev/null || die "fio is unavailable"
command -v modinfo >/dev/null || die "modinfo is unavailable"
command -v ssh >/dev/null || die "ssh is unavailable"
[ -x "${prepare_script}" ] || die "Host preparation script is unavailable"
[ -x "${runner}" ] || die "formal benchmark runner is unavailable"
[ -d "${host_repo}/.git" ] || die "Host repository is unavailable"
[ -b /dev/nvme0n1 ] || die "/dev/nvme0n1 is unavailable"
findmnt -rn -S /dev/nvme0n1 >/dev/null \
    && die "/dev/nvme0n1 is currently mounted"
pgrep -x fio >/dev/null && die "another fio process is running"

exec 9>/tmp/run_formal_original_matrix_3x.lock
flock -n 9 || die "another original formal matrix is already running"
available_bytes=$(df -B1 --output=avail "${script_dir}" | tail -n 1 | tr -d ' ')
[ "${available_bytes}" -ge "${minimum_free_bytes}" ] \
    || die "at least 15 GiB of free log space is required"
sudo -n -v || die "passwordless sudo is required for an unattended batch"

mkdir -p "${batch_dir}"
printf '%s\n' "${batch_dir}" > "${latest_batch_file}"
printf 'sequence\trepetition\tconfiguration\tworkload\tbw_mib_s\tiops\truntime_s\tio_gib\tfio_error\trun_dir\n' \
    > "${runs_tsv}"
batch_initialized=1
exec > >(tee -a "${batch_dir}/console.log") 2>&1
trap handle_exit EXIT

openssd_provenance=$(verify_openssd_provenance)
detected_ssd_mode=$("${script_dir}/test.sh" --detect-ssd-thread-mode)
[ "${detected_ssd_mode}" = "ssd1t" ] \
    || die "expected SSD1t, detected ${detected_ssd_mode}"
{
    printf 'batch_id=%s\nstarted_at=%s\n' \
        "${batch_id}" "$(date --iso-8601=seconds)"
    printf 'host=%s\nartifact_branch=%s\nartifact_commit=%s\n' \
        "$(hostname)" "$(git -C "${artifact_repo}" branch --show-current)" \
        "$(git -C "${artifact_repo}" rev-parse HEAD)"
    printf 'artifact_tracked_dirty=%s\n' \
        "$([ -n "$(git -C "${artifact_repo}" status --porcelain=v1 --untracked-files=no)" ] && echo 1 || echo 0)"
    printf 'script_sha256=%s\n' "$(sha256sum "${script_path}" | awk '{print $1}')"
    printf 'run_count=12\nrepeat_count=%s\nssd_thread_mode=%s\n' \
        "${repeat_count}" "${detected_ssd_mode}"
    printf 'pinned_host_branch=%s\npinned_host_commit=%s\n' \
        "${host_branch}" "${host_commit}"
    printf '%s\n' "${openssd_provenance}"
    printf 'execution_schedule_begin\n'
    print_schedule
    printf 'execution_schedule_end\n'
} > "${manifest}"

echo "DESTRUCTIVE WARNING: this batch resets and overwrites /dev/nvme0n1 twelve times."
echo "Starting without an interactive confirmation prompt."
echo "Batch directory: ${batch_dir}"
echo "Host revision: ${host_branch}@${host_commit}"
echo "OpenSSD source revision: ${openssd_branch}@${openssd_commit}"
echo "Important: source validation cannot prove the loaded firmware ELF hash."

(
    while sleep 45; do
        sudo -n true || exit
    done
) &
sudo_keepalive_pid=$!

ensure_exact_host_worktree
build_original_host_module

schedule=(
    "1 1 original-csgc smallfile"
    "2 2 original-csgc smallfile"
    "3 3 original-csgc smallfile"
    "4 1 original-csgc bigfile"
    "5 2 original-csgc bigfile"
    "6 3 original-csgc bigfile"
    "7 1 original-ori smallfile"
    "8 2 original-ori smallfile"
    "9 3 original-ori smallfile"
    "10 1 original-ori bigfile"
    "11 2 original-ori bigfile"
    "12 3 original-ori bigfile"
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
{
    printf 'status=complete\ncompleted_runs=12\n'
    printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'summary=%s\n' "${summary_md}"
} > "${state_file}"
batch_complete=1

echo
echo "All 12 original ORI/CSGC benchmarks completed successfully."
echo "Batch manifest: ${manifest}"
echo "Aggregate summary: ${summary_md}"
