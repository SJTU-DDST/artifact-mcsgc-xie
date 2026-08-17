#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
source "${script_dir}/formal_host_worktree.sh"

# Print the supported diagnostic Host configurations.
usage() {
    cat <<'EOF'
Usage: ./prepare_gc_breakdown_host_module.sh <configuration>

Configurations:
  original             Diagnostic module for ORI and original CSGC
  mcsgc8t-nopipeline   Diagnostic module for optimized mCSGC8t no-pipeline
  mcsgc8t-pipeline     Diagnostic module for optimized mCSGC8t pipeline
  mcsgc8t-batch-dnode  Diagnostic module with batched dnode commit
  mcsgc8t-summary-control
                        Summary commit diagnostic control module
  mcsgc8t-batch-summary
                        Diagnostic module with batched summary commit
EOF
}

if [ "$#" -ne 1 ]; then
    usage
    exit 1
fi

case "$1" in
    original)
        expected_branch=exp/diagnostic-original-gc-breakdown-20260811
        ;;
    mcsgc8t-nopipeline)
        expected_branch=exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811
        ;;
    mcsgc8t-pipeline)
        expected_branch=exp/diagnostic-mcsgc8t-pipeline-breakdown-20260812
        ;;
    mcsgc8t-batch-dnode)
        expected_branch=exp/diagnostic-mcsgc8t-batched-dnode-breakdown-20260817
        ;;
    mcsgc8t-summary-control)
        expected_branch=exp/diagnostic-mcsgc8t-summary-control-20260817
        ;;
    mcsgc8t-batch-summary)
        expected_branch=exp/diagnostic-mcsgc8t-batched-summary-breakdown-20260817
        ;;
    *)
        usage
        exit 1
        ;;
esac

host_tree=$(resolve_formal_host_tree "${host_repo}" "${expected_branch}")
actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    echo "ERROR: wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
    exit 1
fi

if [ ! -r "${host_tree}/.config" ]; then
    source_config="${host_repo}/.config"
    if [ ! -r "${source_config}" ]; then
        source_config="/boot/config-$(uname -r)"
    fi
    if [ ! -r "${source_config}" ]; then
        echo "ERROR: no kernel configuration is available to seed the diagnostic worktree." >&2
        exit 1
    fi
    cp "${source_config}" "${host_tree}/.config"
fi

cd "${host_tree}"
./scripts/config --enable F2FS_STAT_FS
make -s olddefconfig LOCALVERSION=-csgcmt
# A newly checked out worktree does not contain generated architecture headers.
make -s prepare modules_prepare LOCALVERSION=-csgcmt

echo "Building GC breakdown module from ${actual_branch}"
sudo ./build_f2fs.sh

module_path="${host_tree}/fs/f2fs/f2fs.ko"
if [ ! -r "${module_path}" ]; then
    echo "ERROR: f2fs module was not produced: ${module_path}" >&2
    exit 1
fi

module_srcversion=$(modinfo -F srcversion "${module_path}")
if [ -z "${module_srcversion}" ]; then
    echo "ERROR: diagnostic f2fs module has no srcversion." >&2
    exit 1
fi

echo "Diagnostic Host tree: ${host_tree}"
echo "Diagnostic Host commit: $(git -C "${host_tree}" rev-parse HEAD)"
echo "Diagnostic module srcversion: ${module_srcversion}"
