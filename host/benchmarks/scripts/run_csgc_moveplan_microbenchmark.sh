#!/bin/bash

# Run a destructive CSGC Move Plan throughput benchmark without mounting F2FS.

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(realpath "${script_dir}/../../..")"
bench_dir="${repo_root}/host/benchmarks/csgc_move_bench"
bench_bin="${bench_dir}/csgc_move_bench"
openssd_repo="${OPENSSD_REPO:-/home/xin/work-xie/openssd-newest/openssd-csgc}"
nvme_cli="${NVME_CLI:-${repo_root}/host/src/nvme-cli/nvme}"
mkfs_f2fs="${MKFS_F2FS:-mkfs.f2fs}"
device="${1:-}"
profile="${2:-core}"
timestamp="$(date +%Y%m%d_%H%M%S)"
output_root="${CSGC_MP_BENCH_OUTPUT_ROOT:-${script_dir}/outputs-csgc-moveplan-bench}"
output_dir="${output_root}/${timestamp}_$(basename "${device:-no-device}")_${profile}"
terminal_log="${output_dir}/terminal.log"
summary_csv="${output_dir}/results.csv"
interrupted=0

usage()
{
    cat <<'EOF'
Usage:
  run_csgc_moveplan_microbenchmark.sh <device> [smoke|core|full]

Profiles:
  smoke  Validate one QD=1, 512-block Move Plan for two seconds.
  core   Sweep QD=1,2,4,8,16,32 with 512-block Move Plans.
  full   Sweep the same queue depths with 32, 128, and 512 blocks.

Example:
  ./run_csgc_moveplan_microbenchmark.sh /dev/nvme0n1 core

Environment overrides:
  NVME_CLI                         Customized nvme-cli path.
  MKFS_F2FS                        mkfs.f2fs path.
  OPENSSD_REPO                     OpenSSD source repository path.
  CSGC_MP_BENCH_OUTPUT_ROOT        Timestamped result root.
  CSGC_MP_BENCH_RUNTIME            Measured seconds per formal case (default 20).
  CSGC_MP_BENCH_WARMUP             Warmup seconds per formal case (default 1).
  CSGC_MP_BENCH_RUNS               Runs per formal case (default 1).
  CSGC_MP_BENCH_TIMEOUT_MS         Per-command timeout (default 120000).
  CSGC_MP_BENCH_POOL_SIZE          Segment-pair pool; must be a QD multiple.
  CSGC_MP_BENCH_EXPECTED_WORKERS   Require device workers=1 or workers=2.
  CSGC_MP_BENCH_REQUIRE_STATS=0    Allow firmware without Move Plan breakdown.
  CSGC_MP_BENCH_ASSUME_YES=1       Skip destructive confirmation.

WARNING: This benchmark resets, formats, and overwrites the selected namespace.
It leaves the namespace unmounted and invalidates normal filesystem contents.
Run it as a regular user; the script invokes sudo only where required.
EOF
}

die()
{
    echo "ERROR: $*" >&2
    exit 1
}

require_command()
{
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

run_capture()
{
    local log_file=$1
    shift

    "$@" 2>&1 | tee "${log_file}"
    return "${PIPESTATUS[0]}"
}

extract_field()
{
    local line=$1
    local key=$2

    awk -v key="${key}" '{
        for (i = 1; i <= NF; i++) {
            if ($i ~ ("^" key "=")) {
                split($i, fields, "=");
                gsub(/[^0-9].*$/, "", fields[2]);
                print fields[2];
                exit;
            }
        }
    }' <<<"${line}"
}

capture_dmesg()
{
    local output_file=$1

    "${sudo_cmd[@]}" dmesg --ctime >"${output_file}" 2>&1 || true
}

finish()
{
    local rc=$?

    trap - EXIT
    set +e
    echo
    echo "End time: $(date -Ins)"
    echo "Exit status: ${rc}"
    echo "Result directory: ${output_dir}"
    capture_dmesg "${output_dir}/dmesg.after.log"
    if (( interrupted != 0 )); then
        echo "WARNING: The benchmark was interrupted with CSGC requests possibly active."
        echo "Restart all OpenSSD applications before issuing normal IO."
    elif (( rc != 0 )); then
        echo "WARNING: The benchmark failed. Preserve logs before resetting the device."
    else
        echo "WARNING: The namespace remains destructive and unmounted."
        echo "Reset and recreate it before running filesystem workloads."
    fi
    exit "${rc}"
}

handle_interrupt()
{
    interrupted=1
    exit 130
}

if [[ "${device}" == "-h" || "${device}" == "--help" ]]; then
    usage
    exit 0
fi
if [[ -z "${device}" ]]; then
    usage
    exit 1
fi

case "${profile}" in
    smoke|core|full)
        ;;
    *)
        usage
        die "Unsupported profile: ${profile}"
        ;;
esac

formal_runtime="${CSGC_MP_BENCH_RUNTIME:-20}"
formal_warmup="${CSGC_MP_BENCH_WARMUP:-1}"
formal_runs="${CSGC_MP_BENCH_RUNS:-1}"
timeout_ms="${CSGC_MP_BENCH_TIMEOUT_MS:-120000}"
pool_override="${CSGC_MP_BENCH_POOL_SIZE:-}"
expected_workers="${CSGC_MP_BENCH_EXPECTED_WORKERS:-}"
require_stats="${CSGC_MP_BENCH_REQUIRE_STATS:-1}"

[[ "${formal_runtime}" =~ ^[1-9][0-9]*$ ]] || die "CSGC_MP_BENCH_RUNTIME must be positive"
[[ "${formal_warmup}" =~ ^[0-9]+$ ]] || die "CSGC_MP_BENCH_WARMUP must be non-negative"
[[ "${formal_runs}" =~ ^[1-9][0-9]*$ ]] || die "CSGC_MP_BENCH_RUNS must be positive"
[[ "${timeout_ms}" =~ ^[1-9][0-9]*$ ]] || die "CSGC_MP_BENCH_TIMEOUT_MS must be positive"
[[ -z "${pool_override}" || "${pool_override}" =~ ^[1-9][0-9]*$ ]] ||
    die "CSGC_MP_BENCH_POOL_SIZE must be positive"
[[ -z "${expected_workers}" || "${expected_workers}" == "1" || "${expected_workers}" == "2" ]] ||
    die "CSGC_MP_BENCH_EXPECTED_WORKERS must be 1 or 2"
[[ "${require_stats}" == "0" || "${require_stats}" == "1" ]] ||
    die "CSGC_MP_BENCH_REQUIRE_STATS must be 0 or 1"

require_command awk
require_command cp
require_command findmnt
require_command fuser
require_command gcc
require_command git
require_command grep
require_command lsblk
require_command realpath
require_command sha256sum
require_command tee
require_command tr
require_command "${mkfs_f2fs}"

[[ -x "${nvme_cli}" ]] || die "Customized nvme-cli is not executable: ${nvme_cli}"
[[ -x "${bench_dir}/build.sh" ]] || die "Benchmark build script is not executable"
[[ -d "${openssd_repo}/.git" ]] || die "OpenSSD repository not found: ${openssd_repo}"
[[ -b "${device}" ]] || die "Not a block device: ${device}"
device="$(realpath "${device}")"

if (( EUID == 0 )); then
    sudo_cmd=()
else
    require_command sudo
    sudo_cmd=(sudo)
    sudo -v
fi

"${bench_dir}/build.sh"
"${bench_bin}" --self-test

root_source="$(findmnt -nro SOURCE / || true)"
root_source_real="$(realpath "${root_source}" 2>/dev/null || true)"
while IFS= read -r block_path; do
    if [[ -n "${root_source_real}" && "${block_path}" == "${root_source_real}" ]]; then
        die "The selected device tree contains the host root filesystem: ${root_source}"
    fi
done < <(lsblk -nrpo PATH "${device}")

mounted_entries="$(lsblk -nrpo PATH,MOUNTPOINTS "${device}" | awk 'NF > 1')"
if [[ -n "${mounted_entries}" ]]; then
    echo "Mounted entries under the selected device:"
    echo "${mounted_entries}"
    die "Unmount the namespace and all child partitions before this test"
fi

while IFS= read -r block_path; do
    if "${sudo_cmd[@]}" fuser -s "${block_path}"; then
        "${sudo_cmd[@]}" fuser -v "${block_path}" || true
        die "The selected device tree is in use: ${block_path}"
    fi
done < <(lsblk -nrpo PATH "${device}")

mkdir -p "${output_dir}/cases"
exec > >(tee -a "${terminal_log}") 2>&1
export PS4='+ [${SECONDS}s] ${BASH_SOURCE##*/}:${LINENO}: '
SECONDS=0
set -x

trap finish EXIT
trap handle_interrupt INT TERM

echo "OpenSSD CSGC Move Plan microbenchmark"
echo "Start time: $(date -Ins)"
echo "Device: ${device}"
echo "Profile: ${profile}"
echo "Output directory: ${output_dir}"
echo "Customized nvme-cli: ${nvme_cli}"

echo "Host and repository metadata:"
date -Ins
hostname
uname -a
sha256sum "${nvme_cli}" "${bench_bin}"
cp -- "$0" "${output_dir}/script.snapshot.sh"
cp -- "${bench_dir}/csgc_move_bench.c" "${output_dir}/client.snapshot.c"
git -C "${repo_root}" rev-parse --abbrev-ref HEAD
git -C "${repo_root}" rev-parse HEAD
git -C "${repo_root}" status --short
git -C "${repo_root}" diff --binary >"${output_dir}/artifact-working-tree.patch"
git -C "${openssd_repo}" rev-parse --abbrev-ref HEAD
git -C "${openssd_repo}" rev-parse HEAD
git -C "${openssd_repo}" status --short
git -C "${openssd_repo}" diff --binary >"${output_dir}/openssd-working-tree.patch"

echo "NVMe device metadata:"
"${sudo_cmd[@]}" "${nvme_cli}" list
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS "${device}"
"${sudo_cmd[@]}" "${nvme_cli}" id-ctrl "${device}"
"${sudo_cmd[@]}" "${nvme_cli}" id-ns "${device}"
capture_dmesg "${output_dir}/dmesg.before.log"

if [[ "${CSGC_MP_BENCH_ASSUME_YES:-0}" != "1" ]]; then
    set +x
    echo
    echo "DESTRUCTIVE WARNING: This test resets and overwrites ${device}."
    echo "All filesystem data on this namespace will be lost."
    read -r -p "Type 'DESTROY ${device}' to continue: " confirmation
    set -x
    [[ "${confirmation}" == "DESTROY ${device}" ]] || die "Destructive confirmation did not match"
fi

echo "Initialize separate-L2P F2FS geometry without mounting the namespace"
"${sudo_cmd[@]}" "${nvme_cli}" ssd-admin "${device}" -o 1 --l2p 2 --nand 0 --dsm 0
"${sudo_cmd[@]}" "${mkfs_f2fs}" -f -s 8 "${device}"
"${sudo_cmd[@]}" "${nvme_cli}" fs-ready "${device}" -f 1
"${sudo_cmd[@]}" "${mkfs_f2fs}" -f -s 8 "${device}"
"${sudo_cmd[@]}" "${bench_bin}" --device "${device}" --dry-run

printf '%s\n' \
    'profile,case,run,qd,moves,pool,warmup_s,runtime_s,command_rc,stat_rc,validation,requests,total_moves,elapsed_s,requests_s,logical_mib_s,avg_us,p50_us,p95_us,p99_us,max_us,stats_enabled,workers,device_req,device_ok,device_fail,device_decl,device_sub,device_done,device_bytes,supply_x10000,service_x10000,dma_x10000,device_srv_mib_s,device_good_mib_s' \
    >"${summary_csv}"

run_case()
{
    local moves=$1
    local qd=$2
    local run_index=$3
    local runtime=$4
    local warmup=$5
    local case_name="m${moves}_qd${qd}"
    local case_dir="${output_dir}/cases/${case_name}/run${run_index}"
    local command_log="${case_dir}/command.log"
    local reset_log="${case_dir}/reset.log"
    local ssd_raw_log="${case_dir}/ssd-stat.raw.log"
    local ssd_log="${case_dir}/ssd-stat.log"
    local command_rc=0
    local stat_rc=0
    local reset_rc=0
    local host_line cfg_line mp_line channel_line
    local -a host_fields=()
    local -a validation_errors=()
    local validation
    local pool_arg=()
    local stats_enabled workers device_req device_ok device_fail
    local device_decl device_sub device_done device_bytes
    local supply service dma srv_mib_s good_mib_s

    mkdir -p "${case_dir}"
    echo
    echo "========================================================================"
    echo "Case ${case_name}, run ${run_index}: moves=${moves} qd=${qd} warmup=${warmup}s runtime=${runtime}s"

    if [[ -n "${pool_override}" ]]; then
        (( pool_override >= qd && pool_override % qd == 0 )) ||
            die "CSGC_MP_BENCH_POOL_SIZE=${pool_override} is invalid for QD=${qd}"
        pool_arg=(--pool-size "${pool_override}")
    fi

    set +e
    run_capture "${reset_log}" \
        "${sudo_cmd[@]}" "${nvme_cli}" ssd-admin "${device}" -o 2
    reset_rc=$?
    set -e
    (( reset_rc == 0 )) || die "SSD stat reset failed for ${case_name}/run${run_index}"

    set +e
    run_capture "${command_log}" \
        "${sudo_cmd[@]}" "${bench_bin}" --device "${device}" \
        --queue-depth "${qd}" --moves "${moves}" --warmup "${warmup}" \
        --runtime "${runtime}" --timeout-ms "${timeout_ms}" \
        "${pool_arg[@]}"
    command_rc=$?

    "${sudo_cmd[@]}" "${nvme_cli}" read "${device}" \
        -s 123 -c 1 -z 4096 -t -L >"${ssd_raw_log}" 2>&1
    stat_rc=$?
    tr -d '\000' <"${ssd_raw_log}" >"${ssd_log}"
    cat "${ssd_log}"
    set -e

    host_line="$(grep -F 'CSGC_MOVE_BENCH_CSV,' "${command_log}" | tail -n 1 || true)"
    cfg_line="$(grep -F '<OpenSSD>: csgc_mp_cfg:' "${ssd_log}" | tail -n 1 || true)"
    mp_line="$(grep -F '<OpenSSD>: csgc_mp:' "${ssd_log}" | tail -n 1 || true)"
    channel_line="$(grep -F '<OpenSSD>: csgc_mp_channel_x10000:' "${ssd_log}" | tail -n 1 || true)"

    IFS=',' read -r -a host_fields <<<"${host_line}"
    stats_enabled="$(extract_field "${cfg_line}" enabled)"
    workers="$(extract_field "${cfg_line}" workers)"
    device_req="$(extract_field "${mp_line}" req)"
    device_ok="$(extract_field "${mp_line}" ok)"
    device_fail="$(extract_field "${mp_line}" fail)"
    device_decl="$(extract_field "${mp_line}" decl)"
    device_sub="$(extract_field "${mp_line}" sub)"
    device_done="$(extract_field "${mp_line}" done)"
    device_bytes="$(extract_field "${mp_line}" bytes)"
    supply="$(extract_field "${channel_line}" supply)"
    service="$(extract_field "${channel_line}" service)"
    dma="$(extract_field "${channel_line}" dma)"
    srv_mib_s="$(extract_field "${channel_line}" srv_mib_s)"
    good_mib_s="$(extract_field "${channel_line}" good_mib_s)"

    [[ "${command_rc}" == "0" ]] || validation_errors+=("command_rc=${command_rc}")
    [[ "${stat_rc}" == "0" ]] || validation_errors+=("stat_rc=${stat_rc}")
    [[ ${#host_fields[@]} -eq 16 && "${host_fields[0]:-}" == "CSGC_MOVE_BENCH_CSV" ]] ||
        validation_errors+=("host_csv=missing_or_invalid")
    [[ -n "${cfg_line}" ]] || validation_errors+=("device_cfg=missing")
    if [[ -n "${expected_workers}" && "${workers}" != "${expected_workers}" ]]; then
        validation_errors+=("workers=${workers:-missing}")
    fi
    if [[ "${require_stats}" == "1" ]]; then
        [[ "${stats_enabled}" == "1" ]] || validation_errors+=("stats_enabled=${stats_enabled:-missing}")
        [[ "${device_req}" =~ ^[1-9][0-9]*$ ]] || validation_errors+=("device_req=${device_req:-missing}")
        [[ "${device_ok}" == "${device_req}" ]] || validation_errors+=("device_ok=${device_ok:-missing}")
        [[ "${device_fail}" == "0" ]] || validation_errors+=("device_fail=${device_fail:-missing}")
        [[ "${device_decl}" =~ ^[1-9][0-9]*$ ]] || validation_errors+=("device_decl=${device_decl:-missing}")
        [[ "${device_sub}" == "${device_decl}" ]] || validation_errors+=("device_sub=${device_sub:-missing}")
        [[ "${device_done}" == "${device_decl}" ]] || validation_errors+=("device_done=${device_done:-missing}")
    fi

    if (( ${#validation_errors[@]} == 0 )); then
        validation=PASS
    else
        validation=FAIL
    fi

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "${profile}" "${case_name}" "${run_index}" \
        "${host_fields[1]:-${qd}}" "${host_fields[2]:-${moves}}" \
        "${host_fields[3]:-}" "${host_fields[4]:-${warmup}}" \
        "${host_fields[5]:-${runtime}}" "${command_rc}" "${stat_rc}" \
        "${validation}" "${host_fields[6]:-}" "${host_fields[7]:-}" \
        "${host_fields[8]:-}" "${host_fields[9]:-}" "${host_fields[10]:-}" \
        "${host_fields[11]:-}" "${host_fields[12]:-}" "${host_fields[13]:-}" \
        "${host_fields[14]:-}" "${host_fields[15]:-}" "${stats_enabled}" \
        "${workers}" "${device_req}" "${device_ok}" "${device_fail}" \
        "${device_decl}" "${device_sub}" "${device_done}" "${device_bytes}" \
        "${supply}" "${service}" \
        "${dma}" "${srv_mib_s}" "${good_mib_s}" >>"${summary_csv}"

    echo "Validation: ${validation}"
    if [[ "${validation}" != "PASS" ]]; then
        printf 'Validation errors: %s\n' "${validation_errors[*]}"
        return 1
    fi
}

run_matrix()
{
    local moves=$1
    local qd
    local run_index

    for qd in 1 2 4 8 16 32; do
        for ((run_index = 1; run_index <= formal_runs; run_index++)); do
            run_case "${moves}" "${qd}" "${run_index}" \
                "${formal_runtime}" "${formal_warmup}"
        done
    done
}

if [[ "${profile}" == "smoke" ]]; then
    run_case 512 1 1 2 0
elif [[ "${profile}" == "core" ]]; then
    run_matrix 512
else
    run_matrix 32
    run_matrix 128
    run_matrix 512
fi

echo
echo "All requested cases passed."
echo "Summary CSV: ${summary_csv}"
