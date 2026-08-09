#!/bin/bash

set -o pipefail

# gc_mode="ori"      # vanilla f2fs
# gc_mode="iplfs"    # iplfs
# gc_mode="cs"       # csgc
light_evaluation=0

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
openssd_remote_host="192.168.98.31"
openssd_sync_script="/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc/scripts/sync_code.sh"

# Detect the active worker label from every Vitis project populated by sync_code.sh.
detect_ssd_thread_mode() {
  local -a ssh_command=(
    ssh
    -o BatchMode=yes
    -o ConnectTimeout=10
    -o ConnectionAttempts=1
    -o StrictHostKeyChecking=yes
  )
  local remote_command
  local remote_output
  local reported_sync_script=""
  local reported_workspace=""
  local reported_configs=""
  local reported_target_count=""
  local config_digest=""
  local worker_count=""
  local production_mode=""
  local move_plan_v2=""
  local move_plan_fast_unsafe=""
  local key value extra

  # A sudo-launched benchmark must use the invoking user's SSH identity, not root's.
  if (( EUID == 0 )) && [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
    if ! id -u "${SUDO_USER}" >/dev/null 2>&1; then
      echo "Error: SUDO_USER does not name a local account: ${SUDO_USER}" >&2
      echo "Action: run the script through sudo from the account that can access ${openssd_remote_host}." >&2
      return 1
    fi
    ssh_command=(sudo -H -u "${SUDO_USER}" -- "${ssh_command[@]}")
  fi

  remote_command=$(cat <<'REMOTE_SCRIPT'
set -eu
sync_script='/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc/scripts/sync_code.sh'

if [ ! -r "${sync_script}" ]; then
  echo "Remote error: sync_code.sh is not readable: ${sync_script}" >&2
  exit 20
fi

# Read one literal assignment without executing the destructive sync script.
read_assignment() {
  assignment_name=$1
  awk -v name="${assignment_name}" '
    $0 ~ "^[[:space:]]*" name "[[:space:]]*=" {
      line = $0
      sub(/^[^=]*=/, "", line)
      sub(/[[:space:]]*#.*/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      if ((substr(line, 1, 1) == "\"" && substr(line, length(line), 1) == "\"") ||
          (substr(line, 1, 1) == "\047" && substr(line, length(line), 1) == "\047")) {
        line = substr(line, 2, length(line) - 2)
      }
      count++
      value = line
    }
    END {
      if (count != 1 || value == "") {
        printf "Remote error: expected one literal assignment for %s in sync_code.sh\n", name > "/dev/stderr"
        exit 1
      }
      print value
    }
  ' "${sync_script}"
}

# Confirm that sync_code.sh populates each inspected project from src/shared.
require_shared_copy() {
  project_variable=$1
  awk -v project_variable="${project_variable}" '
    {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if (line ~ /^#/) {
        next
      }
      gsub(/[[:space:]]+/, " ", line)
      expected = "sudo cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${" project_variable "}/src"
      expected_without_sudo = "cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${" project_variable "}/src"
      if (line == expected || line == expected_without_sudo) {
        count++
      }
    }
    END {
      if (count != 1) {
        printf "Remote error: expected one src/shared copy rule for %s in sync_code.sh, found %d\n", project_variable, count > "/dev/stderr"
        exit 1
      }
    }
  ' "${sync_script}"
}

# Read one numeric configuration macro without invoking the build system.
# Missing optional protocol switches are reported as "undefined".
read_optional_macro() {
  config_path=$1
  macro_name=$2
  awk -v name="${macro_name}" '
    $0 ~ "^[[:space:]]*#[[:space:]]*define[[:space:]]+" name "([[:space:]]|$)" {
      line = $0
      sub("^[[:space:]]*#[[:space:]]*define[[:space:]]+" name "[[:space:]]+", "", line)
      sub(/[[:space:]].*$/, "", line)
      count++
      value = line
    }
    END {
      if (count == 0) {
        print "undefined"
      } else if (count == 1 && (value == "0" || value == "1" || value == "2")) {
        print value
      } else {
        printf "Remote error: expected zero or one literal definition for %s in %s, found %d value=%s\n", name, FILENAME, count, value > "/dev/stderr"
        exit 1
      }
    }
  ' "${config_path}"
}

workspace=$(read_assignment VITIS_WORKSPACE_DIR) || exit 21
ftl_project=$(read_assignment VITIS_FTL_PROJECT_NAME) || exit 21
leader_project=$(read_assignment VITIS_CS_PROJECT_NAME) || exit 21
worker1_project=$(read_assignment VITIS_CS_WORKER1_PROJECT_NAME) || exit 21
worker2_project=$(read_assignment VITIS_CS_WORKER2_PROJECT_NAME) || exit 21

require_shared_copy VITIS_FTL_PROJECT_NAME || exit 22
require_shared_copy VITIS_CS_PROJECT_NAME || exit 22
require_shared_copy VITIS_CS_WORKER1_PROJECT_NAME || exit 22
require_shared_copy VITIS_CS_WORKER2_PROJECT_NAME || exit 22

if ! printf '%s\n' "${workspace}" | grep -Eq '^/[A-Za-z0-9._/-]+$'; then
  echo "Remote error: unsafe or non-literal VITIS_WORKSPACE_DIR: ${workspace}" >&2
  exit 23
fi

common_workers=''
common_digest=''
common_production=''
common_move_plan_v2=''
common_move_plan_fast_unsafe=''
config_paths=''
seen_projects=''
target_count=0
for project in "${ftl_project}" "${leader_project}" "${worker1_project}" "${worker2_project}"; do
  if ! printf '%s\n' "${project}" | grep -Eq '^[A-Za-z0-9._-]+$'; then
    echo "Remote error: unsafe or non-literal Vitis project name: ${project}" >&2
    exit 24
  fi

  case " ${seen_projects} " in
    *" ${project} "*)
      echo "Remote error: duplicate Vitis project parsed from sync_code.sh: ${project}" >&2
      exit 25
      ;;
  esac
  seen_projects="${seen_projects} ${project}"

  config="${workspace}/${project}/src/config.h"
  if [ ! -r "${config}" ]; then
    echo "Remote error: Vitis config is not readable: ${config}" >&2
    exit 26
  fi

  workers=$(awk '
    BEGIN { count = 0; value = "" }
    $0 ~ /^[[:space:]]*#[[:space:]]*define[[:space:]]+CONFIG_CSGC_ACTIVE_WORKERS([[:space:]]|$)/ {
      line = $0
      sub(/^[[:space:]]*#[[:space:]]*define[[:space:]]+CONFIG_CSGC_ACTIVE_WORKERS[[:space:]]+/, "", line)
      sub(/[[:space:]].*$/, "", line)
      count++
      value = line
    }
    END {
      if (count != 1) {
        printf "Remote error: expected exactly one active CONFIG_CSGC_ACTIVE_WORKERS definition in %s, found %d\n", FILENAME, count > "/dev/stderr"
        exit 1
      }
      if (value != "1" && value != "2") {
        printf "Remote error: CONFIG_CSGC_ACTIVE_WORKERS in %s must be the literal 1 or 2, got: %s\n", FILENAME, value > "/dev/stderr"
        exit 1
      }
      print value
    }
  ' "${config}") || exit 27
  digest_line=$(sha256sum "${config}") || exit 28
  digest=${digest_line%% *}
  production=$(read_optional_macro "${config}" CONFIG_OPENSSD_PRODUCTION_PERFORMANCE) || exit 30
  move_plan_v2=$(read_optional_macro "${config}" CONFIG_CSGC_MOVE_PLAN_V2) || exit 30
  move_plan_fast_unsafe=$(read_optional_macro "${config}" CONFIG_CSGC_MOVE_PLAN_FAST_UNSAFE) || exit 30

  if [ -z "${common_workers}" ]; then
    common_workers="${workers}"
    common_digest="${digest}"
    common_production="${production}"
    common_move_plan_v2="${move_plan_v2}"
    common_move_plan_fast_unsafe="${move_plan_fast_unsafe}"
  elif [ "${workers}" != "${common_workers}" ] \
      || [ "${digest}" != "${common_digest}" ] \
      || [ "${production}" != "${common_production}" ] \
      || [ "${move_plan_v2}" != "${common_move_plan_v2}" ] \
      || [ "${move_plan_fast_unsafe}" != "${common_move_plan_fast_unsafe}" ]; then
    echo "Remote error: Vitis config.h copies are inconsistent; rerun sync_code.sh and rebuild the firmware." >&2
    exit 29
  fi

  if [ -z "${config_paths}" ]; then
    config_paths="${config}"
  else
    config_paths="${config_paths},${config}"
  fi
  target_count=$((target_count + 1))
done

printf 'sync_script\t%s\n' "${sync_script}"
printf 'workspace\t%s\n' "${workspace}"
printf 'configs\t%s\n' "${config_paths}"
printf 'target_count\t%s\n' "${target_count}"
printf 'digest\t%s\n' "${common_digest}"
printf 'workers\t%s\n' "${common_workers}"
printf 'production\t%s\n' "${common_production}"
printf 'move_plan_v2\t%s\n' "${common_move_plan_v2}"
printf 'move_plan_fast_unsafe\t%s\n' "${common_move_plan_fast_unsafe}"
REMOTE_SCRIPT
)

  if ! remote_output="$("${ssh_command[@]}" "${openssd_remote_host}" "${remote_command}")"; then
    echo "Error: failed to detect the SSD worker count from the Vitis workspace referenced by ${openssd_remote_host}:${openssd_sync_script}." >&2
    echo "Action: verify non-interactive SSH access, sync_code.sh assignments, all four Vitis config.h copies, and the macro definition, then rerun the command." >&2
    return 1
  fi

  while IFS=$'\t' read -r key value extra; do
    if [[ -n "${extra}" ]]; then
      echo "Error: malformed SSD worker detection output for key '${key}'." >&2
      return 1
    fi
    case "${key}" in
      sync_script)
        reported_sync_script="${value}"
        ;;
      workspace)
        reported_workspace="${value}"
        ;;
      configs)
        reported_configs="${value}"
        ;;
      target_count)
        reported_target_count="${value}"
        ;;
      digest)
        config_digest="${value}"
        ;;
      workers)
        worker_count="${value}"
        ;;
      production)
        production_mode="${value}"
        ;;
      move_plan_v2)
        move_plan_v2="${value}"
        ;;
      move_plan_fast_unsafe)
        move_plan_fast_unsafe="${value}"
        ;;
      *)
        echo "Error: unexpected SSD worker detection output: ${key}" >&2
        return 1
        ;;
    esac
  done <<< "${remote_output}"

  if [[ "${reported_sync_script}" != "${openssd_sync_script}" ||
        ! "${reported_workspace}" =~ ^/[A-Za-z0-9._/-]+$ ||
        -z "${reported_configs}" ||
        "${reported_target_count}" != "4" ||
        ! "${config_digest}" =~ ^[0-9a-fA-F]{64}$ ||
        ! "${worker_count}" =~ ^[12]$ ||
        ! "${production_mode}" =~ ^(0|1|undefined)$ ||
        ! "${move_plan_v2}" =~ ^(0|1|undefined)$ ||
        ! "${move_plan_fast_unsafe}" =~ ^(0|1|undefined)$ ]]; then
    echo "Error: incomplete or invalid provenance from the remote Vitis workspace." >&2
    echo "Action: inspect ${openssd_remote_host}:${openssd_sync_script} and rerun the command." >&2
    return 1
  fi

  echo "OpenSSD Vitis workspace: host=${openssd_remote_host} path=${reported_workspace}" >&2
  echo "Validated Vitis configs: count=${reported_target_count} sha256=${config_digest} files=${reported_configs}" >&2
  echo "Validated OpenSSD mode: production=${production_mode} move_plan_v2=${move_plan_v2} move_plan_fast_unsafe=${move_plan_fast_unsafe}" >&2

  if [[ -n "${CSGC_EXPECTED_OPENSSD_PRODUCTION_PERFORMANCE:-}" \
        && "${production_mode}" != "${CSGC_EXPECTED_OPENSSD_PRODUCTION_PERFORMANCE}" ]]; then
    echo "Error: OpenSSD production mode mismatch: expected=${CSGC_EXPECTED_OPENSSD_PRODUCTION_PERFORMANCE} actual=${production_mode}." >&2
    return 1
  fi
  if [[ -n "${CSGC_EXPECTED_MOVE_PLAN_V2:-}" \
        && "${move_plan_v2}" != "${CSGC_EXPECTED_MOVE_PLAN_V2}" ]]; then
    echo "Error: OpenSSD Move Plan v2 mismatch: expected=${CSGC_EXPECTED_MOVE_PLAN_V2} actual=${move_plan_v2}." >&2
    return 1
  fi
  if [[ -n "${CSGC_EXPECTED_MOVE_PLAN_FAST_UNSAFE:-}" \
        && "${move_plan_fast_unsafe}" != "${CSGC_EXPECTED_MOVE_PLAN_FAST_UNSAFE}" ]]; then
    echo "Error: OpenSSD unsafe fast-path mismatch: expected=${CSGC_EXPECTED_MOVE_PLAN_FAST_UNSAFE} actual=${move_plan_fast_unsafe}." >&2
    return 1
  fi

  case "${worker_count}" in
    1)
      printf '%s\n' "ssd1t"
      ;;
    2)
      printf '%s\n' "ssd2t"
      ;;
  esac
}

usage() {
  echo "Usage: $0 <mode> <config>"
  echo "  mode: any value containing the case-sensitive substring 'csgc' selects CSGC"
  echo "        ori and iplfs select the existing baseline paths"
  echo "  SSD thread mode is detected from the Vitis workspace referenced by"
  echo "  ${openssd_remote_host}:${openssd_sync_script}"
}

if [[ $# -eq 1 && "$1" == "--detect-ssd-thread-mode" ]]; then
  detect_ssd_thread_mode
  exit $?
fi

if [[ $# -eq 3 && "$2" =~ ^ssd[12]t$ ]]; then
  echo "Error: the explicit SSD thread argument has been removed." >&2
  echo "Action: use '$0 <mode> <config>'; the script now detects ssd1t or ssd2t automatically." >&2
  exit 1
fi

if [ $# -ne 2 ]; then
  usage
  exit 1
fi

mcsgc_mode=$1
config_path=$2

if [ ! -f "${config_path}" ]; then
    if [ -f "${script_dir}/${config_path}" ]; then
        config_path="${script_dir}/${config_path}"
    else
        echo "Error: config file not found: ${config_path}"
        exit 1
    fi
fi

# Keep the user-provided mode as an output label while normalizing CSGC variants.
case "${mcsgc_mode}" in
    *csgc*)
        gc_mode="cs"
        ;;
    "ori"|"iplfs")
        gc_mode="${mcsgc_mode}"
        ;;
    *)
        echo "Error: unsupported mode '${mcsgc_mode}'."
        exit 1
        ;;
esac

if ! ssd_thread_mode="$(detect_ssd_thread_mode)"; then
    exit 1
fi

if [[ -n "${CSGC_EXPECTED_SSD_THREAD_MODE:-}" &&
      "${ssd_thread_mode}" != "${CSGC_EXPECTED_SSD_THREAD_MODE}" ]]; then
    echo "Error: detected SSD thread mode '${ssd_thread_mode}', expected '${CSGC_EXPECTED_SSD_THREAD_MODE}'." >&2
    echo "Action: verify the server-31 Vitis workspace and the firmware loaded on the SSD." >&2
    exit 1
fi

echo "gc_mode=$gc_mode"
echo "ssd_thread_mode=$ssd_thread_mode"

source "${config_path}"

str_debug="test begin in bash"
ts_local=$(date '+%b %e %H:%M:%S')
host_local=$(hostname)
ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)
# Send to /dev/kmsg so any running `dmesg -w >> kernel_log.txt` will capture it.
printf '<6>IN BASH %s %s [%s] %s\n' \
  "$ts_local" "$host_local" "$ts_upt" "$str_debug" | sudo tee /dev/kmsg >/dev/null

pushd "${script_dir}" > /dev/null

: "${fio_gc_precondition:=0}"
: "${fio_gc_precondition_size_per_job:=4G}"
: "${fio_gc_precondition_max_rounds:=4}"

host_mem_usage="8G"
use_cgroup=1
nr_cs_cores="1"
csgc_sync=0
fsck_after_run=0
ssd_enable_l2p=0    # 0=>no-FTL 1=>conventional, 2=>sFTL, 3=>interval-mapping
ssd_enable_nand_lat=0
ssd_enable_dsm=1

current_time=$(date +"%Y%m%d_%H%M%S")

echo "Running evaluation for $gc_mode..."

# for gc_mode in "${gc_modes[@]}"; do
    output_path_base="./outputs-${mcsgc_mode}-${ssd_thread_mode}/${current_time}"
    mkdir -p "${output_path_base}"
    case "${gc_mode}" in 
        "ori")
            ssd_enable_l2p=1
            ;;
        "cs")
            ssd_enable_l2p=2
            ;;
        "iplfs")
            segs_per_sec_list=("1") # iplfs crashes for other section size
            ssd_enable_l2p=3
            fsck_after_run=0
            ;;
        *)
            echo "workload_type not supported"
            exit 1
            ;;
    esac

    for workload in "${workloads[@]}"; do
        workload_type=$(echo $workload | cut -d':' -f1)
        bmname=$(echo $workload | cut -d':' -f2)
    for random_distribution in "${random_distributions[@]}"; do
    for prefill_ratio in "${prefill_ratios[@]}"; do
    for segs_per_sec in "${segs_per_sec_list[@]}"; do

        export gc_mode ssd_thread_mode workload_type bmname random_distribution segs_per_sec output_path_base \
        prefill_ratio use_cgroup host_mem_usage nr_cs_cores csgc_sync fio_timebased\
        ssd_enable_l2p ssd_enable_nand_lat ssd_enable_dsm fsck_after_run light_evaluation \
        fio_gc_precondition fio_gc_precondition_size_per_job fio_gc_precondition_max_rounds \
        formal_performance_only FORMAL_HOST_BRANCH FORMAL_HOST_COMMIT FORMAL_MODULE_SHA256
        
        case "${workload_type}" in 
            "filebench")
                echo "Running filebench: ${bmname}..."
                if ! ./run_filebench.sh; then
                    echo "Error: filebench runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            "fio")
                echo "Running fio: ${bmname}..."
                if ! ./run_fio.sh; then
                    echo "Error: fio runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            "ycsb")
                echo "Running ycsb: ${bmname}..."
                if ! ./run_ycsb.sh; then
                    echo "Error: YCSB runner failed: ${bmname}" >&2
                    exit 1
                fi
                ;;
            *)
                echo "workload_type not supported"
                exit 1
                ;;
        esac

    done
    done
    done
    done
# done

popd > /dev/null

str_debug="test finish in bash"
ts_local=$(date '+%b %e %H:%M:%S')
host_local=$(hostname)
ts_upt=$(awk '{ printf "%.6f", $1 }' /proc/uptime)
# send to /dev/kmsg so any running `dmesg -w >> kernel_log.txt` will capture it
printf '<6>IN BASH %s %s [%s] %s\n' \
  "$ts_local" "$host_local" "$ts_upt" "$str_debug" | sudo tee /dev/kmsg >/dev/null

sudo "${script_dir}/flush_dmesg_buffer.sh"
echo "Test script completed."
