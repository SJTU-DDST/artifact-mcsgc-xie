#!/usr/bin/env bash

# 用法：./run_n_times.sh <p1> <p2> <N>

if [ $# -ne 3 ]; then
  echo "Usage: $0 <p1> <p2> <N>"
  exit 1
fi

p1=$1
p2=$2
N=$3

# 检查 N 是否为正整数
if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
  echo "Error: N must be a positive integer, got: $N"
  exit 1
fi

cmd=(sudo ./test.sh "$p1" "$p2")

echo "==== [INFO] Will run ${cmd[*]} for N=$N times in $(pwd) at $(date) ===="

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