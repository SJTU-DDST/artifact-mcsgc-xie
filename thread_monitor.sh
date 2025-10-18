#!/usr/bin/env bash
# Realtime thread monitor for given PIDs; if none, auto-discover F2FS-related tasks.
# Usage:
#   ./thread_monitor.sh [-i interval_sec] [PID ...]
# Notes:
#   - Appends output (no screen refresh).
#   - Prints all threads (TIDs) under each target TGID.
#   - Requires /proc access; no root needed generally, but some fields may show "?" if restricted.

set -uo pipefail
shopt -s nullglob

INTERVAL=1

# --- args ---
while getopts ":i:" opt; do
  case "$opt" in
    i) INTERVAL="${OPTARG:-1}";;
    *) ;;
  esac
done
shift $((OPTIND-1))

is_number() { [[ "$1" =~ ^[0-9]+$ ]]; }

# --- syscall name map (minimal, x86_64 only) ---
declare -A SYSCALL_MAP=(
  [0]="read" [1]="write" [2]="open" [3]="close" [4]="stat" [5]="fstat" [6]="lstat"
  [7]="poll" [8]="lseek" [9]="mmap" [11]="munmap" [12]="brk" [16]="ioctl" [17]="pread64" [18]="pwrite64"
  [21]="access" [22]="pipe" [23]="select" [32]="dup" [33]="dup2" [35]="nanosleep" [56]="clone"
  [57]="fork" [58]="vfork" [59]="execve" [60]="exit" [61]="wait4" [62]="kill" [72]="fcntl"
  [73]="flock" [74]="fsync" [75]="fdatasync" [78]="getdents" [79]="getcwd" [80]="chdir"
  [97]="getrusage" [158]="arch_prctl" [186]="gettid" [202]="futex" [217]="getdents64"
  [231]="exit_group" [257]="openat" [262]="newfstatat" [263]="unlinkat" [264]="renameat"
  [270]="pselect6" [271]="ppoll" [273]="splice" [280]="utimensat" [291]="sendmmsg"
  [318]="getrandom" [319]="memfd_create" [322]="execveat" [388]="preadv2" [392]="pwritev2"
  [425]="io_uring_setup" [426]="io_uring_enter" [427]="io_uring_register"
)

arch_is_x86_64() {
  local u
  u="$(uname -m 2>/dev/null || true)"
  [[ "$u" == "x86_64" ]]
}

map_syscall() {
  local num="$1"
  if [[ "$num" == "-1" || "$num" == "" ]]; then
    echo "none"
    return
  fi
  if arch_is_x86_64 && [[ -n "${SYSCALL_MAP[$num]+x}" ]]; then
    echo "${SYSCALL_MAP[$num]}(#$num)"
  else
    echo "syscall#$num"
  fi
}

read_state_letter() {
  # /proc/<tid>/status: "State:\tR (running)"
  local tid="$1" f="/proc/$1/status"
  [[ -r "$f" ]] || { echo "?"; return; }
  awk -F'[: \t]+' '/^State:/ {print $2; exit}' "$f" 2>/dev/null || echo "?"
}

read_nice() {
  local tid="$1" f="/proc/$1/status"
  [[ -r "$f" ]] || { echo "?"; return; }
  awk -F'[: \t]+' '/^Nice:/ {print $2; exit}' "$f" 2>/dev/null || echo "?"
}

read_cpu_from_stat() {
  # field 39 of /proc/<tid>/stat ("processor"); we parse safely around comm "()"
  local tid="$1" statline rest cpu
  [[ -r "/proc/$tid/stat" ]] || { echo "?"; return; }
  statline="$(</proc/$tid/stat)"
  # remove "<pid> (comm) " prefix
  rest="${statline#*) }"
  # fields now start at original #3 (state)
  # processor is original #39 => index 37 (0-based) in this split
  # shellcheck disable=SC2206
  local arr=($rest)
  cpu="${arr[36]}"
  [[ -n "$cpu" ]] && echo "$cpu" || echo "?"
}

read_wchan() {
  local tid="$1" w
  if [[ -r "/proc/$tid/wchan" ]]; then
    w="$(</proc/$tid/wchan)"
    [[ "$w" == "0" || -z "$w" ]] && echo "-" || echo "$w"
  else
    echo "?"
  fi
}

read_syscall_num() {
  local tid="$1" s n
  if [[ -r "/proc/$tid/syscall" ]]; then
    # format: "<nr> <arg1> <arg2> ...", or "-1" if not in syscall
    read -r n _ <"/proc/$tid/syscall" || true
    echo "${n:--1}"
  else
    echo "-1"
  fi
}

discover_f2fs_pids() {
  # Echo a space-separated list of TGIDs whose comm or cmdline contains "f2fs" (case-insensitive)
  local p comm cmd out=()
  for p in /proc/[0-9]*; do
    p="${p#/proc/}"
    [[ -d "/proc/$p" ]] || continue
    comm="$(tr -d '\n' </proc/"$p"/comm 2>/dev/null || true)"
    cmd="$(tr '\0' ' ' </proc/"$p"/cmdline 2>/dev/null || true)"
    if [[ "$comm" =~ [Ff][2][Ff][Ss] || "$cmd" =~ [Ff][2][Ff][Ss] ]]; then
      out+=("$p")
    fi
  done
  # dedup
  if ((${#out[@]})); then
    printf "%s\n" "${out[@]}" | awk '!seen[$0]++' | tr '\n' ' '
  fi
}

collect_target_pids() {
  # Fill global array TARGET_PIDS
  TARGET_PIDS=()
  if (($# > 0)); then
    for pid in "$@"; do
      if is_number "$pid" && [[ -d "/proc/$pid" ]]; then
        TARGET_PIDS+=("$pid")
      else
        printf 'WARN: skip invalid PID "%s"\n' "$pid" >&2
      fi
    done
  else
    read -r -a auto_pids <<<"$(discover_f2fs_pids || true)"
    if ((${#auto_pids[@]})); then
      TARGET_PIDS=("${auto_pids[@]}")
      printf 'Auto-discovered F2FS-related processes: %d\n' "${#TARGET_PIDS[@]}"
      printf 'PIDs: %s\n' "${TARGET_PIDS[*]}"
    else
      echo "Auto-discovered F2FS-related processes: 0"
    fi
  fi
}

print_header() {
  printf "%-8s %-8s %-1s %-3s %-3s %-22s %-24s %-18s\n" \
    "PID" "TID" "S" "CPU" "NI" "NAME" "WCHAN" "CURRENT_SYSCALL"
  printf -- "-------- -------- - --- --- ---------------------- ------------------------ ------------------\n"
}

print_tid_row() {
  local tgid="$1" tid="$2"
  [[ -r "/proc/$tid/comm" ]] || return
  local name state ni cpu wchan scnum scstr
  name="$(tr -d '\n' </proc/"$tid"/comm 2>/dev/null || echo "?")"
  state="$(read_state_letter "$tid")"
  ni="$(read_nice "$tid")"
  cpu="$(read_cpu_from_stat "$tid")"
  wchan="$(read_wchan "$tid")"
  scnum="$(read_syscall_num "$tid")"
  scstr="$(map_syscall "$scnum")"
  # print truncated name to keep columns tidy
  printf "%-8s %-8s %-1s %-3s %-3s %-22.22s %-24.24s %-18.18s\n" \
    "$tgid" "$tid" "$state" "$cpu" "$ni" "$name" "$wchan" "$scstr"
}

print_frame() {
  local mode="$1"
  echo
  echo "Thread monitor (mode=${mode})  $(date '+%F %T')"
  print_header
}

trap 'echo; echo "Interrupted. Bye."; exit 0' INT

main_loop() {
  local mode
  if (($# > 0)); then mode="PIDLIST"; else mode="auto-F2FS"; fi

  while :; do
    # Re-discover targets each round to catch dynamic changes (only for auto mode)
    if [[ "$mode" == "auto-F2FS" ]]; then
      collect_target_pids
    fi

    print_frame "$mode"

    if ((${#TARGET_PIDS[@]} == 0)); then
      echo "(no target processes found)"
    else
      for pid in "${TARGET_PIDS[@]}"; do
        [[ -d "/proc/$pid" ]] || continue
        for t in /proc/"$pid"/task/*; do
          t="${t#/proc/}"; t="${t#*/task/}"
          is_number "$t" || continue
          print_tid_row "$pid" "$t"
        done
      done
    fi

    sleep "$INTERVAL"
  done
}

# --- entry ---
declare -a TARGET_PIDS=()
if (($# > 0)); then
  collect_target_pids "$@"
else
  collect_target_pids
fi

main_loop "$@"
