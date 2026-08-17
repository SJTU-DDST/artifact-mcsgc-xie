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


def parse_modern_records(
    lines: Iterable[str],
    metrics: DefaultDict[str, List[int]],
) -> Dict[str, int]:
    """Parse mCSGC structured records and legacy phase traces."""
    gc_starts: Dict[Tuple[int, int], int] = {}
    phase_starts: DefaultDict[Tuple[str, int, int, int], List[int]] = defaultdict(list)
    post_detail_by_segment: Dict[Tuple[int, int], Tuple[int, int]] = {}
    pre_detail_by_segment: Dict[Tuple[int, int], Dict[str, str]] = {}
    pre_detail_parts: DefaultDict[Tuple[int, int], Set[str]] = defaultdict(set)
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

    for line in lines:
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
    return {
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
    diagnostics = parse_modern_records(window_lines, metrics)

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
        "",
    ]

    emit_group(output, "complete f2fs_gc calls", metrics, ("gc_call_", "gc_with_", "gc_no_"))
    emit_group(output, "collector paths", metrics, ("collector_", "gc_end_", "gc_path_"))
    emit_group(output, "cross-version comparable breakdown", metrics, ("comparable_",))
    emit_group(output, "section wall-clock", metrics, ("original_section_", "modern_section_", "phase_section_"))
    emit_group(output, "cross-section pipeline", metrics, ("pipeline_",))
    emit_group(output, "coarse PRE/SSD/POST intervals", metrics, ("phase_pre_", "phase_ssd_", "phase_post_"))
    emit_group(output, "original CSGC segment breakdown", metrics, ("original_segment_",))
    emit_group(output, "original CSGC detailed PRE/SSD breakdown", metrics, ("original_detail_",))
    emit_group(output, "original CSGC detailed POST breakdown", metrics, ("original_post_detail_",))
    emit_group(output, "mCSGC8t segment breakdown", metrics, ("modern_segment_",))
    emit_group(output, "mCSGC8t detailed PRE/SSD breakdown", metrics, ("modern_detail_",))
    emit_group(output, "mCSGC8t detailed POST breakdown", metrics, ("modern_post_detail_",))
    emit_dnode_batch_aggregate(output, metrics)
    emit_group(output, "mCSGC8t resource release breakdown", metrics, ("modern_release_",))

    Path(args.output).write_text("\n".join(output), encoding="utf-8")
    print(f"Wrote {args.output}")
    if args.crop_output:
        print(f"Wrote {args.crop_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
