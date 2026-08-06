#!/bin/bash

# Run the destructive OpenSSD Core3 CDMA microbenchmark and preserve all
# commands, device metadata, raw SSD logs, validation results, and dmesg.

set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(realpath "${script_dir}/../../..")"
openssd_repo="${OPENSSD_REPO:-/home/xin/work-xie/openssd-newest/openssd-csgc}"
nvme_cli="${NVME_CLI:-${repo_root}/host/src/nvme-cli/nvme}"
device="${1:-}"
profile="${2:-core}"
timestamp="$(date +%Y%m%d_%H%M%S)"
output_root="${CDMA_BENCH_OUTPUT_ROOT:-${script_dir}/outputs-cdma-bench}"
output_dir="${output_root}/${timestamp}_$(basename "${device:-no-device}")_${profile}"
terminal_log="${output_dir}/terminal.log"
summary_csv="${output_dir}/results.csv"
interrupted=0

usage()
{
    cat <<'EOF'
Usage:
  run_cdma_microbenchmark.sh <device> [smoke|core|full]

Profiles:
  smoke  Run one 16 MiB SG validation test.
  core   Run smoke, then three key configurations three times each.
  full   Run smoke, then the complete simple/SG/cache test matrix.

Example:
  ./run_cdma_microbenchmark.sh /dev/nvme0n1 core

Environment overrides:
  NVME_CLI                   Path to the customized nvme-cli binary.
  OPENSSD_REPO               Path to the OpenSSD source repository.
  CDMA_BENCH_OUTPUT_ROOT     Root directory for timestamped results.
  CDMA_BENCH_RUNS            Independent runs per formal configuration (default 3).
  CDMA_BENCH_REPEATS         Measured repeats per command (default 5).
  CDMA_BENCH_WARMUP          Warmup batches per command (default 32).
  CDMA_BENCH_TIMEOUT_MS      Admin command timeout in milliseconds (default 120000).
  CDMA_BENCH_PAUSE_SECONDS   Pause between formal runs (default 1).
  CDMA_BENCH_ASSUME_YES=1    Skip the interactive destructive confirmation.

WARNING: The benchmark overwrites raw OpenSSD storage. Run it only while the
namespace is unmounted, before mkfs/fs-ready, and with no concurrent IO.
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
        echo "WARNING: The command was interrupted. The device may still own Core3/CDMA."
        echo "Do not issue normal IO until the four OpenSSD applications are restarted."
    elif (( rc != 0 )); then
        echo "WARNING: The benchmark did not finish successfully. Preserve the logs before reset."
    fi
    exit "${rc}"
}

handle_interrupt()
{
    interrupted=1
    exit 130
}

if [[ -z "${device}" || "${device}" == "-h" || "${device}" == "--help" ]]; then
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

formal_runs="${CDMA_BENCH_RUNS:-3}"
formal_repeats="${CDMA_BENCH_REPEATS:-5}"
formal_warmup="${CDMA_BENCH_WARMUP:-32}"
timeout_ms="${CDMA_BENCH_TIMEOUT_MS:-120000}"
pause_seconds="${CDMA_BENCH_PAUSE_SECONDS:-1}"

[[ "${formal_runs}" =~ ^[1-9][0-9]*$ ]] || die "CDMA_BENCH_RUNS must be positive"
[[ "${formal_repeats}" =~ ^[1-9][0-9]*$ ]] || die "CDMA_BENCH_REPEATS must be positive"
[[ "${formal_warmup}" =~ ^[0-9]+$ ]] || die "CDMA_BENCH_WARMUP must be non-negative"
[[ "${timeout_ms}" =~ ^[1-9][0-9]*$ ]] || die "CDMA_BENCH_TIMEOUT_MS must be positive"
[[ "${pause_seconds}" =~ ^[0-9]+$ ]] || die "CDMA_BENCH_PAUSE_SECONDS must be non-negative"
(( formal_repeats <= 16 )) || die "Repeats must not exceed the device limit of 16"
(( formal_warmup <= 255 )) || die "Warmup must not exceed 255"

require_command awk
require_command cp
require_command findmnt
require_command fuser
require_command git
require_command grep
require_command lsblk
require_command realpath
require_command sha256sum
require_command tee

[[ -x "${nvme_cli}" ]] || die "Customized nvme-cli is not executable: ${nvme_cli}"
[[ -b "${device}" ]] || die "Not a block device: ${device}"
device="$(realpath "${device}")"

if (( EUID == 0 )); then
    sudo_cmd=()
else
    require_command sudo
    sudo_cmd=(sudo)
    sudo -v
fi

mkdir -p "${output_dir}/cases"
exec > >(tee -a "${terminal_log}") 2>&1
export PS4='+ [${SECONDS}s] ${BASH_SOURCE##*/}:${LINENO}: '
SECONDS=0
set -x

trap finish EXIT
trap handle_interrupt INT TERM

echo "OpenSSD Core3 CDMA microbenchmark"
echo "Start time: $(date -Ins)"
echo "Device: ${device}"
echo "Profile: ${profile}"
echo "Output directory: ${output_dir}"
echo "Customized nvme-cli: ${nvme_cli}"

"${nvme_cli}" version
set +e
set +x
nvme_help_output="$("${nvme_cli}" cdma-bench --help 2>&1)"
nvme_help_rc=$?
set -x
set -e
printf '%s\n' "${nvme_help_output}"
echo "cdma-bench help exit status: ${nvme_help_rc}"
grep -q 'OpenSSD Core3 CDMA microbenchmark' <<<"${nvme_help_output}" ||
    die "The selected nvme-cli does not contain the customized cdma-bench command"

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
    die "Unmount the namespace and all child partitions before this destructive test"
fi

while IFS= read -r block_path; do
    if "${sudo_cmd[@]}" fuser -s "${block_path}"; then
        "${sudo_cmd[@]}" fuser -v "${block_path}" || true
        die "The selected device tree is in use: ${block_path}"
    fi
done < <(lsblk -nrpo PATH "${device}")

echo "Relevant running processes (informational only):"
ps -eo pid,stat,comm,args | grep -E '[f]io|[f]ile_writer|[t]est\.sh|[m]kfs|[f]sck' || true

echo "Host and repository metadata:"
date -Ins
hostname
uname -a
uptime
sha256sum "${nvme_cli}"
cp -- "$0" "${output_dir}/script.snapshot.sh"
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
"${sudo_cmd[@]}" "${nvme_cli}" smart-log "${device}" || true
findmnt --source "${device}" || true
"${sudo_cmd[@]}" fuser -v "${device}" || true

capture_dmesg "${output_dir}/dmesg.before.log"

if [[ "${CDMA_BENCH_ASSUME_YES:-0}" != "1" ]]; then
    set +x
    echo
    echo "DESTRUCTIVE WARNING: This test overwrites raw storage on ${device}."
    echo "It must run before mkfs/fs-ready and without any concurrent IO."
    read -r -p "Type 'DESTROY ${device}' to continue: " confirmation
    set -x
    [[ "${confirmation}" == "DESTROY ${device}" ]] || die "Destructive confirmation did not match"
fi

printf '%s\n' \
    'case,run,mode,size,vectors,interval,warmup,iterations,repeat,flush,command_rc,read_rc,validation,state,status,batch,bytes,ns,bw_x1000,mib_s,gib_s,min_x1000,max_x1000,cdma_err,verify_err' \
    >"${summary_csv}"

run_case()
{
    local case_name=$1
    local run_index=$2
    local mode=$3
    local transfer_size=$4
    local vectors=$5
    local interval=$6
    local warmup=$7
    local iterations=$8
    local repeats=$9
    local flush=${10}
    local case_dir="${output_dir}/cases/${case_name}/run${run_index}"
    local command_log="${case_dir}/command.log"
    local reset_log="${case_dir}/reset.log"
    local ssd_log="${case_dir}/ssd.log"
    local expected_batch=$((transfer_size * vectors))
    local expected_bytes=$((expected_batch * iterations * repeats))
    local command_rc=0
    local read_rc=0
    local reset_rc=0
    local bench_line
    local result_line
    local state status done verify batch bytes ns bw min_bw max_bw cdma_err verify_err
    local mib_s gib_s validation
    local -a validation_errors=()

    mkdir -p "${case_dir}"
    echo
    echo "========================================================================"
    echo "Case ${case_name}, run ${run_index}"
    echo "mode=${mode} size=${transfer_size} vectors=${vectors} interval=${interval}"
    echo "warmup=${warmup} iterations=${iterations} repeat=${repeats} flush=${flush}"
    echo "expected_batch=${expected_batch} expected_bytes=${expected_bytes}"

    set +e
    run_capture "${reset_log}" \
        "${sudo_cmd[@]}" "${nvme_cli}" ssd-admin "${device}" -o 2
    reset_rc=$?
    set -e
    (( reset_rc == 0 )) || die "SSD stat reset failed for ${case_name}/run${run_index}"

    set +e
    run_capture "${command_log}" \
        "${sudo_cmd[@]}" "${nvme_cli}" cdma-bench "${device}" \
        --mode="${mode}" \
        --src-lba=0 --dst-lba=16384 \
        --transfer-size="${transfer_size}" \
        --vectors="${vectors}" --interval="${interval}" \
        --warmup="${warmup}" --iterations="${iterations}" \
        --repeat="${repeats}" --flush="${flush}" \
        --timeout="${timeout_ms}"
    command_rc=$?

    run_capture "${ssd_log}" \
        "${sudo_cmd[@]}" "${nvme_cli}" read "${device}" \
        -s 123 -c 1 -z 4096 -t -L
    read_rc=$?
    set -e

    # GET_SSD_LOG transfers a block-aligned buffer with trailing NUL padding.
    # Force text mode so grep returns the matching log lines instead of only
    # reporting that the file is binary.
    bench_line="$(grep -aF '<OpenSSD>: cdma_bench:' "${ssd_log}" | tail -n 1 || true)"
    result_line="$(grep -aF '<OpenSSD>: cdma_bench_result:' "${ssd_log}" | tail -n 1 || true)"

    state="$(extract_field "${bench_line}" state)"
    status="$(extract_field "${bench_line}" status)"
    done="$(extract_field "${bench_line}" done)"
    verify="$(extract_field "${bench_line}" verify)"
    batch="$(extract_field "${result_line}" batch)"
    bytes="$(extract_field "${result_line}" bytes)"
    ns="$(extract_field "${result_line}" ns)"
    bw="$(extract_field "${result_line}" bw_mib_s_x1000)"
    min_bw="$(extract_field "${result_line}" min)"
    max_bw="$(extract_field "${result_line}" max)"
    cdma_err="$(extract_field "${result_line}" cdma_err)"
    verify_err="$(extract_field "${result_line}" verify_err)"

    [[ "${command_rc}" == "0" ]] || validation_errors+=("command_rc=${command_rc}")
    [[ "${read_rc}" == "0" ]] || validation_errors+=("read_rc=${read_rc}")
    [[ "${state}" == "2" ]] || validation_errors+=("state=${state:-missing}")
    [[ "${status}" == "0" ]] || validation_errors+=("status=${status:-missing}")
    [[ "${done}" == "${repeats}" ]] || validation_errors+=("done=${done:-missing}")
    [[ "${verify}" == "${vectors}" ]] || validation_errors+=("verify=${verify:-missing}")
    [[ "${batch}" == "${expected_batch}" ]] || validation_errors+=("batch=${batch:-missing}")
    [[ "${bytes}" == "${expected_bytes}" ]] || validation_errors+=("bytes=${bytes:-missing}")
    [[ "${ns}" =~ ^[1-9][0-9]*$ ]] || validation_errors+=("ns=${ns:-missing}")
    [[ "${cdma_err}" == "0" ]] || validation_errors+=("cdma_err=${cdma_err:-missing}")
    [[ "${verify_err}" == "0" ]] || validation_errors+=("verify_err=${verify_err:-missing}")

    if [[ "${bw}" =~ ^[0-9]+$ ]]; then
        mib_s="$(awk -v value="${bw}" 'BEGIN { printf "%.3f", value / 1000.0 }')"
        gib_s="$(awk -v value="${bw}" 'BEGIN { printf "%.6f", value / 1024000.0 }')"
    else
        mib_s=""
        gib_s=""
        validation_errors+=("bw_mib_s_x1000=${bw:-missing}")
    fi

    if (( ${#validation_errors[@]} == 0 )); then
        validation=PASS
    else
        validation=FAIL
    fi

    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "${case_name}" "${run_index}" "${mode}" "${transfer_size}" \
        "${vectors}" "${interval}" "${warmup}" "${iterations}" \
        "${repeats}" "${flush}" "${command_rc}" "${read_rc}" \
        "${validation}" "${state}" "${status}" "${batch}" "${bytes}" \
        "${ns}" "${bw}" "${mib_s}" "${gib_s}" "${min_bw}" "${max_bw}" \
        "${cdma_err}" "${verify_err}" >>"${summary_csv}"

    echo "Validation: ${validation}"
    echo "Throughput: ${mib_s:-N/A} MiB/s (${gib_s:-N/A} GiB/s)"
    if [[ "${validation}" != "PASS" ]]; then
        printf 'Validation errors: %s\n' "${validation_errors[*]}"
        return 1
    fi
    return 0
}

run_formal_case()
{
    local case_name=$1
    local mode=$2
    local transfer_size=$3
    local vectors=$4
    local interval=$5
    local iterations=$6
    local flush=$7

    for ((run_index = 1; run_index <= formal_runs; run_index++)); do
        run_case "${case_name}" "${run_index}" "${mode}" "${transfer_size}" \
            "${vectors}" "${interval}" "${formal_warmup}" "${iterations}" \
            "${formal_repeats}" "${flush}"
        sleep "${pause_seconds}"
    done
}

run_case smoke 1 1 4096 32 1 4 128 1 0

if [[ "${profile}" == "core" ]]; then
    run_formal_case simple_128k 0 131072 1 1 8192 0
    # Production CSIO sends one-vector requests through the simple CDMA path.
    run_formal_case simple_4k 0 4096 1 1 262144 0
    run_formal_case sg_4k_v32 1 4096 32 1 8192 0
elif [[ "${profile}" == "full" ]]; then
    run_formal_case simple_4k 0 4096 1 1 262144 0
    run_formal_case simple_16k 0 16384 1 1 65536 0
    run_formal_case simple_64k 0 65536 1 1 16384 0
    run_formal_case simple_128k 0 131072 1 1 8192 0
    run_formal_case simple_512k 0 524288 1 1 2048 0
    run_formal_case simple_1m 0 1048576 1 1 1024 0

    run_formal_case sg_4k_v2 1 4096 2 1 131072 0
    run_formal_case sg_4k_v4 1 4096 4 1 65536 0
    run_formal_case sg_4k_v8 1 4096 8 1 32768 0
    run_formal_case sg_4k_v16 1 4096 16 1 16384 0
    run_formal_case sg_4k_v32 1 4096 32 1 8192 0

    run_formal_case sg_16k_v32 1 16384 32 1 2048 0
    run_formal_case sg_64k_v32 1 65536 32 1 512 0
    run_formal_case sg_128k_v32 1 131072 32 1 256 0
    run_formal_case sg_256k_v32 1 262144 32 1 128 0

    run_formal_case simple_128k_flush 0 131072 1 1 8192 1
    run_formal_case sg_4k_v32_flush 1 4096 32 1 8192 1
fi

aggregate_csv="${output_dir}/results-by-case.csv"
{
    echo "case,count,mean_mib_s,stddev_mib_s,min_mib_s,max_mib_s"
    awk -F, '
        NR == 1 { next }
        $13 == "PASS" {
            count[$1]++;
            sum[$1] += $20;
            sum_sq[$1] += $20 * $20;
            if (!(($1) in min) || $20 < min[$1]) min[$1] = $20;
            if (!(($1) in max) || $20 > max[$1]) max[$1] = $20;
        }
        END {
            for (name in count) {
                mean = sum[name] / count[name];
                variance = sum_sq[name] / count[name] - mean * mean;
                if (variance < 0) variance = 0;
                printf "%s,%d,%.3f,%.3f,%.3f,%.3f\n", name, count[name],
                       mean, sqrt(variance), min[name], max[name];
            }
        }
    ' "${summary_csv}" | sort
} >"${aggregate_csv}"

echo
echo "Per-run results:"
cat "${summary_csv}"
echo
echo "Aggregated results:"
cat "${aggregate_csv}"
echo
echo "All requested CDMA benchmark cases completed successfully."
echo "Do not reuse any pre-existing filesystem. Reinitialize OpenSSD and run mkfs before F2FS tests."
