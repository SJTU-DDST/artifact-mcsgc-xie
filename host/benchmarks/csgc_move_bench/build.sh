#!/bin/bash

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

gcc \
    -O2 \
    -std=gnu11 \
    -Wall \
    -Wextra \
    -Werror \
    -pthread \
    -o "${script_dir}/csgc_move_bench" \
    "${script_dir}/csgc_move_bench.c"

echo "Built ${script_dir}/csgc_move_bench"
