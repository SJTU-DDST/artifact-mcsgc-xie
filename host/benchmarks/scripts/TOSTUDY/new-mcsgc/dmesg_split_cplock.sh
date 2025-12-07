#!/usr/bin/env bash

# 1. check arg count
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <logfile.log>" >&2
    exit 1
fi

orig_log="$1"

# 2. check suffix ".log"
case "$orig_log" in
    *.log)
        ;;
    *)
        echo "Error: logfile must end with .log" >&2
        exit 1
        ;;
esac

base_name="${orig_log%.log}"
cplock_log="${base_name}_cplock.log"

# 3. check file existence
if [ -e "$orig_log" ]; then
    echo "Error: file already exists: $orig_log" >&2
    exit 1
fi

if [ -e "$cplock_log" ]; then
    echo "Error: file already exists: $cplock_log" >&2
    exit 1
fi

# start split logging
sudo dmesg -w \
  | tee >(grep 'CP_LOCK' >> "$cplock_log") \
  | grep -v 'CP_LOCK' >> "$orig_log"
