#!/usr/bin/env python3

"""Summarize Host GC timing records inside the measured fio window."""

import argparse
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Set, Tuple


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


def add_sum_if_present(
    metrics: DefaultDict[str, List[int]],
    values: Dict[str, str],
    source_keys: Tuple[str, ...],
    output_key: str,
) -> Optional[int]:
    """Append the sum of a complete set of nonnegative integer fields."""
    items = [int_value(values, key) for key in source_keys]
    if any(item is None or item < 0 for item in items):
        return None
    total = sum(item for item in items if item is not None)
    metrics[output_key].append(total)
    return total


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
            "checkpoint_location1_calls",
            "checkpoint_location1_us",
            "checkpoint_location2_calls",
            "checkpoint_location2_us",
            "checkpoint_location3_calls",
            "checkpoint_location3_us",
            "checkpoint_location4_calls",
            "checkpoint_location4_us",
            "initial_pressure",
            "loop_rounds",
            "refill_active",
            "refill_sections",
            "refill_target",
            "free_sections_start",
            "free_sections_end",
            "unsafe_reclaim_calls",
            "unsafe_reclaim_segments",
            "unsafe_reclaim_sections",
            "unsafe_reclaim_skipped",
            "unsafe_reclaim_us",
            "csgc_collectors",
            "origc_collectors",
        ):
            add_if_present(metrics, values, key, f"gc_call_{key}")
        stop_reason = values.get("stop_reason")
        if stop_reason:
            metrics[f"gc_stop_reason_{stop_reason}"].append(1)
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

    if "F2FS_GC_CHECKPOINT_DIAG " in line:
        values = parse_kv(line)
        location = int_value(values, "location")
        if location is not None:
            add_if_present(
                metrics,
                values,
                "duration_us",
                f"gc_checkpoint_location{location}_duration_us",
            )
        add_if_present(metrics, values, "duration_us", "gc_checkpoint_duration_us")
        return True

    if "F2FS_GC_UNSAFE_RECLAIM_DIAG " in line:
        values = parse_kv(line)
        for key in ("duration_us", "segments", "sections", "skipped"):
            add_if_present(metrics, values, key, f"gc_unsafe_reclaim_{key}")
        location = int_value(values, "location")
        if location is not None:
            add_if_present(
                metrics,
                values,
                "duration_us",
                f"gc_unsafe_reclaim_location{location}_duration_us",
            )
        return True

    if "F2FS_GC_COLLECTOR_DIAG " in line:
        values = parse_kv(line)
        kind = values.get("kind", "unknown")
        seg_type = values.get("seg_type", "unknown")
        duration = int_value(values, "duration_us")
        sections = int_value(values, "sections") or 1
        add_if_present(
            metrics,
            values,
            "duration_us",
            f"collector_{kind}_{seg_type}_duration_us",
        )
        metrics[f"collector_{kind}_{seg_type}_sections"].append(sections)
        if duration is not None and duration >= 0 and sections > 0:
            metrics[f"collector_{kind}_{seg_type}_duration_per_section_us"].append(
                duration // sections
            )
        return True

    if "CSGC_ORIGINAL_SECTION " in line:
        values = parse_kv(line)
        for key in ("section_gc_time_us", "section_sync_us", "collector_us"):
            add_if_present(metrics, values, key, f"original_section_{key}")
            add_if_present(metrics, values, key, f"comparable_section_{key}")
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
        add_if_present(
            metrics,
            values,
            "approx_segment_total_us",
            "comparable_segment_total_us",
        )
        add_if_present(
            metrics,
            values,
            "post_queue_delay_us",
            "comparable_post_queue_delay_us",
        )
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

        direct_comparable = {
            "pre_work_total_us": "comparable_pre_work_total_us",
            "pre_attempts": "comparable_pre_attempts",
            "pre_success_attempt_us": "comparable_pre_success_attempt_us",
            "pre_failed_attempts_us": "comparable_pre_failed_attempts_us",
            "pre_retry_gap_us": "comparable_pre_retry_gap_us",
            "pre_sum_us": "comparable_pre_sum_us",
            "pre_node_list_us": "comparable_pre_node_list_us",
            "pre_inode_lock_us": "comparable_pre_inode_lock_us",
            "pre_data_lock_us": "comparable_pre_data_lock_us",
            "pre_cp_rwsem_lock_us": "comparable_pre_cp_rwsem_lock_us",
            "pre_node_pages_lock_precise_us": "comparable_pre_node_pages_lock_us",
            "pre_data_revalidate_us": "comparable_pre_data_revalidate_us",
            "pre_preallocate_us": "comparable_pre_preallocate_us",
            "ssd_trigger_roundtrip_us": "comparable_ssd_trigger_roundtrip_us",
            "ssd_inter_submit_gap_us": "comparable_ssd_inter_submit_gap_us",
            "ssd_completion_wait_us": "comparable_ssd_completion_wait_us",
            "approx_gc_cs_ssd_us": "comparable_ssd_total_us",
        }
        for source_key, output_key in direct_comparable.items():
            add_if_present(metrics, values, source_key, output_key)
        add_sum_if_present(
            metrics,
            values,
            ("pre_pack_node_us", "pre_pack_sit_us"),
            "comparable_pre_request_metadata_us",
        )

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

        post_comparable = {
            "post_update_meta_us": "comparable_post_update_meta_us",
            "post_status_check_us": "comparable_post_result_validation_us",
            "post_seg_update_us": "comparable_post_segment_metadata_us",
            "post_dnode_update_us": "comparable_post_dnode_update_us",
            "post_unlock_op_us": "comparable_post_unlock_op_us",
            "post_put_data_pages_us": "comparable_post_put_data_pages_us",
            "post_cleanup_us": "comparable_post_cleanup_us",
        }
        for source_key, output_key in post_comparable.items():
            add_if_present(metrics, values, source_key, output_key)

        update_total = int_value(values, "post_update_meta_us")
        put_pages = int_value(values, "post_put_data_pages_us")
        cleanup = int_value(values, "post_cleanup_us")
        if (
            update_total is not None
            and put_pages is not None
            and update_total >= put_pages
        ):
            metrics["comparable_post_metadata_without_page_release_us"].append(
                update_total - put_pages
            )
        if update_total is not None and cleanup is not None:
            metrics["comparable_post_total_work_us"].append(
                update_total + cleanup
            )

        update_keys = (
            "post_status_check_us",
            "post_seg_update_us",
            "post_dnode_update_us",
            "post_unlock_op_us",
            "post_put_data_pages_us",
        )
        update_values = [int_value(values, key) for key in update_keys]
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


def modern_segment_key(values: Dict[str, str]) -> Optional[Tuple[int, int]]:
    """Return the stable identity shared by split mCSGC segment records."""
    segno = int_value(values, "segno")
    start_ns = int_value(values, "start_ns")
    if segno is None or start_ns is None:
        return None
    return segno, start_ns


def record_modern_pre_detail(
    values: Dict[str, str],
    metrics: DefaultDict[str, List[int]],
) -> None:
    """Record one complete mCSGC PRE breakdown assembled from split lines."""
    detail_keys = (
        "pre_work_total_us",
        "pre_callback_total_us",
        "pre_attempts",
        "pre_success_attempt_us",
        "pre_failed_attempts_us",
        "pre_retry_gap_us",
        "pre_build_valid_offsets_us",
        "pre_sum_us",
        "pre_node_list_us",
        "pre_inode_lock_us",
        "pre_data_lock_us",
        "pre_dirty_source_scan_us",
        "pre_cp_rwsem_lock_us",
        "pre_node_pages_lock_us",
        "pre_get_valid_blocks_us",
        "pre_check_data_validness_us",
        "pre_prepare_move_plan_us",
        "pre_preallocate_us",
        "pre_finalize_move_plan_us",
        "pre_tail_us",
        "pre_prealloc_lock_wait_us",
        "pre_prealloc_sync_us",
        "pre_prealloc_wait_sync_us",
        "pre_prealloc_alloc_us",
    )
    for key in detail_keys:
        add_if_present(metrics, values, key, f"modern_detail_{key}")

    direct_comparable = {
        "pre_work_total_us": "comparable_pre_work_total_us",
        "pre_attempts": "comparable_pre_attempts",
        "pre_success_attempt_us": "comparable_pre_success_attempt_us",
        "pre_failed_attempts_us": "comparable_pre_failed_attempts_us",
        "pre_retry_gap_us": "comparable_pre_retry_gap_us",
        "pre_node_list_us": "comparable_pre_node_list_us",
        "pre_inode_lock_us": "comparable_pre_inode_lock_us",
        "pre_data_lock_us": "comparable_pre_data_lock_us",
        "pre_cp_rwsem_lock_us": "comparable_pre_cp_rwsem_lock_us",
        "pre_node_pages_lock_us": "comparable_pre_node_pages_lock_us",
        "pre_preallocate_us": "comparable_pre_preallocate_us",
    }
    for source_key, output_key in direct_comparable.items():
        add_if_present(metrics, values, source_key, output_key)
    add_sum_if_present(
        metrics,
        values,
        ("pre_build_valid_offsets_us", "pre_sum_us"),
        "comparable_pre_sum_us",
    )
    add_sum_if_present(
        metrics,
        values,
        ("pre_get_valid_blocks_us", "pre_check_data_validness_us"),
        "comparable_pre_data_revalidate_us",
    )
    add_sum_if_present(
        metrics,
        values,
        ("pre_prepare_move_plan_us", "pre_finalize_move_plan_us"),
        "comparable_pre_request_metadata_us",
    )

    pre_stage_keys = (
        "pre_build_valid_offsets_us",
        "pre_sum_us",
        "pre_node_list_us",
        "pre_inode_lock_us",
        "pre_data_lock_us",
        "pre_dirty_source_scan_us",
        "pre_cp_rwsem_lock_us",
        "pre_node_pages_lock_us",
        "pre_get_valid_blocks_us",
        "pre_check_data_validness_us",
        "pre_prepare_move_plan_us",
        "pre_preallocate_us",
        "pre_finalize_move_plan_us",
        "pre_tail_us",
    )
    accounted = add_sum_if_present(
        metrics,
        values,
        pre_stage_keys,
        "modern_detail_pre_success_accounted_us",
    )
    pre_success = int_value(values, "pre_success_attempt_us")
    if accounted is not None and pre_success is not None:
        metrics["modern_detail_pre_success_accounting_delta_us"].append(
            pre_success - accounted
        )


def record_modern_ssd_detail(
    values: Dict[str, str],
    metrics: DefaultDict[str, List[int]],
) -> None:
    """Record the Host-visible SSD request lifecycle for one mCSGC segment."""
    for key in (
        "ssd_trigger_roundtrip_us",
        "ssd_inter_submit_gap_us",
        "ssd_completion_wait_us",
        "approx_gc_cs_ssd_us",
    ):
        add_if_present(metrics, values, key, f"modern_detail_{key}")

    direct_comparable = {
        "ssd_trigger_roundtrip_us": "comparable_ssd_trigger_roundtrip_us",
        "ssd_inter_submit_gap_us": "comparable_ssd_inter_submit_gap_us",
        "ssd_completion_wait_us": "comparable_ssd_completion_wait_us",
        "approx_gc_cs_ssd_us": "comparable_ssd_total_us",
    }
    for source_key, output_key in direct_comparable.items():
        add_if_present(metrics, values, source_key, output_key)

    accounted = add_sum_if_present(
        metrics,
        values,
        (
            "ssd_trigger_roundtrip_us",
            "ssd_inter_submit_gap_us",
            "ssd_completion_wait_us",
        ),
        "modern_detail_ssd_accounted_us",
    )
    total = int_value(values, "approx_gc_cs_ssd_us")
    if accounted is not None and total is not None:
        metrics["modern_detail_ssd_accounting_delta_us"].append(
            total - accounted
        )


def record_modern_prealloc_detail(
    values: Dict[str, str],
    metrics: DefaultDict[str, List[int]],
) -> bool:
    """Record sampled PRE allocation stages and validate record invariants."""
    scalar_keys = (
        "sample_stride",
        "blocks",
        "sampled_blocks",
        "ranges",
        "rollovers",
        "lock_hold_us",
        "rollover_us",
        "dirty_batch_us",
        "dirty_candidate_calls",
        "dirty_actual_calls",
        "dirty_unique_segments",
    )
    for key in scalar_keys:
        add_if_present(metrics, values, key, f"modern_prealloc_{key}")

    blocks = int_value(values, "blocks")
    samples = int_value(values, "sampled_blocks")
    stride = int_value(values, "sample_stride")
    ranges = int_value(values, "ranges")
    candidates = int_value(values, "dirty_candidate_calls")
    actual = int_value(values, "dirty_actual_calls")
    unique = int_value(values, "dirty_unique_segments")
    mismatch = False

    if blocks is None or samples is None or stride is None or stride <= 0:
        mismatch = True
    else:
        expected_samples = (blocks + stride - 1) // stride
        if samples != expected_samples:
            mismatch = True
        metrics["modern_prealloc_sample_coverage_permille"].append(
            samples * 1000 // blocks if blocks else 0
        )

    if blocks is not None and candidates is not None and candidates != blocks * 2:
        mismatch = True
    if candidates is not None and actual is not None and actual > candidates:
        mismatch = True
    if ranges is not None and unique is not None and unique > ranges + 1:
        mismatch = True

    sample_fields = (
        "discard_sample_ns",
        "curseg_advance_sample_ns",
        "block_stat_sample_ns",
        "mtime_sample_ns",
        "sit_sample_ns",
        "dirty_locate_sample_ns",
    )
    for key in sample_fields:
        raw = int_value(values, key)
        add_if_present(metrics, values, key, f"modern_prealloc_{key}")
        if raw is None or raw < 0 or samples is None or samples <= 0:
            continue
        stage = key.removesuffix("_sample_ns")
        metrics[f"modern_prealloc_{stage}_ns_per_sample"].append(
            raw // samples
        )
        if blocks is not None:
            metrics[f"modern_prealloc_{stage}_estimated_us"].append(
                raw * blocks // samples // 1000
            )

    return mismatch


def aggregate_section_critical_paths(
    section_records: Dict[Tuple[int, int], Dict[str, int]],
    timelines: DefaultDict[Tuple[int, int], List[Dict[str, int]]],
    metrics: DefaultDict[str, List[int]],
) -> Dict[str, int]:
    """Aggregate absolute segment timestamps into section critical paths."""
    timeline_records = sum(len(records) for records in timelines.values())
    invalid_records = 0
    unmatched_records = 0
    incomplete_sections = 0
    zero_submission_sections = 0
    aggregated_sections = 0

    for section_key, section_record in section_records.items():
        records = timelines.get(section_key, [])
        expected_submitted = section_record.get("submitted", len(records))
        if expected_submitted == 0:
            zero_submission_sections += 1
            invalid_records += len(records)
            continue

        valid_records = [
            record
            for record in records
            if record["valid"] == 1 and record["ret"] == 0
        ]
        invalid_records += len(records) - len(valid_records)
        if expected_submitted != len(valid_records):
            incomplete_sections += 1
            continue

        start_ns = section_record["start_ns"]
        end_ns = section_record["end_ns"]
        if end_ns < start_ns:
            invalid_records += len(valid_records)
            continue

        first_pre_start_ns = min(record["pre_start_ns"] for record in valid_records)
        last_pre_ready = max(valid_records, key=lambda record: record["pre_ready_ns"])
        first_submit_ns = min(record["trigger_done_ns"] for record in valid_records)
        last_submit = max(valid_records, key=lambda record: record["trigger_done_ns"])
        last_completion = max(
            valid_records,
            key=lambda record: record["ssd_completion_ns"],
        )
        last_post = max(valid_records, key=lambda record: record["post_done_ns"])

        intervals = sorted(
            (
                max(start_ns, record["trigger_done_ns"]),
                min(end_ns, record["ssd_completion_ns"]),
            )
            for record in valid_records
        )
        merged_busy_ns = 0
        current_start = 0
        current_end = 0
        for interval_start, interval_end in intervals:
            if interval_end < interval_start:
                invalid_records += 1
                continue
            if current_end == 0 or interval_start > current_end:
                if current_end:
                    merged_busy_ns += current_end - current_start
                current_start = interval_start
                current_end = interval_end
            elif interval_end > current_end:
                current_end = interval_end
        if current_end:
            merged_busy_ns += current_end - current_start

        events: List[Tuple[int, int]] = []
        for record in valid_records:
            events.append((record["trigger_done_ns"], 1))
            events.append((record["ssd_completion_ns"], -1))
        outstanding = 0
        peak_outstanding = 0
        for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
            outstanding += delta
            peak_outstanding = max(peak_outstanding, outstanding)

        section_ns = end_ns - start_ns
        total_moves = sum(record["moves"] for record in valid_records)
        metrics["modern_section_critical_active_segments"].append(
            len(valid_records)
        )
        metrics["modern_section_critical_total_moves"].append(total_moves)
        metrics["modern_section_critical_pre_span_us"].append(
            (last_pre_ready["pre_ready_ns"] - first_pre_start_ns) // 1000
        )
        metrics["modern_section_critical_pre_tail_us"].append(
            (last_pre_ready["pre_ready_ns"] - start_ns) // 1000
        )
        metrics["modern_section_critical_submit_span_us"].append(
            (last_submit["trigger_done_ns"] - first_submit_ns) // 1000
        )
        metrics["modern_section_critical_first_submit_from_start_us"].append(
            (first_submit_ns - start_ns) // 1000
        )
        metrics["modern_section_critical_last_submit_from_start_us"].append(
            (last_submit["trigger_done_ns"] - start_ns) // 1000
        )
        metrics["modern_section_critical_ssd_drain_us"].append(
            (last_completion["ssd_completion_ns"] - first_submit_ns) // 1000
        )
        metrics["modern_section_critical_last_submit_drain_us"].append(
            (last_completion["ssd_completion_ns"] -
             last_submit["trigger_done_ns"]) // 1000
        )
        metrics["modern_section_critical_completion_after_last_pre_us"].append(
            (last_completion["ssd_completion_ns"] -
             last_pre_ready["pre_ready_ns"]) // 1000
        )
        metrics["modern_section_critical_last_completion_from_start_us"].append(
            (last_completion["ssd_completion_ns"] - start_ns) // 1000
        )
        metrics["modern_section_critical_post_drain_us"].append(
            (end_ns - last_completion["ssd_completion_ns"]) // 1000
        )
        metrics["modern_section_critical_last_post_from_start_us"].append(
            (last_post["post_done_ns"] - start_ns) // 1000
        )
        metrics["modern_section_critical_section_us"].append(section_ns // 1000)
        metrics["modern_section_critical_ssd_busy_union_us"].append(
            merged_busy_ns // 1000
        )
        metrics["modern_section_critical_ssd_idle_us"].append(
            max(0, section_ns - merged_busy_ns) // 1000
        )
        metrics["modern_section_critical_internal_supply_gap_us"].append(
            max(0, last_completion["ssd_completion_ns"] -
                first_submit_ns - merged_busy_ns) // 1000
        )
        if section_ns > 0:
            metrics["modern_section_critical_supply_coverage_permille"].append(
                merged_busy_ns * 1000 // section_ns
            )
        metrics["modern_section_critical_peak_outstanding"].append(
            peak_outstanding
        )
        metrics["modern_section_critical_last_pre_req_idx"].append(
            last_pre_ready["req_idx"]
        )
        metrics["modern_section_critical_last_completion_req_idx"].append(
            last_completion["req_idx"]
        )
        metrics["modern_section_critical_last_post_req_idx"].append(
            last_post["req_idx"]
        )
        if total_moves > 0:
            metrics["modern_section_critical_ns_per_move"].append(
                section_ns // total_moves
            )
        aggregated_sections += 1

    for section_key, records in timelines.items():
        if section_key not in section_records:
            unmatched_records += len(records)

    return {
        "timeline_records": timeline_records,
        "timeline_invalid_records": invalid_records,
        "timeline_unmatched_records": unmatched_records,
        "timeline_incomplete_sections": incomplete_sections,
        "timeline_zero_submission_sections": zero_submission_sections,
        "timeline_sections": aggregated_sections,
    }


def interval_overlap_ns(
    start_ns: int,
    end_ns: int,
    intervals: List[Tuple[int, int]],
) -> int:
    """Return the union overlap between one range and sorted intervals."""
    overlap_ns = 0
    for interval_start, interval_end in intervals:
        if interval_end <= start_ns:
            continue
        if interval_start >= end_ns:
            break
        overlap_ns += max(
            0,
            min(end_ns, interval_end) - max(start_ns, interval_start),
        )
    return overlap_ns


def aggregate_global_supply_gaps(
    timelines: DefaultDict[Tuple[int, int], List[Dict[str, int]]],
    gc_intervals: List[Tuple[int, int]],
    checkpoint_intervals: List[Tuple[int, int]],
    window_start_ns: int,
    window_end_ns: int,
    metrics: DefaultDict[str, List[int]],
) -> Dict[str, int]:
    """Attribute Host-visible CSGC supply gaps across the fio window."""
    request_intervals = sorted(
        (
            max(window_start_ns, record["trigger_done_ns"]),
            min(window_end_ns, record["ssd_completion_ns"]),
        )
        for records in timelines.values()
        for record in records
        if record["valid"] == 1
        and record["ret"] == 0
        and record["ssd_completion_ns"] >= window_start_ns
        and record["trigger_done_ns"] <= window_end_ns
    )

    merged: List[Tuple[int, int]] = []
    for interval_start, interval_end in request_intervals:
        if interval_end < interval_start:
            continue
        if not merged or interval_start > merged[-1][1]:
            merged.append((interval_start, interval_end))
        elif interval_end > merged[-1][1]:
            merged[-1] = (merged[-1][0], interval_end)

    duration_ns = max(0, window_end_ns - window_start_ns)
    busy_ns = sum(end_ns - start_ns for start_ns, end_ns in merged)
    metrics["host_supply_window_us"].append(duration_ns // 1000)
    metrics["host_supply_busy_us"].append(busy_ns // 1000)
    metrics["host_supply_idle_us"].append(max(0, duration_ns - busy_ns) // 1000)
    if duration_ns:
        metrics["host_supply_coverage_permille"].append(
            busy_ns * 1000 // duration_ns
        )

    if not merged:
        metrics["host_supply_initial_gap_us"].append(duration_ns // 1000)
        return {
            "global_supply_intervals": 0,
            "global_supply_internal_gaps": 0,
            "global_supply_checkpoint_gaps": 0,
            "global_supply_same_gc_gaps": 0,
            "global_supply_between_gc_gaps": 0,
        }

    metrics["host_supply_initial_gap_us"].append(
        max(0, merged[0][0] - window_start_ns) // 1000
    )
    metrics["host_supply_final_gap_us"].append(
        max(0, window_end_ns - merged[-1][1]) // 1000
    )

    sorted_checkpoints = sorted(checkpoint_intervals)
    sorted_gc_intervals = sorted(gc_intervals)
    checkpoint_index = 0
    gc_index = 0
    checkpoint_gaps = 0
    same_gc_gaps = 0
    between_gc_gaps = 0
    for index in range(1, len(merged)):
        gap_start = merged[index - 1][1]
        gap_end = merged[index][0]
        if gap_end <= gap_start:
            continue
        gap_ns = gap_end - gap_start
        while (
            checkpoint_index < len(sorted_checkpoints)
            and sorted_checkpoints[checkpoint_index][1] <= gap_start
        ):
            checkpoint_index += 1
        checkpoint_overlap = 0
        candidate_index = checkpoint_index
        while (
            candidate_index < len(sorted_checkpoints)
            and sorted_checkpoints[candidate_index][0] < gap_end
        ):
            checkpoint_overlap += max(
                0,
                min(gap_end, sorted_checkpoints[candidate_index][1])
                - max(gap_start, sorted_checkpoints[candidate_index][0]),
            )
            candidate_index += 1
        metrics["host_supply_internal_gap_us"].append(gap_ns // 1000)
        metrics["host_supply_checkpoint_overlap_us"].append(
            checkpoint_overlap // 1000
        )

        if checkpoint_overlap:
            checkpoint_gaps += 1
            metrics["host_supply_checkpoint_gap_us"].append(gap_ns // 1000)
            continue

        while (
            gc_index < len(sorted_gc_intervals)
            and sorted_gc_intervals[gc_index][1] <= gap_start
        ):
            gc_index += 1
        in_same_gc = (
            gc_index < len(sorted_gc_intervals)
            and sorted_gc_intervals[gc_index][0] <= gap_start
            and sorted_gc_intervals[gc_index][1] >= gap_end
        )
        if in_same_gc:
            same_gc_gaps += 1
            metrics["host_supply_same_gc_gap_us"].append(gap_ns // 1000)
        else:
            between_gc_gaps += 1
            metrics["host_supply_between_gc_gap_us"].append(gap_ns // 1000)

    return {
        "global_supply_intervals": len(merged),
        "global_supply_internal_gaps": max(0, len(merged) - 1),
        "global_supply_checkpoint_gaps": checkpoint_gaps,
        "global_supply_same_gc_gaps": same_gc_gaps,
        "global_supply_between_gc_gaps": between_gc_gaps,
    }


def parse_modern_records(
    lines: Iterable[str],
    metrics: DefaultDict[str, List[int]],
    window_start_ns: int,
    window_end_ns: int,
) -> Dict[str, int]:
    """Parse mCSGC structured records and legacy phase traces."""
    line_list = list(lines)
    section_windows: DefaultDict[int, List[Tuple[int, int]]] = defaultdict(list)
    section_records: Dict[Tuple[int, int], Dict[str, int]] = {}
    timelines: DefaultDict[Tuple[int, int], List[Dict[str, int]]] = defaultdict(list)
    for line in line_list:
        if "MCSGC_SECTION " not in line:
            continue
        values = parse_kv(line)
        section = int_value(values, "section")
        section_start = int_value(values, "start_ns")
        section_end = int_value(values, "end_ns")
        segments = int_value(values, "segments")
        if (
            section is None
            or section_start is None
            or section_end is None
            or segments is None
            or segments <= 0
        ):
            continue
        for segno in range(section, section + segments):
            section_windows[segno].append((section_start, section_end))
        section_record = {
            "start_ns": section_start,
            "end_ns": section_end,
            "segments": segments,
        }
        submitted = int_value(values, "submitted")
        if submitted is not None:
            section_record["submitted"] = submitted
        section_records[(section, section_start)] = section_record

    gc_starts: Dict[Tuple[int, int], int] = {}
    phase_starts: DefaultDict[Tuple[str, int, int, int], List[int]] = defaultdict(list)
    post_detail_by_segment: Dict[Tuple[int, int], Tuple[int, int]] = {}
    pre_detail_by_segment: Dict[Tuple[int, int], Dict[str, str]] = {}
    pre_detail_parts: DefaultDict[Tuple[int, int], Set[str]] = defaultdict(set)
    summary_batches: Dict[Tuple[int, int], Dict[str, int]] = {}
    summary_batch_mismatches = 0
    summary_segments = 0
    prealloc_records = 0
    prealloc_record_mismatches = 0
    gc_intervals: List[Tuple[int, int]] = []
    checkpoint_intervals: List[Tuple[int, int]] = []
    unmatched_ends = 0
    structured_records = 0
    pipeline_records = 0
    pipeline_dual_records = 0
    pipeline_single_records = 0
    pipeline_second_victim_attempts = 0
    pipeline_second_victim_failures = 0
    pipeline_second_victim_non_data = 0
    pipeline_incomplete_timings = 0
    pipeline_errors = 0
    continuous_supply_records = 0
    continuous_supply_successor_useful_records = 0
    continuous_supply_conflict_records = 0

    for line in line_list:
        if "F2FS_GC_CHECKPOINT_DIAG " in line:
            values = parse_kv(line)
            start_ns = int_value(values, "start_ns")
            end_ns = int_value(values, "end_ns")
            if start_ns is not None and end_ns is not None and end_ns >= start_ns:
                checkpoint_intervals.append((start_ns, end_ns))
            continue

        if "F2FS_GC_DIAG " in line:
            values = parse_kv(line)
            start_ns = int_value(values, "start_ns")
            end_ns = int_value(values, "end_ns")
            if start_ns is not None and end_ns is not None and end_ns >= start_ns:
                gc_intervals.append((start_ns, end_ns))
            continue

        if "MCSGC_SEGMENT_TIMELINE " in line:
            values = parse_kv(line)
            structured_records += 1
            required_keys = (
                "section",
                "segno",
                "req_idx",
                "valid",
                "ret",
                "moves",
                "pre_attempts",
                "section_start_ns",
                "segment_start_ns",
                "pre_start_ns",
                "pre_ready_ns",
                "trigger_done_ns",
                "completion_read_submit_ns",
                "ssd_completion_ns",
                "post_start_ns",
                "post_done_ns",
            )
            record = {key: int_value(values, key) for key in required_keys}
            if any(value is None for value in record.values()):
                unmatched_ends += 1
                continue
            parsed_record = {
                key: value for key, value in record.items() if value is not None
            }
            timelines[
                (parsed_record["section"], parsed_record["section_start_ns"])
            ].append(parsed_record)
            continue

        if "CSGC_CONTINUOUS_SUPPLY_STAT " in line:
            values = parse_kv(line)
            structured_records += 1
            continuous_supply_records += 1
            for key in (
                "watermark",
                "pair_wall_us",
                "first_ready_us",
                "second_ready_us",
                "successor_launch_gap_us",
                "first_done_at_successor_launch",
                "gate_hit_watermark",
                "ready_submitted",
                "ready_pre_finished",
                "first_submitted",
                "second_submitted",
                "first_pre_finished",
                "second_pre_finished",
                "first_inode_conflicts",
                "second_inode_conflicts",
            ):
                add_if_present(metrics, values, key, f"continuous_supply_{key}")

            first_submitted = int_value(values, "first_submitted")
            second_submitted = int_value(values, "second_submitted")
            first_pre_finished = int_value(values, "first_pre_finished")
            second_pre_finished = int_value(values, "second_pre_finished")
            first_conflicts = int_value(values, "first_inode_conflicts")
            second_conflicts = int_value(values, "second_inode_conflicts")
            if second_submitted is not None and second_submitted > 0:
                continuous_supply_successor_useful_records += 1
            if first_submitted is not None and second_submitted is not None:
                metrics["continuous_supply_total_submitted"].append(
                    first_submitted + second_submitted
                )
            if first_pre_finished is not None and second_pre_finished is not None:
                metrics["continuous_supply_total_pre_finished"].append(
                    first_pre_finished + second_pre_finished
                )
            if first_conflicts is not None and second_conflicts is not None:
                total_conflicts = first_conflicts + second_conflicts
                metrics["continuous_supply_total_inode_conflicts"].append(
                    total_conflicts
                )
                if total_conflicts:
                    continuous_supply_conflict_records += 1
            continue

        if "MCSGC_PIPELINE " in line:
            values = parse_kv(line)
            structured_records += 1
            pipeline_records += 1
            sections = int_value(values, "sections")
            timing_complete = int_value(values, "timing_complete")
            ret = int_value(values, "ret")
            second_victim_attempted = int_value(values, "second_victim_attempted")
            second_victim_ret = int_value(values, "second_victim_ret")
            if second_victim_attempted == 1:
                pipeline_second_victim_attempts += 1

            for key in (
                "sections",
                "wall_us",
                "section0_us",
                "section1_us",
                "section_sum_us",
                "section_union_us",
                "section_span_us",
                "overlap_us",
                "inter_section_idle_us",
                "launch_gap_us",
                "outer_pre_us",
                "outer_post_us",
                "second_victim_attempted",
                "second_victim_select_us",
                "valid_blocks",
                "seg_freed",
                "full_sections",
            ):
                add_if_present(metrics, values, key, f"pipeline_{key}")

            wall_us = int_value(values, "wall_us")
            section_sum_us = int_value(values, "section_sum_us")
            section_union_us = int_value(values, "section_union_us")
            section_span_us = int_value(values, "section_span_us")
            overlap_us = int_value(values, "overlap_us")
            effective_parallelism: Optional[int] = None
            section_parallelism: Optional[int] = None
            overlap_fraction: Optional[int] = None
            if sections is not None and sections > 0:
                if wall_us is not None:
                    metrics["pipeline_wall_per_section_us"].append(
                        wall_us // sections
                    )
                if section_sum_us is not None:
                    metrics["pipeline_section_sum_per_section_us"].append(
                        section_sum_us // sections
                    )
            if wall_us is not None and wall_us > 0 and section_sum_us is not None:
                effective_parallelism = section_sum_us * 1000 // wall_us
                metrics["pipeline_effective_parallelism_milli"].append(
                    effective_parallelism
                )
                metrics["pipeline_net_saved_us"].append(
                    section_sum_us - wall_us
                )
            if (
                section_union_us is not None
                and section_union_us > 0
                and section_sum_us is not None
            ):
                section_parallelism = section_sum_us * 1000 // section_union_us
                metrics["pipeline_section_parallelism_milli"].append(
                    section_parallelism
                )
            if (
                section_union_us is not None
                and section_union_us > 0
                and overlap_us is not None
            ):
                overlap_fraction = overlap_us * 1000 // section_union_us
                metrics["pipeline_overlap_fraction_permille"].append(
                    overlap_fraction
                )

            if sections == 2:
                pipeline_dual_records += 1
                for key in (
                    "wall_us",
                    "section_sum_us",
                    "section_union_us",
                    "section_span_us",
                    "overlap_us",
                    "inter_section_idle_us",
                    "launch_gap_us",
                ):
                    add_if_present(metrics, values, key, f"pipeline_dual_{key}")
                if effective_parallelism is not None:
                    metrics["pipeline_dual_effective_parallelism_milli"].append(
                        effective_parallelism
                    )
                if wall_us is not None and section_sum_us is not None:
                    metrics["pipeline_dual_net_saved_us"].append(
                        section_sum_us - wall_us
                    )
                if wall_us is not None:
                    metrics["pipeline_dual_wall_per_section_us"].append(
                        wall_us // sections
                    )
                if section_parallelism is not None:
                    metrics["pipeline_dual_section_parallelism_milli"].append(
                        section_parallelism
                    )
                if overlap_fraction is not None:
                    metrics["pipeline_dual_overlap_fraction_permille"].append(
                        overlap_fraction
                    )
                if (
                    section_span_us is not None
                    and section_span_us > 0
                    and section_sum_us is not None
                ):
                    metrics["pipeline_dual_span_parallelism_milli"].append(
                        section_sum_us * 1000 // section_span_us
                    )
            elif sections == 1:
                pipeline_single_records += 1
                add_if_present(metrics, values, "wall_us", "pipeline_single_wall_us")
                if second_victim_attempted == 1:
                    if second_victim_ret is not None and second_victim_ret != 0:
                        pipeline_second_victim_failures += 1
                    elif second_victim_ret == 0:
                        pipeline_second_victim_non_data += 1
            if timing_complete != 1:
                pipeline_incomplete_timings += 1
            if ret is not None and ret != 0:
                pipeline_errors += 1
            continue

        if "MCSGC_SECTION " in line:
            values = parse_kv(line)
            structured_records += 1
            for key in ("section_gc_time_us", "section_sync_us", "collector_us"):
                add_if_present(metrics, values, key, f"modern_section_{key}")
                add_if_present(metrics, values, key, f"comparable_section_{key}")
            continue

        if "MCSGC_SEGMENT_DETAIL " in line:
            # Compatibility with the short-lived combined diagnostic format.
            values = parse_kv(line)
            structured_records += 1
            key = modern_segment_key(values)
            if key is None:
                unmatched_ends += 1
            else:
                pre_detail_by_segment.setdefault(key, {}).update(values)
                pre_detail_parts[key].update(("pre", "move"))
            record_modern_ssd_detail(values, metrics)
            continue

        if "MCSGC_SEGMENT_PRE_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            key = modern_segment_key(values)
            if key is None:
                unmatched_ends += 1
            else:
                pre_detail_by_segment.setdefault(key, {}).update(values)
                pre_detail_parts[key].add("pre")
            continue

        if "MCSGC_SEGMENT_MOVE_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            key = modern_segment_key(values)
            if key is None:
                unmatched_ends += 1
            else:
                pre_detail_by_segment.setdefault(key, {}).update(values)
                pre_detail_parts[key].add("move")
            continue

        if "MCSGC_SEGMENT_PREALLOC_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            prealloc_records += 1
            if modern_segment_key(values) is None:
                unmatched_ends += 1
            if record_modern_prealloc_detail(values, metrics):
                prealloc_record_mismatches += 1
            continue

        if "MCSGC_SEGMENT_SSD_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            if modern_segment_key(values) is None:
                unmatched_ends += 1
            record_modern_ssd_detail(values, metrics)
            continue

        if "MCSGC_SEGMENT_POST_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            post_ret = int_value(values, "ret")
            if post_ret is not None:
                metrics["modern_post_ret"].append(post_ret)
            if post_ret != 0:
                metrics["modern_post_failures"].append(1)
                add_if_present(
                    metrics,
                    values,
                    "post_rollback_us",
                    "modern_post_failure_rollback_us",
                )
                continue
            post_keys = (
                "post_update_meta_us",
                "post_result_status_us",
                "post_device_result_validate_us",
                "post_local_commit_validate_us",
                "post_cache_invalidate_us",
                "post_summary_commit_us",
                "post_summary_queue_wait_us",
                "post_summary_curseg_lock_wait_us",
                "post_summary_curseg_mutex_wait_us",
                "post_summary_page_get_us",
                "post_summary_resolve_us",
                "post_summary_entry_update_us",
                "post_summary_dirty_put_us",
                "post_summary_service_us",
                "post_summary_batch_id",
                "post_summary_batch_size",
                "post_summary_moves",
                "post_summary_pages",
                "post_dnode_commit_us",
                "post_dnode_blocks",
                "post_dnode_batches",
                "post_dnode_extents",
                "post_dnode_batch_build_us",
                "post_dnode_lock_wait_us",
                "post_dnode_writeback_wait_us",
                "post_dnode_page_update_us",
                "post_dnode_extent_cache_us",
                "post_dnode_ref_release_us",
                "post_account_us",
                "post_rollback_us",
                "post_unlock_op_us",
                "post_cleanup_us",
            )
            for key in post_keys:
                add_if_present(metrics, values, key, f"modern_post_detail_{key}")

            summary_batch_id = int_value(values, "post_summary_batch_id")
            summary_batch_size = int_value(values, "post_summary_batch_size")
            summary_start_ns = int_value(values, "section_start_ns")
            if summary_start_ns is None:
                segment_start_ns = int_value(values, "start_ns")
                summary_segno = int_value(values, "segno")
                if segment_start_ns is not None and summary_segno is not None:
                    matches = [
                        section_start
                        for section_start, section_end in section_windows.get(
                            summary_segno, []
                        )
                        if section_start <= segment_start_ns <= section_end
                    ]
                    if len(matches) == 1:
                        summary_start_ns = matches[0]
            if (
                summary_batch_id is not None
                and summary_batch_size is not None
                and summary_batch_size > 0
                and summary_start_ns is not None
            ):
                summary_segments += 1
                batch_key = (summary_start_ns, summary_batch_id)
                batch_fields = {
                    key: value
                    for key in (
                        "post_summary_batch_size",
                        "post_summary_moves",
                        "post_summary_pages",
                        "post_summary_curseg_lock_wait_us",
                        "post_summary_curseg_mutex_wait_us",
                        "post_summary_page_get_us",
                        "post_summary_resolve_us",
                        "post_summary_entry_update_us",
                        "post_summary_dirty_put_us",
                        "post_summary_service_us",
                    )
                    if (value := int_value(values, key)) is not None and value >= 0
                }
                previous = summary_batches.get(batch_key)
                if previous is None:
                    summary_batches[batch_key] = batch_fields
                elif previous != batch_fields:
                    summary_batch_mismatches += 1
            add_if_present(
                metrics,
                values,
                "post_update_meta_us",
                "comparable_post_metadata_without_page_release_us",
            )
            add_sum_if_present(
                metrics,
                values,
                (
                    "post_result_status_us",
                    "post_device_result_validate_us",
                    "post_local_commit_validate_us",
                ),
                "comparable_post_result_validation_us",
            )
            add_sum_if_present(
                metrics,
                values,
                (
                    "post_cache_invalidate_us",
                    "post_summary_commit_us",
                    "post_account_us",
                ),
                "comparable_post_segment_metadata_us",
            )
            add_if_present(
                metrics,
                values,
                "post_dnode_commit_us",
                "comparable_post_dnode_update_us",
            )
            dnode_accounted = add_sum_if_present(
                metrics,
                values,
                (
                    "post_dnode_batch_build_us",
                    "post_dnode_lock_wait_us",
                    "post_dnode_writeback_wait_us",
                    "post_dnode_page_update_us",
                    "post_dnode_extent_cache_us",
                    "post_dnode_ref_release_us",
                ),
                "modern_post_detail_post_dnode_accounted_us",
            )
            dnode_total = int_value(values, "post_dnode_commit_us")
            if dnode_accounted is not None and dnode_total is not None:
                metrics[
                    "modern_post_detail_post_dnode_accounting_delta_us"
                ].append(dnode_total - dnode_accounted)
            add_if_present(
                metrics,
                values,
                "post_unlock_op_us",
                "comparable_post_unlock_op_us",
            )

            update_accounted = add_sum_if_present(
                metrics,
                values,
                (
                    "post_result_status_us",
                    "post_device_result_validate_us",
                    "post_local_commit_validate_us",
                    "post_cache_invalidate_us",
                    "post_summary_commit_us",
                    "post_dnode_commit_us",
                    "post_account_us",
                    "post_rollback_us",
                    "post_unlock_op_us",
                ),
                "modern_post_detail_update_accounted_us",
            )
            update_total = int_value(values, "post_update_meta_us")
            if update_accounted is not None and update_total is not None:
                metrics["modern_post_detail_update_accounting_delta_us"].append(
                    update_total - update_accounted
                )

            segno = int_value(values, "segno")
            start_ns = int_value(values, "start_ns")
            cleanup_us = int_value(values, "post_cleanup_us")
            update_us = int_value(values, "post_update_meta_us")
            if (
                segno is not None
                and start_ns is not None
                and cleanup_us is not None
                and update_us is not None
                and cleanup_us >= 0
                and update_us >= 0
            ):
                post_detail_by_segment[(segno, start_ns)] = (
                    update_us,
                    cleanup_us,
                )
            continue

        if "MCSGC_SEGMENT_RELEASE_DETAIL " in line:
            values = parse_kv(line)
            structured_records += 1
            if int_value(values, "success") != 1:
                continue
            for key in (
                "post_put_data_pages_us",
                "post_release_cleanup_us",
                "post_release_total_us",
            ):
                add_if_present(metrics, values, key, f"modern_release_{key}")
            add_if_present(
                metrics,
                values,
                "post_put_data_pages_us",
                "comparable_post_put_data_pages_us",
            )
            segno = int_value(values, "segno")
            start_ns = int_value(values, "start_ns")
            put_pages = int_value(values, "post_put_data_pages_us")
            release_cleanup = int_value(values, "post_release_cleanup_us")
            release_total = int_value(values, "post_release_total_us")
            if (
                segno is None
                or start_ns is None
                or put_pages is None
                or release_cleanup is None
                or release_total is None
            ):
                unmatched_ends += 1
                continue
            metrics["modern_release_accounting_delta_us"].append(
                release_total - put_pages - release_cleanup
            )
            post_detail = post_detail_by_segment.pop((segno, start_ns), None)
            if post_detail is None:
                unmatched_ends += 1
            else:
                update_us, cleanup_us = post_detail
                comparable_update = update_us + put_pages
                comparable_cleanup = cleanup_us + release_cleanup
                metrics["comparable_post_update_meta_us"].append(
                    comparable_update
                )
                metrics["comparable_post_cleanup_us"].append(
                    comparable_cleanup
                )
                metrics["comparable_post_total_work_us"].append(
                    comparable_update + comparable_cleanup
                )
            continue

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
            add_if_present(
                metrics,
                values,
                "approx_segment_total_us",
                "comparable_segment_total_us",
            )
            add_if_present(
                metrics,
                values,
                "post_queue_delay_us",
                "comparable_post_queue_delay_us",
            )

    incomplete_pre_details = 0
    for key, values in pre_detail_by_segment.items():
        if pre_detail_parts[key] != {"pre", "move"}:
            incomplete_pre_details += 1
            continue
        record_modern_pre_detail(values, metrics)

    unmatched_starts = len(gc_starts) + sum(
        len(starts) for starts in phase_starts.values()
    ) + len(post_detail_by_segment) + incomplete_pre_details
    if pipeline_records:
        metrics["pipeline_dual_batch_fraction_permille"].append(
            pipeline_dual_records * 1000 // pipeline_records
        )
        metrics["pipeline_single_batch_fraction_permille"].append(
            pipeline_single_records * 1000 // pipeline_records
        )
    if pipeline_second_victim_attempts:
        metrics["pipeline_second_victim_failure_fraction_permille"].append(
            pipeline_second_victim_failures * 1000 // pipeline_second_victim_attempts
        )
        metrics["pipeline_second_victim_non_data_fraction_permille"].append(
            pipeline_second_victim_non_data * 1000 // pipeline_second_victim_attempts
        )
    if continuous_supply_records:
        metrics["continuous_supply_successor_useful_fraction_permille"].append(
            continuous_supply_successor_useful_records * 1000
            // continuous_supply_records
        )
        metrics["continuous_supply_conflict_pair_fraction_permille"].append(
            continuous_supply_conflict_records * 1000
            // continuous_supply_records
        )

    for batch in summary_batches.values():
        for source_key, value in batch.items():
            output_key = source_key.removeprefix("post_summary_")
            metrics[f"summary_batch_{output_key}"].append(value)

    timeline_diagnostics = aggregate_section_critical_paths(
        section_records,
        timelines,
        metrics,
    )
    supply_diagnostics = aggregate_global_supply_gaps(
        timelines,
        gc_intervals,
        checkpoint_intervals,
        window_start_ns,
        window_end_ns,
        metrics,
    )

    diagnostics = {
        "unmatched_starts": unmatched_starts,
        "unmatched_ends": unmatched_ends,
        "incomplete_pre_details": incomplete_pre_details,
        "structured_records": structured_records,
        "pipeline_records": pipeline_records,
        "pipeline_dual_records": pipeline_dual_records,
        "pipeline_single_records": pipeline_single_records,
        "pipeline_second_victim_attempts": pipeline_second_victim_attempts,
        "pipeline_second_victim_failures": pipeline_second_victim_failures,
        "pipeline_second_victim_non_data": pipeline_second_victim_non_data,
        "pipeline_incomplete_timings": pipeline_incomplete_timings,
        "pipeline_errors": pipeline_errors,
        "continuous_supply_records": continuous_supply_records,
        "continuous_supply_successor_useful_records": (
            continuous_supply_successor_useful_records
        ),
        "continuous_supply_conflict_records": continuous_supply_conflict_records,
        "summary_segments": summary_segments,
        "summary_batches": len(summary_batches),
        "summary_batch_mismatches": summary_batch_mismatches,
        "prealloc_records": prealloc_records,
        "prealloc_record_mismatches": prealloc_record_mismatches,
    }
    diagnostics.update(timeline_diagnostics)
    diagnostics.update(supply_diagnostics)
    return diagnostics


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


def emit_dnode_batch_aggregate(
    output: List[str],
    metrics: DefaultDict[str, List[int]],
) -> None:
    """Emit weighted aggregate ratios for the batched dnode commit path."""
    blocks = sum(metrics.get("modern_post_detail_post_dnode_blocks", []))
    batches = sum(metrics.get("modern_post_detail_post_dnode_batches", []))
    extents = sum(metrics.get("modern_post_detail_post_dnode_extents", []))

    output.append("=== mCSGC8t batched dnode aggregate ===")
    if not blocks or not batches:
        output.append("no records")
    else:
        reduction = (1.0 - batches / blocks) * 100.0
        output.extend(
            (
                f"post_dnode_total_blocks={blocks}",
                f"post_dnode_total_batches={batches}",
                f"post_dnode_total_extents={extents}",
                f"post_dnode_blocks_per_batch={blocks / batches:.6f}",
                f"post_dnode_extents_per_batch={extents / batches:.6f}",
                f"post_dnode_commit_call_reduction_pct={reduction:.6f}",
            )
        )
    output.append("")


def emit_summary_batch_aggregate(
    output: List[str],
    metrics: DefaultDict[str, List[int]],
    diagnostics: Dict[str, int],
) -> None:
    """Emit de-duplicated summary batch distributions and invariants."""
    segments = diagnostics["summary_segments"]
    batches = diagnostics["summary_batches"]
    batch_sizes = metrics.get("summary_batch_batch_size", [])
    moves = metrics.get("summary_batch_moves", [])
    pages = metrics.get("summary_batch_pages", [])
    batch_size_sum = sum(batch_sizes)

    output.append("=== mCSGC8t summary batch aggregate ===")
    if not segments or not batches:
        output.append("no records")
    else:
        reduction = (1.0 - batches / segments) * 100.0
        output.extend(
            (
                f"post_summary_segments={segments}",
                f"post_summary_batches={batches}",
                f"post_summary_batch_size_sum={batch_size_sum}",
                f"post_summary_batch_size_matches_segments={int(batch_size_sum == segments)}",
                f"post_summary_batch_record_mismatches={diagnostics['summary_batch_mismatches']}",
                f"post_summary_commit_call_reduction_pct={reduction:.6f}",
                f"post_summary_moves_per_batch={sum(moves) / batches:.6f}",
                f"post_summary_pages_per_batch={sum(pages) / batches:.6f}",
            )
        )
        output.extend(
            metric_line(name, metrics[name])
            for name in sorted(metrics)
            if name.startswith("summary_batch_")
        )
    output.append("")


def emit_prealloc_aggregate(
    output: List[str],
    metrics: DefaultDict[str, List[int]],
    diagnostics: Dict[str, int],
) -> None:
    """Emit weighted PRE allocation counts and dirty-call reduction."""
    blocks = sum(metrics.get("modern_prealloc_blocks", []))
    samples = sum(metrics.get("modern_prealloc_sampled_blocks", []))
    candidates = sum(metrics.get("modern_prealloc_dirty_candidate_calls", []))
    actual = sum(metrics.get("modern_prealloc_dirty_actual_calls", []))

    output.append("=== mCSGC8t PRE allocation aggregate ===")
    if not diagnostics["prealloc_records"]:
        output.append("no records")
    else:
        reduction = (1.0 - actual / candidates) * 100.0 if candidates else 0.0
        output.extend(
            (
                f"prealloc_records={diagnostics['prealloc_records']}",
                f"prealloc_record_mismatches={diagnostics['prealloc_record_mismatches']}",
                f"prealloc_total_blocks={blocks}",
                f"prealloc_total_sampled_blocks={samples}",
                f"prealloc_dirty_candidate_calls={candidates}",
                f"prealloc_dirty_actual_calls={actual}",
                f"prealloc_dirty_call_reduction_pct={reduction:.6f}",
            )
        )
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
    base_structured_records = sum(
        1 for line in window_lines if parse_original_record(line, metrics)
    )
    diagnostics = parse_modern_records(
        window_lines,
        metrics,
        start_us * 1000,
        end_us * 1000,
    )

    output = [
        "GC breakdown diagnostic summary",
        f"input={input_path}",
        f"measured_start_us={start_us}",
        f"measured_end_us={end_us}",
        f"measured_duration_us={max(0, end_us - start_us)}",
        f"window_lines={len(window_lines)}",
        f"base_structured_records={base_structured_records}",
        f"mcsgc_structured_records={diagnostics['structured_records']}",
        f"unmatched_starts={diagnostics['unmatched_starts']}",
        f"unmatched_ends={diagnostics['unmatched_ends']}",
        f"incomplete_mcsgc_pre_details={diagnostics['incomplete_pre_details']}",
        f"pipeline_records={diagnostics['pipeline_records']}",
        f"pipeline_dual_records={diagnostics['pipeline_dual_records']}",
        f"pipeline_single_records={diagnostics['pipeline_single_records']}",
        f"pipeline_second_victim_attempts={diagnostics['pipeline_second_victim_attempts']}",
        f"pipeline_second_victim_failures={diagnostics['pipeline_second_victim_failures']}",
        f"pipeline_second_victim_non_data={diagnostics['pipeline_second_victim_non_data']}",
        f"pipeline_incomplete_timings={diagnostics['pipeline_incomplete_timings']}",
        f"pipeline_errors={diagnostics['pipeline_errors']}",
        f"continuous_supply_records={diagnostics['continuous_supply_records']}",
        f"continuous_supply_successor_useful_records={diagnostics['continuous_supply_successor_useful_records']}",
        f"continuous_supply_conflict_records={diagnostics['continuous_supply_conflict_records']}",
        f"summary_segments={diagnostics['summary_segments']}",
        f"summary_batches={diagnostics['summary_batches']}",
        f"summary_batch_mismatches={diagnostics['summary_batch_mismatches']}",
        f"timeline_records={diagnostics['timeline_records']}",
        f"timeline_sections={diagnostics['timeline_sections']}",
        f"timeline_invalid_records={diagnostics['timeline_invalid_records']}",
        f"timeline_unmatched_records={diagnostics['timeline_unmatched_records']}",
        f"timeline_incomplete_sections={diagnostics['timeline_incomplete_sections']}",
        f"timeline_zero_submission_sections={diagnostics['timeline_zero_submission_sections']}",
        f"global_supply_intervals={diagnostics['global_supply_intervals']}",
        f"global_supply_internal_gaps={diagnostics['global_supply_internal_gaps']}",
        f"global_supply_checkpoint_gaps={diagnostics['global_supply_checkpoint_gaps']}",
        f"global_supply_same_gc_gaps={diagnostics['global_supply_same_gc_gaps']}",
        f"global_supply_between_gc_gaps={diagnostics['global_supply_between_gc_gaps']}",
        "",
    ]

    emit_group(output, "complete f2fs_gc calls", metrics, ("gc_call_", "gc_with_", "gc_no_"))
    emit_group(
        output,
        "GC checkpoint and unsafe reclaim",
        metrics,
        ("gc_checkpoint_", "gc_unsafe_reclaim_", "gc_stop_reason_"),
    )
    emit_group(output, "Host CSGC supply gaps", metrics, ("host_supply_",))
    emit_group(output, "collector paths", metrics, ("collector_", "gc_end_", "gc_path_"))
    emit_group(output, "cross-version comparable breakdown", metrics, ("comparable_",))
    emit_group(
        output,
        "section wall-clock",
        metrics,
        ("original_section_", "modern_section_section_", "modern_section_collector_", "phase_section_"),
    )
    emit_group(
        output,
        "mCSGC8t section critical path",
        metrics,
        ("modern_section_critical_",),
    )
    emit_group(output, "cross-section pipeline", metrics, ("pipeline_",))
    emit_group(
        output,
        "continuous cross-section supply",
        metrics,
        ("continuous_supply_",),
    )
    emit_group(output, "coarse PRE/SSD/POST intervals", metrics, ("phase_pre_", "phase_ssd_", "phase_post_"))
    emit_group(output, "original CSGC segment breakdown", metrics, ("original_segment_",))
    emit_group(output, "original CSGC detailed PRE/SSD breakdown", metrics, ("original_detail_",))
    emit_group(output, "original CSGC detailed POST breakdown", metrics, ("original_post_detail_",))
    emit_group(output, "mCSGC8t segment breakdown", metrics, ("modern_segment_",))
    emit_group(output, "mCSGC8t detailed PRE/SSD breakdown", metrics, ("modern_detail_",))
    emit_group(output, "mCSGC8t PRE allocation detail", metrics, ("modern_prealloc_",))
    emit_prealloc_aggregate(output, metrics, diagnostics)
    emit_group(output, "mCSGC8t detailed POST breakdown", metrics, ("modern_post_detail_",))
    emit_dnode_batch_aggregate(output, metrics)
    emit_summary_batch_aggregate(output, metrics, diagnostics)
    emit_group(output, "mCSGC8t resource release breakdown", metrics, ("modern_release_",))

    Path(args.output).write_text("\n".join(output), encoding="utf-8")
    print(f"Wrote {args.output}")
    if args.crop_output:
        print(f"Wrote {args.crop_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
