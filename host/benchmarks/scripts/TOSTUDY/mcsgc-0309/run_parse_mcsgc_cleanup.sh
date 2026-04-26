#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <log_file>" >&2
    exit 1
fi

input="$1"

if [[ ! -f "$input" ]]; then
    echo "Error: log file not found: $input" >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
parser="$script_dir/parse_mcsgc_cleanup_log.py"

if [[ ! -f "$parser" ]]; then
    echo "Error: parser not found: $parser" >&2
    exit 1
fi

output="${input}parse-cleanup-speed.log"

python3 -u "$parser" "$input" 2>&1 | tee "$output"