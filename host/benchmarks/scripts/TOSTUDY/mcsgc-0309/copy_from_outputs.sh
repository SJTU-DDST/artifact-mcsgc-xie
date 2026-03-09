#!/usr/bin/env bash
# c.sh - Copy sources under BASE into DEST, preserving the path relative to BASE.
# Usage:
#   ./c.sh [--dry-run] /abs/path1 [/abs/path2 ...]
# Notes:
#   * All sources must exist and be inside BASE.
#   * This script is intended to be run from DEST, but it does not depend on CWD.

set -euo pipefail

BASE="/home/xin/artifact-csgc/host/benchmarks/scripts"
DEST="/home/xin/artifact-csgc/host/benchmarks/scripts/TOSTUDY/importantdata"
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage:
  ./c.sh [--dry-run] /abs/src1 [/abs/src2 ...]
Options:
  --dry-run   Print planned actions without copying.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    /*)        ARGS+=("$1"); shift ;;
    *)         echo "ERROR: source must be an absolute path: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST"

BASE_ABS="$(readlink -f "$BASE")"
DEST_ABS="$(readlink -f "$DEST")"

copy_one() {
  local src_abs="$1"

  # Normalize and verify existence
  src_abs="$(readlink -f "$src_abs" || true)"
  if [[ -z "$src_abs" || ! -e "$src_abs" ]]; then
    echo "WARN: source not found: $1" >&2
    return 1
  fi

  # Ensure src is under BASE
  case "$src_abs" in
    "$BASE_ABS"/*) ;;
    *)
      echo "ERROR: source is outside BASE: $src_abs" >&2
      return 2
      ;;
  esac

  # Compute relative path to BASE
  local rel="${src_abs#$BASE_ABS/}"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] From: $src_abs"
    echo "[DRY-RUN] Rel : $rel"
    echo "[DRY-RUN] Into: $DEST_ABS"
    return 0
  fi

  # Preserve structure from BASE using --parents; copy attributes with -a
  ( cd "$BASE_ABS" && cp -av --parents -- "$rel" "$DEST_ABS" )
}

ret=0
for s in "${ARGS[@]}"; do
  if ! copy_one "$s"; then
    ret=1
  fi
done

exit "$ret"
