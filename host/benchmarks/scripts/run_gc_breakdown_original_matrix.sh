#!/usr/bin/env bash

set -euo pipefail

# Execute an in-memory snapshot so repository updates cannot alter a live run.
if [ -z "${GC_BREAKDOWN_ORIGINAL_MATRIX_SNAPSHOT:-}" ]; then
    matrix_script_path=$(readlink -f -- "${BASH_SOURCE[0]}")
    matrix_script_body=$(<"${matrix_script_path}")
    export GC_BREAKDOWN_ORIGINAL_MATRIX_SNAPSHOT=1
    export GC_BREAKDOWN_ORIGINAL_MATRIX_SCRIPT_PATH="${matrix_script_path}"
    exec /bin/bash -c "${matrix_script_body}" "${matrix_script_path}" "$@"
fi

matrix_script_path=${GC_BREAKDOWN_ORIGINAL_MATRIX_SCRIPT_PATH}
script_dir=$(cd -- "$(dirname -- "${matrix_script_path}")" && pwd)
runner="${script_dir}/run_gc_breakdown_diagnostic.sh"
batch_id=$(date +"%Y%m%d_%H%M%S")
batch_dir="${script_dir}/outputs-gc-breakdown-original-matrix/${batch_id}"
manifest="${batch_dir}/results.txt"

# Print the command syntax without starting a destructive benchmark.
usage() {
    cat >&2 <<EOF
Usage: sudo $0 [--csgc-only|--ori-only]

With no option, run all four original CSGC/ORI diagnostics.
EOF
}

selection=all
case "$#:$*" in
    0:)
        ;;
    1:--csgc-only)
        selection=csgc
        ;;
    1:--ori-only)
        selection=ori
        ;;
    *)
        usage
        exit 1
        ;;
esac

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: run this diagnostic batch through sudo." >&2
    echo "Run: sudo $0" >&2
    exit 1
fi

if [ ! -x "${runner}" ]; then
    echo "ERROR: GC breakdown runner is unavailable: ${runner}" >&2
    exit 1
fi

mkdir -p "${batch_dir}"
printf 'batch_id=%s\nstarted_at=%s\n' \
    "${batch_id}" "$(date --iso-8601=seconds)" > "${manifest}"

case "${selection}" in
    all)
        configurations=(
            "original-csgc bigfile 01-original-csgc-bigfile"
            "original-csgc smallfile 02-original-csgc-smallfile"
            "original-ori bigfile 03-original-ori-bigfile"
            "original-ori smallfile 04-original-ori-smallfile"
        )
        execution_order="CSGC bigfile, CSGC smallfile, ORI bigfile, ORI smallfile"
        ;;
    csgc)
        configurations=(
            "original-csgc bigfile 01-original-csgc-bigfile"
            "original-csgc smallfile 02-original-csgc-smallfile"
        )
        execution_order="CSGC bigfile, CSGC smallfile"
        ;;
    ori)
        configurations=(
            "original-ori bigfile 01-original-ori-bigfile"
            "original-ori smallfile 02-original-ori-smallfile"
        )
        execution_order="ORI bigfile, ORI smallfile"
        ;;
esac

echo "============================================================"
echo "Original CSGC/ORI GC breakdown matrix"
echo "Batch directory: ${batch_dir}"
echo "Selection: ${selection}"
echo "Execution order: ${execution_order}"
echo "============================================================"

for entry in "${configurations[@]}"; do
    read -r configuration workload label <<< "${entry}"
    result_path_file="${batch_dir}/${label}.result-path"

    echo
    echo "============================================================"
    echo "Starting ${label}"
    echo "Start time: $(date --iso-8601=seconds)"
    echo "============================================================"

    if GC_BREAKDOWN_RESULT_PATH_FILE="${result_path_file}" \
        "${runner}" "${configuration}" "${workload}"; then
        run_dir=$(< "${result_path_file}")
        kernel_log="${run_dir}/external-dmesg.log"
        summary_path="${run_dir}/gc-breakdown-diagnostic-result.txt"

        if [ ! -s "${kernel_log}" ]; then
            echo "ERROR: kernel log is missing or empty: ${kernel_log}" >&2
            exit 1
        fi
        if [ ! -s "${summary_path}" ]; then
            echo "ERROR: breakdown summary is missing or empty: ${summary_path}" >&2
            exit 1
        fi

        # A hard link gives the batch a stable, descriptive kernel-log name
        # without duplicating a potentially large dmesg file.
        batch_kernel_log="${batch_dir}/${label}-kernel.log"
        ln "${kernel_log}" "${batch_kernel_log}"

        {
            printf '\n[%s]\n' "${label}"
            printf 'configuration=%s\n' "${configuration}"
            printf 'workload=%s\n' "${workload}"
            printf 'completed_at=%s\n' "$(date --iso-8601=seconds)"
            printf 'run_dir=%s\n' "${run_dir}"
            printf 'kernel_log=%s\n' "${kernel_log}"
            printf 'batch_kernel_log=%s\n' "${batch_kernel_log}"
            printf 'summary=%s\n' "${summary_path}"
        } >> "${manifest}"

        echo "Completed ${label}"
        echo "Kernel log: ${batch_kernel_log}"
        echo "Summary: ${summary_path}"
    else
        status=$?
        echo "ERROR: ${label} failed with status ${status}." >&2
        echo "The remaining diagnostic runs were not started." >&2
        echo "Batch manifest: ${manifest}" >&2
        exit "${status}"
    fi
done

printf '\ncompleted_at=%s\nstatus=success\n' \
    "$(date --iso-8601=seconds)" >> "${manifest}"

echo
echo "Selected original GC breakdown diagnostics completed successfully."
echo "Batch directory: ${batch_dir}"
echo "Result manifest: ${manifest}"
