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

echo "Tracing dmesg to: $out"
echo "Backup saved to : $old"
echo "Filtering rules : unchanged (two systemd-journald ignores)"
echo "Speed tweak     : removed per-line fflush; write via single shell redirection"

# Follow new messages only; keep your original filters; FAST path
# - awk only filters; it prints to stdout
# - shell appends stdout to $out (buffered, fast)
# - stdbuf keeps the pipeline line-oriented without forcing a disk flush per line
sudo dmesg -w --color=never \
| stdbuf -oL -eL awk '
  /systemd-journald/ &&
  /Failed to write entry/ &&
  /ignoring: Cannot assign requested address/ { next }

  /systemd-journald/ &&
  /Journal file corrupted, rotating/ { next }

  { print }
' >> "$out"
