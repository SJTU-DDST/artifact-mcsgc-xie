#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runner="${script_dir}/run_formal_performance_test.sh"
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
expected_branch=exp/formal-csgc-original-quiet-20260809
configuration=original-ori
repeats=3
source "${script_dir}/formal_host_worktree.sh"

if [ "$#" -ne 0 ]; then
    echo "Usage: sudo $0" >&2
    exit 1
fi

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: this benchmark batch must run through sudo." >&2
    echo "Run: sudo $0" >&2
    exit 1
fi

if [ -z "${SUDO_USER:-}" ] || [ "${SUDO_USER}" = "root" ]; then
    echo "ERROR: run this script with sudo from the account that can SSH to server 31." >&2
    exit 1
fi

if [ ! -x "${runner}" ]; then
    echo "ERROR: formal benchmark runner is unavailable: ${runner}" >&2
    exit 1
fi

host_tree=$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")
module_path="${host_tree}/fs/f2fs/f2fs.ko"
actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    echo "ERROR: wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
    exit 1
fi

if [ ! -r "${module_path}" ]; then
    echo "ERROR: original formal Host module has not been built: ${module_path}" >&2
    echo "Action: run './prepare_formal_host_module.sh original-ori' first." >&2
    exit 1
fi

echo "============================================================"
echo "Formal original ORI benchmark"
echo "Host branch: ${actual_branch}"
echo "Host commit: $(git -C "${host_tree}" rev-parse HEAD)"
echo "Repeats: ${repeats}"
echo "============================================================"

for ((round = 1; round <= repeats; round++)); do
    echo
    echo "============================================================"
    echo "Starting round ${round}/${repeats}: ${configuration}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    if "${runner}" "${configuration}"; then
        echo "Completed round ${round}/${repeats}: ${configuration}"
        echo "Completion time: $(date --iso-8601=seconds)"
    else
        status=$?
        echo "ERROR: round ${round}/${repeats} failed: ${configuration}, status=${status}" >&2
        echo "The remaining ORI runs were not started." >&2
        exit "${status}"
    fi
done

echo
echo "All three formal original ORI runs completed successfully."
