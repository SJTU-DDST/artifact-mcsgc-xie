#!/usr/bin/env bash
set -euo pipefail

input="${1:-kern.log}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

normalize_output_name() {
  local name="$1"
  local dir base stem prefix num candidate

  dir="$(dirname -- "$name")"
  base="$(basename -- "$name")"

  if [[ "$base" == *.log ]]; then
    stem="${base%.log}"
  else
    stem="$base"
  fi

  if [[ "$stem" =~ ^(.*)-([0-9]+)$ ]]; then
    prefix="${BASH_REMATCH[1]}"
    num="${BASH_REMATCH[2]}"
  else
    prefix="$stem"
    num="1"
  fi

  while true; do
    candidate="${dir}/${prefix}-${num}.log"
    if [[ ! -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    num=$((num + 1))
  done
}

analyze_seq_gaps() {
  local raw_log="$1"
  local report_file="$2"

  awk '
  BEGIN {
    prev_seq = ""
    gap_events = 0
    total_lost = 0
  }
  {
    raw = $0
    semi = index(raw, ";")
    if (semi == 0)
      next

    header = substr(raw, 1, semi - 1)
    n = split(header, fields, ",")
    if (n < 3)
      next

    seq = fields[2]
    ts  = fields[3]

    if (seq !~ /^[0-9]+$/ || ts !~ /^[0-9]+$/)
      next

    if (prev_seq != "" && seq > prev_seq + 1) {
      lost = seq - prev_seq - 1
      gap_events++
      total_lost += lost
      printf "6,%s,%s,-;FAIL: some message probably lost. estimated_lost_count=%s seq gap: %s -> %s\n",
             seq, ts, lost, prev_seq, seq
    }

    prev_seq = seq
  }
  END {
    printf "SUMMARY: gap_events=%d estimated_total_lost=%d\n",
           gap_events, total_lost
  }' "$raw_log" > "$report_file"
}

filter_unwanted_lines_inplace() {
  local raw_log="$1"
  local tmp_output tmp_count deleted_count

  tmp_output="$(mktemp "${raw_log}.tmp.XXXXXX")"
  tmp_count="$(mktemp "${raw_log}.count.XXXXXX")"

  awk -v count_file="$tmp_count" '
  BEGIN {
    removed = 0
  }
  {
    if ($0 ~ /systemd-journald/ &&
        $0 ~ /Failed to write entry/ &&
        $0 ~ /ignoring: Cannot assign requested address/) {
      removed++
      next
    }

    if ($0 ~ /systemd-journald/ &&
        $0 ~ /Journal file corrupted, rotating/) {
      removed++
      next
    }

    print
  }
  END {
    print removed + 0 > count_file
  }' "$raw_log" > "$tmp_output"

  mv -f "$tmp_output" "$raw_log"
  deleted_count="$(cat "$tmp_count")"
  rm -f "$tmp_count"

  printf '%s\n' "$deleted_count"
}

out="$(normalize_output_name "$input")"
old="${out}.old.log"
seq_report="${out}.seqcheck.txt"

handle_interrupt() {
  local deleted_count

  trap - INT

  echo
  echo "Interrupted. Stopping /dev/kmsg collector..."

  if [ -n "${trace_pid:-}" ]; then
    kill -TERM -- "-${trace_pid}" 2>/dev/null || true
    wait "${trace_pid}" 2>/dev/null || true
  fi

  echo "Running sequence-gap analysis..."
  analyze_seq_gaps "$out" "$seq_report"
  echo "Sequence report saved to: $seq_report"

  echo "Filtering unwanted journald lines from raw log..."
  deleted_count="$(filter_unwanted_lines_inplace "$out")"

  printf "REMOVED_UNWANTED_LINES=%s\n" "$deleted_count" >> "$seq_report"

  echo "======"
  echo "Removed unwanted journald lines: $deleted_count"
  echo "======"

  echo "Running: python3 ${script_dir}/finderror.py ${out}"
  python3 "${script_dir}/finderror.py" "${out}"
  exit 0
}

trap handle_interrupt INT

mkdir -p "$(dirname "$out")"

if [ -e "$old" ]; then
  mv -f "$old" "${old}.$(date +%Y%m%d_%H%M%S)"
fi

sudo dmesg --color=never > "$old"

: > "$out"
: > "$seq_report"

sudo dmesg -C

echo "Collecting raw /dev/kmsg to: $out"
echo "Backup saved to           : $old"
echo "Sequence report target    : $seq_report"
echo "Collector mode            : append raw /dev/kmsg records only"
echo "Postprocess order         : analyze gaps, remove unwanted journald lines, then run finderror.py"
echo "On Ctrl+C                 : stop collector, analyze sequence gaps, clean raw log, then run finderror.py"

setsid bash -c '
  exec sudo cat /dev/kmsg >> "$1"
' bash "$out" &
trace_pid=$!

wait "$trace_pid"