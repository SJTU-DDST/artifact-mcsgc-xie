#!/usr/bin/env bash
set -euo pipefail

# Function to inject messages into /dev/kmsg
inject_kmsg() {
    local message="$1"
    
    # Generate timestamps and hostname
    local ts_local=$(date '+%b %e %H:%M:%S')
    local host_local=$(hostname)
    local ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)

    # <6> corresponds to KERN_INFO level.
    # We pipe this to /dev/kmsg.
    printf '<6>IN BASH %s %s [%s] %s\n' \
      "$ts_local" "$host_local" "$ts_upt" "$message" | sudo tee /dev/kmsg >/dev/null
}

echo "Injecting padding data to flush dmesg buffer..."

# Loop 60 times.
# Each line with headers is approx 80-90 bytes.
# 60 * 80 bytes = 4800 bytes, which exceeds the standard 4KB (4096 bytes) buffer.
# This ensures that any pending data in the pipe is pushed through to the output file.
for i in {1..60}; do
    inject_kmsg "--- FLUSH BUFFER PADDING [$i/60] ---"
done

echo "Flush complete."