#!/usr/bin/env bash

set -u -o pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 5 ]; then
    echo "Usage: $0 DEVICE LOG_PATH [TIMEOUT_SECONDS] [MAX_LOG_LINES] [EXPECTED_SEGS_PER_SEC]" >&2
    exit 2
fi

device=$1
log_path=$2
timeout_seconds=${3:-900}
max_log_lines=${4:-20000}
expected_segs_per_sec=${5:-8}
status_path=${log_path}.status
fsck_f2fs=${FSCK_F2FS:-/usr/local/sbin/fsck.f2fs}

if [ ! -b "${device}" ]; then
    echo "ERROR: block device is unavailable: ${device}" >&2
    exit 2
fi
if findmnt -rn -S "${device}" >/dev/null; then
    echo "ERROR: refusing to run fsck while ${device} is mounted" >&2
    exit 2
fi
if [[ ! "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: timeout must be a positive integer" >&2
    exit 2
fi
if [[ ! "${max_log_lines}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: max log lines must be a positive integer" >&2
    exit 2
fi
if [[ ! "${expected_segs_per_sec}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: expected segments per section must be a positive integer" >&2
    exit 2
fi
if [ ! -x "${fsck_f2fs}" ]; then
    echo "ERROR: project fsck.f2fs is unavailable: ${fsck_f2fs}" >&2
    exit 2
fi

mkdir -p -- "$(dirname -- "${log_path}")"
: > "${log_path}"
: > "${status_path}"

if [ "${EUID}" -eq 0 ]; then
    command_prefix=(/usr/bin/timeout --foreground --signal=INT --kill-after=30s "${timeout_seconds}s")
else
    command_prefix=(/usr/bin/sudo -n /usr/bin/timeout --foreground --signal=INT --kill-after=30s "${timeout_seconds}s")
fi

set +e
LC_ALL=C "${command_prefix[@]}" "${fsck_f2fs}" \
    --dry-run "${device}" 2>&1 \
    | awk -v log_path="${log_path}" \
        -v status_path="${status_path}" \
        -v max_lines="${max_log_lines}" \
        -v expected_segs_per_sec="${expected_segs_per_sec}" '
        {
            lower = tolower($0)
            if (NR <= max_lines)
                print $0 >> log_path
            if ($0 ~ /^Done:/)
                done = 1
            if ($0 ~ /^Info: Segments per section = [0-9]+$/)
                segs_per_sec = $NF + 0
            if (lower ~ /^info: corrupted|\[assert\]|\[error\]|\[fail\]|failed to fix|segmentation fault|sbi_need_fsck/)
                anomaly = 1
        }
        END {
            if (NR > max_lines)
                printf "... suppressed %d additional fsck lines ...\n", NR - max_lines >> log_path
            printf "line_count=%d\ndone_marker=%d\nanomaly_marker=%d\nsegments_per_section=%d\nexpected_segments_per_section=%d\n", \
                NR, done + 0, anomaly + 0, segs_per_sec + 0, \
                expected_segs_per_sec > status_path
        }
    '
pipeline_status=("${PIPESTATUS[@]}")
set -e

fsck_status=${pipeline_status[0]}
awk_status=${pipeline_status[1]}
{
    printf 'fsck_exit_status=%s\n' "${fsck_status}"
    printf 'filter_exit_status=%s\n' "${awk_status}"
    printf 'timeout_seconds=%s\n' "${timeout_seconds}"
    printf 'dry_run=1\n'
    printf 'fsck_tool=%s\n' "$(readlink -f -- "${fsck_f2fs}")"
    printf 'fsck_tool_sha256=%s\n' "$(sha256sum "${fsck_f2fs}" | awk '{print $1}')"
} >> "${status_path}"

done_marker=$(sed -n 's/^done_marker=//p' "${status_path}" | tail -n 1)
anomaly_marker=$(sed -n 's/^anomaly_marker=//p' "${status_path}" | tail -n 1)
segs_per_sec=$(sed -n 's/^segments_per_section=//p' "${status_path}" | tail -n 1)
if [ "${fsck_status}" -eq 0 ] \
    && [ "${awk_status}" -eq 0 ] \
    && [ "${done_marker:-0}" -eq 1 ] \
    && [ "${anomaly_marker:-1}" -eq 0 ] \
    && [ "${segs_per_sec:-0}" -eq "${expected_segs_per_sec}" ]; then
    printf 'result=pass\n' >> "${status_path}"
    exit 0
fi

printf 'result=fail\n' >> "${status_path}"
if [ "${fsck_status}" -eq 124 ] || [ "${fsck_status}" -eq 137 ]; then
    echo "ERROR: read-only fsck timed out after ${timeout_seconds}s; see ${log_path}" >&2
elif [ "${segs_per_sec:-0}" -ne "${expected_segs_per_sec}" ]; then
    echo "ERROR: read-only fsck reported segs_per_sec=${segs_per_sec:-missing}, expected=${expected_segs_per_sec}; see ${log_path}" >&2
else
    echo "ERROR: read-only fsck found an anomaly; see ${log_path}" >&2
fi
exit 1
