#!/usr/bin/env python3

"""Summarize Host GC timing records inside the measured fio window."""

import argparse
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple


START_MARKER = "MEASURED_FIO_START"
END_MARKER = "MEASURED_FIO_END"
KV_RE = re.compile(r"([A-Za-z0-9_]+)=([^\s,]+)")
DMESG_TIME_RE = re.compile(r"^\[\s*(\d+(?:\.\d+)?)\]")


def parse_args() -> argparse.Namespace:
    """Parse command-line paths for the diagnostic input and outputs."""
    parser = argparse.ArgumentParser(
        description="Analyze original-CSGC and mCSGC Host timing records."
    )
    parser.add_argument("input", help="Full external dmesg log")
    parser.add_argument("output", help="Summary output path")
    parser.add_argument(
        "--crop-output",
        help="Optional path for the measured-fio-only dmesg excerpt",
    )
    return parser.parse_args()


def parse_kv(line: str) -> Dict[str, str]:
    """Extract whitespace-delimited key/value fields from one trace line."""
    return {match.group(1): match.group(2) for match in KV_RE.finditer(line)}


def int_value(values: Dict[str, str], key: str) -> Optional[int]:
    """Return one decimal integer field, or None when it is unavailable."""
    value = values.get(key)
    if value is None:
        return None
    try:
        return int(value, 10)
    except ValueError:
        return None


def dmesg_time_us(line: str) -> Optional[int]:
    """Convert a standard dmesg timestamp prefix to integer microseconds."""
    match = DMESG_TIME_RE.search(line)
    if not match:
        return None
    return int(float(match.group(1)) * 1_000_000)


def find_latest_window(lines: List[str]) -> Tuple[int, int, int, int]:
    """Locate the latest complete measured-fio marker pair."""
    pending_start: Optional[Tuple[int, int]] = None
    completed: Optional[Tuple[int, int, int, int]] = None

    for index, line in enumerate(lines):
        if START_MARKER in line:
            timestamp = dmesg_time_us(line)
            if timestamp is not None:
                pending_start = (index, timestamp)
        if END_MARKER in line and pending_start is not None:
            timestamp = dmesg_time_us(line)
            if timestamp is not None and index >= pending_start[0]:
                completed = (
                    pending_start[0],
                    index,
                    pending_start[1],
                    timestamp,
                )
                pending_start = None

    if completed is None:
        raise ValueError("no complete MEASURED_FIO_START/END window found")
    return completed


def percentile(values: List[int], percentile_value: float) -> float:
    """Calculate an interpolated percentile for integer timing samples."""
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def metric_line(name: str, values: List[int]) -> str:
    """Format one timing distribution as a stable text record."""
    if not values:
        return f"{name}: count=0"
    return (
        f"{name}: count={len(values)} mean={statistics.fmean(values):.3f} "
        f"median={statistics.median(values):.3f} "
        f"p95={percentile(values, 95):.3f} "
        f"p99={percentile(values, 99):.3f} "
        f"min={min(values)} max={max(values)} sum={sum(values)}"
    )


def add_if_present(
    metrics: DefaultDict[str, List[int]],
    values: Dict[str, str],
    source_key: str,
    output_key: Optional[str] = None,
) -> None:
    """Append a nonnegative integer field to the requested metric."""
    value = int_value(values, source_key)
    if value is not None and value >= 0:
        metrics[output_key or source_key].append(value)


def parse_original_record(
    line: str,
    metrics: DefaultDict[str, List[int]],
) -> bool:
    """Parse one structured record emitted by the original CSGC branch."""
    if "F2FS_GC_DIAG " in line:
        values = parse_kv(line)
        for key in (
            "duration_us",
            "collector_us",
            "non_collector_us",
            "victim_select_us",
            "checkpoint_us",
            "other_us",
            "victim_select_attempts",
            "victims_found",
            "checkpoint_calls",
        ):
            add_if_present(metrics, values, key, f"gc_call_{key}")
        csgc_count = int_value(values, "csgc_collectors") or 0
        origc_count = int_value(values, "origc_collectors") or 0
        collector_invoked = int_value(values, "collector_invoked")
        if collector_invoked is None:
            collector_invoked = int(csgc_count + origc_count > 0)
        if collector_invoked:
            add_if_present(metrics, values, "duration_us", "gc_with_work_duration_us")
        else:
            add_if_present(
                metrics,
                values,
                "duration_us",
                "gc_no_collector_duration_us",
            )
            add_if_present(metrics, values, "duration_us", "gc_no_work_duration_us")
        path = values.get("gc_path")
        if path:
            add_if_present(metrics, values, "duration_us", f"gc_path_{path}_duration_us")

        phase_keys = ("victim_select_us", "checkpoint_us", "collector_us", "other_us")
        phase_values = [int_value(values, key) for key in phase_keys]
        duration = int_value(values, "duration_us")
        if duration is not None and all(value is not None for value in phase_values):
            accounted = sum(value for value in phase_values if value is not None)
            metrics["gc_call_accounted_us"].append(accounted)
            metrics["gc_call_accounting_delta_us"].append(duration - accounted)
        return True

    if "F2FS_GC_COLLECTOR_DIAG " in line:
        values = parse_kv(line)
        kind = values.get("kind", "unknown")
        seg_type = values.get("seg_type", "unknown")
        add_if_present(
            metrics,
            values,
            "duration_us",
            f"collector_{kind}_{seg_type}_duration_us",
        )
        return True

    if "CSGC_ORIGINAL_SECTION " in line:
        values = parse_kv(line)
        for key in ("section_gc_time_us", "section_sync_us", "collector_us"):
            add_if_present(metrics, values, key, f"original_section_{key}")
        return True

    if "CSGC_ORIGINAL_SEGMENT " in line:
        values = parse_kv(line)
        for key in (
            "section_sync_us",
            "pre_work_total_us",
            "pre_sum_us",
            "pre_node_list_us",
            "pre_inode_lock_us",
            "pre_data_lock_us",
            "pre_node_pages_lock_us",
            "pre_pack_prealloc_us",
            "approx_gc_cs_ssd_us",
            "post_queue_delay_us",
            "post_update_meta_us",
            "post_middle_work_us",
            "approx_segment_total_us",
            "segment_finish_offset_us",
        ):
            add_if_present(metrics, values, key, f"original_segment_{key}")
        return True

    if "CSGC_ORIGINAL_SEGMENT_DETAIL " in line:
        values = parse_kv(line)
        detail_keys = (
            "pre_work_total_us",
            "pre_attempts",
            "pre_success_attempt_us",
            "pre_failed_attempts_us",
            "pre_retry_gap_us",
            "pre_sum_us",
            "pre_node_list_us",
            "pre_inode_lock_us",
            "pre_data_lock_us",
            "pre_cp_rwsem_lock_us",
            "pre_node_pages_lock_precise_us",
            "pre_data_revalidate_us",
            "pre_pack_node_us",
            "pre_pack_sit_us",
            "pre_preallocate_us",
            "pre_prealloc_lock_wait_us",
            "pre_prealloc_sync_us",
            "pre_prealloc_wait_sync_us",
            "pre_prealloc_alloc_us",
            "ssd_trigger_roundtrip_us",
            "ssd_inter_submit_gap_us",
            "ssd_completion_wait_us",
            "approx_gc_cs_ssd_us",
        )
        for key in detail_keys:
            add_if_present(metrics, values, key, f"original_detail_{key}")

        precise_pre_keys = (
            "pre_sum_us",
            "pre_node_list_us",
            "pre_inode_lock_us",
            "pre_data_lock_us",
            "pre_cp_rwsem_lock_us",
            "pre_node_pages_lock_precise_us",
            "pre_data_revalidate_us",
            "pre_pack_node_us",
            "pre_pack_sit_us",
            "pre_preallocate_us",
        )
        precise_pre_values = [int_value(values, key) for key in precise_pre_keys]
        pre_total = int_value(values, "pre_success_attempt_us")
        if pre_total is not None and all(value is not None for value in precise_pre_values):
            accounted = sum(value for value in precise_pre_values if value is not None)
            metrics["original_detail_pre_success_accounted_us"].append(accounted)
            metrics["original_detail_pre_success_accounting_delta_us"].append(
                pre_total - accounted
            )

        ssd_keys = (
            "ssd_trigger_roundtrip_us",
            "ssd_inter_submit_gap_us",
            "ssd_completion_wait_us",
        )
        ssd_values = [int_value(values, key) for key in ssd_keys]
        ssd_total = int_value(values, "approx_gc_cs_ssd_us")
        if ssd_total is not None and all(value is not None for value in ssd_values):
            accounted = sum(value for value in ssd_values if value is not None)
            metrics["original_detail_ssd_accounted_us"].append(accounted)
            metrics["original_detail_ssd_accounting_delta_us"].append(
                ssd_total - accounted
            )
        return True

    if "CSGC_ORIGINAL_SEGMENT_POST_DETAIL " in line:
        values = parse_kv(line)
        post_keys = (
            "post_update_meta_us",
            "post_status_check_us",
            "post_seg_update_us",
            "post_dnode_update_us",
            "post_unlock_op_us",
            "post_put_data_pages_us",
            "post_cleanup_us",
        )
        for key in post_keys:
            add_if_present(metrics, values, key, f"original_post_detail_{key}")

        update_keys = (
            "post_status_check_us",
            "post_seg_update_us",
            "post_dnode_update_us",
            "post_unlock_op_us",
            "post_put_data_pages_us",
        )
        update_values = [int_value(values, key) for key in update_keys]
        update_total = int_value(values, "post_update_meta_us")
        if update_total is not None and all(
            value is not None for value in update_values
        ):
            accounted = sum(value for value in update_values if value is not None)
            metrics["original_post_detail_update_accounted_us"].append(accounted)
            metrics["original_post_detail_update_accounting_delta_us"].append(
                update_total - accounted
            )
        return True

    return False


def modern_trace_time(values: Dict[str, str]) -> Optional[int]:
    """Read the epoch-relative timestamp preferred by modern traces."""
    value = int_value(values, "epoch_t_us")
    if value is not None:
        return value
    return int_value(values, "t_us")


def parse_modern_records(
    lines: Iterable[str],
    metrics: DefaultDict[str, List[int]],
) -> Dict[str, int]:
    """Pair modern GC and CSGC phase events and collect durations."""
    gc_starts: Dict[Tuple[int, int], int] = {}
    phase_starts: DefaultDict[Tuple[str, int, int, int], List[int]] = defaultdict(list)
    unmatched_ends = 0

    for line in lines:
        if "F2FS_GC_HEAVY_TRACE " in line:
            values = parse_kv(line)
            event = values.get("event")
            epoch = int_value(values, "epoch") or 0
            call_id = int_value(values, "call_id")
            timestamp = modern_trace_time(values)
            if call_id is None or timestamp is None:
                continue
            key = (epoch, call_id)
            if event == "GC_START":
                gc_starts[key] = timestamp
            elif event == "GC_END":
                start = gc_starts.pop(key, None)
                if start is None or timestamp < start:
                    unmatched_ends += 1
                else:
                    metrics["gc_call_duration_us"].append(timestamp - start)
                    path = values.get("path", "unknown")
                    metrics[f"gc_path_{path}_duration_us"].append(timestamp - start)
                for field in (
                    "csgc_data_time_us",
                    "origc_data_time_us",
                    "origc_node_time_us",
                ):
                    add_if_present(metrics, values, field, f"gc_end_{field}")
                sections = int_value(values, "csgc_data_sections") or 0
                csgc_time = int_value(values, "csgc_data_time_us")
                if sections > 0 and csgc_time is not None:
                    metrics["gc_end_csgc_time_per_section_us"].append(
                        csgc_time // sections
                    )
            continue

        if "CSGC_HEAVY_TRACE " in line:
            values = parse_kv(line)
            event = values.get("event", "")
            timestamp = modern_trace_time(values)
            if timestamp is None or "_" not in event:
                continue
            phase, boundary = event.rsplit("_", 1)
            section = int_value(values, "section") or 0
            segno = int_value(values, "segno") or 0
            req_idx = int_value(values, "req_idx") or 0
            if phase == "SECTION":
                segno = 0
                req_idx = 0
            key = (phase, section, segno, req_idx)
            if boundary == "START":
                phase_starts[key].append(timestamp)
            elif boundary == "END":
                starts = phase_starts.get(key)
                if not starts:
                    unmatched_ends += 1
                else:
                    start = starts.pop(0)
                    if timestamp >= start:
                        metrics[f"phase_{phase.lower()}_duration_us"].append(
                            timestamp - start
                        )
                    else:
                        unmatched_ends += 1
            continue

        if "mCSGC8t_STAT without wait " in line:
            values = parse_kv(line)
            for field in (
                "section_sync_us",
                "pre_queue_delay_us",
                "pre_work_total_us",
                "pre_sum_us",
                "pre_node_list_us",
                "pre_inode_lock_us",
                "pre_data_lock_us",
                "pre_cp_rwsem_lock_us",
                "pre_node_pages_lock_us",
                "pre_get_valid_blocks_us",
                "pre_check_data_validness_us",
                "pre_pack_prealloc_us",
                "pre_request_trigger_us",
                "pre_submit_completion_read_us",
                "pre_tail_us",
                "approx_gc_cs_ssd_us",
                "post_queue_delay_us",
                "post_update_meta_us",
                "post_middle_work_us",
                "approx_segment_total_us",
                "segment_finish_offset_us",
            ):
                add_if_present(metrics, values, field, f"modern_segment_{field}")

    unmatched_starts = len(gc_starts) + sum(
        len(starts) for starts in phase_starts.values()
    )
    return {
        "unmatched_starts": unmatched_starts,
        "unmatched_ends": unmatched_ends,
    }


def emit_group(
    output: List[str],
    title: str,
    metrics: DefaultDict[str, List[int]],
    prefixes: Tuple[str, ...],
) -> None:
    """Emit all metrics matching a group of name prefixes."""
    names = sorted(name for name in metrics if name.startswith(prefixes))
    output.append(f"=== {title} ===")
    if not names:
        output.append("no records")
    else:
        output.extend(metric_line(name, metrics[name]) for name in names)
    output.append("")


def main() -> int:
    """Crop the measured window, parse records, and write the summary."""
    args = parse_args()
    input_path = Path(args.input)
    lines = input_path.read_text(encoding="utf-8", errors="replace").splitlines()

    try:
        start_index, end_index, start_us, end_us = find_latest_window(lines)
    except ValueError as error:
        raise SystemExit(f"ERROR: {error}") from error

    window_lines = lines[start_index : end_index + 1]
    if args.crop_output:
        crop_path = Path(args.crop_output)
        crop_path.write_text("\n".join(window_lines) + "\n", encoding="utf-8")

    metrics: DefaultDict[str, List[int]] = defaultdict(list)
    original_records = sum(
        1 for line in window_lines if parse_original_record(line, metrics)
    )
    diagnostics = parse_modern_records(window_lines, metrics)

    output = [
        "GC breakdown diagnostic summary",
        f"input={input_path}",
        f"measured_start_us={start_us}",
        f"measured_end_us={end_us}",
        f"measured_duration_us={max(0, end_us - start_us)}",
        f"window_lines={len(window_lines)}",
        f"original_structured_records={original_records}",
        f"unmatched_starts={diagnostics['unmatched_starts']}",
        f"unmatched_ends={diagnostics['unmatched_ends']}",
        "",
    ]

    emit_group(output, "complete f2fs_gc calls", metrics, ("gc_call_", "gc_with_", "gc_no_"))
    emit_group(output, "collector paths", metrics, ("collector_", "gc_end_", "gc_path_"))
    emit_group(output, "section wall-clock", metrics, ("original_section_", "phase_section_"))
    emit_group(output, "coarse PRE/SSD/POST intervals", metrics, ("phase_pre_", "phase_ssd_", "phase_post_"))
    emit_group(output, "original CSGC segment breakdown", metrics, ("original_segment_",))
    emit_group(output, "original CSGC detailed PRE/SSD breakdown", metrics, ("original_detail_",))
    emit_group(output, "original CSGC detailed POST breakdown", metrics, ("original_post_detail_",))
    emit_group(output, "mCSGC8t segment breakdown", metrics, ("modern_segment_",))

    Path(args.output).write_text("\n".join(output), encoding="utf-8")
    print(f"Wrote {args.output}")
    if args.crop_output:
        print(f"Wrote {args.crop_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
