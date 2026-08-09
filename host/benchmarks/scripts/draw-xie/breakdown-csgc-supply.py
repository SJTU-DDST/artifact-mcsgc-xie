#!/usr/bin/env python3
"""Analyze low-overhead Host CSGC request supply statistics."""

import argparse
import os
import re
from typing import Dict, List, Optional, Tuple


RE_SUPPLY = re.compile(r"F2FS_CSGC_SUPPLY_STAT\s+(?P<kv>.*)$")


def parse_kv_blob(blob: str) -> Dict[str, str]:
    """Parse one space-separated key=value payload."""
    result: Dict[str, str] = {}
    for part in blob.strip().split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key:
            result[key] = value
    return result


def int_value(row: Dict[str, str], key: str) -> Optional[int]:
    """Return one integer field, or None when it is unavailable."""
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError):
        return None


def read_rows(path: str) -> List[Dict[str, str]]:
    """Read every structured Host supply row from a kernel log."""
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as source:
        for line in source:
            match = RE_SUPPLY.search(line)
            if not match:
                continue
            row = parse_kv_blob(match.group("kv"))
            if row:
                rows.append(row)
    return rows


def row_epoch(row: Dict[str, str]) -> int:
    """Return the explicit measurement epoch, defaulting legacy rows to zero."""
    value = int_value(row, "epoch")
    return value if value is not None else 0


def select_epoch(rows: List[Dict[str, str]]) -> Tuple[int, str, List[int]]:
    """Prefer the newest workload epoch, otherwise use the newest epoch."""
    available = sorted({row_epoch(row) for row in rows})
    workload = sorted(
        {
            row_epoch(row)
            for row in rows
            if row.get("scope") == "workload"
        }
    )
    epoch = workload[-1] if workload else available[-1]
    if workload:
        scope = "workload"
    else:
        scopes = {
            row.get("scope", "legacy")
            for row in rows
            if row_epoch(row) == epoch
        }
        scope = sorted(scopes)[-1] if scopes else "legacy"
    return epoch, scope, available


def fraction(numerator: Optional[int], denominator: Optional[int]) -> Optional[float]:
    """Return a ratio when both integer inputs define a valid denominator."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / float(denominator)


def format_optional(value: Optional[float], digits: int = 6) -> str:
    """Format an optional floating-point value for the result file."""
    return "unavailable" if value is None else f"{value:.{digits}f}"


def write_result(
    output_path: str,
    source_path: str,
    selected_epoch: int,
    selected_scope: str,
    available_epochs: List[int],
    rows: List[Dict[str, str]],
) -> None:
    """Write one workload-scoped Host supply analysis file."""
    selected = [
        row
        for row in rows
        if row_epoch(row) == selected_epoch
        and row.get("scope", "legacy") == selected_scope
    ]
    summary_indexes = [
        index for index, row in enumerate(selected) if row.get("kind") == "summary"
    ]
    if summary_indexes:
        # One dump emits summary first. Keep only the newest complete snapshot
        # if an epoch was dumped more than once.
        selected = selected[summary_indexes[-1] :]

    summaries = [row for row in selected if row.get("kind") == "summary"]
    depths = [row for row in selected if row.get("kind") == "depth"]
    overflow_depths = [
        row for row in selected if row.get("kind") == "depth_overflow"
    ]
    gap_buckets = [row for row in selected if row.get("kind") == "gap_bucket"]
    summary = summaries[-1] if summaries else {}

    elapsed_us = int_value(summary, "epoch_elapsed_us")
    idle_us = int_value(summary, "idle_us")
    busy_us = int_value(summary, "busy_us")
    single_us = int_value(summary, "single_us")
    ge2_us = int_value(summary, "ge2_us")
    started = int_value(summary, "requests_started")
    completed = int_value(summary, "requests_completed")
    aborted = int_value(summary, "requests_aborted")
    current = int_value(summary, "current_outstanding")

    diagnostics: List[str] = []
    if not summary:
        diagnostics.append("missing_summary_row")
    if summary.get("invariant_ok") not in (None, "1"):
        diagnostics.append("request_lifecycle_invariant_failed")
    for field in (
        "duplicate_start_events",
        "unmatched_completion_events",
        "underflow_events",
        "overflow_events",
        "timestamp_regressions",
    ):
        value = int_value(summary, field)
        if value:
            diagnostics.append(f"{field}={value}")
    if elapsed_us is not None and idle_us is not None and busy_us is not None:
        if abs(elapsed_us - idle_us - busy_us) > 2:
            diagnostics.append(
                f"elapsed_partition_mismatch_us={elapsed_us - idle_us - busy_us}"
            )
    if None not in (started, completed, aborted, current):
        if started != completed + aborted + current:
            diagnostics.append("recomputed_request_lifecycle_invariant_failed")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as output:
        output.write("=== Host CSGC supply coverage analysis ===\n")
        output.write(f"source_file={os.path.abspath(source_path)}\n")
        output.write(f"result_file={os.path.abspath(output_path)}\n")
        output.write(f"selected_epoch={selected_epoch}\n")
        output.write(f"selected_scope={selected_scope}\n")
        output.write(
            "available_epochs=" + ",".join(str(value) for value in available_epochs) + "\n"
        )
        output.write("metric_scope=host_view_request_supply_not_device_utilization\n")
        output.write("outstanding_start=pre_alloc_time\n")
        output.write("outstanding_end=csgc_read_finish_time\n")
        output.write(f"kernel_summary_rows={len(summaries)}\n")
        output.write("\n")

        output.write("=== supply window ===\n")
        for key in (
            "epoch_elapsed_us",
            "idle_us",
            "busy_us",
            "single_us",
            "ge2_us",
            "supply_coverage",
            "ge2_fraction_when_supplied",
            "avg_outstanding",
            "avg_outstanding_when_supplied",
            "max_outstanding",
            "current_outstanding",
        ):
            output.write(f"{key}={summary.get(key, 'unavailable')}\n")
        output.write(
            "recomputed_supply_coverage="
            + format_optional(fraction(busy_us, elapsed_us))
            + "\n"
        )
        output.write(
            "recomputed_host_starved_fraction="
            + format_optional(fraction(idle_us, elapsed_us))
            + "\n"
        )
        output.write(
            "recomputed_ge2_fraction_when_supplied="
            + format_optional(fraction(ge2_us, busy_us))
            + "\n"
        )
        output.write("\n")

        output.write("=== request lifecycle ===\n")
        for key in (
            "requests_started",
            "requests_completed",
            "requests_aborted",
            "invariant_ok",
            "duplicate_start_events",
            "unmatched_completion_events",
            "underflow_events",
            "overflow_events",
            "overflow_time_us",
            "timestamp_regressions",
        ):
            output.write(f"{key}={summary.get(key, 'unavailable')}\n")
        output.write("\n")

        output.write("=== supply gap summary ===\n")
        for key in (
            "gap_count",
            "gap_total_us",
            "gap_mean_us",
            "gap_max_us",
            "gap_p50_upper_us",
            "gap_p95_upper_us",
            "gap_p99_upper_us",
            "open_gap_us",
        ):
            output.write(f"{key}={summary.get(key, 'unavailable')}\n")
        output.write("percentiles_are_log2_bucket_upper_bounds=1\n")
        output.write("\n")

        output.write("=== outstanding depth distribution ===\n")
        output.write("outstanding time_us fraction_of_epoch fraction_when_supplied\n")
        for row in sorted(depths, key=lambda item: int_value(item, "outstanding") or 0):
            depth = int_value(row, "outstanding")
            time_us = int_value(row, "time_us")
            output.write(
                f"{depth if depth is not None else 'unknown'} "
                f"{time_us if time_us is not None else 'unavailable'} "
                f"{format_optional(fraction(time_us, elapsed_us))} "
                f"{format_optional(fraction(time_us, busy_us) if depth else 0.0)}\n"
            )
        for row in overflow_depths:
            output.write(
                "overflow "
                f"{row.get('time_us', 'unavailable')} unavailable unavailable\n"
            )
        output.write("\n")

        output.write("=== supply gap histogram ===\n")
        output.write("bucket lower_us upper_us count\n")
        for row in sorted(gap_buckets, key=lambda item: int_value(item, "bucket") or 0):
            output.write(
                f"{row.get('bucket', 'unknown')} "
                f"{row.get('lower_us', 'unavailable')} "
                f"{row.get('upper_us', 'unavailable')} "
                f"{row.get('count', 'unavailable')}\n"
            )
        output.write("\n")

        output.write("=== diagnostics ===\n")
        if diagnostics:
            for diagnostic in diagnostics:
                output.write(f"diagnostic={diagnostic}\n")
        else:
            output.write("diagnostic=none\n")


def main() -> int:
    """Parse arguments and analyze one kernel log when supply rows exist."""
    parser = argparse.ArgumentParser(
        description="Parse low-overhead Host CSGC supply statistics."
    )
    parser.add_argument("logfile", help="path to the kernel log file")
    parser.add_argument("output", help="path to the output .txt file")
    args = parser.parse_args()

    rows = read_rows(args.logfile)
    if not rows:
        return 0
    selected_epoch, selected_scope, available_epochs = select_epoch(rows)
    write_result(
        args.output,
        args.logfile,
        selected_epoch,
        selected_scope,
        available_epochs,
        rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
