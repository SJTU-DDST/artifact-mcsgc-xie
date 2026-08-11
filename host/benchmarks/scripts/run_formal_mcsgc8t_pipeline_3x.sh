#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runner="${script_dir}/run_formal_performance_test.sh"
host_repo="/home/xin/work-xie/mcsgc-real/linux-cs"
expected_branch="exp/formal-mcsgc8t-pipeline-quiet-20260809"
configuration="mcsgc8t-pipeline"
repeats=3

# shellcheck source=formal_host_worktree.sh
source "${script_dir}/formal_host_worktree.sh"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

banner() {
    printf '\n%s\n' "============================================================"
    printf '%s\n' "$*"
    printf '%s\n' "============================================================"
}

if [[ $# -ne 0 ]]; then
    die "this script does not accept arguments"
fi

if [[ ${EUID} -ne 0 ]]; then
    die "run this script with sudo"
fi

if [[ -z "${SUDO_USER:-}" || "${SUDO_USER}" == "root" ]]; then
    die "SUDO_USER is unavailable; invoke this script as a regular user via sudo"
fi

[[ -x "${runner}" ]] || die "formal performance runner is not executable: ${runner}"

host_tree="$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")"
actual_branch="$(git -C "${host_tree}" branch --show-current)"
if [[ "${actual_branch}" != "${expected_branch}" ]]; then
    die "wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}"
fi

host_commit="$(git -C "${host_tree}" rev-parse HEAD)"
module_path="${host_tree}/fs/f2fs/f2fs.ko"

if [[ ! -r "${module_path}" ]]; then
    die "formal mCSGC8t pipeline Host module has not been built. Run './prepare_formal_host_module.sh mcsgc8t-pipeline' first"
fi

banner "Formal optimized mCSGC8t pipeline benchmark"
echo "Host tree: ${host_tree}"
echo "Host branch: ${expected_branch}"
echo "Host commit: ${host_commit}"
echo "Host module: ${module_path}"
echo "Configuration: ${configuration}"
echo "Repetitions: ${repeats}"

for ((run = 1; run <= repeats; run++)); do
    banner "mCSGC8t pipeline run ${run}/${repeats}"

    if ! "${runner}" "${configuration}"; then
        echo "ERROR: mCSGC8t pipeline run ${run}/${repeats} failed." >&2
        echo "The remaining mCSGC8t pipeline runs were not started." >&2
        exit 1
    fi
done

banner "All three formal optimized mCSGC8t pipeline runs completed successfully"
