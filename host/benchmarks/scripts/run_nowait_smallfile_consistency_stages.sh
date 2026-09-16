#!/usr/bin/env bash

set -euo pipefail

# Execute a stable in-memory copy so edits cannot alter a live destructive run.
if [ -z "${NOWAIT_CONSISTENCY_SNAPSHOT:-}" ]; then
    snapshot_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    snapshot_body=$(<"${snapshot_path}")
    export NOWAIT_CONSISTENCY_SNAPSHOT=1
    export NOWAIT_CONSISTENCY_SCRIPT_PATH="${snapshot_path}"
    exec /bin/bash -c "${snapshot_body}" "${snapshot_path}" "$@"
fi

SCRIPT_PATH=${NOWAIT_CONSISTENCY_SCRIPT_PATH}
SCRIPT_DIR=$(cd -- "$(dirname -- "${SCRIPT_PATH}")" && pwd)
HOST_REPO=/home/xin/work-xie/mcsgc-real/linux-cs
HOST_BRANCH=exp/formal-mcsgc8t-nowait-quiet-20260916
HOST_COMMIT=dec1964f0bf196b1929799119e93393bfa6b79fb
DEVICE=/dev/nvme0n1
NVME_CLI=/home/xin/artifact-csgc/host/src/nvme-cli/nvme
NVME_CLI_SHA256=73b9a48e3a183fe14743e6f27ec0adc982d0d27e4a6c8a5a5b9d559a62528563
PREPARE_SCRIPT=${SCRIPT_DIR}/prepare_gc_breakdown_host_module.sh
RUNNER=${SCRIPT_DIR}/run_gc_breakdown_diagnostic.sh
READONLY_FSCK=${SCRIPT_DIR}/offline_fsck_readonly.sh
PREFLIGHT=${SCRIPT_DIR}/run_formal_mcsgc8t_nowait_quiet_gcheavy_3x.sh
RESULT_BASE=${SCRIPT_DIR}/outputs-nowait-smallfile-consistency-stages
BATCH_DIR=""
HOST_TREE=""
MODULE_PATH=""
MODULE_SRCVERSION=""
MODULE_SHA256=""

# Keep unattended execution non-interactive.
sudo() {
    command /usr/bin/sudo -n "$@"
}
export -f sudo

# Abort while preserving every artifact produced so far.
die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Persist a simple state record for sparse external monitoring.
write_state() {
    local status=$1

    [ -n "${BATCH_DIR}" ] || return 0
    {
        printf 'batch_status=%s\n' "${status}"
        printf 'updated_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'outer_pid=%s\n' "$$"
        printf 'outer_start_ticks=%s\n' "$(awk '{print $22}' "/proc/$$/stat")"
        printf 'batch_dir=%s\n' "${BATCH_DIR}"
    } > "${BATCH_DIR}/state.env"
}

# Preserve a terminal failed state if an unexpected command exits the script.
finalize() {
    local status=$?

    trap - EXIT
    if [ "${status}" -ne 0 ] && [ -n "${BATCH_DIR}" ] && [ -d "${BATCH_DIR}" ]; then
        current_status=$(sed -n 's/^batch_status=//p' "${BATCH_DIR}/state.env" 2>/dev/null | tail -n 1)
        if [[ "${current_status}" != corrupt-* ]]; then
            write_state failed
        fi
        printf 'failed_at=%s\nexit_status=%s\n' \
            "$(date --iso-8601=seconds)" "${status}" > "${BATCH_DIR}/failure.env"
    fi
    exit "${status}"
}
trap finalize EXIT

# Locate and validate the pinned Host worktree.
resolve_host_tree() {
    source "${SCRIPT_DIR}/formal_host_worktree.sh"
    HOST_TREE=$(resolve_formal_host_tree "${HOST_REPO}" "${HOST_BRANCH}")
    [ "$(git -C "${HOST_TREE}" rev-parse HEAD)" = "${HOST_COMMIT}" ] \
        || die "Host worktree is not at the pinned commit"
}

# Build once and record the exact module identity used by all stages.
build_host_module() {
    git -C "${HOST_TREE}" show "${HOST_COMMIT}:.config" > "${HOST_TREE}/.config"
    "${PREPARE_SCRIPT}" mcsgc8t-nowait-quiet
    MODULE_PATH=${HOST_TREE}/fs/f2fs/f2fs.ko
    [ -r "${MODULE_PATH}" ] || die "Host module was not produced"
    MODULE_SRCVERSION=$(modinfo -F srcversion "${MODULE_PATH}")
    MODULE_SHA256=$(sha256sum "${MODULE_PATH}" | awk '{print $1}')
}

# Replace the unloaded F2FS module and verify the live build identity.
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
        || die "loaded F2FS module does not match the pinned build"
}

# Reject a stage with a high-confidence kernel or device failure.
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

# Run one fresh-filesystem stage and stop at the first failing read-only fsck.
run_stage() {
    local sequence=$1
    local stage=$2
    local stop_after=${stage}
    local label result_path run_dir fsck_status=0

    label=$(printf '%02d-%s' "${sequence}" "${stage}")
    result_path=${BATCH_DIR}/${label}.result-path
    if [ "${stage}" = measurement ]; then
        stop_after=none
    fi

    write_state "running-${stage}"
    echo
    echo "Starting consistency stage ${stage} at $(date --iso-8601=seconds)"
    load_host_module
    sudo env \
        GC_BREAKDOWN_RESULT_PATH_FILE="${result_path}" \
        KERNEL_PANIC_TIMEOUT=0 \
        FIO_CONSISTENCY_STOP_AFTER="${stop_after}" \
        NVME_CLI="${NVME_CLI}" \
        "${RUNNER}" mcsgc8t-nowait-quiet smallfile

    [ -s "${result_path}" ] || die "result path was not recorded for ${stage}"
    run_dir=$(<"${result_path}")
    check_kernel_anomalies "${run_dir}"
    set +e
    "${READONLY_FSCK}" "${DEVICE}" "${BATCH_DIR}/${label}.fsck.log" 900 20000
    fsck_status=$?
    set -e

    printf '%s\t%s\t%s\t%s\n' \
        "${sequence}" "${stage}" "${fsck_status}" "${run_dir}" \
        >> "${BATCH_DIR}/results.tsv"
    ln -s "${run_dir}" "${BATCH_DIR}/${label}.run"
    if [ "${fsck_status}" -ne 0 ]; then
        write_state "corrupt-${stage}"
        echo "The first observed consistency failure is after stage: ${stage}" \
            | tee "${BATCH_DIR}/conclusion.txt"
        exit 3
    fi
    echo "Consistency stage passed: ${stage}"
}

[ "${EUID}" -ne 0 ] || die "run this script as the login user"
[ "$#" -eq 0 ] || die "this script takes no arguments"
[ -b "${DEVICE}" ] || die "block device is unavailable: ${DEVICE}"
findmnt -rn -S "${DEVICE}" >/dev/null && die "${DEVICE} is currently mounted"
sudo -n true || die "passwordless sudo is unavailable"
[ -x "${RUNNER}" ] || die "benchmark runner is unavailable"
[ -x "${READONLY_FSCK}" ] || die "read-only fsck helper is unavailable"
[ -x "${NVME_CLI}" ] || die "pinned nvme-cli is unavailable: ${NVME_CLI}"
[ "$(sha256sum "${NVME_CLI}" | awk '{print $1}')" = "${NVME_CLI_SHA256}" ] \
    || die "pinned nvme-cli hash does not match"

"${PREFLIGHT}" --preflight
resolve_host_tree

mkdir -p "${RESULT_BASE}"
exec 9>"${RESULT_BASE}/stages.lock"
flock -n 9 || die "another consistency-stage run is active"
BATCH_DIR=${RESULT_BASE}/$(date +%Y%m%d_%H%M%S)
mkdir -p "${BATCH_DIR}"
printf '%s\n' "${BATCH_DIR}" > "${RESULT_BASE}/latest-batch.txt"
printf 'sequence\tstage\tfsck_status\trun_dir\n' > "${BATCH_DIR}/results.tsv"
exec > >(tee -a "${BATCH_DIR}/runner.log") 2>&1

{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'host_branch=%s\nhost_commit=%s\n' "${HOST_BRANCH}" "${HOST_COMMIT}"
    printf 'device=%s\nworkload=smallfile\n' "${DEVICE}"
    printf 'stages=prefill,precondition,measurement\n'
    printf 'fsck_mode=force-full-dry-run\n'
    printf 'nvme_cli=%s\nnvme_cli_sha256=%s\n' \
        "${NVME_CLI}" "${NVME_CLI_SHA256}"
} > "${BATCH_DIR}/manifest.txt"

echo "DESTRUCTIVE WARNING: this diagnostic resets and overwrites ${DEVICE} up to three times."
echo "Batch directory: ${BATCH_DIR}"
write_state preparing
build_host_module
{
    printf 'host_tree=%s\nmodule_sha256=%s\nmodule_srcversion=%s\n' \
        "${HOST_TREE}" "${MODULE_SHA256}" "${MODULE_SRCVERSION}"
} >> "${BATCH_DIR}/manifest.txt"

run_stage 1 prefill
run_stage 2 precondition
run_stage 3 measurement

write_state completed
printf 'All consistency stages passed.\n' | tee "${BATCH_DIR}/conclusion.txt"
