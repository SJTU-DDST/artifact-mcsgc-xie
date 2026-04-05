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
  echo "Interrupted. Stopping dmesg tracing..."
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

echo "Tracing dmesg to: $out"
echo "Backup saved to : $old"
echo "Filtering rules : unchanged (two systemd-journald ignores)"
echo "Speed tweak     : removed per-line fflush; write via single shell redirection"
echo "On Ctrl+C       : stop tracing, run finderror.py, then exit"

setsid bash -c '
  out="$1"
  sudo dmesg -w --color=never \
  | stdbuf -oL -eL awk '"'"'
    /systemd-journald/ &&
    /Failed to write entry/ &&
    /ignoring: Cannot assign requested address/ { next }

    /systemd-journald/ &&
    /Journal file corrupted, rotating/ { next }

    { print }
  '"'"' >> "$out"
' bash "$out" &
trace_pid=$!

wait "$trace_pid"