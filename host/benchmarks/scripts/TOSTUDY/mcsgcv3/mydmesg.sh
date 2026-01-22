#!/usr/bin/env bash
set -euo pipefail

out="${1:-kern.log}"
old="${out}.old.log"

# Ensure output directory exists (in case out contains a path)
mkdir -p "$(dirname "$out")"

# Backup current ring buffer before clearing
if [ -e "$old" ]; then
  mv -f "$old" "${old}.$(date +%Y%m%d_%H%M%S)"
fi
sudo dmesg --color=never > "$old"

# Create the new output file early
: > "$out"

# Drop old ring-buffer messages so the output file cannot contain old dmesg
sudo dmesg -C

# Follow new messages only; keep your original filters; flush each line to file
sudo dmesg -w --color=never | awk -v out="$out" '
  /systemd-journald/ &&
  /Failed to write entry/ &&
  /ignoring: Cannot assign requested address/ { next }

  /systemd-journald/ &&
  /Journal file corrupted, rotating/ { next }

  { print >> out; fflush() }
'
