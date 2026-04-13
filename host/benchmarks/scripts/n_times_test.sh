#!/usr/bin/env bash

set -u
set -o pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 <p1> <p2> <N>"
  exit 1
fi

p1=$1
p2=$2
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

mkdir -p -- "$log_dir"

name_p1="${p1##*/}"
name_p2="${p2##*/}"

date_part="$(date +%Y%m%d)"
time_part="$(date +%H%M%S)"
log_file="${log_dir}/${date_part}${time_part}${name_p1}${name_p2}.log"

exec > >(tee -a "$log_file") 2>&1

cmd=(sudo "$test_script" "$p1" "$p2")

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
done

echo "==== [INFO] All $N runs completed at $(date) ===="