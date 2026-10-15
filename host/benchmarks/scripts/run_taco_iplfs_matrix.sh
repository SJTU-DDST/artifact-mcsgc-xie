#!/usr/bin/env bash
set -euo pipefail
# Keep the safe command-line interface independent of the caller's directory.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1
exec python3 -B "${SCRIPT_DIR}/iplfs_paper/matrix.py" "$@"
