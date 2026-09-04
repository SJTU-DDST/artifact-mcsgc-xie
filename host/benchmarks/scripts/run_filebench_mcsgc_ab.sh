#!/usr/bin/env bash

set -euo pipefail

# Isolate the Filebench regression with one Host variable per configuration.

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)
REPRO_TREE=$(cd -- "${SCRIPT_DIR}/../../.." && pwd)
SOURCE_COMMIT=0271b907ec00ed643fd139403b726817c9fe8c32
NVME_CLI_DIR=${REPRO_TREE}/host/src/nvme-cli
NVME_CLI_PATH=${NVME_CLI_DIR}/nvme
HOST_REPO=/home/xin/work-xie/mcsgc-real/linux-cs
OPENSSD_HOST=192.168.98.31
OPENSSD_TREE=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
OPENSSD_BRANCH=exp/formal-mcsgc-quiet-20260809
OPENSSD_COMMIT=52831c159c9f7a73f9670c163a6b513750f64b47
DEVICE=/dev/nvme0n1
RESULT_BASE=/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab
RUN_PROFILE=${FILEBENCH_AB_PROFILE:-screen}
REPETITIONS=${FILEBENCH_AB_REPETITIONS:-3}
REPEAT_CONFIGS=${FILEBENCH_AB_CONFIGS:-control,standard-prefree}
WORKLOAD_FILTER=${FILEBENCH_AB_WORKLOADS:-filebench-fileserver,filebench-varmail}
REPORT_INTERVAL=${FILEBENCH_AB_REPORT_INTERVAL:-5}
STATUS_SAMPLE_INTERVAL=${FILEBENCH_AB_STATUS_SAMPLE_INTERVAL:-5}
for diagnostic_interval in "${REPORT_INTERVAL}" "${STATUS_SAMPLE_INTERVAL}"; do
    case "${diagnostic_interval}" in
        ''|*[!0-9]*)
            echo "ERROR: diagnostic intervals must be non-negative integers" >&2
            exit 2
            ;;
    esac
done
IFS=',' read -r -a WORKLOADS <<< "${WORKLOAD_FILTER}"
[ "${#WORKLOADS[@]}" -ge 1 ] || {
    echo "ERROR: FILEBENCH_AB_WORKLOADS must not be empty" >&2
    exit 2
}
declare -A SEEN_WORKLOADS=()
for workload in "${WORKLOADS[@]}"; do
    case "${workload}" in
        filebench-fileserver|filebench-varmail) ;;
        *) echo "ERROR: unsupported Filebench workload: ${workload}" >&2; exit 2 ;;
    esac
    [ -z "${SEEN_WORKLOADS[${workload}]:-}" ] || {
        echo "ERROR: duplicate Filebench workload: ${workload}" >&2
        exit 2
    }
    SEEN_WORKLOADS[${workload}]=1
done
case "${RUN_PROFILE}" in
    screen)
        declare -a CONFIGURATIONS=(control standard-prefree pre-sync single-section)
        REPETITIONS=1
        ;;
    repeat)
        IFS=',' read -r -a CONFIGURATIONS <<< "${REPEAT_CONFIGS}"
        [ "${REPETITIONS}" -ge 1 ] 2>/dev/null ||
            { echo "ERROR: FILEBENCH_AB_REPETITIONS must be positive" >&2; exit 2; }
        ;;
    *) echo "ERROR: unsupported FILEBENCH_AB_PROFILE=${RUN_PROFILE}" >&2; exit 2 ;;
esac
EXPECTED_CASES=$((${#CONFIGURATIONS[@]} * ${#WORKLOADS[@]} * REPETITIONS))
MINIMUM_FREE_BYTES=$((5 * 1024 * 1024 * 1024))

declare -A HOST_BRANCHES=(
    [control]=exp/diagnostic-mcsgc8t-filebench-control-20260831
    [standard-prefree]=exp/diagnostic-mcsgc8t-filebench-standard-prefree-20260831
    [pre-sync]=exp/diagnostic-mcsgc8t-filebench-presync-20260831
    [single-section]=exp/diagnostic-mcsgc8t-filebench-single-section-20260831
    [node-checkpoint]=exp/diagnostic-mcsgc8t-filebench-node-checkpoint-20260903
    [allocator-first]=exp/diagnostic-mcsgc8t-filebench-allocator-first-20260903
    [segment-window1]=exp/diagnostic-mcsgc8t-filebench-segment-window1-20260901
    [segment-window2]=exp/diagnostic-mcsgc8t-filebench-segment-window2-20260901
    [segment-window4]=exp/diagnostic-mcsgc8t-filebench-segment-window4-20260901
)
declare -A HOST_COMMITS=(
    [control]=b6fb9bccbbbe4c3bf7dd666f808fb6f2e1e1c145
    [standard-prefree]=6800913218b20ffe9cd5b8c8b2c4ee52837ae8ca
    [pre-sync]=38ab7d74408ecb8b9d05f7df933a42539aaf269f
    [single-section]=d38d3355588d22bb8c17ecb53ca60a79ad6879f0
    [node-checkpoint]=af64f2ee7bc66d48528b382c14c78919590ddad3
    [allocator-first]=7fb6548b802d2408c586040d90f8bbdb5a4ed679
    [segment-window1]=d46a18679aa81f84885d5950ff2de812af7dcac5
    [segment-window2]=5035e83f40644295c0d99fd777ad6011174444f9
    [segment-window4]=0064d7c870952166e9203d6bc16fee8a947a2508
)
declare -A HOST_BASE_COMMITS=(
    [control]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [standard-prefree]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [pre-sync]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [single-section]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [node-checkpoint]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [allocator-first]=5262b5a3979cc55302ae0300cbd8f24b51c60c24
    [segment-window1]=01e74974489c6d72555d70909541ec22ec887acd
    [segment-window2]=01e74974489c6d72555d70909541ec22ec887acd
    [segment-window4]=01e74974489c6d72555d70909541ec22ec887acd
)
declare -A PREFERRED_WORKTREES=(
    [control]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-control-20260831
    [standard-prefree]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-prefree-20260831
    [pre-sync]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-presync-20260831
    [single-section]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-single-20260831
    [node-checkpoint]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-node-checkpoint-20260903
    [allocator-first]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-allocator-first-20260903
    [segment-window1]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-window1-20260901
    [segment-window2]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-window2-20260901
    [segment-window4]=/home/xin/work-xie/mcsgc-real/linux-cs-filebench-window4-20260901
)
declare -A HOST_TREES=()
declare -A MODULE_PATHS=()
declare -A MODULE_SRCVERSIONS=()
declare -A COMMITTED_CONFIG_SHA256S=()
declare -A BUILD_CONFIG_SHA256S=()

SUDO_KEEPALIVE_PID=""
BATCH_DIR=""
CASE_RESULTS=""
OUTER_START_TICKS=""
STARTED_AT=""
NVME_CLI_SHA256=""

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

The default screen builds four pinned Host configurations and runs Filebench
fileserver and varmail once per configuration, for eight destructive cases.
Every case resets, formats, and overwrites ${DEVICE}. No interactive prompt is
used. Run this script as the regular login user; it invokes sudo internally.

Expected OpenSSD source:
  ${OPENSSD_BRANCH}@${OPENSSD_COMMIT}

After screening, repeat selected configurations with:
  FILEBENCH_AB_PROFILE=repeat
  FILEBENCH_AB_CONFIGS=control,CONFIGURATION
  FILEBENCH_AB_WORKLOADS=filebench-fileserver,filebench-varmail
  FILEBENCH_AB_REPETITIONS=3

Low-overhead F2FS status sampling and periodic Filebench output both default to
5 seconds. Set FILEBENCH_AB_STATUS_SAMPLE_INTERVAL=0 or
FILEBENCH_AB_REPORT_INTERVAL=0 to disable the corresponding timeline.
EOF
}

# Abort while preserving all completed batch files.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Emit the two exact Euro-Par Filebench workloads.
write_base_cases() {
    local workload

    for workload in "${WORKLOADS[@]}"; do
        case "${workload}" in
            filebench-fileserver)
                printf 'filebench-fileserver\tfilebench\tfileserver_4t_60G_1M_54k\trandom\t0.86\t8\t0\n'
                ;;
            filebench-varmail)
                printf 'filebench-varmail\tfilebench\tvarmail_4t_60G_1M_54k\trandom\t0.86\t8\t0\n'
                ;;
        esac
    done
}

# Alternate configuration order across workloads and repetitions.
write_schedule() {
    local path=$1
    local index=0
    local repetition
    local base_id workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased
    local configuration
    local -a order

    printf 'case_id\tconfiguration\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tfio_timebased\n' > "${path}"
    for ((repetition = 1; repetition <= REPETITIONS; repetition++)); do
        while IFS=$'\t' read -r base_id workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased; do
            order=("${CONFIGURATIONS[@]}")
            if (((index + repetition - 1) % 2 != 0)); then
                mapfile -t order < <(printf '%s\n' "${order[@]}" | tac)
            fi
            for configuration in "${order[@]}"; do
                printf '%s-%s-r%s\t%s\tcs\t%s\t%s\t%s\t%s\t%s\t%s\n' \
                    "${configuration}" "${base_id}" "${repetition}" \
                    "${configuration}" "${workload_type}" "${bmname}" \
                    "${distribution}" "${prefill_ratio}" "${segs_per_sec}" \
                    "${fio_timebased}" >> "${path}"
            done
            index=$((index + 1))
        done < <(write_base_cases)
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
        printf 'run_profile=%q\n' "${RUN_PROFILE}"
        printf 'workloads=%q\n' "${WORKLOADS[*]}"
        printf 'filebench_report_interval_s=%q\n' "${REPORT_INTERVAL}"
        printf 'f2fs_status_sample_interval_s=%q\n' "${STATUS_SAMPLE_INTERVAL}"
        printf 'batch_dir=%q\n' "${BATCH_DIR}"
    } > "${BATCH_DIR}/state.env"
}

# Show progress without touching the device or running processes.
show_status() {
    local batch=$1
    local completed=0
    local failed=0
    if [ -f "${batch}/case-results.tsv" ]; then
        completed=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${batch}/case-results.tsv")
        failed=$(awk -F '\t' 'NR > 1 && $11 != 0 {n++} END {print n + 0}' "${batch}/case-results.tsv")
    fi
    printf 'batch=%s\ncompleted=%s/%s\nfailed_rows=%s\n' \
        "${batch}" "${completed}" "${EXPECTED_CASES}" "${failed}"
    [ ! -f "${batch}/state.env" ] || sed -n '1,12p' "${batch}/state.env"
}

# Return success when a case already has a successful result row.
case_succeeded() {
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

# Build quiet candidate code with the common F2FS statistics used by the baseline.
build_configuration() {
    local configuration=$1
    local tree=${HOST_TREES[${configuration}]}
    local commit=${HOST_COMMITS[${configuration}]}
    local module_path="${tree}/fs/f2fs/f2fs.ko"
    local committed_sha build_sha module_sha module_srcversion

    echo "Building ${configuration} from ${commit} at $(date --iso-8601=seconds)"
    git -C "${tree}" show "${commit}:.config" > "${tree}/.config"
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
    module_sha=$(sha256sum "${module_path}" | awk '{print $1}')
    module_srcversion=$(modinfo -F srcversion "${module_path}")
    [ -n "${module_srcversion}" ] || die "module has no srcversion: ${module_path}"

    MODULE_PATHS[${configuration}]=${module_path}
    MODULE_SRCVERSIONS[${configuration}]=${module_srcversion}
    COMMITTED_CONFIG_SHA256S[${configuration}]=${committed_sha}
    BUILD_CONFIG_SHA256S[${configuration}]=${build_sha}
    {
        printf '\n[build-%s]\n' "${configuration}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'host_tree=%s\nhost_branch=%s\nhost_commit=%s\n' \
            "${tree}" "${HOST_BRANCHES[${configuration}]}" "${commit}"
        printf 'committed_config_sha256=%s\nbuild_config_sha256=%s\n' \
            "${committed_sha}" "${build_sha}"
        printf 'comparison_config_override=CONFIG_F2FS_STAT_FS=y\n'
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
}

# Check the non-destructive environment and exact workload source.
preflight() {
    local source_diff openssd_state available_bytes process configuration
    [ "${EUID}" -ne 0 ] || die "run this launcher as the regular user"
    sudo -n true || sudo -v
    [ -b "${DEVICE}" ] || die "missing block device: ${DEVICE}"
    findmnt -rn -S "${DEVICE}" >/dev/null && die "${DEVICE} is mounted"
    for process in fio filebench java mysqld; do
        ! pgrep -x "${process}" >/dev/null || die "process is already running: ${process}"
    done
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
        [ -n "${HOST_COMMITS[${configuration}]:-}" ] \
            || die "unknown Host configuration: ${configuration}"
        git -C "${HOST_REPO}" cat-file -e "${HOST_COMMITS[${configuration}]}^{commit}"
    done
    for process in filebench python2 cgexec bc flock tac; do
        command -v "${process}" >/dev/null || die "required command is missing: ${process}"
    done
    [ -x "${SCRIPT_DIR}/../file_writer/build.sh" ] || die "file writer build script is missing"
    [ -f "${NVME_CLI_DIR}/Makefile" ] || die "nvme-cli source tree is missing"
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
        printf 'outer_script=%s\nrun_profile=%s\nstarted_at=%s\n' \
            "${SCRIPT_PATH}" "${RUN_PROFILE}" "${STARTED_AT}"
        printf 'artifact_branch=%s\nartifact_commit=%s\nsource_commit=%s\n' \
            "$(git -C "${REPRO_TREE}" branch --show-current)" \
            "$(git -C "${REPRO_TREE}" rev-parse HEAD)" "${SOURCE_COMMIT}"
        printf 'filebench_ab_profile=%s\nrepetitions=%s\nconfigurations=%s\nworkloads=%s\n' \
            "${RUN_PROFILE}" "${REPETITIONS}" "${CONFIGURATIONS[*]}" \
            "${WORKLOADS[*]}"
        printf 'filebench_report_interval_s=%s\nf2fs_status_sample_interval_s=%s\n' \
            "${REPORT_INTERVAL}" "${STATUS_SAMPLE_INTERVAL}"
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

# Reject a completed case with missing output or a high-confidence kernel failure.
validate_case() {
    local workload_type=$1 output_path=$2 log_path
    case "${workload_type}" in
        filebench)
            log_path="${output_path}/filebench.log"
            grep -q 'IO Summary:' "${log_path}"
            ! grep -Eq 'NO VALID RESULTS|Failed to open file|flowop .* failed|Input/output error' \
                "${log_path}"
            ;;
        fio)
            log_path="${output_path}/fio.log"
            grep -q 'Run status group' "${log_path}"
            ! grep -Eq '(^|[^[:alpha:]])err=[1-9][0-9]*' "${log_path}"
            ;;
        ycsb)
            log_path="${output_path}/ycsb.log"
            grep -q '\[OVERALL\], Throughput(ops/sec)' "${log_path}"
            grep -q '\[CLEANUP\], Operations, 36' "${log_path}"
            ! grep -q 'Return=ERROR' "${log_path}"
            ;;
    esac
    if grep -aEiq 'BUG:|Oops:|kernel panic|NULL pointer dereference|blocked for more than|refcount.*(underflow|saturated)|SIT.*(corrupt|inconsistent)|Inconsistent segment.*SSA and SIT|EUCLEAN|nvme.*(timeout|reset controller)|I/O error' \
        "${output_path}/dmesg.log"; then
        die "kernel or device anomaly detected in ${output_path}/dmesg.log"
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
    mkdir -p "${BATCH_DIR}/generated-configs" "${BATCH_DIR}/raw"
    write_schedule "${BATCH_DIR}/schedule.tsv"
    printf 'case_id\tmode\tworkload_type\tbmname\tdistribution\tprefill_ratio\tsegs_per_sec\tstarted_at\tended_at\tduration_s\tstatus\toutput_path\n' \
        > "${BATCH_DIR}/case-results.tsv"
else
    [ -f "${BATCH_DIR}/schedule.tsv" ] || die "resume batch has no schedule.tsv"
    [ -f "${BATCH_DIR}/case-results.tsv" ] || die "resume batch has no case-results.tsv"
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
fi
build_nvme_cli
for configuration in "${CONFIGURATIONS[@]}"; do
    ensure_exact_host_worktree "${configuration}"
    build_configuration "${configuration}"
done
reference_configuration=${CONFIGURATIONS[0]}
for configuration in "${CONFIGURATIONS[@]:1}"; do
    [ "${COMMITTED_CONFIG_SHA256S[${reference_configuration}]}" = \
      "${COMMITTED_CONFIG_SHA256S[${configuration}]}" ] \
        || die "Host commits contain different kernel configurations"
    [ "${BUILD_CONFIG_SHA256S[${reference_configuration}]}" = \
      "${BUILD_CONFIG_SHA256S[${configuration}]}" ] \
        || die "Host build configurations differ"
done

completed_before=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
echo "Starting matrix with ${completed_before}/${EXPECTED_CASES} successful cases already recorded."

while IFS=$'\t' read -r case_id configuration mode workload_type bmname distribution prefill_ratio segs_per_sec fio_timebased <&8; do
    [ "${case_id}" = case_id ] && continue
    if case_succeeded "${case_id}"; then
        echo "Skipping completed case ${case_id}"
        continue
    fi

    verify_openssd_provenance >/dev/null
    verify_nvme_cli
    load_configuration "${configuration}"
    config_path="${BATCH_DIR}/generated-configs/${case_id}.sh"
    write_case_config "${config_path}" "${workload_type}" "${bmname}" "${distribution}" \
        "${prefill_ratio}" "${segs_per_sec}" "${fio_timebased}"
    chmod +x "${config_path}"

    echo "MATRIX_CASE_START id=${case_id} configuration=${configuration} at=$(date --iso-8601=seconds)"
    EUROPAR_OUTPUT_ROOT="${BATCH_DIR}/raw/${configuration}" \
    EUROPAR_CASE_RESULTS="${CASE_RESULTS}" \
    EUROPAR_CASE_ID="${case_id}" \
    FILEBENCH_REPORT_INTERVAL="${REPORT_INTERVAL}" \
    F2FS_STATUS_SAMPLE_INTERVAL="${STATUS_SAMPLE_INTERVAL}" \
        "${SCRIPT_DIR}/test.sh" "${mode}" "${config_path}"

    output_path=$(awk -F '\t' -v id="${case_id}" \
        '$1 == id && $11 == 0 {path=$12} END {print path}' "${CASE_RESULTS}")
    [ -n "${output_path}" ] || die "no successful result row for ${case_id}"
    validate_case "${workload_type}" "${output_path}"
    successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
    echo "MATRIX_CASE_END id=${case_id} progress=${successful}/${EXPECTED_CASES} at=$(date --iso-8601=seconds)"
done 8< "${BATCH_DIR}/schedule.tsv"

successful=$(awk -F '\t' 'NR > 1 && $11 == 0 {n++} END {print n + 0}' "${CASE_RESULTS}")
[ "${successful}" -eq "${EXPECTED_CASES}" ] \
    || die "expected ${EXPECTED_CASES} successful cases, found ${successful}"

"${SCRIPT_DIR}/analyze_filebench_mcsgc_ab.py" "${BATCH_DIR}"
COMPLETED_AT=$(date --iso-8601=seconds)
printf 'started_at=%s\ncompleted_at=%s\nsuccessful_cases=%s\n' \
    "${STARTED_AT}" "${COMPLETED_AT}" "${successful}" > "${BATCH_DIR}/completed.env"
write_state success
stop_sudo_keepalive
trap - EXIT
echo "EUROPAR_MCSGC_CANDIDATE_MATRIX_COMPLETE status=success cases=${successful} completed_at=${COMPLETED_AT}"
