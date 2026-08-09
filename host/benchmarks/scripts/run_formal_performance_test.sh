#!/bin/bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
config_path="configs/config24_fio_formal_performance_16t26336file.sh"
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
source "${script_dir}/formal_host_worktree.sh"

usage() {
    cat <<'EOF'
Usage: sudo ./run_formal_performance_test.sh <configuration>

Configurations:
  original-ori          Original Host and device code, conventional F2FS
  original-csgc         Original Host and device code, original CSGC
  mcsgc8t-pipeline      Current optimized mCSGC8t with cross-section pipeline
  mcsgc8t-nopipeline    Current optimized mCSGC8t without the pipeline
EOF
}

if [ $# -ne 1 ]; then
    usage
    exit 1
fi

case "$1" in
    original-ori)
        expected_branch=exp/formal-csgc-original-quiet-20260809
        test_mode=ori
        expected_move_plan_v2=undefined
        expected_move_plan_fast_unsafe=undefined
        ;;
    original-csgc)
        expected_branch=exp/formal-csgc-original-quiet-20260809
        test_mode=csgc-original-formal
        expected_move_plan_v2=undefined
        expected_move_plan_fast_unsafe=undefined
        ;;
    mcsgc8t-pipeline)
        expected_branch=exp/formal-mcsgc8t-pipeline-quiet-20260809
        test_mode=mcsgc8t-pipeline-formal-csgc
        expected_move_plan_v2=1
        expected_move_plan_fast_unsafe=1
        ;;
    mcsgc8t-nopipeline)
        expected_branch=exp/formal-mcsgc8t-nopipe-quiet-20260809
        test_mode=mcsgc8t-nopipeline-formal-csgc
        expected_move_plan_v2=1
        expected_move_plan_fast_unsafe=1
        ;;
    *)
        usage
        exit 1
        ;;
esac

host_tree=$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")
if [ ! -d "${host_tree}/.git" ] && [ ! -f "${host_tree}/.git" ]; then
    echo "ERROR: expected Host worktree is unavailable: ${host_tree}" >&2
    exit 1
fi

actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    echo "ERROR: wrong Host branch in ${host_tree}: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
    exit 1
fi

module_path="${host_tree}/fs/f2fs/f2fs.ko"
if [ ! -r "${module_path}" ]; then
    echo "ERROR: formal Host module has not been built: ${module_path}" >&2
    echo "Action: run './prepare_formal_host_module.sh $1' first." >&2
    exit 1
fi

if ! nm "${module_path}" \
    | awk '$NF == "f2fs_build_stats" { found = 1 } END { exit !found }'; then
    echo "ERROR: CONFIG_F2FS_STAT_FS is not enabled in the formal Host module." >&2
    echo "Action: run './prepare_formal_host_module.sh $1' first." >&2
    exit 1
fi

host_commit=$(git -C "${host_tree}" rev-parse HEAD)
module_sha256=$(sha256sum "${module_path}" | awk '{print $1}')
module_srcversion=$(modinfo -F srcversion "${module_path}")
if [ -z "${module_srcversion}" ]; then
    echo "ERROR: the formal f2fs module has no srcversion: ${module_path}" >&2
    exit 1
fi
if [ -r /sys/module/f2fs/srcversion ]; then
    loaded_srcversion=$(< /sys/module/f2fs/srcversion)
    if [ "${loaded_srcversion^^}" != "${module_srcversion^^}" ]; then
        echo "ERROR: the loaded f2fs module does not match the formal module." >&2
        echo "Loaded srcversion: ${loaded_srcversion}" >&2
        echo "Formal srcversion: ${module_srcversion}" >&2
        echo "Action: run './prepare_formal_host_module.sh $1'." >&2
        exit 1
    fi
fi

echo "Formal configuration: $1"
echo "Host tree: ${host_tree}"
echo "Host branch: ${actual_branch}"
echo "Host commit: ${host_commit}"
echo "Host module SHA-256: ${module_sha256}"
echo "Expected OpenSSD worker mode: ssd1t"

export F2FS_KERNEL_PATH_OVERRIDE="${host_tree}"
export CSGC_EXPECTED_SSD_THREAD_MODE=ssd1t
export CSGC_EXPECTED_OPENSSD_PRODUCTION_PERFORMANCE=1
export CSGC_EXPECTED_MOVE_PLAN_V2="${expected_move_plan_v2}"
export CSGC_EXPECTED_MOVE_PLAN_FAST_UNSAFE="${expected_move_plan_fast_unsafe}"
export FORMAL_HOST_BRANCH="${actual_branch}"
export FORMAL_HOST_COMMIT="${host_commit}"
export FORMAL_MODULE_SHA256="${module_sha256}"

cd "${script_dir}"
exec ./test.sh "${test_mode}" "${config_path}"
