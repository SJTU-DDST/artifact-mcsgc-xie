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

out="$(normalize_output_name "$input")"
old="${out}.old.log"

handle_interrupt() {
  trap - INT

  echo
  echo "Interrupted. Stopping kmsg tracing..."
  if [ -n "${trace_pid:-}" ]; then
    kill -TERM -- "-${trace_pid}" 2>/dev/null || true
    wait "${trace_pid}" 2>/dev/null || true
  fi

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

sudo dmesg -C

echo "Tracing /dev/kmsg to: $out"
echo "Backup saved to     : $old"
echo "Filtering rules     : unchanged (two systemd-journald ignores)"
echo "Extra check         : detect /dev/kmsg sequence gaps and append FAIL line"
echo "Output format       : keep raw /dev/kmsg style"
echo "On Ctrl+C           : stop tracing, run finderror.py, then exit"

setsid bash -c '
set +e

out="$1"
last_seq=""

should_skip() {
  local msg="$1"

  if [[ "$msg" == *systemd-journald* &&
        "$msg" == *"Failed to write entry"* &&
        "$msg" == *"ignoring: Cannot assign requested address"* ]]; then
    return 0
  fi

  if [[ "$msg" == *systemd-journald* &&
        "$msg" == *"Journal file corrupted, rotating"* ]]; then
    return 0
  fi

  return 1
}

write_gap_line() {
  local ts="$1"
  local prev_seq="$2"
  local curr_seq="$3"
  local lost_count

  lost_count=$((curr_seq - prev_seq - 1))

  printf "6,%s,%s,-;FAIL: some message probably lost. estimated_lost_count=%s seq gap: %s -> %s\n" \
    "$curr_seq" "$ts" "$lost_count" "$prev_seq" "$curr_seq" >> "$out"
}

while true; do
  while IFS= read -r raw; do
    [[ -z "$raw" ]] && continue
    [[ "$raw" != *";"* ]] && continue

    header=${raw%%;*}
    msg=${raw#*;}

    pri=""
    seq=""
    ts=""
    flags=""
    IFS=, read -r pri seq ts flags <<< "$header"

    [[ "$seq" =~ ^[0-9]+$ ]] || continue
    [[ "$ts" =~ ^[0-9]+$ ]] || ts=0

    if [[ -n "$last_seq" ]] && (( seq > last_seq + 1 )); then
      write_gap_line "$ts" "$last_seq" "$seq"
    fi

    last_seq="$seq"

    if should_skip "$msg"; then
      continue
    fi

    printf "%s\n" "$raw" >> "$out"
  done < <(sudo cat /dev/kmsg 2>/dev/null)

  sleep 0.05
done
' bash "$out" &
trace_pid=$!

wait "$trace_pid"