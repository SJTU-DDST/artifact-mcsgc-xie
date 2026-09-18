#!/usr/bin/env bash

set -euo pipefail

# Collect low-overhead paper metrics from one pinned Host/device pair.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)
REPRO_TREE=$(cd -- "${SCRIPT_DIR}/../../.." && pwd)
SOURCE_COMMIT=0271b907ec00ed643fd139403b726817c9fe8c32
NVME_CLI_DIR=${REPRO_TREE}/host/src/nvme-cli
NVME_CLI_PATH=${NVME_CLI_DIR}/nvme
HOST_REPO=/home/xin/work-xie/mcsgc-real/linux-cs
OPENSSD_HOST=192.168.98.31
OPENSSD_TREE=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
DEVICE=/dev/nvme0n1
FSCK_TIMEOUT_SECONDS=${PAPER_METRICS_FSCK_TIMEOUT_SECONDS:-600}
DEFAULT_BASELINE_BATCH=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-original-reproduction/20260828_185514
BASELINE_BATCH=${EUROPAR_BASELINE_BATCH:-${DEFAULT_BASELINE_BATCH}}
RUN_PROFILE=${PAPER_METRICS_PROFILE:-full}
case "${RUN_PROFILE}" in
    full)
        OPENSSD_BRANCH=exp/formal-mcsgc-quiet-20260809
        OPENSSD_COMMIT=52831c159c9f7a73f9670c163a6b513750f64b47
        RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-nowait-paper-metrics
        CASES_PER_REPETITION=6
        HOST_BRANCH=exp/diagnostic-mcsgc8t-nowait-paper-metrics-20260917
        HOST_COMMIT=d772d6c0092aab0343f01cacac26330ebb28050b
        HOST_BASE_COMMIT=dec1964f0bf196b1929799119e93393bfa6b79fb
        PREFERRED_WORKTREE=/home/xin/work-xie/mcsgc-real/linux-cs-nowait-paper-metrics-20260917
        HOST_CONFIG_TEMPLATE=
        HOST_CONFIG_SHA256=
        COLLECT_GC_PAPER_METRICS=1
        ANALYZER_SCRIPT=${SCRIPT_DIR}/analyze_europar25_paper_metrics.py
        PROFILE_DESCRIPTION="five section-size fio cases and one strict 300-second Filebench timeline"
        ;;
    section16)
        OPENSSD_BRANCH=exp/diagnostic-mcsgc-paper-waf-20260917
        OPENSSD_COMMIT=ca8a6dd48ee72c67d1945703bec0430ca4a8a76d
        RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-nowait-section16-paper-metrics
        CASES_PER_REPETITION=1
        HOST_BRANCH=exp/diagnostic-mcsgc8t-nowait-secs16-paper-metrics-20260918
        HOST_COMMIT=f85761e7e5e6a4357c82324981ae2aaa2c8348a7
        HOST_BASE_COMMIT=dec1964f0bf196b1929799119e93393bfa6b79fb
        PREFERRED_WORKTREE=/home/xin/work-xie/mcsgc-real/linux-cs-nowait-secs16-paper-metrics-20260918
        HOST_CONFIG_TEMPLATE=
        HOST_CONFIG_SHA256=
        COLLECT_GC_PAPER_METRICS=1
        ANALYZER_SCRIPT=${SCRIPT_DIR}/analyze_europar25_paper_metrics.py
        PROFILE_DESCRIPTION="the corrected widest-section fio case"
        ;;
    section16-quiet)
        OPENSSD_BRANCH=exp/formal-mcsgc-quiet-20260809
        OPENSSD_COMMIT=52831c159c9f7a73f9670c163a6b513750f64b47
        RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-nowait-section16-quiet
        CASES_PER_REPETITION=1
        HOST_BRANCH=exp/formal-mcsgc8t-nowait-secs16-quiet-20260917
        HOST_COMMIT=9b3f8f2ce3077d5ccc010ce22363deffe1efbf2c
        HOST_BASE_COMMIT=dec1964f0bf196b1929799119e93393bfa6b79fb
        PREFERRED_WORKTREE=/home/xin/work-xie/mcsgc-real/linux-cs-nowait-secs16-quiet-20260917
        HOST_CONFIG_TEMPLATE=
        HOST_CONFIG_SHA256=
        COLLECT_GC_PAPER_METRICS=0
        ANALYZER_SCRIPT=${SCRIPT_DIR}/analyze_europar25_nowait_section16_quiet.py
        PROFILE_DESCRIPTION="the corrected widest-section fio case using the quiet Host build"
        ;;
    original)
        OPENSSD_BRANCH=formal-original-csgc-main-20260809
        OPENSSD_COMMIT=463e8b0b13ad345ed99c2176b1f81ad34d3c986a
        RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-original-paper-metrics
        CASES_PER_REPETITION=12
        HOST_BRANCH=exp/diagnostic-original-paper-metrics-20260918
        HOST_COMMIT=3620ae3298fade7062fa50457df7fc97cba81368
        HOST_BASE_COMMIT=ecfcc36c89b8ecb29ded364257f672dd9ff363e2
        PREFERRED_WORKTREE=/home/xin/work-xie/mcsgc-real/linux-cs-original-paper-metrics-20260918
        HOST_CONFIG_TEMPLATE=/home/xin/work-xie/mcsgc-real/linux-cs-formal-original-lifecycle-fix-20260828/.config
        HOST_CONFIG_SHA256=db1bcdafeff95fcde20d12ab60ba84fd4ce7c2a0349f451d363a887a4ad82a18
        COLLECT_GC_PAPER_METRICS=1
        CONTINUE_ON_FSCK_FAILURE=1
        ANALYZER_SCRIPT=${SCRIPT_DIR}/analyze_europar25_original_paper_metrics.py
        PROFILE_DESCRIPTION="ORI and original CSGC section-size fio cases plus strict 300-second Filebench timelines"
        ;;
    *)
        echo "ERROR: unsupported PAPER_METRICS_PROFILE: ${RUN_PROFILE}" >&2
        exit 2
        ;;
esac
CONTINUE_ON_FSCK_FAILURE=${PAPER_METRICS_CONTINUE_ON_FSCK_FAILURE:-${CONTINUE_ON_FSCK_FAILURE:-0}}
case "${FSCK_TIMEOUT_SECONDS}:${CONTINUE_ON_FSCK_FAILURE}" in
    *[!0-9:]*|:*|*:|0:*)
        echo "ERROR: invalid fsck policy configuration" >&2
        exit 2
        ;;
esac
REPETITIONS=${PAPER_METRICS_REPETITIONS:-3}
case "${REPETITIONS}" in
    ''|*[!0-9]*|0)
        echo "ERROR: PAPER_METRICS_REPETITIONS must be a positive integer" >&2
        exit 2
        ;;
esac
EXPECTED_CASES=$((CASES_PER_REPETITION * REPETITIONS))
MINIMUM_FREE_BYTES=$((5 * 1024 * 1024 * 1024))

declare -a CONFIGURATIONS=(paper-metrics)
declare -A HOST_BRANCHES=(
    [paper-metrics]=${HOST_BRANCH}
)
declare -A HOST_COMMITS=(
    [paper-metrics]=${HOST_COMMIT}
)
declare -A HOST_BASE_COMMITS=(
    [paper-metrics]=${HOST_BASE_COMMIT}
)
declare -A PREFERRED_WORKTREES=(
    [paper-metrics]=${PREFERRED_WORKTREE}
)
declare -A HOST_TREES=()
declare -A MODULE_PATHS=()
declare -A MODULE_SRCVERSIONS=()

SUDO_KEEPALIVE_PID=""
BATCH_DIR=""
CASE_RESULTS=""
OUTER_START_TICKS=""
STARTED_AT=""
NVME_CLI_SHA256=""
LOADED_NODE_READAHEAD_MODE=compiled-in
FSCK_LAST_RESULT=skipped

# Keep every descendant benchmark non-interactive. A detached tmux session
# cannot answer a sudo prompt, so fail immediately instead of stalling a case.
sudo() {
    command /usr/bin/sudo -n "$@"
}
export -f sudo

# Print the destructive one-command interface and recovery commands.
usage() {
    cat <<EOF
Usage:
  ./$(basename -- "${SCRIPT_PATH}")
  ./$(basename -- "${SCRIPT_PATH}") --resume BATCH_DIR
  ./$(basename -- "${SCRIPT_PATH}") --status BATCH_DIR
  ./$(basename -- "${SCRIPT_PATH}") --preflight
  ./$(basename -- "${SCRIPT_PATH}") --dry-run

This command builds one pinned Host candidate and runs profile '${RUN_PROFILE}'
for ${EXPECTED_CASES} destructive cases. Each repetition contains
${PROFILE_DESCRIPTION}. Every case resets, formats, and overwrites ${DEVICE}. No interactive
prompt is used. Run this script as the regular login user; it invokes sudo
internally.

Expected OpenSSD source:
  ${OPENSSD_BRANCH}@${OPENSSD_COMMIT}

Default original-system baseline:
  ${DEFAULT_BASELINE_BATCH}

Set PAPER_METRICS_REPETITIONS to override the default three repetitions.
EOF
}

# Abort while preserving all completed batch files.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Emit every Host-only measurement still missing from the paper figures.
write_base_cases() {
    local sec

    if [ "${RUN_PROFILE}" = section16 ] || [ "${RUN_PROFILE}" = section16-quiet ]; then
        printf 'fio-section-16\tfio\trandwrite\trandom\t0.86\t16\t0\n'
        return
    fi
    for sec in 1 2 4 8 16; do
        printf 'fio-section-%s\tfio\trandwrite\trandom\t0.86\t%s\t0\n' "${sec}" "${sec}"
    done
    # Keep the historically less robust stateful workload last.
    printf 'filebench-period\tfilebench\tfileserver_4t_60G_1M_54k_period\trandom\t0.86\t8\t0\n'
}

# Complete one full selected matrix before starting the next repetition.
write_schedule() {
    local path=$1
    local repetition
    local base_id mode workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased

    printf 'case_id\tconfiguration\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tfio_timebased\n' > "${path}"
    for ((repetition = 1; repetition <= REPETITIONS; repetition++)); do
        if [ "${RUN_PROFILE}" = original ]; then
            for mode in cs ori; do
                while IFS=$'\t' read -r base_id workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased; do
                    printf 'original-paper-metrics-%s-%s-r%s\tpaper-metrics\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                        "${mode}" "${base_id}" "${repetition}" "${mode}" \
                        "${workload_type}" "${bmname}" "${distribution}" \
                        "${prefill_ratio}" "${segs_per_sec}" "${fio_timebased}" >> "${path}"
                done < <(write_base_cases)
            done
        else
            while IFS=$'\t' read -r base_id workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased; do
                if [ "${RUN_PROFILE}" = section16-quiet ]; then
                    printf 'nowait-section16-quiet-%s-r%s\tpaper-metrics\tcs\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                        "${base_id}" "${repetition}" "${workload_type}" \
                        "${bmname}" "${distribution}" "${prefill_ratio}" \
                        "${segs_per_sec}" "${fio_timebased}" >> "${path}"
                else
                    printf 'paper-metrics-%s-r%s\tpaper-metrics\tcs\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                        "${base_id}" "${repetition}" "${workload_type}" \
                        "${bmname}" "${distribution}" "${prefill_ratio}" \
                        "${segs_per_sec}" "${fio_timebased}" >> "${path}"
                fi
            done < <(write_base_cases)
        fi
    done
}

# Persist enough process identity to monitor or resume the exact batch.
write_state() {
    local status=$1
    {
        printf 'batch_status=%q\n' "${status}"
        printf 'updated_at=%q\n' "$(date --iso-8601=seconds)"
        printf 'outer_pid=%q\n' "$$"
        printf 'outer_start_ticks=%q\n' "${OUTER_START_TICKS}"
        printf 'expected_cases=%q\n' "${EXPECTED_CASES}"
        printf 'repetitions=%q\n' "${REPETITIONS}"
        printf 'batch_dir=%q\n' "${BATCH_DIR}"
    } > "${BATCH_DIR}/state.env"
}

# Show progress without touching the device or running processes.
show_status() {
    local batch=$1
    local completed=0
    local validated=0
    local failed=0
    local expected=${EXPECTED_CASES}
    if [ -f "${batch}/state.env" ]; then
        expected=$(sed -n 's/^expected_cases=//p' "${batch}/state.env" | tail -n 1)
    fi
    if [ -f "${batch}/case-results.tsv" ]; then
        completed=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${batch}/case-results.tsv")
        failed=$(awk -F '\t' 'NR > 1 && $11 != 0 {n++} END {print n + 0}' "${batch}/case-results.tsv")
    fi
    if [ -d "${batch}/validated" ]; then
        validated=$(find "${batch}/validated" -maxdepth 1 -type f -name '*.ok' | wc -l)
    fi
    printf 'batch=%s\nresult_rows=%s/%s\nvalidated=%s/%s\nfailed_rows=%s\n' \
        "${batch}" "${completed}" "${expected}" \
        "${validated}" "${expected}" "${failed}"
    [ ! -f "${batch}/state.env" ] || sed -n '1,12p' "${batch}/state.env"
}

# Return success when a case has both a successful result and validation marker.
case_succeeded() {
    local case_id=$1

    [ -f "${BATCH_DIR}/validated/${case_id}.ok" ] && case_has_success_row "${case_id}"
}

# Return success when test.sh already emitted a successful result row.
case_has_success_row() {
    local case_id=$1

    awk -F '\t' -v id="${case_id}" \
        'NR > 1 && $1 == id && $11 == 0 {found=1} END {exit !found}' "${CASE_RESULTS}"
}

# Generate the single-case config consumed by the unmodified artifact runner.
write_case_config() {
    local path=$1 workload_type=$2 bmname=$3 distribution=$4
    local prefill_ratio=$5 segs_per_sec=$6 fio_timebased=$7
    cat > "${path}" <<EOF
#!/usr/bin/env bash
workloads=("${workload_type}:${bmname}")
random_distributions=("${distribution}")
prefill_ratios=("${prefill_ratio}")
segs_per_sec_list=("${segs_per_sec}")
fio_timebased=${fio_timebased}
EOF
}

# Read and validate OpenSSD source provenance without changing server 31.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${OPENSSD_HOST}" \
        "repo='${OPENSSD_TREE}'; git -C \"\${repo}\" branch --show-current; git -C \"\${repo}\" rev-parse HEAD; if test -n \"\$(git -C \"\${repo}\" status --porcelain --untracked-files=no)\"; then echo dirty; else echo clean; fi"
}

# Require the pinned mCSGC source before every destructive case.
verify_openssd_provenance() {
    local output branch commit dirty
    output=$(read_openssd_provenance) || die "cannot read OpenSSD provenance"
    branch=$(sed -n '1p' <<< "${output}")
    commit=$(sed -n '2p' <<< "${output}")
    dirty=$(sed -n '3p' <<< "${output}")
    [ "${branch}" = "${OPENSSD_BRANCH}" ] \
        || die "wrong OpenSSD branch: expected=${OPENSSD_BRANCH} actual=${branch}"
    [ "${commit}" = "${OPENSSD_COMMIT}" ] \
        || die "wrong OpenSSD commit: expected=${OPENSSD_COMMIT} actual=${commit}"
    [ "${dirty}" = clean ] || die "OpenSSD tracked tree is dirty"
    printf 'openssd_branch=%s\nopenssd_commit=%s\nopenssd_tracked_state=%s\n' \
        "${branch}" "${commit}" "${dirty}"
}

# Resolve or create a worktree at one exact pinned Host revision.
ensure_exact_host_worktree() {
    local configuration=$1
    local branch=${HOST_BRANCHES[${configuration}]}
    local commit=${HOST_COMMITS[${configuration}]}
    local preferred_path=${PREFERRED_WORKTREES[${configuration}]}
    local branch_ref="refs/heads/${branch}"
    local worktree_output path status_line
    local -a matches=() live_matches=()

    git -C "${HOST_REPO}" fetch --quiet --prune origin
    git -C "${HOST_REPO}" show-ref --verify --quiet "refs/remotes/origin/${branch}" \
        || die "missing remote Host branch: origin/${branch}"
    [ "$(git -C "${HOST_REPO}" rev-parse "origin/${branch}")" = "${commit}" ] \
        || die "remote Host branch moved: ${branch}"

    if git -C "${HOST_REPO}" show-ref --verify --quiet "${branch_ref}"; then
        [ "$(git -C "${HOST_REPO}" rev-parse "${branch_ref}")" = "${commit}" ] \
            || die "local Host branch moved: ${branch}"
    else
        git -C "${HOST_REPO}" branch --track "${branch}" "origin/${branch}"
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
        [ ! -e "${preferred_path}" ] || preferred_path="${preferred_path}-$(date +%s)"
        git -C "${HOST_REPO}" worktree add --quiet --force "${preferred_path}" "${branch}"
        path=${preferred_path}
    elif [ "${#live_matches[@]}" -eq 1 ]; then
        path=${live_matches[0]}
    else
        die "multiple live worktrees claim ${branch}"
    fi

    [ "$(git -C "${path}" rev-parse HEAD)" = "${commit}" ] \
        || die "Host worktree is not pinned: ${path}"
    git -C "${path}" merge-base --is-ancestor \
        "${HOST_BASE_COMMITS[${configuration}]}" "${commit}" \
        || die "Host repair is not based on the expected quiet revision"
    while IFS= read -r status_line; do
        [ -z "${status_line}" ] && continue
        [ "${status_line:3}" = .config ] \
            || die "unsupported Host worktree change in ${path}: ${status_line}"
    done < <(git -C "${path}" status --porcelain=v1 --untracked-files=no)
    HOST_TREES[${configuration}]=${path}
}

# Build the low-overhead Host metrics candidate.
build_configuration() {
    local configuration=$1
    local tree=${HOST_TREES[${configuration}]}
    local commit=${HOST_COMMITS[${configuration}]}
    local module_path="${tree}/fs/f2fs/f2fs.ko"
    local committed_sha build_sha module_sha module_srcversion config_origin

    echo "Building ${configuration} from ${commit} at $(date --iso-8601=seconds)"
    if [ -n "${HOST_CONFIG_TEMPLATE}" ]; then
        [ -r "${HOST_CONFIG_TEMPLATE}" ] \
            || die "Host config template is missing: ${HOST_CONFIG_TEMPLATE}"
        [ "$(sha256sum "${HOST_CONFIG_TEMPLATE}" | awk '{print $1}')" = "${HOST_CONFIG_SHA256}" ] \
            || die "Host config template changed: ${HOST_CONFIG_TEMPLATE}"
        cp -- "${HOST_CONFIG_TEMPLATE}" "${tree}/.config"
        config_origin=${HOST_CONFIG_TEMPLATE}
    else
        git -C "${tree}" show "${commit}:.config" > "${tree}/.config"
        config_origin="${commit}:.config"
    fi
    committed_sha=$(sha256sum "${tree}/.config" | awk '{print $1}')
    "${tree}/scripts/config" --file "${tree}/.config" --enable F2FS_STAT_FS
    make -s -C "${tree}" olddefconfig LOCALVERSION=-csgcmt
    make -s -C "${tree}" prepare modules_prepare LOCALVERSION=-csgcmt
    build_sha=$(sha256sum "${tree}/.config" | awk '{print $1}')
    (
        cd "${tree}"
        sudo ./build_f2fs.sh
    )
    [ -r "${module_path}" ] || die "module was not produced: ${module_path}"
    if [ "${COLLECT_GC_PAPER_METRICS}" -eq 1 ]; then
        grep -aFq 'gc_paper_metrics' "${module_path}" \
            || die "module does not contain the GC paper metrics interface"
    elif grep -aFq 'gc_paper_metrics' "${module_path}"; then
        die "quiet module unexpectedly contains the GC paper metrics interface"
    fi
    module_sha=$(sha256sum "${module_path}" | awk '{print $1}')
    module_srcversion=$(modinfo -F srcversion "${module_path}")
    [ -n "${module_srcversion}" ] || die "module has no srcversion: ${module_path}"

    MODULE_PATHS[${configuration}]=${module_path}
    MODULE_SRCVERSIONS[${configuration}]=${module_srcversion}
    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\nhost_branch=%s\nhost_commit=%s\n' \
            "${tree}" "${HOST_BRANCHES[${configuration}]}" "${commit}"
        printf 'config_origin=%s\n' "${config_origin}"
        printf 'committed_config_sha256=%s\nbuild_config_sha256=%s\n' \
            "${committed_sha}" "${build_sha}"
        printf 'comparison_config_override=CONFIG_F2FS_STAT_FS=y\n'
        printf 'paper_metrics_enabled=%s\n' "${COLLECT_GC_PAPER_METRICS}"
        printf 'module_sha256=%s\nmodule_srcversion=%s\n' "${module_sha}" "${module_srcversion}"
    } >> "${BATCH_DIR}/provenance.txt"
}

# Build the private nvme-cli used for SSD reset, fs-ready, and statistics.
build_nvme_cli() {
    echo "Building nvme-cli at $(date --iso-8601=seconds)"
    make -s -C "${NVME_CLI_DIR}" -j"$(nproc)"
    [ -x "${NVME_CLI_PATH}" ] || die "nvme-cli was not produced: ${NVME_CLI_PATH}"
    "${NVME_CLI_PATH}" version >/dev/null
    sudo "${NVME_CLI_PATH}" id-ctrl "${DEVICE}" >/dev/null
    NVME_CLI_SHA256=$(sha256sum "${NVME_CLI_PATH}" | awk '{print $1}')
    {
        printf '\n[nvme-cli]\n'
        printf 'path=%s\nsha256=%s\n' "${NVME_CLI_PATH}" "${NVME_CLI_SHA256}"
    } >> "${BATCH_DIR}/provenance.txt"
}

# Reject a missing or changed private nvme-cli before touching the device.
verify_nvme_cli() {
    local actual_sha

    [ -x "${NVME_CLI_PATH}" ] || die "nvme-cli is missing: ${NVME_CLI_PATH}"
    actual_sha=$(sha256sum "${NVME_CLI_PATH}" | awk '{print $1}')
    [ "${actual_sha}" = "${NVME_CLI_SHA256}" ] \
        || die "nvme-cli changed during the matrix"
}

# Load one prebuilt candidate after verifying the namespace is unmounted.
load_configuration() {
    local configuration=$1
    local module_path=${MODULE_PATHS[${configuration}]}
    local expected_srcversion=${MODULE_SRCVERSIONS[${configuration}]}
    local loaded_srcversion

    findmnt -rn -S "${DEVICE}" >/dev/null && die "${DEVICE} is mounted before module replacement"
    if lsmod | awk '$1 == "f2fs" {found=1} END {exit !found}'; then
        sudo rmmod f2fs
    fi
    sudo insmod "${module_path}"
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    [ "${loaded_srcversion^^}" = "${expected_srcversion^^}" ] \
        || die "loaded module does not match ${configuration}"
    ! modinfo -p "${module_path}" | grep -q '^csgc_node_readahead_nowait:' \
        || die "quiet candidate still exposes the NOWAIT A/B parameter"
}

# Check the non-destructive environment and exact workload source.
preflight() {
    local source_diff openssd_state available_bytes process configuration
    [ "${EUID}" -ne 0 ] || die "run this launcher as the regular user"
    sudo -n true || sudo -v
    [ -b "${DEVICE}" ] || die "missing block device: ${DEVICE}"
    findmnt -rn -S "${DEVICE}" >/dev/null && die "${DEVICE} is mounted"
    ! pgrep -x fio >/dev/null || die "process is already running: fio"
    if [ "${RUN_PROFILE}" = full ] || [ "${RUN_PROFILE}" = original ]; then
        ! pgrep -x filebench >/dev/null \
            || die "process is already running: filebench"
    fi
    if [ "${RUN_PROFILE}" = full ]; then
        for process in java mysqld; do
            ! pgrep -x "${process}" >/dev/null \
                || die "process is already running: ${process}"
        done
    fi
    if [ -r /sys/module/f2fs/refcnt ] && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
        die "f2fs has active references"
    fi

    [ "$(git -C "${REPRO_TREE}" merge-base "${SOURCE_COMMIT}" HEAD)" = "${SOURCE_COMMIT}" ] \
        || die "artifact branch is not based on ${SOURCE_COMMIT}"
    source_diff=$(git -C "${REPRO_TREE}" diff --name-only "${SOURCE_COMMIT}" -- \
        host/benchmarks/myworkloads host/benchmarks/scripts/configs)
    [ -z "${source_diff}" ] || die "original workload/config files changed: ${source_diff}"

    openssd_state=$(verify_openssd_provenance)
    printf '%s\n' "${openssd_state}"
    for configuration in "${CONFIGURATIONS[@]}"; do
        git -C "${HOST_REPO}" cat-file -e "${HOST_COMMITS[${configuration}]}^{commit}"
    done
    for process in fio cgexec bc flock; do
        command -v "${process}" >/dev/null || die "required command is missing: ${process}"
    done
    [ -x "${ANALYZER_SCRIPT}" ] || die "analysis script is missing: ${ANALYZER_SCRIPT}"
    [ -x "${SCRIPT_DIR}/../file_writer/build.sh" ] || die "file writer build script is missing"
    [ -f "${NVME_CLI_DIR}/Makefile" ] || die "nvme-cli source tree is missing"
    if [ "${RUN_PROFILE}" = full ]; then
        for process in filebench java python2 mysqladmin; do
            command -v "${process}" >/dev/null \
                || die "required command is missing: ${process}"
        done
        [ -x "${SCRIPT_DIR}/../ycsb-0.17.0/bin/ycsb" ] || die "YCSB is missing"
        sudo test -f /var/lib/mysql/ycsb_db/usertable.ibd \
            || die "preloaded YCSB database is missing"
        grep -Rqs '^datadir[[:space:]]*=[[:space:]]*/mnt/openssd_f2fs/mysql' /etc/mysql \
            || die "MySQL datadir is not configured for the OpenSSD mount"
        ! systemctl is-active --quiet mysql || die "MySQL must be stopped"
    fi
    if [ "${RUN_PROFILE}" = original ]; then
        command -v filebench >/dev/null || die "required command is missing: filebench"
    fi
    [ -f "${BASELINE_BATCH}/case-results.tsv" ] || die "baseline batch is incomplete: ${BASELINE_BATCH}"
    available_bytes=$(df -B1 --output=avail "${RESULT_BASE}" 2>/dev/null | tail -n 1 | tr -d ' ')
    if [ -z "${available_bytes}" ]; then
        available_bytes=$(df -B1 --output=avail "$(dirname "${RESULT_BASE}")" | tail -n 1 | tr -d ' ')
    fi
    [ "${available_bytes}" -ge "${MINIMUM_FREE_BYTES}" ] || die "less than 5 GiB is available"
    echo "Preflight checks passed. Source validation cannot prove the running ELF identity."
}

# Record code identity, Vitis inputs, and an immutable workload snapshot.
write_provenance() {
    local openssd_info vitis_info
    openssd_info=$(read_openssd_provenance)
    vitis_info=$(ssh -o BatchMode=yes "${OPENSSD_HOST}" \
        "find /home/xin/vitis_workspaces/xie_csgc_withjin -path '*/src/config.h' -type f -print0 | sort -z | xargs -0 sha256sum; find /home/xin/vitis_workspaces/xie_csgc_withjin -path '*/src/shared_mem.h' -type f -print0 | sort -z | xargs -0 sha256sum")
    {
        printf 'operator=%s\n' "${USER}"
        printf 'outer_script=%s\nrepetitions=%s\nstarted_at=%s\n' \
            "${SCRIPT_PATH}" "${REPETITIONS}" "${STARTED_AT}"
        printf 'artifact_branch=%s\nartifact_commit=%s\nsource_commit=%s\n' \
            "$(git -C "${REPRO_TREE}" branch --show-current)" \
            "$(git -C "${REPRO_TREE}" rev-parse HEAD)" "${SOURCE_COMMIT}"
        printf 'run_profile=%s\n' "${RUN_PROFILE}"
        printf 'baseline_batch=%s\n' "${BASELINE_BATCH}"
        if [ "${RUN_PROFILE}" = section16 ] || [ "${RUN_PROFILE}" = section16-quiet ]; then
            printf 'case_order=section-size-16-fio\n'
        elif [ "${RUN_PROFILE}" = original ]; then
            printf 'case_order=original-csgc-section-fio,filebench-300s,ori-section-fio,filebench-300s\n'
        else
            printf 'case_order=section-size-fio,filebench-300s\n'
        fi
        printf 'fsck_after_case=1\ncheck_checkpoints=1\n'
        printf 'fsck_mode=full-dry-run\nfsck_timeout_seconds=%s\n' "${FSCK_TIMEOUT_SECONDS}"
        printf 'continue_on_fsck_failure=%s\n' "${CONTINUE_ON_FSCK_FAILURE}"
        printf 'filebench_runtime_s=300\nfilebench_report_interval_s=5\n'
        printf 'f2fs_status_sample_interval_s=0\n'
        printf 'gc_paper_metrics_enabled=%s\n' "${COLLECT_GC_PAPER_METRICS}"
        printf 'kernel_panic_timeout_s=0\n'
        printf 'openssd_expected_branch=%s\nopenssd_expected_commit=%s\n' \
            "${OPENSSD_BRANCH}" "${OPENSSD_COMMIT}"
        printf 'firmware_identity_limit=source and Vitis hashes do not prove running ELF identity\n'
        printf '\n[openssd-source]\n%s\n\n[vitis-input-hashes]\n%s\n' "${openssd_info}" "${vitis_info}"
    } > "${BATCH_DIR}/provenance.txt"
    mkdir -p "${BATCH_DIR}/source-snapshot"
    git -C "${REPRO_TREE}" archive "${SOURCE_COMMIT}" \
        host/benchmarks/scripts/configs host/benchmarks/scripts/plot host/benchmarks/myworkloads \
        | tar -x -C "${BATCH_DIR}/source-snapshot"
    find "${BATCH_DIR}/source-snapshot" -type f -print0 | sort -z | xargs -0 sha256sum \
        > "${BATCH_DIR}/source-snapshot-sha256.txt"
}

# Run a full offline consistency check after a successful, unmounted case.
run_offline_fsck() {
    local case_id=$1 output_path=$2 log_path="${output_path}/fsck.log"
    local status=0 reason=ok

    FSCK_LAST_RESULT=skipped
    ! findmnt -rn -S "${DEVICE}" >/dev/null \
        || die "cannot run fsck while ${DEVICE} is mounted"
    echo "Running offline fsck for ${output_path}"
    # The invoking user owns the output directory; only fsck needs privilege.
    # shellcheck disable=SC2024
    sudo timeout --foreground --signal=TERM --kill-after=15s \
        "${FSCK_TIMEOUT_SECONDS}" fsck.f2fs -f --dry-run "${DEVICE}" \
        > "${log_path}" 2>&1 || status=$?
    if [ "${status}" -eq 124 ] || [ "${status}" -eq 137 ]; then
        reason=timeout
    elif grep -aEiq '\[ASSERT\]|\[ERROR\]|Segmentation fault|failed to fix' "${log_path}"; then
        status=253
        reason=reported-anomaly
    elif [ "${status}" -ne 0 ]; then
        reason=nonzero-exit
    elif ! grep -q '^Done:' "${log_path}"; then
        status=254
        reason=missing-completion
    fi
    if [ "${status}" -eq 0 ]; then
        FSCK_LAST_RESULT=pass
    else
        FSCK_LAST_RESULT=fail
    fi
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "${case_id}" "${FSCK_LAST_RESULT}" "${status}" "${reason}" "${log_path}" \
        >> "${BATCH_DIR}/fsck-results.tsv"
    if [ "${status}" -ne 0 ]; then
        echo "WARNING: offline fsck failed for ${case_id}: status=${status} reason=${reason}"
        [ "${CONTINUE_ON_FSCK_FAILURE}" -eq 1 ] \
            || die "offline fsck failed for ${output_path}"
    fi
}

# Validate both raw checkpoint packs after a clean unmount.
run_checkpoint_check() {
    local case_id=$1 output_path=$2
    local log_path="${output_path}/checkpoint-curseg-check.log"
    local status=0 result=pass

    ! findmnt -rn -S "${DEVICE}" >/dev/null \
        || die "cannot inspect checkpoints while ${DEVICE} is mounted"
    echo "Checking raw checkpoint curseg offsets for ${output_path}"
    # The output directory is user-owned; only the raw device read needs sudo.
    # shellcheck disable=SC2024
    sudo python3 "${SCRIPT_DIR}/check_f2fs_checkpoint_curseg.py" \
        "${DEVICE}" > "${log_path}" 2>&1 || status=$?
    if [ "${status}" -ne 0 ]; then
        result=fail
    fi
    printf '%s\t%s\t%s\t%s\n' \
        "${case_id}" "${result}" "${status}" "${log_path}" \
        >> "${BATCH_DIR}/checkpoint-results.tsv"
    [ "${status}" -eq 0 ] \
        || die "raw checkpoint validation failed for ${output_path}"
}

# Reject a completed case with missing output or a high-confidence kernel failure.
validate_case() {
    local workload_type=$1 output_path=$2 log_path
    case "${workload_type}" in
        filebench)
            log_path="${output_path}/filebench.log"
            grep -q 'IO Summary:' "${log_path}"
            grep -q 'Run took 300 seconds' "${log_path}"
            [ "$(grep -c 'IO Summary:' "${log_path}")" -eq 60 ]
            ! grep -Eq 'NO VALID RESULTS|Failed to open file|flowop .* failed|Input/output error' \
                "${log_path}"
            ;;
        fio)
            log_path="${output_path}/fio.log"
            grep -q 'Run status group' "${log_path}"
            ! grep -Eq '(^|[^[:alpha:]])err=[1-9][0-9]*' "${log_path}"
            if [ "${COLLECT_GC_PAPER_METRICS}" -eq 1 ]; then
                test -s "${output_path}/gc-paper-metrics.log"
                grep -q 'active=0' "${output_path}/gc-paper-metrics.log"
                grep -Eq '(csgc|origc)_blocks=[1-9][0-9]*' \
                    "${output_path}/gc-paper-metrics.log"
            fi
            ;;
        ycsb)
            log_path="${output_path}/ycsb.log"
            grep -q '\[OVERALL\], Throughput(ops/sec)' "${log_path}"
            grep -q '\[CLEANUP\], Operations, 36' "${log_path}"
            ! grep -q 'Return=ERROR' "${log_path}"
            ;;
    esac
    if grep -aEiq 'BUG:|Oops:|kernel panic|NULL pointer dereference|refcount.*(underflow|saturated)|SIT.*(corrupt|inconsistent)|Inconsistent segment.*SSA and SIT|EUCLEAN|nvme.*(timeout|reset controller)|I/O error|INFO: task .* blocked for more than' \
        "${output_path}/dmesg.log"; then
        die "kernel, device, or lifecycle anomaly detected in ${output_path}/dmesg.log"
    fi
}

# Keep sudo credentials alive during the multi-hour matrix.
start_sudo_keepalive() {
    sudo -v
    (
        while sleep 45; do
            sudo -n -v >/dev/null 2>&1 || exit
        done
    ) &
    SUDO_KEEPALIVE_PID=$!
}

# Stop the credential helper without touching benchmark processes.
stop_sudo_keepalive() {
    if [ -n "${SUDO_KEEPALIVE_PID}" ] && kill -0 "${SUDO_KEEPALIVE_PID}" 2>/dev/null; then
        kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
        wait "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
    fi
    SUDO_KEEPALIVE_PID=""
}

MODE=start
case "${1:-}" in
    --resume)
        [ "$#" -eq 2 ] || { usage; exit 2; }
        MODE=resume
        BATCH_DIR=$(realpath "$2")
        ;;
    --status)
        [ "$#" -eq 2 ] || { usage; exit 2; }
        show_status "$(realpath "$2")"
        exit 0
        ;;
    --dry-run)
        write_schedule /dev/stdout
        exit 0
        ;;
    --preflight)
        mkdir -p "${RESULT_BASE}"
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
flock -n 9 || die "another candidate matrix is already running"

if [ "${MODE}" = start ]; then
    BATCH_DIR="${RESULT_BASE}/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "${BATCH_DIR}/generated-configs" "${BATCH_DIR}/raw" \
        "${BATCH_DIR}/validated"
    write_schedule "${BATCH_DIR}/schedule.tsv"
    printf 'case_id\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tstarted_at\tended_at\tduration_s\tstatus\toutput_path\n' \
        > "${BATCH_DIR}/case-results.tsv"
    printf 'case_id\tconfiguration\tmodule_parameter\tmodule_sha256\n' \
        > "${BATCH_DIR}/runtime-modes.tsv"
    printf 'case_id\tresult\texit_status\treason\tlog_path\n' \
        > "${BATCH_DIR}/fsck-results.tsv"
    printf 'case_id\tresult\texit_status\tlog_path\n' \
        > "${BATCH_DIR}/checkpoint-results.tsv"
else
    [ -f "${BATCH_DIR}/schedule.tsv" ] || die "resume batch has no schedule.tsv"
    [ -f "${BATCH_DIR}/case-results.tsv" ] || die "resume batch has no case-results.tsv"
    mkdir -p "${BATCH_DIR}/validated"
    if [ ! -f "${BATCH_DIR}/runtime-modes.tsv" ]; then
        printf 'case_id\tconfiguration\tmodule_parameter\tmodule_sha256\n' \
            > "${BATCH_DIR}/runtime-modes.tsv"
    fi
    if [ ! -f "${BATCH_DIR}/fsck-results.tsv" ]; then
        printf 'case_id\tresult\texit_status\treason\tlog_path\n' \
            > "${BATCH_DIR}/fsck-results.tsv"
    fi
    if [ ! -f "${BATCH_DIR}/checkpoint-results.tsv" ]; then
        printf 'case_id\tresult\texit_status\tlog_path\n' \
            > "${BATCH_DIR}/checkpoint-results.tsv"
    fi
fi

CASE_RESULTS="${BATCH_DIR}/case-results.tsv"
OUTER_START_TICKS=$(awk '{print $22}' "/proc/$$/stat")
STARTED_AT=$(date --iso-8601=seconds)
printf '%s\n' "${BATCH_DIR}" > "${RESULT_BASE}/latest-batch.txt"
exec > >(tee -a "${BATCH_DIR}/runner.log") 2>&1

# Mark an interrupted or failed outer matrix without discarding partial results.
finalize() {
    local status=$?
    stop_sudo_keepalive
    if [ "${status}" -ne 0 ]; then
        write_state failed
        printf 'failed_at=%s\nexit_status=%s\n' "$(date --iso-8601=seconds)" "${status}" \
            > "${BATCH_DIR}/failure.env"
    fi
    exit "${status}"
}
trap finalize EXIT

echo "Batch directory: ${BATCH_DIR}"
echo "This matrix resets and overwrites ${DEVICE} once per case."
echo "No interactive confirmation is required."
preflight
write_state running
start_sudo_keepalive

if [ "${MODE}" = start ]; then
    write_provenance
else
    {
        printf '\n[resume]\nresumed_at=%s\nartifact_branch=%s\nartifact_commit=%s\n' \
            "$(date --iso-8601=seconds)" \
            "$(git -C "${REPRO_TREE}" branch --show-current)" \
            "$(git -C "${REPRO_TREE}" rev-parse HEAD)"
    } >> "${BATCH_DIR}/provenance.txt"
fi
build_nvme_cli
for configuration in "${CONFIGURATIONS[@]}"; do
    ensure_exact_host_worktree "${configuration}"
    build_configuration "${configuration}"
done

completed_before=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
echo "Starting matrix with ${completed_before}/${EXPECTED_CASES} successful cases already recorded."

while IFS=$'\t' read -r case_id configuration mode workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased <&8; do
    [ "${case_id}" = case_id ] && continue
    if case_succeeded "${case_id}"; then
        echo "Skipping completed case ${case_id}"
        continue
    fi
    if case_has_success_row "${case_id}"; then
        output_path=$(awk -F '\t' -v id="${case_id}" \
            '$1 == id && $11 == 0 {path=$12} END {print path}' "${CASE_RESULTS}")
        echo "Recovering validation for completed test ${case_id}"
        validate_case "${workload_type}" "${output_path}"
        run_checkpoint_check "${case_id}" "${output_path}"
        run_offline_fsck "${case_id}" "${output_path}"
        printf 'validated_at=%s\noutput_path=%s\nfsck_result=%s\n' \
            "$(date --iso-8601=seconds)" "${output_path}" "${FSCK_LAST_RESULT}" \
            > "${BATCH_DIR}/validated/${case_id}.ok"
        continue
    fi

    verify_openssd_provenance >/dev/null
    verify_nvme_cli
    load_configuration "${configuration}"
    printf '%s\t%s\t%s\t%s\n' \
        "${case_id}" "${configuration}" "${LOADED_NODE_READAHEAD_MODE}" \
        "$(sha256sum "${MODULE_PATHS[${configuration}]}" | awk '{print $1}')" \
        >> "${BATCH_DIR}/runtime-modes.tsv"
    config_path="${BATCH_DIR}/generated-configs/${case_id}.sh"
    write_case_config "${config_path}" "${workload_type}" "${bmname}" "${distribution}" \
        "${prefill_ratio}" "${segs_per_sec}" "${fio_timebased}"
    chmod +x "${config_path}"

    echo "MATRIX_CASE_START id=${case_id} configuration=${configuration} at=$(date --iso-8601=seconds)"
    EUROPAR_OUTPUT_ROOT="${BATCH_DIR}/raw/${configuration}" \
    EUROPAR_CASE_RESULTS="${CASE_RESULTS}" \
    EUROPAR_CASE_ID="${case_id}" \
    FILEBENCH_REPORT_INTERVAL=$([ "${workload_type}" = filebench ] && echo 5 || echo 0) \
    FILEBENCH_RUNTIME_OVERRIDE=$([ "${workload_type}" = filebench ] && echo 300 || echo '') \
    F2FS_STATUS_SAMPLE_INTERVAL=0 \
    GC_PAPER_METRICS=$([ "${workload_type}" = fio ] && echo "${COLLECT_GC_PAPER_METRICS}" || echo 0) \
    KERNEL_PANIC_TIMEOUT=0 \
        "${SCRIPT_DIR}/test.sh" "${mode}" "${config_path}"

    output_path=$(awk -F '\t' -v id="${case_id}" \
        '$1 == id && $11 == 0 {path=$12} END {print path}' "${CASE_RESULTS}")
    [ -n "${output_path}" ] || die "no successful result row for ${case_id}"
    validate_case "${workload_type}" "${output_path}"
    run_checkpoint_check "${case_id}" "${output_path}"
    run_offline_fsck "${case_id}" "${output_path}"
    printf 'validated_at=%s\noutput_path=%s\nfsck_result=%s\n' \
        "$(date --iso-8601=seconds)" "${output_path}" "${FSCK_LAST_RESULT}" \
        > "${BATCH_DIR}/validated/${case_id}.ok"
    successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
    echo "MATRIX_CASE_END id=${case_id} progress=${successful}/${EXPECTED_CASES} at=$(date --iso-8601=seconds)"
done 8< "${BATCH_DIR}/schedule.tsv"

successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
[ "${successful}" -eq "${EXPECTED_CASES}" ] \
    || die "expected ${EXPECTED_CASES} successful cases, found ${successful}"
validated=$(find "${BATCH_DIR}/validated" -maxdepth 1 -type f -name '*.ok' | wc -l)
[ "${validated}" -eq "${EXPECTED_CASES}" ] \
    || die "expected ${EXPECTED_CASES} validated cases, found ${validated}"
fsck_failures=$(awk -F '\t' 'NR > 1 && $2 == "fail" {n++} END {print n + 0}' \
    "${BATCH_DIR}/fsck-results.tsv")
[ "${fsck_failures}" -eq 0 ] || [ "${CONTINUE_ON_FSCK_FAILURE}" -eq 1 ] \
    || die "offline fsck failures: ${fsck_failures}"

COMPLETED_AT=$(date --iso-8601=seconds)
printf 'started_at=%s\ncompleted_at=%s\nsuccessful_cases=%s\nvalidated_cases=%s\nfsck_failures=%s\n' \
    "${STARTED_AT}" "${COMPLETED_AT}" "${successful}" "${validated}" \
    "${fsck_failures}" > "${BATCH_DIR}/completed.env"
"${ANALYZER_SCRIPT}" "${BATCH_DIR}"
write_state success
stop_sudo_keepalive
trap - EXIT
echo "EUROPAR_PAPER_METRICS_COMPLETE status=success cases=${successful} validated=${validated} completed_at=${COMPLETED_AT}"
