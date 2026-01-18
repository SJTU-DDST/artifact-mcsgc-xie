#!/usr/bin/env bash
set -euo pipefail

out="${1:-kern.log}"

sudo dmesg -w --color=never | awk '
  BEGIN {
    recording = 0
  }

  /systemd-journald/ &&
  /Failed to write entry/ &&
  /ignoring: Cannot assign requested address/ { next }

  /systemd-journald/ &&
  /Journal file corrupted, rotating/ { next }

  /IN BASH/ && /test begin in bash/ {
    recording = 1
    print
    next
  }

  recording == 0 { next }

  /IN BASH/ && /test finish in bash/ {
    print
    exit 0
  }

  { print }
' >> "$out" || {
  rc=$?
  if [ "$rc" -ne 141 ]; then
    exit "$rc"
  fi
}
