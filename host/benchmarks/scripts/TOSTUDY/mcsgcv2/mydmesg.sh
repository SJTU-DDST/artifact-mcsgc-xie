#!/usr/bin/env bash
set -euo pipefail

out="${1:-kern.log}"

sudo dmesg -w | awk '
  /systemd-journald/ &&
  /Failed to write entry/ &&
  /ignoring: Cannot assign requested address/ { next }
  { print }
' | tee -a "$out"
