#!/usr/bin/env python3

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


BLOCK_SIZE_KIB = 4
BLOCKS_PER_MIB = 1024 // BLOCK_SIZE_KIB
DEFAULT_BLOCKS_PER_SEGMENT = 512
DEFAULT_SEGMENTS_PER_SECTION = 8


@dataclass
class CleanupRun:
    mount_line_no: Optional[int] = None
    mount_label: Optional[str] = None
    csgc_stat_line_no: Optional[int] = None
    window_line_no: Optional[int] = None
    start_ns: Optional[int] = None
    end_ns: Optional[int] = None
    window_ns: Optional[int] = None
    valid_blks: Optional[int] = None
    reclaimed_blks: Optional[int] = None
    csgc_called: Optional[int] = None
    csgc_segs_freed: Optional[int] = None
    csgc_blocks_migrated: Optional[int] = None
    csgc_cleanup_window_ns: Optional[int] = None
    csgc_cleanup_valid_blks: Optional[int] = None
    csgc_cleanup_reclaimed_blks: Optional[int] = None
    csgc_cleanup_sections: Optional[int] = None
    csgc_cleanup_migration_rate_mib_s: Optional[float] = None
    csgc_cleanup_reclaim_rate_mib_s: Optional[float] = None
    csgc_cleanup_section_rate_sections_s: Optional[float] = None
    gc_idle_ns: Optional[int] = None
    gc_active_ns: Optional[int] = None
    gc_total_ns: Optional[int] = None
    max_active: Optional[int] = None
    gc_active_fraction: Optional[float] = None
    max_thread_fraction_in_gc: Optional[float] = None
    avg_gc_parallelism: Optional[float] = None
    overflow_events: Optional[int] = None
    overflow_time_ns: Optional[int] = None
    section_gc_times_us: List[int] = field(default_factory=list)
    section_gc_time_avg_us: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


MOUNT_RE = re.compile(r"MOUNT:\s*(?P<label>CSGC-va|mCSGC-8t)\b")
SECTION_GC_TIME_RE = re.compile(
    r"section_gc_time = (\d+) us,\s*from pid=\d+ tgid=\d+ comm="
)
WINDOW_RE = re.compile(
    r"UNMOUNT mCSGC cleanup window: start_ns=(\d+) end_ns=(\d+) window_ns=(\d+)"
)
MIGRATION_RE = re.compile(
    r"UNMOUNT mCSGC migration: valid_blks=(\d+) migrated_data=(\d+)\.(\d{3}) MiB "
    r"valid_blk_rate=(\d+)\.(\d{3}) blocks/s "
    r"migrated_data_rate=(\d+)\.(\d{3}) MiB/s"
)
RECLAIM_RE = re.compile(
    r"UNMOUNT mCSGC reclaim: reclaimed_blks=(\d+) reclaimed_space=(\d+)\.(\d{3}) MiB "
    r"reclaimed_blk_rate=(\d+)\.(\d{3}) blocks/s "
    r"reclaimed_space_rate=(\d+)\.(\d{3}) MiB/s"
)
SECTION_RE = re.compile(
    r"UNMOUNT mCSGC section: processed_sections=(\d+) "
    r"section_rate=(\d+)\.(\d{3}) sections/s"
)
WORK_TIME_RE = re.compile(
    r"UNMOUNT mCSGC\. time\(ns\): idle=(\d+) gc=(\d+) total=(\d+) max_active=(\d+) csgc_called=(\d+)"
)
GC_ACTIVE_RATIO_RE = re.compile(
    r"UNMOUNT mCSGC\. ratio: gc_active_fraction=(\d+)\.(\d+)"
)
MAX_THREAD_RATIO_RE = re.compile(
    r"UNMOUNT mCSGC\. ratio: max_thread_fraction_in_gc=(\d+)\.(\d+)"
)
AVG_PARALLELISM_RE = re.compile(
    r"UNMOUNT mCSGC\. ratio: avg_gc_parallelism=(\d+)\.(\d+)"
)
OVERFLOW_RE = re.compile(
    r"UNMOUNT mCSGC\. overflow: events=(\d+) time\(ns\)=(\d+)"
)
CSGC_STAT_RE = re.compile(
    r"<CSGC STAT> f2fs (?:mcsgc 3\.0|va-csgc) called (?P<called>\d+) times, "
    r"segs freed: (?P<segs_freed>\d+), blocks migrated: (?P<blocks_migrated>\d+), "
    r"bytes read: (?P<bytes_read>\d+), bytes write: (?P<bytes_write>\d+), "
    r"avg blocks migrated (?P<avg_blocks_migrated>\d+), "
    r"avg time\[gc: (?P<avg_gc_ns>\d+) ns, seg migration: (?P<avg_seg_ns>\d+) ns, "
    r"block migration: (?P<avg_block_ns>\d+) ns\]"
    r"(?:, cleanup\[window: (?P<cleanup_window_ns>\d+) ns, "
    r"valid blks: (?P<cleanup_valid_blks>\d+), "
    r"reclaimed blks: (?P<cleanup_reclaimed_blks>\d+), "
    r"(?:sections: (?P<cleanup_sections>\d+), )?"
    r"migration rate: (?P<cleanup_migration_rate_int>\d+)\.(?P<cleanup_migration_rate_frac>\d{3}) MiB/s, "
    r"reclaim rate: (?P<cleanup_reclaim_rate_int>\d+)\.(?P<cleanup_reclaim_rate_frac>\d{3}) MiB/s"
    r"(?:, section rate: (?P<cleanup_section_rate_int>\d+)\.(?P<cleanup_section_rate_frac>\d{3}) sections/s|, section rate: unavailable)?\])?"
)


def decimal_groups_to_float(int_part: str, frac_part: str) -> float:
    return float(f"{int_part}.{frac_part}")


def ns_to_s(ns: int) -> float:
    return ns / 1_000_000_000.0


def blocks_to_mib(blocks: int) -> float:
    return blocks / BLOCKS_PER_MIB


def blocks_per_sec(blocks: int, window_ns: int) -> float:
    if window_ns <= 0:
        return math.nan
    return blocks * 1_000_000_000.0 / window_ns


def mib_per_sec(blocks: int, window_ns: int) -> float:
    return blocks_to_mib(blocks_per_sec(blocks, window_ns))


def ensure_run(current_run: Optional[CleanupRun], line_no: int) -> CleanupRun:
    if current_run is None:
        return CleanupRun(mount_line_no=line_no)
    return current_run


def derive_processed_geometry(valid_blks: int, reclaimed_blks: int) -> dict:
    total_processed = valid_blks + reclaimed_blks
    result = {
        "total_processed": total_processed,
        "processed_segments": None,
        "processed_sections": None,
    }

    if total_processed <= 0:
        return result

    if total_processed % DEFAULT_BLOCKS_PER_SEGMENT != 0:
        return result

    processed_segments = total_processed // DEFAULT_BLOCKS_PER_SEGMENT
    result["processed_segments"] = processed_segments

    if processed_segments <= 0:
        return result

    if processed_segments % DEFAULT_SEGMENTS_PER_SECTION != 0:
        return result

    result["processed_sections"] = (
        processed_segments // DEFAULT_SEGMENTS_PER_SECTION
    )
    return result


def validate_run(run: CleanupRun) -> None:
    if run.section_gc_times_us:
        run.section_gc_time_avg_us = (
            sum(run.section_gc_times_us) / len(run.section_gc_times_us)
        )

    if (
        run.start_ns is not None
        and run.end_ns is not None
        and run.window_ns is not None
    ):
        expected_window_ns = run.end_ns - run.start_ns
        if expected_window_ns != run.window_ns:
            run.warnings.append("window_ns does not match end_ns - start_ns")
    elif any(
        value is not None for value in (run.start_ns, run.end_ns, run.window_ns)
    ):
        run.warnings.append("cleanup window record is incomplete")

    if run.csgc_blocks_migrated is not None and run.valid_blks is not None:
        if run.csgc_blocks_migrated != run.valid_blks:
            run.warnings.append(
                "CSGC STAT blocks migrated differs from cleanup valid_blks"
            )

    if (
        run.csgc_cleanup_window_ns is not None
        and run.window_ns is not None
        and run.csgc_cleanup_window_ns != run.window_ns
    ):
        run.warnings.append(
            "CSGC STAT cleanup window differs from cleanup-window line"
        )

    if run.csgc_cleanup_valid_blks is not None and run.valid_blks is not None:
        if run.csgc_cleanup_valid_blks != run.valid_blks:
            run.warnings.append(
                "CSGC STAT cleanup valid blks differs from migration line"
            )

    if (
        run.csgc_cleanup_reclaimed_blks is not None
        and run.reclaimed_blks is not None
    ):
        if run.csgc_cleanup_reclaimed_blks != run.reclaimed_blks:
            run.warnings.append(
                "CSGC STAT cleanup reclaimed blks differs from reclaim line"
            )

    if run.csgc_cleanup_sections is not None and run.csgc_called is not None:
        if run.csgc_cleanup_sections != run.csgc_called:
            run.warnings.append(
                "CSGC STAT cleanup sections differs from csgc_called"
            )

    section_gc_count = len(run.section_gc_times_us)
    if run.csgc_called is not None and section_gc_count != run.csgc_called:
        run.warnings.append(
            "section_gc_time line count differs from csgc_called"
        )

    if (
        run.csgc_cleanup_sections is not None
        and section_gc_count != run.csgc_cleanup_sections
    ):
        run.warnings.append(
            "section_gc_time line count differs from cleanup sections"
        )

    if run.csgc_called is not None and run.csgc_called > 0 and section_gc_count == 0:
        run.warnings.append("no section_gc_time lines were captured")

    if run.valid_blks is not None and run.reclaimed_blks is not None:
        geometry = derive_processed_geometry(run.valid_blks, run.reclaimed_blks)
        total_processed = geometry["total_processed"]
        processed_segments = geometry["processed_segments"]
        processed_sections = geometry["processed_sections"]

        if total_processed is None or total_processed <= 0:
            run.warnings.append(
                "valid_blks + reclaimed_blks is not a positive total"
            )
            return

        if total_processed % DEFAULT_BLOCKS_PER_SEGMENT != 0:
            run.warnings.append(
                f"valid_blks + reclaimed_blks is not divisible by "
                f"{DEFAULT_BLOCKS_PER_SEGMENT}"
            )
            return

        if processed_segments is None or processed_segments <= 0:
            run.warnings.append(
                "derived processed segment count is not a positive integer"
            )
            return

        if processed_segments % DEFAULT_SEGMENTS_PER_SECTION != 0:
            run.warnings.append(
                f"derived processed segment count is not divisible by "
                f"{DEFAULT_SEGMENTS_PER_SECTION}"
            )
            return

        if processed_sections is None or processed_sections <= 0:
            run.warnings.append(
                "derived processed section count is not a positive integer"
            )
            return

        if (
            run.csgc_cleanup_sections is not None
            and processed_sections != run.csgc_cleanup_sections
        ):
            run.warnings.append(
                "derived processed section count differs from CSGC STAT cleanup sections"
            )

        if run.csgc_called is not None and processed_sections != run.csgc_called:
            run.warnings.append(
                "derived processed section count differs from csgc_called"
            )

        if section_gc_count != processed_sections:
            run.warnings.append(
                "section_gc_time line count differs from derived processed section count"
            )


def parse_log(log_path: Path) -> List[CleanupRun]:
    current_run: Optional[CleanupRun] = None
    runs: List[CleanupRun] = []

    with log_path.open("r", encoding="utf-8", errors="replace") as infile:
        for line_no, line in enumerate(infile, start=1):
            match = MOUNT_RE.search(line)
            if match:
                if current_run is not None:
                    current_run.warnings.append(
                        "new mount marker seen before previous test finished"
                    )
                    runs.append(current_run)
                current_run = CleanupRun(
                    mount_line_no=line_no,
                    mount_label=match.group("label"),
                )
                continue

            match = SECTION_GC_TIME_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.section_gc_times_us.append(int(match.group(1)))
                continue

            match = WORK_TIME_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.gc_idle_ns = int(match.group(1))
                current_run.gc_active_ns = int(match.group(2))
                current_run.gc_total_ns = int(match.group(3))
                current_run.max_active = int(match.group(4))
                current_run.csgc_called = int(match.group(5))
                continue

            match = GC_ACTIVE_RATIO_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.gc_active_fraction = decimal_groups_to_float(
                    match.group(1), match.group(2)
                )
                continue

            match = MAX_THREAD_RATIO_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.max_thread_fraction_in_gc = decimal_groups_to_float(
                    match.group(1), match.group(2)
                )
                continue

            match = AVG_PARALLELISM_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.avg_gc_parallelism = decimal_groups_to_float(
                    match.group(1), match.group(2)
                )
                continue

            match = OVERFLOW_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.overflow_events = int(match.group(1))
                current_run.overflow_time_ns = int(match.group(2))
                continue

            match = WINDOW_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.window_line_no = line_no
                current_run.start_ns = int(match.group(1))
                current_run.end_ns = int(match.group(2))
                current_run.window_ns = int(match.group(3))
                continue

            match = MIGRATION_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.valid_blks = int(match.group(1))
                continue

            match = RECLAIM_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.reclaimed_blks = int(match.group(1))
                continue

            match = SECTION_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.csgc_cleanup_sections = int(match.group(1))
                current_run.csgc_cleanup_section_rate_sections_s = (
                    decimal_groups_to_float(match.group(2), match.group(3))
                )
                continue

            match = CSGC_STAT_RE.search(line)
            if match:
                current_run = ensure_run(current_run, line_no)
                current_run.csgc_stat_line_no = line_no
                current_run.csgc_called = int(match.group("called"))
                current_run.csgc_segs_freed = int(match.group("segs_freed"))
                current_run.csgc_blocks_migrated = int(
                    match.group("blocks_migrated")
                )

                cleanup_window_ns = match.group("cleanup_window_ns")
                if cleanup_window_ns is not None:
                    current_run.csgc_cleanup_window_ns = int(cleanup_window_ns)
                    current_run.csgc_cleanup_valid_blks = int(
                        match.group("cleanup_valid_blks")
                    )
                    current_run.csgc_cleanup_reclaimed_blks = int(
                        match.group("cleanup_reclaimed_blks")
                    )
                    cleanup_sections = match.group("cleanup_sections")
                    if cleanup_sections is not None:
                        current_run.csgc_cleanup_sections = int(
                            cleanup_sections
                        )
                    current_run.csgc_cleanup_migration_rate_mib_s = (
                        decimal_groups_to_float(
                            match.group("cleanup_migration_rate_int"),
                            match.group("cleanup_migration_rate_frac"),
                        )
                    )
                    current_run.csgc_cleanup_reclaim_rate_mib_s = (
                        decimal_groups_to_float(
                            match.group("cleanup_reclaim_rate_int"),
                            match.group("cleanup_reclaim_rate_frac"),
                        )
                    )
                    cleanup_section_rate_int = match.group(
                        "cleanup_section_rate_int"
                    )
                    if cleanup_section_rate_int is not None:
                        current_run.csgc_cleanup_section_rate_sections_s = (
                            decimal_groups_to_float(
                                cleanup_section_rate_int,
                                match.group("cleanup_section_rate_frac"),
                            )
                        )

                runs.append(current_run)
                current_run = None
                continue

    if current_run is not None:
        current_run.warnings.append("log ended before CSGC STAT terminator")
        runs.append(current_run)

    for run in runs:
        validate_run(run)

    return runs


def print_run(run: CleanupRun, run_idx: int) -> None:
    print(f"Run {run_idx}")

    if run.mount_line_no is not None:
        if run.mount_label is not None:
            print(
                f"  mount marker: line {run.mount_line_no} ({run.mount_label})"
            )
        else:
            print(f"  mount marker: line {run.mount_line_no}")

    if (
        run.window_line_no is not None
        and run.start_ns is not None
        and run.end_ns is not None
        and run.window_ns is not None
    ):
        print(f"  cleanup window line: {run.window_line_no}")
        print(f"  cleanup window: {ns_to_s(run.window_ns):.6f} s")
        print(f"  cleanup start/end: {run.start_ns} -> {run.end_ns}")
    else:
        print("  cleanup window: unavailable")

    if run.valid_blks is not None:
        print(
            "  migrated valid data: "
            f"{run.valid_blks} blocks ({blocks_to_mib(run.valid_blks):.3f} MiB)"
        )
        if run.window_ns is not None:
            print(
                "  migrated valid data rate: "
                f"{blocks_per_sec(run.valid_blks, run.window_ns):.3f} blocks/s "
                f"({mib_per_sec(run.valid_blks, run.window_ns):.3f} MiB/s)"
            )

    if run.reclaimed_blks is not None:
        print(
            "  reclaimed space: "
            f"{run.reclaimed_blks} blocks ({blocks_to_mib(run.reclaimed_blks):.3f} MiB)"
        )
        if run.window_ns is not None:
            print(
                "  reclaimed space rate: "
                f"{blocks_per_sec(run.reclaimed_blks, run.window_ns):.3f} blocks/s "
                f"({mib_per_sec(run.reclaimed_blks, run.window_ns):.3f} MiB/s)"
            )

    if run.valid_blks is not None and run.reclaimed_blks is not None:
        total_processed = run.valid_blks + run.reclaimed_blks
        geometry = derive_processed_geometry(run.valid_blks, run.reclaimed_blks)
        reclaim_fraction = 0.0
        migration_overhead = math.inf
        if total_processed > 0:
            reclaim_fraction = run.reclaimed_blks * 100.0 / total_processed
        if run.reclaimed_blks > 0:
            migration_overhead = run.valid_blks / run.reclaimed_blks

        print(
            "  processed victim space: "
            f"{total_processed} blocks ({blocks_to_mib(total_processed):.3f} MiB)"
        )
        if geometry["processed_segments"] is not None:
            print(
                "  derived processed segments "
                f"(assuming {DEFAULT_BLOCKS_PER_SEGMENT} blocks/segment): "
                f"{geometry['processed_segments']}"
            )
        if geometry["processed_sections"] is not None:
            print(
                "  derived processed sections "
                f"(assuming {DEFAULT_SEGMENTS_PER_SECTION} segments/section): "
                f"{geometry['processed_sections']}"
            )
        if run.csgc_cleanup_sections is not None:
            print(f"  logged processed sections: {run.csgc_cleanup_sections}")
        if run.csgc_cleanup_section_rate_sections_s is not None:
            print(
                "  logged section cleanup rate: "
                f"{run.csgc_cleanup_section_rate_sections_s:.3f} sections/s"
            )

        print(
            f"  reclaimed fraction in processed victim space: {reclaim_fraction:.2f}%"
        )
        if math.isfinite(migration_overhead):
            print(
                "  migration overhead: "
                f"{migration_overhead:.3f} migrated valid blocks per reclaimed block"
            )
        else:
            print("  migration overhead: inf (no reclaimed blocks)")

    if run.section_gc_times_us:
        print(f"  section_gc_time samples: {len(run.section_gc_times_us)}")
        print(
            "  average section_gc_time: "
            f"{run.section_gc_time_avg_us:.3f} us"
        )

    if run.gc_active_fraction is not None:
        print(f"  GC active fraction: {run.gc_active_fraction:.5f}")
    if run.avg_gc_parallelism is not None:
        print(f"  average GC parallelism: {run.avg_gc_parallelism:.3f}")
    if run.max_active is not None:
        print(f"  max active workers: {run.max_active}")
    if run.csgc_called is not None:
        print(f"  csgc called: {run.csgc_called}")
    if run.csgc_segs_freed is not None:
        print(f"  csgc segs freed: {run.csgc_segs_freed}")
    if run.overflow_events is not None:
        print(f"  overflow events: {run.overflow_events}")
    if run.overflow_time_ns is not None:
        print(f"  overflow time: {ns_to_s(run.overflow_time_ns):.6f} s")

    if run.warnings:
        print("  warnings:")
        for warning in run.warnings:
            print(f"    - {warning}")


def print_summary(runs: List[CleanupRun]) -> None:
    completed_runs = [run for run in runs if run.csgc_stat_line_no is not None]
    cleanup_complete_runs = [
        run
        for run in completed_runs
        if run.window_ns is not None
        and run.valid_blks is not None
        and run.reclaimed_blks is not None
    ]
    all_section_gc_times_us = [
        value for run in completed_runs for value in run.section_gc_times_us
    ]
    runs_with_called = [
        run for run in completed_runs if run.csgc_called is not None
    ]

    print()
    print(f"Found {len(runs)} test record(s)")
    print(f"Completed tests terminated by CSGC STAT: {len(completed_runs)}")
    print(
        "Completed tests with cleanup window and reclaim data: "
        f"{len(cleanup_complete_runs)}"
    )

    if runs_with_called:
        total_called = sum(
            run.csgc_called for run in runs_with_called if run.csgc_called is not None
        )
        print(f"Aggregate csgc called: {total_called}")

    if all_section_gc_times_us:
        avg_section_gc_time_us = (
            sum(all_section_gc_times_us) / len(all_section_gc_times_us)
        )
        print(
            "Aggregate section_gc_time samples: "
            f"{len(all_section_gc_times_us)}"
        )
        print(
            "Aggregate average section_gc_time: "
            f"{avg_section_gc_time_us:.3f} us"
        )

    if not cleanup_complete_runs:
        return

    total_window_ns = sum(
        run.window_ns for run in cleanup_complete_runs if run.window_ns is not None
    )
    total_valid_blks = sum(
        run.valid_blks
        for run in cleanup_complete_runs
        if run.valid_blks is not None
    )
    total_reclaimed_blks = sum(
        run.reclaimed_blks
        for run in cleanup_complete_runs
        if run.reclaimed_blks is not None
    )
    total_processed = total_valid_blks + total_reclaimed_blks
    geometry = derive_processed_geometry(total_valid_blks, total_reclaimed_blks)

    print()
    print("Aggregate cleanup summary")
    print(f"  aggregate cleanup window: {ns_to_s(total_window_ns):.6f} s")
    print(
        "  aggregate migrated valid data: "
        f"{total_valid_blks} blocks ({blocks_to_mib(total_valid_blks):.3f} MiB)"
    )
    print(
        "  aggregate reclaimed space: "
        f"{total_reclaimed_blks} blocks ({blocks_to_mib(total_reclaimed_blks):.3f} MiB)"
    )
    print(
        "  aggregate migrated valid data rate: "
        f"{blocks_per_sec(total_valid_blks, total_window_ns):.3f} blocks/s "
        f"({mib_per_sec(total_valid_blks, total_window_ns):.3f} MiB/s)"
    )
    print(
        "  aggregate reclaimed space rate: "
        f"{blocks_per_sec(total_reclaimed_blks, total_window_ns):.3f} blocks/s "
        f"({mib_per_sec(total_reclaimed_blks, total_window_ns):.3f} MiB/s)"
    )
    print(
        "  aggregate processed victim space: "
        f"{total_processed} blocks ({blocks_to_mib(total_processed):.3f} MiB)"
    )

    if geometry["processed_segments"] is not None:
        print(
            "  aggregate derived processed segments "
            f"(assuming {DEFAULT_BLOCKS_PER_SEGMENT} blocks/segment): "
            f"{geometry['processed_segments']}"
        )

    if geometry["processed_sections"] is not None:
        print(
            "  aggregate derived processed sections "
            f"(assuming {DEFAULT_SEGMENTS_PER_SECTION} segments/section): "
            f"{geometry['processed_sections']}"
        )
        print(
            "  aggregate section cleanup rate: "
            f"{geometry['processed_sections'] * 1_000_000_000.0 / total_window_ns:.3f} "
            "sections/s"
        )

    if total_processed > 0:
        reclaim_fraction = total_reclaimed_blks * 100.0 / total_processed
        print(f"  aggregate reclaimed fraction: {reclaim_fraction:.2f}%")

    if total_reclaimed_blks > 0:
        migration_overhead = total_valid_blks / total_reclaimed_blks
        print(
            "  aggregate migration overhead: "
            f"{migration_overhead:.3f} migrated valid blocks per reclaimed block"
        )

    best_run_idx = max(
        range(len(cleanup_complete_runs)),
        key=lambda idx: mib_per_sec(
            cleanup_complete_runs[idx].reclaimed_blks,
            cleanup_complete_runs[idx].window_ns,
        ),
    )
    best_run = cleanup_complete_runs[best_run_idx]
    print(
        "  best reclaimed-space run: "
        f"Run {runs.index(best_run) + 1} "
        f"({mib_per_sec(best_run.reclaimed_blks, best_run.window_ns):.3f} MiB/s)"
    )


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse mCSGC cleanup, reclaim, and section timing statistics "
            "from a dmesg log file."
        )
    )
    parser.add_argument(
        "log_file",
        type=Path,
        help="Path to the dmesg log file collected during the experiment.",
    )
    return parser


def main() -> int:
    parser = build_argparser()
    args = parser.parse_args()

    if not args.log_file.is_file():
        print(f"error: log file not found: {args.log_file}", file=sys.stderr)
        return 1

    runs = parse_log(args.log_file)
    if not runs:
        print("No mCSGC test records were found in the log.", file=sys.stderr)
        return 2

    print(f"Input log: {args.log_file}")
    print()

    for idx, run in enumerate(runs, start=1):
        print_run(run, idx)
        if idx != len(runs):
            print()

    print_summary(runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
