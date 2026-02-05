#!/usr/bin/env bash

# =========================
# =========================
if [ $# -ne 1 ]; then
    echo "Usage: $0 <N>"
    exit 1
fi

N=$1

if ! [[ "$N" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: N must be a positive integer"
    exit 1
fi

# =========================
# =========================
for ((i=1; i<=N; i++)); do
    echo "========================================"
    echo "[INFO] Run $i / $N started at $(date)"
    echo "Command: sudo ./test.sh mcsgc8thread configs/config06_fio_rand.sh"
    echo "========================================"

    sudo ./test.sh mcsgc8thread configs/config06_fio_rand.sh
    ret=$?

    echo "----------------------------------------"
    echo "[INFO] Run $i / $N finished at $(date)"
    echo "[INFO] Exit code: $ret"
    echo "----------------------------------------"
    echo
done

echo "[INFO] All $N runs completed at $(date)"
