#!/bin/bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: ./prepare_formal_host_module.sh <configuration>

Configurations:
  original-ori
  original-csgc
  mcsgc8t-pipeline
  mcsgc8t-nopipeline
EOF
}

if [ $# -ne 1 ]; then
    usage
    exit 1
fi

case "$1" in
    original-ori|original-csgc)
        host_tree=/tmp/linux-cs-formal-original-quiet
        expected_branch=exp/formal-csgc-original-quiet-20260809
        ;;
    mcsgc8t-pipeline)
        host_tree=/tmp/linux-cs-formal-mcsgc8t-pipe-quiet
        expected_branch=exp/formal-mcsgc8t-pipeline-quiet-20260809
        ;;
    mcsgc8t-nopipeline)
        host_tree=/tmp/linux-cs-formal-mcsgc8t-nopipe-quiet
        expected_branch=exp/formal-mcsgc8t-nopipe-quiet-20260809
        ;;
    *)
        usage
        exit 1
        ;;
esac

actual_branch=$(git -C "${host_tree}" branch --show-current)
if [ "${actual_branch}" != "${expected_branch}" ]; then
    echo "ERROR: wrong Host branch: expected=${expected_branch} actual=${actual_branch:-detached}" >&2
    exit 1
fi

if [ ! -r "${host_tree}/.config" ]; then
    source_config=/home/xin/work-xie/mcsgc-real/linux-cs/.config
    if [ ! -r "${source_config}" ]; then
        source_config="/boot/config-$(uname -r)"
    fi
    if [ ! -r "${source_config}" ]; then
        echo "ERROR: no kernel configuration is available to seed the formal worktree." >&2
        exit 1
    fi
    cp "${source_config}" "${host_tree}/.config"
fi

cd "${host_tree}"
./scripts/config --disable F2FS_STAT_FS
make -s olddefconfig LOCALVERSION=-csgcmt
if grep -q '^CONFIG_F2FS_STAT_FS=y$' .config; then
    echo "ERROR: failed to disable CONFIG_F2FS_STAT_FS" >&2
    exit 1
fi

echo "Building formal Host module from ${actual_branch}"
exec sudo ./build_f2fs.sh
