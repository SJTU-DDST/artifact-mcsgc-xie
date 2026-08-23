#!/usr/bin/env bash

set -euo pipefail

# Execute an in-memory snapshot so repository updates cannot alter a live run.
if [ -z "${GC_CORE3_SCHEDULER_AB_SNAPSHOT:-}" ]; then
    script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    script_body=$(<"${script_path}")
    export GC_CORE3_SCHEDULER_AB_SNAPSHOT=1
    export GC_CORE3_SCHEDULER_AB_SCRIPT_PATH="${script_path}"
    exec /bin/bash -c "${script_body}" "${script_path}" "$@"
fi

script_path=${GC_CORE3_SCHEDULER_AB_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${script_path}")" && pwd)
artifact_repo=$(git -C "${script_dir}" rev-parse --show-toplevel)
artifact_branch=exp/diagnostic-csgc-core3-fair-scheduler-20260823
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
host_branch=exp/diagnostic-mcsgc8t-supply-rootcause-lifecycle-20260823
host_commit=f5ce2311115c466a7583ddefe045b1de8aa9020d
host_worktree=/home/xin/work-xie/mcsgc-real/linux-cs-supply-rootcause-lifecycle-20260823
openssd_host=192.168.98.31
openssd_repo=/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
openssd_branch=exp/diagnostic-mcsgc-core3-fair-scheduler-20260823
openssd_commit=274b005c096f74f5609277a0a0cb76cf7ecece55
configuration=mcsgc8t-supply-rootcause
prepare_script="${script_dir}/prepare_gc_breakdown_host_module.sh"
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
validator="${script_dir}/validate-core3-scheduler-run.py"
comparator="${script_dir}/draw-xie/compare-core3-scheduler-results.py"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-core3-scheduler-ab/${batch_id}"
manifest="${batch_dir}/results.txt"
mode=${1:-all}
bigfile_batch=${GC_CORE3_BIGFILE_BATCH_DIR:-}
sudo_keepalive_pid=""
module_path=""

# Describe the destructive experiment and its optional phases.
usage() {
    cat <<EOF
Usage: ./$(basename -- "${script_path}") [all|smoke|full|smallfile]

  all    Run a budget=4 bigfile smoke test and then all six A/B runs.
  smoke  Run only the budget=4 bigfile smoke test and offline fsck.
  full   Run only budget=0/4/8 x bigfile/smallfile.
  smallfile
         Resume with budget=0/4/8 smallfile runs. Set
         GC_CORE3_BIGFILE_BATCH_DIR to a validated batch containing the three
         completed bigfile result-path files; the final report combines both.

Every run resets, reformats, and overwrites /dev/nvme0n1. Run this outer
script as the normal login user; it invokes sudo internally.
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

# Preserve completed metadata when an operator interrupts the matrix.
handle_signal() {
    stop_sudo_keepalive
    echo "Interrupted. Completed run metadata remains in ${manifest}." >&2
    exit 130
}

# Read source provenance from server 31 without changing remote state.
read_openssd_provenance() {
    ssh -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
        -o StrictHostKeyChecking=yes "${openssd_host}" \
        "repo='${openssd_repo}'; branch=\$(git -C \"\${repo}\" branch --show-current); commit=\$(git -C \"\${repo}\" rev-parse HEAD); dirty=0; [ -n \"\$(git -C \"\${repo}\" status --porcelain=v1 --untracked-files=no)\" ] && dirty=1; sync_hash=\$(sha256sum \"\${repo}/scripts/sync_code.sh\" | awk '{print \$1}'); printf 'openssd_branch=%s\nopenssd_commit=%s\nopenssd_tracked_dirty=%s\nopenssd_sync_script_sha256=%s\n' \"\${branch}\" \"\${commit}\" \"\${dirty}\" \"\${sync_hash}\""
}

# Resolve the unique worktree that owns the fixed Host branch.
resolve_host_tree() {
    git -C "${host_repo}" worktree list --porcelain | awk \
        -v target="refs/heads/${host_branch}" '
            /^worktree / { path = substr($0, 10); next }
            /^branch / && substr($0, 8) == target { print path }
        '
}

# Treat the build configuration as worktree-local state while still rejecting
# any tracked Host source change that would invalidate the fixed baseline.
host_tree_has_source_changes() {
    local tree=$1

    ! git -C "${tree}" diff --quiet HEAD -- . ':!.config' \
        || ! git -C "${tree}" diff --cached --quiet HEAD -- . ':!.config'
}

# Fast-forward the fixed Host branch and attach its existing diagnostic tree.
ensure_host_tree() {
    local remote_commit
    local local_commit
    local resolved

    git -C "${host_repo}" fetch --prune origin
    remote_commit=$(git -C "${host_repo}" rev-parse \
        "origin/${host_branch}")
    if [ "${remote_commit}" != "${host_commit}" ]; then
        echo "ERROR: fixed Host remote tip changed: ${remote_commit}" >&2
        return 1
    fi

    resolved=$(resolve_host_tree)
    if [ -z "${resolved}" ]; then
        local_commit=$(git -C "${host_repo}" rev-parse "${host_branch}")
        if ! git -C "${host_repo}" merge-base --is-ancestor \
                "${local_commit}" "${host_commit}"; then
            echo "ERROR: local fixed Host branch diverged from its remote." >&2
            return 1
        fi
        git -C "${host_repo}" branch -f "${host_branch}" "${host_commit}"
        if [ -d "${host_worktree}" ] \
            && [ "$(git -C "${host_worktree}" rev-parse HEAD)" = "${host_commit}" ] \
            && ! host_tree_has_source_changes "${host_worktree}"; then
            git -C "${host_worktree}" switch "${host_branch}"
        else
            host_worktree=/home/xin/work-xie/mcsgc-real/linux-cs-core3-scheduler-host-20260823
            if [ -e "${host_worktree}" ]; then
                echo "ERROR: fallback Host worktree path already exists: ${host_worktree}" >&2
                return 1
            fi
            git -C "${host_repo}" worktree add "${host_worktree}" "${host_branch}"
        fi
        resolved=$(resolve_host_tree)
    fi
    if [ -z "${resolved}" ] \
        || [ "$(printf '%s\n' "${resolved}" | wc -l)" -ne 1 ]; then
        echo "ERROR: cannot resolve one worktree for ${host_branch}." >&2
        return 1
    fi
    host_worktree=${resolved}
    if [ "$(git -C "${host_worktree}" rev-parse HEAD)" != "${host_commit}" ] \
        || host_tree_has_source_changes "${host_worktree}"; then
        echo "ERROR: fixed Host worktree has tracked source changes at ${host_commit}." >&2
        return 1
    fi
}

# Build the fixed Host module and the nvme-cli that carries operation 5.
build_tools() {
    echo "Building fixed Host module at $(date --iso-8601=seconds)"
    "${prepare_script}" "${configuration}"
    module_path="${host_worktree}/fs/f2fs/f2fs.ko"
    if [ ! -r "${module_path}" ]; then
        echo "ERROR: Host module was not produced: ${module_path}" >&2
        return 1
    fi
    echo "Building diagnostic nvme-cli"
    make -s -C "${artifact_repo}/host/src/nvme-cli" \
        -j"$(nproc)" nvme
    if [ ! -x "${artifact_repo}/host/src/nvme-cli/nvme" ]; then
        echo "ERROR: diagnostic nvme-cli was not produced." >&2
        return 1
    fi
}

# Replace an unloaded F2FS module and verify its srcversion.
load_host_module() {
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
    echo "Loaded fixed Host module: srcversion=${loaded_srcversion}"
}

# Fail closed on high-confidence kernel and device failures.
check_kernel_anomalies() {
    local run_dir=$1
    local output="${run_dir}/kernel-anomalies.log"
    local pattern='kernel BUG at|BUG: unable to handle|BUG: Bad page state|page dumped because|Oops:|general protection fault|slab-out-of-bounds|slab-use-after-free|use-after-free|list_del corruption|corrupted double-linked list|RCU:.*stall|rcu: INFO:.*stall|watchdog: BUG: soft lockup|SBI_NEED_FSCK|EUCLEAN|Structure needs cleaning|Inconsistent error blkaddr|something went wrong during csgc|SIT.*(bitmap|mismatch|inconsistent|corrupt)|SSA.*(mismatch|inconsistent|corrupt)|nvme[^[:space:]]*.*timeout|I/O error|refcount_t: (underflow|saturated)|negative refcount|CSGC_PAGE_REF_BUG|CSGC_CSI_POOL_BUG|CSGC_DEFER_HANDOFF_BUG|CSGC_DEFER_WAIT_BUG'

    if grep -Eai "${pattern}" "${run_dir}/external-dmesg.log" > "${output}"; then
        echo "ERROR: kernel anomaly detected; see ${output}." >&2
        return 1
    fi
    printf 'No configured kernel anomaly patterns matched.\n' > "${output}"
}

# Execute one destructive workload and validate its joint trace.
run_one() {
    local budget=$1
    local workload=$2
    local sequence=$3
    local smoke=${4:-0}
    local label
    local result_path_file
    local run_dir
    local fsck_log
    local fsck_status
    local artifact_commit
    local -a environment

    label=$(printf '%02d-b%s-%s' "${sequence}" "${budget}" "${workload}")
    result_path_file="${batch_dir}/${label}.result-path"
    artifact_commit=$(git -C "${artifact_repo}" rev-parse HEAD)
    environment=(
        "GC_BREAKDOWN_RESULT_PATH_FILE=${result_path_file}"
        "csgc_core3_normal_budget=${budget}"
        "csgc_supply_trace_abi_expected=2"
        "OPENSSD_BRANCH=${openssd_branch}"
        "OPENSSD_COMMIT=${openssd_commit}"
        "ARTIFACT_BRANCH=${artifact_branch}"
        "ARTIFACT_COMMIT=${artifact_commit}"
    )
    if [ "${smoke}" -eq 1 ]; then
        environment+=(
            "GC_BREAKDOWN_SMOKE=1"
            "GC_BREAKDOWN_SMOKE_IO_SIZE_PER_THREAD=1G"
            "GC_BREAKDOWN_SMOKE_RUNTIME=30"
        )
    fi

    echo "Starting ${label} at $(date --iso-8601=seconds)"
    if ! sudo env "${environment[@]}" \
            "${runner}" "${configuration}" "${workload}"; then
        echo "ERROR: ${label} failed." >&2
        return 1
    fi
    if [ ! -s "${result_path_file}" ]; then
        echo "ERROR: result path was not recorded for ${label}." >&2
        return 1
    fi
    run_dir=$(<"${result_path_file}")
    check_kernel_anomalies "${run_dir}"
    python3 "${validator}" "${run_dir}" "${budget}"

    {
        printf '\n[%s]\n' "${label}"
        printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
        printf 'budget=%s\nworkload=%s\nsmoke=%s\n' \
            "${budget}" "${workload}" "${smoke}"
        printf 'run_dir=%s\n' "${run_dir}"
        printf 'supply_analysis=%s\n' "${run_dir}/csgc-supply-analysis.json"
        printf 'kernel_log=%s\n' "${run_dir}/external-dmesg.log"
        printf 'ssd_stats=%s\n' "${run_dir}/ssd-workload-stat.log"
    } >> "${manifest}"

    if [ "${smoke}" -eq 1 ]; then
        fsck_log="${run_dir}/fsck-after-smoke.log"
        set +e
        sudo bash -c "fsck.f2fs -f /dev/nvme0n1 > '${fsck_log}' 2>&1"
        fsck_status=$?
        set -e
        printf 'fsck_log=%s\nfsck_status=%s\n' \
            "${fsck_log}" "${fsck_status}" >> "${manifest}"
    fi
}

if [ "${mode}" = "-h" ] || [ "${mode}" = "--help" ]; then
    usage
    exit 0
fi
if [ "$#" -gt 1 ] || [[ ! "${mode}" =~ ^(all|smoke|full|smallfile)$ ]]; then
    usage >&2
    exit 1
fi
if [ "${EUID}" -eq 0 ]; then
    echo "ERROR: do not run the outer matrix through sudo." >&2
    exit 1
fi
if findmnt -rn -S /dev/nvme0n1 >/dev/null; then
    echo "ERROR: /dev/nvme0n1 is mounted; refusing to replace f2fs." >&2
    exit 1
fi
if [ -r /sys/module/f2fs/refcnt ] \
    && [ "$(< /sys/module/f2fs/refcnt)" -ne 0 ]; then
    echo "ERROR: f2fs still has active references; refusing to run the matrix." >&2
    exit 1
fi
if [ "${mode}" = "smallfile" ]; then
    if [ -z "${bigfile_batch}" ] || [ ! -d "${bigfile_batch}" ]; then
        echo "ERROR: GC_CORE3_BIGFILE_BATCH_DIR must name the completed bigfile batch." >&2
        exit 1
    fi
    for result_path in 01-b0-bigfile 02-b4-bigfile 03-b8-bigfile; do
        if [ ! -s "${bigfile_batch}/${result_path}.result-path" ]; then
            echo "ERROR: missing bigfile result path: ${result_path}" >&2
            exit 1
        fi
    done
    bigfile_batch=$(readlink -f -- "${bigfile_batch}")
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

if [ "$(git -C "${artifact_repo}" branch --show-current)" != "${artifact_branch}" ] \
    || [ -n "$(git -C "${artifact_repo}" status --porcelain=v1 --untracked-files=no)" ]; then
    echo "ERROR: run the committed, clean Artifact experiment branch." >&2
    exit 1
fi

openssd_provenance=$(read_openssd_provenance)
actual_openssd_branch=$(awk -F= '$1 == "openssd_branch" { print $2 }' \
    <<< "${openssd_provenance}")
actual_openssd_commit=$(awk -F= '$1 == "openssd_commit" { print $2 }' \
    <<< "${openssd_provenance}")
openssd_dirty=$(awk -F= '$1 == "openssd_tracked_dirty" { print $2 }' \
    <<< "${openssd_provenance}")
if [ "${actual_openssd_branch}" != "${openssd_branch}" ] \
    || [ "${actual_openssd_commit}" != "${openssd_commit}" ] \
    || [ "${openssd_dirty}" != "0" ]; then
    echo "ERROR: server 31 OpenSSD source does not match the required branch." >&2
    printf '%s\n' "${openssd_provenance}" >&2
    exit 1
fi

ensure_host_tree
{
    printf 'started_at=%s\n' "$(date --iso-8601=seconds)"
    printf 'mode=%s\nhost=%s\nssd_thread_mode=ssd1t\n' \
        "${mode}" "$(hostname)"
    printf 'host_branch=%s\nhost_commit=%s\n' "${host_branch}" "${host_commit}"
    printf 'artifact_branch=%s\nartifact_commit=%s\n' \
        "${artifact_branch}" "$(git -C "${artifact_repo}" rev-parse HEAD)"
    if [ "${mode}" = "smallfile" ]; then
        printf 'bigfile_source_batch=%s\n' "${bigfile_batch}"
    fi
    printf '%s\n' "${openssd_provenance}"
} > "${manifest}"

build_tools
load_host_module
printf 'module_sha256=%s\nmodule_srcversion=%s\n' \
    "$(sha256sum "${module_path}" | awk '{print $1}')" \
    "$(modinfo -F srcversion "${module_path}")" >> "${manifest}"

if [ "${mode}" = "all" ] || [ "${mode}" = "smoke" ]; then
    run_one 4 bigfile 0 1
fi

if [ "${mode}" = "all" ] || [ "${mode}" = "full" ]; then
    run_one 0 bigfile 1
    run_one 4 bigfile 2
    run_one 8 bigfile 3
fi

if [ "${mode}" = "all" ] || [ "${mode}" = "full" ] \
        || [ "${mode}" = "smallfile" ]; then
    run_one 0 smallfile 4
    run_one 4 smallfile 5
    run_one 8 smallfile 6

    report_bigfile_batch=${batch_dir}
    if [ "${mode}" = "smallfile" ]; then
        report_bigfile_batch=${bigfile_batch}
    fi
    report="${batch_dir}/mcsgc-core3-fair-scheduler-analysis-20260823.md"
    report_json="${batch_dir}/mcsgc-core3-fair-scheduler-analysis-20260823.json"
    python3 "${comparator}" \
        --b0-big "$(<"${report_bigfile_batch}/01-b0-bigfile.result-path")" \
        --b4-big "$(<"${report_bigfile_batch}/02-b4-bigfile.result-path")" \
        --b8-big "$(<"${report_bigfile_batch}/03-b8-bigfile.result-path")" \
        --b0-small "$(<"${batch_dir}/04-b0-smallfile.result-path")" \
        --b4-small "$(<"${batch_dir}/05-b4-smallfile.result-path")" \
        --b8-small "$(<"${batch_dir}/06-b8-smallfile.result-path")" \
        --output "${report}" --json-output "${report_json}"
    printf 'report=%s\nreport_json=%s\n' "${report}" "${report_json}" \
        >> "${manifest}"
fi

printf 'completed_at=%s\nstatus=success\n' \
    "$(date --iso-8601=seconds)" >> "${manifest}"
echo "Core3 scheduler experiment completed successfully."
echo "Batch directory: ${batch_dir}"
echo "Manifest: ${manifest}"
