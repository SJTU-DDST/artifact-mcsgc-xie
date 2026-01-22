#!/usr/bin/env bash
set -euo pipefail

# Function to inject messages into /dev/kmsg
# This mimics the format provided in your original request.
inject_kmsg() {
    local message="$1"
    
    # Generate timestamps and hostname
    local ts_local=$(date '+%b %e %H:%M:%S')
    local host_local=$(hostname)
    local ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)

    # <6> corresponds to KERN_INFO level
    # We pipe this to /dev/kmsg so 'dmesg -w' can capture it
    # Suppress output to avoid cluttering the terminal
    printf '<6>IN BASH %s %s [%s] %s\n' \
      "$ts_local" "$host_local" "$ts_upt" "$message" | sudo tee /dev/kmsg >/dev/null
}

echo "--- Starting HEAVY Test Sequence ---"

# 1. Send the START signal
echo "Sending START signal..."
inject_kmsg "test begin in bash"

echo "Flooding kernel buffer to force pipe flush..."
echo "This may take a few seconds..."

# 2. Loop to generate enough data to fill the 4KB buffer
# We inject 500 lines. 500 lines * ~80 bytes > 40KB, which is >> 4KB buffer.
for i in {1..500}; do
    inject_kmsg "PADDING DATA LINE $i: To fill the pipe buffer and force a flush"
done

# 3. Send the FINISH signal
echo "Sending FINISH signal..."
inject_kmsg "test finish in bash"

echo "--- HEAVY Test Sequence Complete ---"