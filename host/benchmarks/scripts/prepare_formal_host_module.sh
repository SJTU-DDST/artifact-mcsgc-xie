#!/bin/bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
host_repo=/home/xin/work-xie/mcsgc-real/linux-cs
source "${script_dir}/formal_host_worktree.sh"

# Generate the architecture headers required by an out-of-tree module build.
prepare_kernel_build_tree() {
    local header
    local -a required_headers=(
        arch/x86/include/generated/asm/rwonce.h
        arch/x86/include/generated/asm/unaligned.h
        arch/x86/include/generated/uapi/asm/types.h
    )

    echo "Preparing kernel build metadata in ${host_tree}"
    make -s prepare modules_prepare LOCALVERSION=-csgcmt

    for header in "${required_headers[@]}"; do
        if [ ! -r "${header}" ]; then
            echo "ERROR: required generated header is unavailable: ${header}" >&2
            return 1
        fi
    done
}

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
        expected_branch=exp/formal-csgc-original-quiet-20260809
        ;;
    mcsgc8t-pipeline)
        expected_branch=exp/formal-mcsgc8t-pipeline-quiet-20260809
        ;;
    mcsgc8t-nopipeline)
        expected_branch=exp/formal-mcsgc8t-nopipe-quiet-20260809
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
./scripts/config --enable F2FS_STAT_FS
make -s olddefconfig LOCALVERSION=-csgcmt
if ! grep -q '^CONFIG_F2FS_STAT_FS=y$' .config; then
    echo "ERROR: failed to enable CONFIG_F2FS_STAT_FS" >&2
    exit 1
fi
prepare_kernel_build_tree

echo "Building formal Host module from ${actual_branch}"
sudo ./build_f2fs.sh

if ! nm fs/f2fs/f2fs.ko \
    | awk '$NF == "f2fs_build_stats" { found = 1 } END { exit !found }'; then
    echo "ERROR: the formal Host module was not built with CONFIG_F2FS_STAT_FS=y" >&2
    exit 1
fi
