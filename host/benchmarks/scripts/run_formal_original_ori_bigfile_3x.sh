#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runner="${script_dir}/run_formal_performance_test.sh"
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
expected_branch=exp/formal-csgc-original-quiet-20260809
configuration=original-ori
workload=bigfile
repeats=3

# Resolve the branch worktree before starting any destructive benchmark work.
source "${script_dir}/formal_host_worktree.sh"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

if [ "$#" -ne 0 ]; then
    die "this script does not accept arguments"
fi

if [ "${EUID}" -ne 0 ]; then
    die "run this script with sudo"
fi

if [ -z "${SUDO_USER:-}" ] || [ "${SUDO_USER}" = "root" ]; then
    die "invoke this script as a regular user through sudo"
fi

[ -x "${runner}" ] || die "formal benchmark runner is unavailable: ${runner}"

host_tree=$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")
actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    die "wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}"
fi

module_path="${host_tree}/fs/f2fs/f2fs.ko"
if [ ! -r "${module_path}" ]; then
    die "formal Host module is unavailable; run './prepare_formal_host_module.sh original-ori' first"
fi

echo "============================================================"
echo "Formal original ORI single-big-file benchmark"
echo "Host branch: ${actual_branch}"
echo "Host commit: $(git -C "${host_tree}" rev-parse HEAD)"
echo "Repetitions: ${repeats}"
echo "============================================================"

for ((round = 1; round <= repeats; round++)); do
    echo
    echo "============================================================"
    echo "Starting round ${round}/${repeats}: ${configuration}/${workload}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    if ! "${runner}" "${configuration}" "${workload}"; then
        echo "ERROR: round ${round}/${repeats} failed." >&2
        echo "The remaining original ORI runs were not started." >&2
        exit 1
    fi

    echo "Completed round ${round}/${repeats}."
    echo "Completion time: $(date --iso-8601=seconds)"
done

echo
echo "All three formal original ORI single-big-file runs completed successfully."
