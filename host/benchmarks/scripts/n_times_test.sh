#!/usr/bin/env bash

set -u
set -o pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 <mode> <config> <N>"
  exit 1
fi

mode=$1
config_path=$2
N=$3

if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: N must be a positive integer, got: $N"
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
test_script="${script_dir}/test.sh"
log_dir="${script_dir}/terminalMultipleTest"

if [ ! -x "$test_script" ]; then
  echo "Error: test script not found or not executable: $test_script"
  exit 1
fi

if ! ssd_thread_mode="$("${test_script}" --detect-ssd-thread-mode)"; then
  echo "Error: cannot detect the SSD thread mode; no test was started." >&2
  exit 1
fi

mkdir -p -- "$log_dir"

name_mode="${mode##*/}"
name_config="${config_path##*/}"

date_part="$(date +%Y%m%d)"
time_part="$(date +%H%M%S)"
log_file="${log_dir}/${date_part}${time_part}${name_mode}${ssd_thread_mode}${name_config}.log"

exec > >(tee -a "$log_file") 2>&1

cmd=(sudo "CSGC_EXPECTED_SSD_THREAD_MODE=${ssd_thread_mode}" "$test_script" "$mode" "$config_path")

echo "==== [INFO] Log file: $log_file ===="
echo "==== [INFO] Will run ${cmd[*]} for N=$N times in $script_dir at $(date) ===="

for ((i=1; i<=N; i++)); do
  echo "========================================"
  echo "[INFO] Run $i / $N START  $(date)"
  echo "[INFO] Command: ${cmd[*]}"
  echo "========================================"

  "${cmd[@]}"
  ret=$?

  echo "----------------------------------------"
  echo "[INFO] Run $i / $N END    $(date)"
  echo "[INFO] Exit code: $ret"
  echo "----------------------------------------"
  echo

  if (( ret != 0 )); then
    echo "[ERROR] Run $i failed; aborting the remaining repetitions."
    exit "${ret}"
  fi
done

echo "==== [INFO] All $N runs completed at $(date) ===="
