#!/usr/bin/env bash
# Append-mode real-time thread monitor (no screen clearing).
# Prints the table header ONCE per execution; each refresh appends rows below.
# If no PIDs are given, auto-discover F2FS-related processes and monitor them.
#
# Usage:
#   sudo ./list_threads.sh [--deep] [--interval N] [--once] [--auto] [PID ...]
# Examples:
#   sudo ./list_threads.sh                 # auto F2FS, append every 1s
#   sudo ./list_threads.sh --deep          # auto F2FS deep scan (wchan/stack), 1s
#   sudo ./list_threads.sh --interval 2    # auto F2FS, every 2s
#   sudo ./list_threads.sh 21140 12952     # specific PIDs, 1s
#   sudo ./list_threads.sh --once          # single snapshot (auto F2FS if no PID)

# No 'set -e' to avoid premature exit when /proc races happen
set -u -o pipefail
LC_ALL=C

DEEP=0
FORCE_AUTO=0
INTERVAL=1
ONCE=0
declare -a PIDS=()

print_usage() {
  cat <<'EOF' >&2
Usage: sudo ./list_threads.sh [--deep] [--interval N] [--once] [--auto] [PID ...]
  --deep         Deep auto-discovery: also scan wchan and /proc/<tid>/stack for "f2fs".
  --interval N   Refresh interval in seconds (default: 1).
  --once         Print a single snapshot and exit.
  --auto         Force auto-discovery of F2FS-related processes even if PIDs are supplied.
  PID ...        One or more numeric PIDs to monitor. If omitted, auto-discovery is used.

Columns:
  PID  TID  St  CPU  NI  NAME  WCHAN  CURRENT_SYSCALL
Notes:
  * Install 'auditd' to get 'ausyscall' for syscall name mapping.
  * Deep scan is slower and may require root to read /proc/<tid>/stack.
EOF
}

# -------- CLI parsing --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deep) DEEP=1; shift ;;
    --auto) FORCE_AUTO=1; shift ;;
    --once) ONCE=1; shift ;;
    --interval|-n)
      [[ $# -ge 2 ]] || { echo "Missing value for --interval" >&2; exit 1; }
      INTERVAL="$2"; shift 2 ;;
    -h|--help) print_usage; exit 0 ;;
    --) shift; break ;;
    *)
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        PIDS+=("$1"); shift
      else
        echo "Unknown argument: $1" >&2
        print_usage
        exit 1
      fi
      ;;
  esac
done
if [[ $# -gt 0 ]]; then
  for a in "$@"; do
    [[ "$a" =~ ^[0-9]+$ ]] || { echo "Unknown trailing argument: $a" >&2; exit 1; }
    PIDS+=("$a")
  done
fi

# -------- utils --------
detect_ausarch() {
  local m; m="$(uname -m)"
  case "$m" in
    x86_64) echo "x86_64" ;;
    aarch64|arm64) echo "aarch64" ;;
    armv7l|armv8l) echo "arm" ;;
    ppc64le) echo "ppc64le" ;;
    riscv64) echo "riscv64" ;;
    *) echo "$m" ;;
  esac
}

AUSARCH="$(detect_ausarch)"
HAVE_AUSYSCALL=0
if command -v ausyscall >/dev/null 2>&1; then
  HAVE_AUSYSCALL=1
fi

parse_stat_rest() {
  # $1 = /proc/<tid>/stat
  # Output: state prio nice cpu
  local rest
  rest="$(sed -E 's/^[^)]*\) //' "$1" 2>/dev/null || true)"
  if [[ -z "$rest" ]]; then
    echo "--- --- --- ---"
    return
  fi
  awk '{printf "%s %s %s %s\n", $1, $16, $17, $37}' <<<"$rest"
}

get_syscall() {
  local tid="$1" sc line name
  if [[ -r "/proc/$tid/syscall" ]]; then
    line="$(cat "/proc/$tid/syscall" 2>/dev/null || true)"
    sc="$(awk '{print $1}' <<<"$line")"
    if [[ -z "${sc:-}" ]]; then
      echo "-"
      return
    fi
    if [[ "$sc" == "-1" ]]; then
      echo "userspace"
      return
    fi
    if [[ $HAVE_AUSYSCALL -eq 1 ]]; then
      name="$(ausyscall --exact --arch "$AUSARCH" "$sc" 2>/dev/null || true)"
      if [[ -n "$name" ]]; then
        echo "${name}(${sc})"
        return
      fi
    fi
    echo "syscall#${sc}"
  else
    echo "-"
  fi
}

print_table_header() {
  builtin printf 'Thread monitor started: %s   mode=%s   deep=%s   interval=%ss\n' \
    "$(date '+%F %T')" \
    "$([[ ${#PIDS[@]} -eq 0 || $FORCE_AUTO -eq 1 ]] && echo 'auto-F2FS' || echo 'PIDs')" \
    "$DEEP" "$INTERVAL"
  builtin printf '%s\n' \
    'PID      TID      St CPU NI  NAME               WCHAN                    CURRENT_SYSCALL'
  echo '-------- -------- -- --- --- ------------------ ------------------------ --------------------'
}

# -------- auto-discovery of F2FS-related PIDs --------
auto_collect_f2fs_pids() {
  local pid pd tdir w
  declare -A seen=()
  declare -a found=()

  for pd in /proc/[0-9]*; do
    [[ -d "$pd" ]] || continue
    pid="${pd#/proc/}"

    # Fast path: process name contains "f2fs"
    if [[ -r "$pd/comm" ]] && grep -qi 'f2fs' "$pd/comm" 2>/dev/null; then
      [[ -z "${seen[$pid]+x}" ]] && { seen[$pid]=1; found+=("$pid"); }
      continue
    fi

    # Deep path: scan each thread
    if [[ $DEEP -eq 1 && -d "$pd/task" ]]; then
      for tdir in "$pd"/task/[0-9]*; do
        [[ -d "$tdir" ]] || continue
        if [[ -r "$tdir/comm" ]] && grep -qi 'f2fs' "$tdir/comm" 2>/dev/null; then
          [[ -z "${seen[$pid]+x}" ]] && { seen[$pid]=1; found+=("$pid"); }
          break
        fi
        if [[ -r "$tdir/wchan" ]]; then
          w="$(cat "$tdir/wchan" 2>/dev/null || true)"
          if echo "$w" | grep -qi 'f2fs'; then
            [[ -z "${seen[$pid]+x}" ]] && { seen[$pid]=1; found+=("$pid"); }
            break
          fi
        fi
        if [[ -r "$tdir/stack" ]] && grep -qi 'f2fs' "$tdir/stack" 2>/dev/null; then
          [[ -z "${seen[$pid]+x}" ]] && { seen[$pid]=1; found+=("$pid"); }
          break
        fi
      done
    fi
  done

  if ((${#found[@]})); then
    printf '%s\n' "${found[@]}" | sort -n | uniq
  fi
}

# -------- main --------
print_table_header

trap 'exit 0' INT TERM

while :; do
  declare -a CUR_PIDS=()
  if [[ ${#PIDS[@]} -eq 0 || $FORCE_AUTO -eq 1 ]]; then
    mapfile -t CUR_PIDS < <(auto_collect_f2fs_pids 2>/dev/null || true)
  else
    CUR_PIDS=("${PIDS[@]}")
  fi

  # Snapshot separator (no screen clearing; we append forever)
  builtin printf '\n--- snapshot %s targets=%s ---\n' \
    "$(date '+%F %T')" \
    "$(IFS=,; echo "${CUR_PIDS[*]:-none}")"

  total_rows=0

  if ((${#CUR_PIDS[@]})); then
    for pid in "${CUR_PIDS[@]}"; do
      [[ -d "/proc/$pid" ]] || continue

      if [[ -d "/proc/$pid/task" ]]; then
        mapfile -t tids < <(ls -1 "/proc/$pid/task" 2>/dev/null | sort -n || true)
      else
        tids=("$pid")
      fi

      for tid in "${tids[@]:-}"; do
        tdir="/proc/$pid/task/$tid"
        [[ -d "$tdir" ]] || continue

        tname="$(tr -d '\n' < "$tdir/comm" 2>/dev/null || echo "-")"
        read -r state prio nice cpu <<<"$(parse_stat_rest "$tdir/stat")"
        [[ -z "${state:-}" ]] && state="-"
        [[ -z "${cpu:-}"   ]] && cpu="-"
        [[ -z "${nice:-}"  ]] && nice="-"

        wchan="-"
        if [[ -r "$tdir/wchan" ]]; then
          wchan="$(tr -d '\n' < "$tdir/wchan" 2>/dev/null || echo "-")"
          [[ "$wchan" == "0" ]] && wchan="-"
        fi

        scstr="$(get_syscall "$tid")"

        builtin printf '%-8s %-8s %-2s %-3s %-3s %-18.18s %-24.24s %-20.20s\n' \
          "$pid" "$tid" "$state" "$cpu" "$nice" "$tname" "$wchan" "$scstr"
        ((total_rows++)) || true
      done
    done
  else
    echo '(no matching processes)'
  fi

  builtin printf 'summary: PIDs=%d  Threads=%d  interval=%ss  deep=%s\n' \
    "${#CUR_PIDS[@]}" "$total_rows" "$INTERVAL" "$DEEP"

  [[ $ONCE -eq 1 ]] && break
  sleep "$INTERVAL"
done
