#!/usr/bin/env python3
import argparse
import errno
import math
import os
import re
import sys
from collections import Counter, defaultdict
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple


TRACE_KIND = "csgc"
TRACE_LABEL = "CSGC"
RE_TRACE = re.compile(r"CSGC_HEAVY_TRACE\s+(?P<kv>.*)$")
RE_STAT = re.compile(r"CSGC_HEAVY_STAT\s+(?P<kv>.*)$")
RE_TRIGGER_STAT = re.compile(r"F2FS_GC_TRIGGER_STAT\s+(?P<kv>.*)$")
RE_MEASUREMENT_STAT = re.compile(r"F2FS_GC_MEASUREMENT_STAT\s+(?P<kv>.*)$")
RE_VICTIM_STAT = re.compile(r"F2FS_GC_VICTIM_STAT\s+(?P<kv>.*)$")
RE_MEASUREMENT_BOUNDARY = re.compile(
    r"F2FS_GC_MEASUREMENT_BOUNDARY\s+(?P<kv>.*)$"
)
PHASES = ("section", "pre", "ssd", "post")
PHASE_START_END = {
    "section": ("SECTION_START", "SECTION_END"),
    "pre": ("PRE_START", "PRE_END"),
    "ssd": ("SSD_START", "SSD_END"),
    "post": ("POST_START", "POST_END"),
}
F2FS_GC_COLLECTOR_COUNT_FIELDS = (
    "csgc_data_sections",
    "origc_data_sections",
    "origc_node_sections",
)
F2FS_GC_COLLECTOR_TIME_FIELDS = (
    "csgc_data_time_us",
    "origc_data_time_us",
    "origc_node_time_us",
)
F2FS_GC_COLLECTOR_BASE_FIELDS = (
    F2FS_GC_COLLECTOR_COUNT_FIELDS + F2FS_GC_COLLECTOR_TIME_FIELDS
)
F2FS_GC_COLLECTOR_BLOCK_FIELDS = (
    "csgc_data_victim_valid_blocks",
    "origc_data_victim_valid_blocks",
    "origc_node_victim_valid_blocks",
    "csgc_data_migrated_blocks",
    "origc_data_migrated_blocks",
    "origc_node_migrated_blocks",
)
F2FS_GC_COLLECTOR_FIELDS = (
    F2FS_GC_COLLECTOR_BASE_FIELDS + F2FS_GC_COLLECTOR_BLOCK_FIELDS
)
F2FS_GC_LOCK_START_FIELDS = (
    "gc_lock_wait_tracked",
    "gc_lock_wait_us",
    "gc_lock_acquire_to_call_us",
)
F2FS_GC_DEMAND_START_FIELDS = (
    "gc_demand_tracked",
    "gc_demand_to_call_us",
)
F2FS_GC_TRIGGER_COUNTER_FIELDS = (
    "demands",
    "delegated",
    "blocking_attempts",
    "blocking_acquired",
    "trylock_attempts",
    "trylock_acquired",
    "trylock_failed",
    "calls_started",
    "calls_completed",
    "calls_with_victim",
    "calls_no_victim",
    "calls_negative_ret",
)
F2FS_GC_LOCK_END_FIELDS = (
    "gc_lock_wait_tracked",
    "gc_call_pre_unlock_us",
    "gc_lock_held_us",
    "gc_call_post_unlock_us",
)
GC_RELATED_GAP_TOP_COUNT = 20
GC_RELATED_GAP_THRESHOLDS_US = (1000, 10000, 100000, 1000000)


def configure_trace_kind(kind: str) -> None:
    """Select trace labels and phase definitions for one GC implementation."""
    global TRACE_KIND, TRACE_LABEL, RE_TRACE, RE_STAT, PHASES, PHASE_START_END

    TRACE_KIND = kind
    if kind == "f2fs_gc":
        TRACE_LABEL = "F2FS_GC"
        PHASES = ("gc_call",)
        PHASE_START_END = {
            "gc_call": ("GC_START", "GC_END"),
        }
    elif kind == "origc":
        TRACE_LABEL = "ORIGC"
        PHASES = ("gc_call", "section", "data", "node")
        PHASE_START_END = {
            "gc_call": ("GC_START", "GC_END"),
            "section": ("SECTION_START", "SECTION_END"),
            "data": ("SECTION_START", "SECTION_END"),
            "node": ("SECTION_START", "SECTION_END"),
        }
    else:
        TRACE_LABEL = "CSGC"
        PHASES = ("section", "pre", "ssd", "post")
        PHASE_START_END = {
            "section": ("SECTION_START", "SECTION_END"),
            "pre": ("PRE_START", "PRE_END"),
            "ssd": ("SSD_START", "SSD_END"),
            "post": ("POST_START", "POST_END"),
        }

    RE_TRACE = re.compile(rf"{TRACE_LABEL}_HEAVY_TRACE\s+(?P<kv>.*)$")
    RE_STAT = re.compile(rf"{TRACE_LABEL}_HEAVY_STAT\s+(?P<kv>.*)$")


Trace = Dict[str, object]


def trace_time_us(trace: Trace) -> int:
    """Use the epoch-relative timestamp when available, else legacy t_us."""
    epoch_t_us = int_or_none(trace.get("epoch_t_us"))
    return epoch_t_us if epoch_t_us is not None else int(trace["t_us"])


def parse_kv_blob(blob: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for part in blob.strip().split():
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key:
            kv[key] = value
    return kv


def int_or_none(value: object) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def float_or_none(value: object) -> Optional[float]:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def format_float(value: float, digits: int = 3) -> str:
    if math.isnan(value):
        return "nan"
    return f"{value:.{digits}f}"


def percentile(values: List[int], pct: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])

    sorted_values = sorted(values)
    pos = (len(sorted_values) - 1) * pct / 100.0
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    weight = pos - lo
    return sorted_values[lo] * (1.0 - weight) + sorted_values[hi] * weight


def top_fraction_mean(values: List[int], fraction: float) -> float:
    if not values:
        return float("nan")
    count = max(1, int(math.ceil(len(values) * fraction)))
    return sum(sorted(values)[-count:]) / float(count)


def summarize_values(name: str, values: List[int]) -> str:
    if not values:
        return (
            f"{name}: n=0 mean=nan min=nan max=nan median=nan "
            f"p80=nan p95=nan p99=nan top20_mean=nan sum=0"
        )

    total = sum(values)
    mean = total / float(len(values))
    return (
        f"{name}: n={len(values)} mean={format_float(mean)} "
        f"min={min(values)} max={max(values)} "
        f"median={format_float(percentile(values, 50.0))} "
        f"p80={format_float(percentile(values, 80.0))} "
        f"p95={format_float(percentile(values, 95.0))} "
        f"p99={format_float(percentile(values, 99.0))} "
        f"top20_mean={format_float(top_fraction_mean(values, 0.20))} "
        f"sum={total}"
    )


def parse_input(
    path: str,
) -> Tuple[
    List[Trace],
    List[Dict[str, str]],
    List[str],
    List[Dict[str, str]],
    List[str],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
]:
    traces: List[Trace] = []
    stats: List[Dict[str, str]] = []
    raw_stats: List[str] = []
    trigger_stats: List[Dict[str, str]] = []
    raw_trigger_stats: List[str] = []
    measurement_stats: List[Dict[str, str]] = []
    victim_stats: List[Dict[str, str]] = []
    measurement_boundaries: List[Dict[str, str]] = []

    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for lineno, line in enumerate(fp, 1):
            line = line.rstrip("\n")

            match = RE_TRACE.search(line)
            if match:
                kv = parse_kv_blob(match.group("kv"))
                t_us = int_or_none(kv.get("t_us"))
                event = kv.get("event")
                call_id = int_or_none(kv.get("call_id"))
                if TRACE_KIND == "f2fs_gc":
                    if t_us is None or event is None or call_id is None:
                        continue
                    section = -1
                    segno = int_or_none(kv.get("victim_segno"))
                    if segno is None:
                        segno = -1
                    req_idx = call_id
                else:
                    section = int_or_none(kv.get("section"))
                    segno = int_or_none(kv.get("segno"))
                    req_idx = int_or_none(kv.get("req_idx"))
                    if (
                        t_us is None
                        or event is None
                        or section is None
                        or segno is None
                        or req_idx is None
                    ):
                        continue
                traces.append(
                    {
                        "lineno": lineno,
                        "t_us": t_us,
                        "epoch_t_us": int_or_none(kv.get("epoch_t_us")),
                        "epoch": int_or_none(kv.get("epoch")) or 0,
                        "scope": kv.get("scope", "legacy"),
                        "event": event,
                        "section": section,
                        "segno": segno,
                        "req_idx": req_idx,
                        "call_id": call_id,
                        "source": kv.get("source", ""),
                        "mode": kv.get("mode", ""),
                        "path": kv.get("path", ""),
                        "ret": int_or_none(kv.get("ret")),
                        "init_gc_type": int_or_none(kv.get("init_gc_type")),
                        "final_gc_type": int_or_none(kv.get("final_gc_type")),
                        "total_freed": int_or_none(kv.get("total_freed")),
                        "sec_freed": int_or_none(kv.get("sec_freed")),
                        "csgc_data_sections": int_or_none(
                            kv.get("csgc_data_sections")
                        ),
                        "origc_data_sections": int_or_none(
                            kv.get("origc_data_sections")
                        ),
                        "origc_node_sections": int_or_none(
                            kv.get("origc_node_sections")
                        ),
                        "csgc_data_time_us": int_or_none(
                            kv.get("csgc_data_time_us")
                        ),
                        "origc_data_time_us": int_or_none(
                            kv.get("origc_data_time_us")
                        ),
                        "origc_node_time_us": int_or_none(
                            kv.get("origc_node_time_us")
                        ),
                        "csgc_data_victim_valid_blocks": int_or_none(
                            kv.get("csgc_data_victim_valid_blocks")
                        ),
                        "origc_data_victim_valid_blocks": int_or_none(
                            kv.get("origc_data_victim_valid_blocks")
                        ),
                        "origc_node_victim_valid_blocks": int_or_none(
                            kv.get("origc_node_victim_valid_blocks")
                        ),
                        "csgc_data_migrated_blocks": int_or_none(
                            kv.get("csgc_data_migrated_blocks")
                        ),
                        "origc_data_migrated_blocks": int_or_none(
                            kv.get("origc_data_migrated_blocks")
                        ),
                        "origc_node_migrated_blocks": int_or_none(
                            kv.get("origc_node_migrated_blocks")
                        ),
                        "gc_lock_wait_tracked": int_or_none(
                            kv.get("gc_lock_wait_tracked")
                        ),
                        "gc_demand_tracked": int_or_none(
                            kv.get("gc_demand_tracked")
                        ),
                        "gc_demand_to_call_us": int_or_none(
                            kv.get("gc_demand_to_call_us")
                        ),
                        "gc_lock_wait_us": int_or_none(
                            kv.get("gc_lock_wait_us")
                        ),
                        "gc_lock_acquire_to_call_us": int_or_none(
                            kv.get("gc_lock_acquire_to_call_us")
                        ),
                        "gc_call_pre_unlock_us": int_or_none(
                            kv.get("gc_call_pre_unlock_us")
                        ),
                        "gc_lock_held_us": int_or_none(
                            kv.get("gc_lock_held_us")
                        ),
                        "gc_call_post_unlock_us": int_or_none(
                            kv.get("gc_call_post_unlock_us")
                        ),
                        "seg_type": kv.get("seg_type", ""),
                        "pid": int_or_none(kv.get("pid")),
                        "comm": kv.get("comm", ""),
                        "cpu": int_or_none(kv.get("cpu")),
                    }
                )
                continue

            match = RE_STAT.search(line)
            if match:
                kv = parse_kv_blob(match.group("kv"))
                # Normalize legacy unified-GC logs to the unambiguous field name.
                if (
                    TRACE_KIND == "f2fs_gc"
                    and "f2fs_gc_call_max_active" not in kv
                    and "max_active" in kv
                ):
                    kv["f2fs_gc_call_max_active"] = kv.pop("max_active")
                if kv:
                    stats.append(kv)
                    raw_stats.append(match.group(0))

            if TRACE_KIND == "f2fs_gc":
                match = RE_TRIGGER_STAT.search(line)
                if match:
                    kv = parse_kv_blob(match.group("kv"))
                    if kv.get("source"):
                        trigger_stats.append(kv)
                        raw_trigger_stats.append(match.group(0))

                match = RE_MEASUREMENT_STAT.search(line)
                if match:
                    kv = parse_kv_blob(match.group("kv"))
                    if kv:
                        measurement_stats.append(kv)

                match = RE_VICTIM_STAT.search(line)
                if match:
                    kv = parse_kv_blob(match.group("kv"))
                    if kv:
                        victim_stats.append(kv)

                match = RE_MEASUREMENT_BOUNDARY.search(line)
                if match:
                    kv = parse_kv_blob(match.group("kv"))
                    if kv:
                        measurement_boundaries.append(kv)

    return (
        traces,
        stats,
        raw_stats,
        trigger_stats,
        raw_trigger_stats,
        measurement_stats,
        victim_stats,
        measurement_boundaries,
    )


def row_epoch(row: Dict[str, object]) -> int:
    """Return an explicit epoch id, treating old logs as epoch zero."""
    value = int_or_none(row.get("epoch"))
    return value if value is not None else 0


def row_scope(row: Dict[str, object]) -> str:
    """Return the measurement scope, preserving compatibility with old logs."""
    return str(row.get("scope") or "legacy")


def select_measurement_epoch(
    traces: List[Trace],
    stats: List[Dict[str, str]],
    trigger_stats: List[Dict[str, str]],
    measurement_stats: List[Dict[str, str]],
    victim_stats: List[Dict[str, str]],
) -> Tuple[
    int,
    str,
    List[int],
    List[Trace],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
]:
    """Prefer the latest explicit workload epoch; otherwise analyze legacy data."""
    rows: List[Dict[str, object]] = []
    rows.extend(traces)
    rows.extend(stats)
    rows.extend(trigger_stats)
    rows.extend(measurement_stats)
    rows.extend(victim_stats)

    available_epochs = sorted({row_epoch(row) for row in rows}) or [0]
    workload_epochs = sorted(
        {row_epoch(row) for row in rows if row_scope(row) == "workload"}
    )
    if workload_epochs:
        selected_epoch = workload_epochs[-1]
        selected_scope = "workload"
    else:
        selected_epoch = available_epochs[-1]
        scopes = {
            row_scope(row)
            for row in rows
            if row_epoch(row) == selected_epoch
        }
        selected_scope = next(iter(scopes)) if len(scopes) == 1 else "legacy"

    def selected(row: Dict[str, object]) -> bool:
        if row_epoch(row) != selected_epoch:
            return False
        if selected_scope == "workload":
            return row_scope(row) == "workload"
        return True

    return (
        selected_epoch,
        selected_scope,
        available_epochs,
        [row for row in traces if selected(row)],
        [row for row in stats if selected(row)],
        [row for row in trigger_stats if selected(row)],
        [row for row in measurement_stats if selected(row)],
        [row for row in victim_stats if selected(row)],
    )


def phase_key(phase: str, trace: Trace) -> Tuple[int, ...]:
    epoch = row_epoch(trace)
    if TRACE_KIND == "f2fs_gc":
        return (epoch, int(trace["call_id"]))
    if TRACE_KIND == "origc":
        if phase == "gc_call":
            return (epoch, 0)
        return (epoch, int(trace["section"]))
    if phase == "section":
        return (epoch, int(trace["section"]))
    return (
        epoch,
        int(trace["section"]),
        int(trace["segno"]),
        int(trace["req_idx"]),
    )


def trace_matches_phase(phase: str, trace: Trace) -> bool:
    """Filter shared ORIGC section events into data and node phases."""
    if TRACE_KIND != "origc" or phase not in ("data", "node"):
        return True
    return str(trace.get("seg_type", "")) == phase


def build_phase_intervals(
    traces: List[Trace],
) -> Tuple[
    Dict[str, Dict[Tuple[int, ...], List[int]]],
    Dict[str, List[Tuple[int, int]]],
    Dict[str, Dict[str, int]],
]:
    starts: Dict[str, DefaultDict[Tuple[int, ...], List[int]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    intervals: Dict[str, DefaultDict[Tuple[int, ...], List[int]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    windows: Dict[str, List[Tuple[int, int]]] = {phase: [] for phase in PHASES}
    diagnostics: Dict[str, Dict[str, int]] = {
        phase: {
            "unmatched_starts": 0,
            "unmatched_ends": 0,
            "negative_durations": 0,
        }
        for phase in PHASES
    }

    event_to_phases: DefaultDict[str, List[Tuple[str, str]]] = defaultdict(list)
    for phase, (start_event, end_event) in PHASE_START_END.items():
        event_to_phases[start_event].append((phase, "start"))
        event_to_phases[end_event].append((phase, "end"))

    for trace in traces:
        event = str(trace["event"])
        if event not in event_to_phases:
            continue
        for phase, kind in event_to_phases[event]:
            if not trace_matches_phase(phase, trace):
                continue
            key = phase_key(phase, trace)
            t_us = trace_time_us(trace)

            if kind == "start":
                starts[phase][key].append(t_us)
                continue

            if not starts[phase][key]:
                diagnostics[phase]["unmatched_ends"] += 1
                continue

            start = starts[phase][key].pop()
            duration = t_us - start
            if duration < 0:
                diagnostics[phase]["negative_durations"] += 1
                continue
            intervals[phase][key].append(duration)
            windows[phase].append((start, t_us))

    for phase in PHASES:
        diagnostics[phase]["unmatched_starts"] = sum(
            len(v) for v in starts[phase].values()
        )

    return (
        {phase: dict(intervals[phase]) for phase in PHASES},
        windows,
        diagnostics,
    )


def flatten_intervals(intervals: Dict[Tuple[int, ...], List[int]]) -> List[int]:
    values: List[int] = []
    for durations in intervals.values():
        values.extend(durations)
    return values


def build_f2fs_gc_call_records(traces: List[Trace]) -> List[Dict[str, object]]:
    """Pair unified f2fs_gc call events and retain end-of-call classification."""
    starts: Dict[Tuple[int, int], Trace] = {}
    records: List[Dict[str, object]] = []

    for trace in traces:
        event = str(trace["event"])
        call_id = int_or_none(trace.get("call_id"))
        if call_id is None:
            continue
        key = (row_epoch(trace), call_id)
        if event == "GC_START":
            starts[key] = trace
            continue
        if event != "GC_END" or key not in starts:
            continue

        start = starts.pop(key)
        duration_us = trace_time_us(trace) - trace_time_us(start)
        if duration_us < 0:
            continue
        records.append(
            {
                "call_id": call_id,
                "epoch": row_epoch(trace),
                "scope": row_scope(trace),
                "start_t_us": trace_time_us(start),
                "end_t_us": trace_time_us(trace),
                "start_lineno": int(start["lineno"]),
                "end_lineno": int(trace["lineno"]),
                "duration_us": duration_us,
                "mode": str(trace.get("mode") or start.get("mode") or "unknown"),
                "source": str(start.get("source") or "unknown"),
                "path": str(trace.get("path") or "unknown"),
                "ret": trace.get("ret"),
                "init_gc_type": start.get("init_gc_type"),
                "final_gc_type": trace.get("final_gc_type"),
                "total_freed": trace.get("total_freed"),
                "sec_freed": trace.get("sec_freed"),
                "comm": str(start.get("comm") or ""),
                "gc_demand_tracked": start.get("gc_demand_tracked"),
                "gc_demand_to_call_us": start.get(
                    "gc_demand_to_call_us"
                ),
                "gc_lock_wait_tracked": start.get("gc_lock_wait_tracked"),
                "gc_lock_wait_tracked_end": trace.get(
                    "gc_lock_wait_tracked"
                ),
                "gc_lock_wait_us": start.get("gc_lock_wait_us"),
                "gc_lock_acquire_to_call_us": start.get(
                    "gc_lock_acquire_to_call_us"
                ),
                "gc_call_pre_unlock_us": trace.get(
                    "gc_call_pre_unlock_us"
                ),
                "gc_lock_held_us": trace.get("gc_lock_held_us"),
                "gc_call_post_unlock_us": trace.get(
                    "gc_call_post_unlock_us"
                ),
                **{
                    field: trace.get(field)
                    for field in F2FS_GC_COLLECTOR_FIELDS
                },
            }
        )

    return records


def gc_call_has_victim(record: Dict[str, object]) -> Optional[bool]:
    """Return whether one completed f2fs_gc call selected a victim."""
    path = str(record.get("path") or "unknown")
    if path == "no_victim":
        return False
    if path in ("csgc", "origc", "mixed"):
        return True
    return None


def gc_call_completion_reason(record: Dict[str, object]) -> str:
    """Classify whether a call did work or exited before/after victim search."""
    has_victim = gc_call_has_victim(record)
    if has_victim is True:
        return "with_victim"
    if has_victim is None:
        return "unknown"

    final_gc_type = int_or_none(record.get("final_gc_type"))
    ret = int_or_none(record.get("ret"))
    if final_gc_type == 0 and ret == -errno.EINVAL:
        return "bg_no_foreground_work"
    if final_gc_type == 1 and ret == -errno.ENODATA:
        return "fg_victim_search_enodata"
    return "other_no_victim"


def emit_f2fs_gc_wait_outcome_breakdown(
    out,
    records: List[Dict[str, object]],
    epoch_elapsed_us: Optional[int],
) -> None:
    """Cross-classify lock waiting and victim selection outcomes."""
    out.write("=== f2fs_gc wait/victim outcome breakdown ===\n")
    if not records:
        out.write("gc_wait_outcome_breakdown=unavailable\n")
        return

    buckets: DefaultDict[
        Tuple[str, str], List[Dict[str, object]]
    ] = defaultdict(list)
    reason_buckets: DefaultDict[str, List[Dict[str, object]]] = defaultdict(list)
    for record in records:
        tracked = (
            record.get("gc_lock_wait_tracked") is not None
            and int(record["gc_lock_wait_tracked"]) == 1
            and record.get("gc_lock_wait_us") is not None
        )
        if not tracked:
            wait_state = "untracked"
        elif int(record["gc_lock_wait_us"]) > 0:
            wait_state = "waited"
        else:
            wait_state = "not_waited"

        has_victim = gc_call_has_victim(record)
        victim_state = (
            "with_victim"
            if has_victim is True
            else "no_victim"
            if has_victim is False
            else "unknown_victim"
        )
        buckets[(wait_state, victim_state)].append(record)
        reason_buckets[gc_call_completion_reason(record)].append(record)

    total_calls = len(records)
    waited_known = sum(
        len(bucket)
        for (wait_state, victim_state), bucket in buckets.items()
        if wait_state == "waited"
        and victim_state in ("with_victim", "no_victim")
    )
    waited_with_victim = len(buckets[("waited", "with_victim")])
    victim_time_us = sum(
        int(record["duration_us"])
        for (wait_state, victim_state), bucket in buckets.items()
        if victim_state == "with_victim"
        for record in bucket
    )
    no_victim_time_us = sum(
        int(record["duration_us"])
        for (wait_state, victim_state), bucket in buckets.items()
        if victim_state == "no_victim"
        for record in bucket
    )
    out.write(f"gc_wait_outcome_calls={total_calls}\n")
    out.write(
        "gc_waited_victim_hit_rate="
        + (
            format_float(waited_with_victim / float(waited_known), 6)
            if waited_known
            else "nan"
        )
        + "\n"
    )
    out.write(f"gc_with_victim_call_time_us={victim_time_us}\n")
    out.write(f"gc_no_victim_call_time_us={no_victim_time_us}\n")
    if epoch_elapsed_us and epoch_elapsed_us > 0:
        out.write(
            "gc_with_victim_call_time_fraction_of_epoch="
            f"{format_float(victim_time_us / float(epoch_elapsed_us), 6)}\n"
        )
        out.write(
            "gc_no_victim_call_time_fraction_of_epoch="
            f"{format_float(no_victim_time_us / float(epoch_elapsed_us), 6)}\n"
        )
    else:
        out.write("gc_with_victim_call_time_fraction_of_epoch=unavailable\n")
        out.write("gc_no_victim_call_time_fraction_of_epoch=unavailable\n")

    for reason in (
        "with_victim",
        "bg_no_foreground_work",
        "fg_victim_search_enodata",
        "other_no_victim",
        "unknown",
    ):
        bucket = reason_buckets.get(reason, [])
        duration_sum_us = sum(int(record["duration_us"]) for record in bucket)
        waited_calls = sum(
            1
            for record in bucket
            if record.get("gc_lock_wait_tracked") is not None
            and int(record["gc_lock_wait_tracked"]) == 1
            and record.get("gc_lock_wait_us") is not None
            and int(record["gc_lock_wait_us"]) > 0
        )
        out.write(
            f"gc_completion_reason={reason} calls={len(bucket)} "
            f"fraction_of_all_calls={format_float(len(bucket) / float(total_calls), 6)} "
            f"waited_calls={waited_calls} duration_sum_us={duration_sum_us}\n"
        )

    for wait_state in ("waited", "not_waited", "untracked"):
        for victim_state in ("with_victim", "no_victim", "unknown_victim"):
            bucket = buckets.get((wait_state, victim_state), [])
            if not bucket and victim_state == "unknown_victim":
                continue
            prefix = f"gc_outcome_{wait_state}_{victim_state}"
            duration_values = [int(record["duration_us"]) for record in bucket]
            wait_values = [
                int(record["gc_lock_wait_us"])
                for record in bucket
                if record.get("gc_lock_wait_us") is not None
            ]
            collector_sections = sum(
                sum(
                    int(record.get(field) or 0)
                    for field in F2FS_GC_COLLECTOR_COUNT_FIELDS
                )
                for record in bucket
            )
            sections_freed = sum(
                int(record.get("sec_freed") or 0) for record in bucket
            )
            out.write(
                f"wait_state={wait_state} victim_state={victim_state} "
                f"calls={len(bucket)} "
                f"fraction_of_all_calls={format_float(len(bucket) / float(total_calls), 6)} "
                f"duration_sum_us={sum(duration_values)} "
                f"lock_wait_sum_us={sum(wait_values)} "
                f"collector_sections={collector_sections} "
                f"sections_freed={sections_freed}\n"
            )
            out.write(summarize_values(f"{prefix}_duration_us", duration_values) + "\n")
            if wait_values:
                out.write(summarize_values(f"{prefix}_lock_wait_us", wait_values) + "\n")


def emit_f2fs_gc_collector_breakdown(
    out,
    records: List[Dict[str, object]],
    epoch_elapsed_us: Optional[int],
) -> None:
    """Summarize data/node collector coverage from complete GC_END records."""
    complete = [
        record
        for record in records
        if all(
            record.get(field) is not None
            for field in F2FS_GC_COLLECTOR_BASE_FIELDS
        )
    ]
    block_complete = [
        record
        for record in complete
        if all(
            record.get(field) is not None
            for field in F2FS_GC_COLLECTOR_BLOCK_FIELDS
        )
    ]

    out.write("=== f2fs_gc collector path breakdown ===\n")
    out.write(f"collector_breakdown_calls={len(complete)}\n")
    out.write(f"collector_breakdown_missing_calls={len(records) - len(complete)}\n")
    if not complete:
        out.write("collector_path_breakdown=unavailable\n")
        return

    totals = {
        field: sum(int(record[field]) for record in complete)
        for field in F2FS_GC_COLLECTOR_BASE_FIELDS
    }
    for field in F2FS_GC_COLLECTOR_BASE_FIELDS:
        values = [int(record[field]) for record in complete]
        out.write(summarize_values(f"{field}_per_call", values) + "\n")
        out.write(f"{field}={totals[field]}\n")

    total_sections = sum(totals[field] for field in F2FS_GC_COLLECTOR_COUNT_FIELDS)
    total_data_sections = (
        totals["csgc_data_sections"] + totals["origc_data_sections"]
    )
    total_collector_time_us = sum(
        totals[field] for field in F2FS_GC_COLLECTOR_TIME_FIELDS
    )
    f2fs_gc_call_time_sum_us = sum(
        int(record["duration_us"]) for record in complete
    )
    non_collector_time_us = f2fs_gc_call_time_sum_us - total_collector_time_us

    out.write(f"total_collector_sections={total_sections}\n")
    out.write(f"total_data_sections={total_data_sections}\n")
    out.write(f"total_collector_time_us={total_collector_time_us}\n")
    out.write(f"f2fs_gc_call_time_sum_us={f2fs_gc_call_time_sum_us}\n")
    out.write(f"non_collector_f2fs_gc_time_us={non_collector_time_us}\n")
    if epoch_elapsed_us and epoch_elapsed_us > 0:
        out.write(
            "collector_time_fraction_of_epoch="
            f"{format_float(total_collector_time_us / float(epoch_elapsed_us), 6)}\n"
        )
        out.write(
            "non_collector_f2fs_gc_time_fraction_of_epoch="
            f"{format_float(non_collector_time_us / float(epoch_elapsed_us), 6)}\n"
        )
    else:
        out.write("collector_time_fraction_of_epoch=unavailable\n")
        out.write("non_collector_f2fs_gc_time_fraction_of_epoch=unavailable\n")

    ratios = {
        "csgc_data_section_fraction_of_all_sections": (
            totals["csgc_data_sections"], total_sections
        ),
        "origc_data_section_fraction_of_all_sections": (
            totals["origc_data_sections"], total_sections
        ),
        "origc_node_section_fraction_of_all_sections": (
            totals["origc_node_sections"], total_sections
        ),
        "csgc_data_section_coverage": (
            totals["csgc_data_sections"], total_data_sections
        ),
        "csgc_data_time_fraction_of_collector_time": (
            totals["csgc_data_time_us"], total_collector_time_us
        ),
        "origc_data_time_fraction_of_collector_time": (
            totals["origc_data_time_us"], total_collector_time_us
        ),
        "origc_node_time_fraction_of_collector_time": (
            totals["origc_node_time_us"], total_collector_time_us
        ),
        "collector_time_fraction_of_f2fs_gc_calls": (
            total_collector_time_us, f2fs_gc_call_time_sum_us
        ),
    }
    for name, (numerator, denominator) in ratios.items():
        value = numerator / float(denominator) if denominator else float("nan")
        out.write(f"{name}={format_float(value, 6)}\n")

    out.write(f"collector_block_breakdown_calls={len(block_complete)}\n")
    out.write(
        "collector_block_breakdown_missing_calls="
        f"{len(complete) - len(block_complete)}\n"
    )
    if not block_complete:
        out.write("collector_block_breakdown=unavailable\n")
        return

    block_totals = {
        field: sum(int(record[field]) for record in block_complete)
        for field in F2FS_GC_COLLECTOR_BLOCK_FIELDS
    }
    block_count_totals = {
        field: sum(int(record[field]) for record in block_complete)
        for field in F2FS_GC_COLLECTOR_COUNT_FIELDS
    }
    for field in F2FS_GC_COLLECTOR_BLOCK_FIELDS:
        values = [int(record[field]) for record in block_complete]
        out.write(summarize_values(f"{field}_per_call", values) + "\n")
        out.write(f"{field}={block_totals[field]}\n")

    total_victim_valid_blocks = sum(
        block_totals[field]
        for field in (
            "csgc_data_victim_valid_blocks",
            "origc_data_victim_valid_blocks",
            "origc_node_victim_valid_blocks",
        )
    )
    total_data_victim_valid_blocks = (
        block_totals["csgc_data_victim_valid_blocks"]
        + block_totals["origc_data_victim_valid_blocks"]
    )
    total_migrated_blocks = sum(
        block_totals[field]
        for field in (
            "csgc_data_migrated_blocks",
            "origc_data_migrated_blocks",
            "origc_node_migrated_blocks",
        )
    )
    total_data_migrated_blocks = (
        block_totals["csgc_data_migrated_blocks"]
        + block_totals["origc_data_migrated_blocks"]
    )

    out.write(f"total_victim_valid_blocks={total_victim_valid_blocks}\n")
    out.write(
        f"total_data_victim_valid_blocks={total_data_victim_valid_blocks}\n"
    )
    out.write(f"total_migrated_blocks={total_migrated_blocks}\n")
    out.write(f"total_data_migrated_blocks={total_data_migrated_blocks}\n")

    block_ratios = {
        "csgc_data_victim_valid_block_fraction_of_all": (
            block_totals["csgc_data_victim_valid_blocks"],
            total_victim_valid_blocks,
        ),
        "origc_data_victim_valid_block_fraction_of_all": (
            block_totals["origc_data_victim_valid_blocks"],
            total_victim_valid_blocks,
        ),
        "origc_node_victim_valid_block_fraction_of_all": (
            block_totals["origc_node_victim_valid_blocks"],
            total_victim_valid_blocks,
        ),
        "csgc_data_valid_block_coverage": (
            block_totals["csgc_data_victim_valid_blocks"],
            total_data_victim_valid_blocks,
        ),
        "csgc_data_migrated_block_coverage": (
            block_totals["csgc_data_migrated_blocks"],
            total_data_migrated_blocks,
        ),
        "origc_node_migrated_block_fraction_of_all": (
            block_totals["origc_node_migrated_blocks"],
            total_migrated_blocks,
        ),
        "csgc_data_migrated_to_victim_valid_ratio": (
            block_totals["csgc_data_migrated_blocks"],
            block_totals["csgc_data_victim_valid_blocks"],
        ),
        "origc_data_migrated_to_victim_valid_ratio": (
            block_totals["origc_data_migrated_blocks"],
            block_totals["origc_data_victim_valid_blocks"],
        ),
        "origc_node_migrated_to_victim_valid_ratio": (
            block_totals["origc_node_migrated_blocks"],
            block_totals["origc_node_victim_valid_blocks"],
        ),
        "csgc_data_victim_valid_blocks_per_section": (
            block_totals["csgc_data_victim_valid_blocks"],
            block_count_totals["csgc_data_sections"],
        ),
        "origc_data_victim_valid_blocks_per_section": (
            block_totals["origc_data_victim_valid_blocks"],
            block_count_totals["origc_data_sections"],
        ),
        "origc_node_victim_valid_blocks_per_section": (
            block_totals["origc_node_victim_valid_blocks"],
            block_count_totals["origc_node_sections"],
        ),
    }
    for name, (numerator, denominator) in block_ratios.items():
        value = numerator / float(denominator) if denominator else float("nan")
        out.write(f"{name}={format_float(value, 6)}\n")


def merge_windows(windows: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping non-empty half-open time windows."""
    ordered = sorted((start, end) for start, end in windows if end > start)
    merged: List[Tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def merged_window_time(windows: List[Tuple[int, int]]) -> int:
    """Return union time for windows already merged by merge_windows()."""
    return sum(end - start for start, end in windows)


def build_f2fs_gc_related_windows(
    records: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    """Merge demand-to-completion windows while retaining boundary calls."""
    raw_windows: List[Dict[str, object]] = []
    for record in records:
        demand_tracked = (
            all(record.get(field) is not None for field in F2FS_GC_DEMAND_START_FIELDS)
            and int(record["gc_demand_tracked"]) == 1
            and int(record["gc_demand_to_call_us"]) >= 0
        )
        start_t_us = int(record["start_t_us"])
        if demand_tracked:
            start_t_us -= int(record["gc_demand_to_call_us"])
        raw_windows.append(
            {
                "start_t_us": start_t_us,
                "end_t_us": int(record["end_t_us"]),
                "start_record": record,
                "end_record": record,
                "call_count": 1,
                "demand_untracked_calls": 0 if demand_tracked else 1,
            }
        )

    merged: List[Dict[str, object]] = []
    for window in sorted(
        raw_windows,
        key=lambda item: (int(item["start_t_us"]), int(item["end_t_us"])),
    ):
        if not merged or int(window["start_t_us"]) > int(merged[-1]["end_t_us"]):
            merged.append(dict(window))
            continue

        current = merged[-1]
        current["call_count"] = int(current["call_count"]) + 1
        current["demand_untracked_calls"] = int(
            current["demand_untracked_calls"]
        ) + int(window["demand_untracked_calls"])
        if int(window["end_t_us"]) > int(current["end_t_us"]):
            current["end_t_us"] = int(window["end_t_us"])
            current["end_record"] = window["end_record"]

    return merged


def emit_f2fs_gc_related_gap_breakdown(
    out, records: List[Dict[str, object]]
) -> None:
    """Report idle gaps between merged GC demand-to-completion windows."""
    out.write("=== adjacent complete GC-related window gaps (microseconds) ===\n")
    merged = build_f2fs_gc_related_windows(records)
    demand_untracked_calls = sum(
        int(window["demand_untracked_calls"]) for window in merged
    )
    out.write(f"gc_related_merged_windows={len(merged)}\n")
    out.write(f"gc_related_demand_untracked_calls={demand_untracked_calls}\n")
    out.write(f"gc_related_gap_complete={int(demand_untracked_calls == 0)}\n")
    if not merged:
        out.write(summarize_values("gc_related_gap_us", []) + "\n")
        out.write("gc_related_gap_breakdown=unavailable\n")
        return

    gaps: List[Dict[str, object]] = []
    for previous, following in zip(merged, merged[1:]):
        gap_us = int(following["start_t_us"]) - int(previous["end_t_us"])
        if gap_us <= 0:
            continue
        gaps.append(
            {
                "gap_us": gap_us,
                "gap_start_t_us": int(previous["end_t_us"]),
                "gap_end_t_us": int(following["start_t_us"]),
                "previous": previous,
                "following": following,
            }
        )

    gap_values = [int(gap["gap_us"]) for gap in gaps]
    out.write(summarize_values("gc_related_gap_us", gap_values) + "\n")
    out.write(f"gc_related_gap_sum_us={sum(gap_values)}\n")
    for threshold_us in GC_RELATED_GAP_THRESHOLDS_US:
        selected = [value for value in gap_values if value >= threshold_us]
        out.write(
            f"gc_related_gap_ge_{threshold_us}us_count={len(selected)} "
            f"time_us={sum(selected)}\n"
        )

    transition_counts: Counter = Counter()
    transition_time_us: Counter = Counter()
    next_comm_counts: Counter = Counter()
    next_comm_time_us: Counter = Counter()
    for gap in gaps:
        previous_record = gap["previous"]["end_record"]
        following_record = gap["following"]["start_record"]
        transition = (str(previous_record["path"]), str(following_record["path"]))
        next_comm = str(following_record.get("comm") or "unknown")
        transition_counts[transition] += 1
        transition_time_us[transition] += int(gap["gap_us"])
        next_comm_counts[next_comm] += 1
        next_comm_time_us[next_comm] += int(gap["gap_us"])

    for (previous_path, next_path), count in sorted(transition_counts.items()):
        out.write(
            f"gc_related_gap_transition prev_path={previous_path} "
            f"next_path={next_path} count={count} "
            f"time_us={transition_time_us[(previous_path, next_path)]}\n"
        )
    for comm, count in sorted(next_comm_counts.items()):
        out.write(
            f"gc_related_gap_next_comm comm={comm} count={count} "
            f"time_us={next_comm_time_us[comm]}\n"
        )

    longest = sorted(
        gaps,
        key=lambda item: (
            int(item["gap_us"]),
            -int(item["gap_start_t_us"]),
        ),
        reverse=True,
    )[:GC_RELATED_GAP_TOP_COUNT]
    out.write(f"gc_related_top_gap_count={len(longest)}\n")
    for rank, gap in enumerate(longest, 1):
        previous_window = gap["previous"]
        following_window = gap["following"]
        previous_record = previous_window["end_record"]
        following_record = following_window["start_record"]
        out.write(
            f"gc_related_top_gap rank={rank} "
            f"gap_us={int(gap['gap_us'])} "
            f"gap_start_t_us={int(gap['gap_start_t_us'])} "
            f"gap_end_t_us={int(gap['gap_end_t_us'])} "
            f"prev_call_id={int(previous_record['call_id'])} "
            f"prev_path={previous_record['path']} "
            f"prev_ret={previous_record['ret']} "
            f"prev_sec_freed={previous_record['sec_freed']} "
            f"prev_end_lineno={int(previous_record['end_lineno'])} "
            f"next_call_id={int(following_record['call_id'])} "
            f"next_path={following_record['path']} "
            f"next_ret={following_record['ret']} "
            f"next_sec_freed={following_record['sec_freed']} "
            f"next_start_lineno={int(following_record['start_lineno'])} "
            f"next_gc_lock_wait_us={following_record['gc_lock_wait_us']} "
            f"next_gc_demand_to_call_us={following_record['gc_demand_to_call_us']}\n"
        )


def intersect_merged_windows(
    left: List[Tuple[int, int]], right: List[Tuple[int, int]]
) -> int:
    """Return intersection time between two merged window lists."""
    i = 0
    j = 0
    total = 0
    while i < len(left) and j < len(right):
        start = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > start:
            total += end - start
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return total


def build_window_concurrency(
    windows: List[Tuple[int, int]],
) -> Dict[str, object]:
    """Build active-waiter time bins from reconstructed wait windows."""
    events: List[Tuple[int, int, int]] = []
    for start, end in windows:
        if end <= start:
            continue
        events.append((start, 1, 1))
        events.append((end, 0, -1))

    if not events:
        return {
            "bins": {0: 0},
            "span_us": 0,
            "busy_us": 0,
            "weighted_us": 0,
            "max_active": 0,
        }

    events.sort()
    bins: DefaultDict[int, int] = defaultdict(int)
    active = 0
    max_active = 0
    first_t = events[0][0]
    last_t = first_t
    for t_us, _order, delta in events:
        bins[active] += t_us - last_t
        active += delta
        max_active = max(max_active, active)
        last_t = t_us

    return {
        "bins": dict(sorted(bins.items())),
        "span_us": max(0, last_t - first_t),
        "busy_us": sum(value for level, value in bins.items() if level > 0),
        "weighted_us": sum(
            level * value for level, value in bins.items() if level > 0
        ),
        "max_active": max_active,
    }


def emit_f2fs_gc_lock_breakdown(
    out, records: List[Dict[str, object]]
) -> None:
    """Summarize GC-lock waiting, lock-held service, and queue backlog."""
    required_fields = tuple(
        dict.fromkeys(F2FS_GC_LOCK_START_FIELDS + F2FS_GC_LOCK_END_FIELDS)
    )
    complete = [
        record
        for record in records
        if all(record.get(field) is not None for field in required_fields)
    ]

    out.write("=== f2fs_gc lock wait/hold breakdown ===\n")
    out.write(f"gc_lock_timing_calls={len(complete)}\n")
    out.write(f"gc_lock_timing_missing_calls={len(records) - len(complete)}\n")
    if not complete:
        out.write("gc_lock_timing_breakdown=unavailable\n")
        return

    timing_fields = (
        "gc_lock_wait_us",
        "gc_lock_acquire_to_call_us",
        "gc_call_pre_unlock_us",
        "gc_lock_held_us",
        "gc_call_post_unlock_us",
    )
    for field in timing_fields:
        values = [int(record[field]) for record in complete]
        out.write(summarize_values(field, values) + "\n")

    tracked = [
        record
        for record in complete
        if int(record["gc_lock_wait_tracked"]) == 1
    ]
    tracking_mismatches = sum(
        1
        for record in complete
        if record.get("gc_lock_wait_tracked_end") is not None
        and int(record["gc_lock_wait_tracked"])
        != int(record["gc_lock_wait_tracked_end"])
    )
    call_partition_mismatches = sum(
        1
        for record in complete
        if abs(
            int(record["duration_us"])
            - int(record["gc_call_pre_unlock_us"])
            - int(record["gc_call_post_unlock_us"])
        ) > 2
    )
    held_partition_mismatches = sum(
        1
        for record in tracked
        if abs(
            int(record["gc_lock_held_us"])
            - int(record["gc_lock_acquire_to_call_us"])
            - int(record["gc_call_pre_unlock_us"])
        ) > 2
    )

    locked_windows = merge_windows(
        (
            int(record["start_t_us"]),
            int(record["start_t_us"])
            + int(record["gc_call_pre_unlock_us"]),
        )
        for record in complete
    )
    wait_windows: List[Tuple[int, int]] = []
    for record in tracked:
        acquired_t = (
            int(record["start_t_us"])
            - int(record["gc_lock_acquire_to_call_us"])
        )
        wait_windows.append(
            (acquired_t - int(record["gc_lock_wait_us"]), acquired_t)
        )

    nonempty_wait_windows = [
        window for window in wait_windows if window[1] > window[0]
    ]
    merged_wait_windows = merge_windows(nonempty_wait_windows)
    wait_concurrency = build_window_concurrency(nonempty_wait_windows)
    locked_body_us = merged_window_time(locked_windows)
    wait_active_us = merged_window_time(merged_wait_windows)
    locked_with_waiter_us = intersect_merged_windows(
        locked_windows, merged_wait_windows
    )
    waits_overlapping_locked = sum(
        1
        for window in nonempty_wait_windows
        if intersect_merged_windows(merge_windows([window]), locked_windows) > 0
    )

    tracked_fraction = len(tracked) / float(len(complete))
    locked_with_waiter_fraction = (
        locked_with_waiter_us / float(locked_body_us)
        if locked_body_us > 0
        else float("nan")
    )
    avg_waiters_when_waiting = (
        int(wait_concurrency["weighted_us"])
        / float(int(wait_concurrency["busy_us"]))
        if int(wait_concurrency["busy_us"]) > 0
        else float("nan")
    )

    out.write(f"gc_lock_wait_tracked_calls={len(tracked)}\n")
    out.write(f"gc_lock_wait_untracked_calls={len(complete) - len(tracked)}\n")
    out.write(
        "gc_lock_wait_tracked_fraction="
        f"{format_float(tracked_fraction, 6)}\n"
    )
    out.write(f"gc_lock_wait_positive_calls={len(nonempty_wait_windows)}\n")
    out.write(
        "gc_lock_wait_calls_overlapping_gc_locked_body="
        f"{waits_overlapping_locked}\n"
    )
    out.write(f"gc_locked_body_us={locked_body_us}\n")
    out.write(f"gc_lock_wait_active_us={wait_active_us}\n")
    if nonempty_wait_windows:
        out.write(
            "gc_lock_first_wait_start_t_us="
            f"{min(start for start, _end in nonempty_wait_windows)}\n"
        )
        out.write(
            "gc_lock_last_wait_acquired_t_us="
            f"{max(end for _start, end in nonempty_wait_windows)}\n"
        )
    else:
        out.write("gc_lock_first_wait_start_t_us=unavailable\n")
        out.write("gc_lock_last_wait_acquired_t_us=unavailable\n")
    out.write(
        "gc_lock_waiter_weighted_us="
        f"{int(wait_concurrency['weighted_us'])}\n"
    )
    out.write(
        f"gc_lock_waiter_max_active={int(wait_concurrency['max_active'])}\n"
    )
    out.write(
        "gc_lock_avg_waiters_when_waiting="
        f"{format_float(avg_waiters_when_waiting, 6)}\n"
    )
    for active, time_us in sorted(dict(wait_concurrency["bins"]).items()):
        out.write(f"gc_lock_waiters active={active} time_us={time_us}\n")
    out.write(f"gc_locked_body_with_waiter_us={locked_with_waiter_us}\n")
    out.write(
        "gc_locked_body_with_waiter_fraction="
        f"{format_float(locked_with_waiter_fraction, 6)}\n"
    )
    out.write(
        "gc_lock_wait_not_overlapping_traced_gc_us="
        f"{max(0, wait_active_us - locked_with_waiter_us)}\n"
    )
    out.write(f"gc_lock_tracking_mismatch_calls={tracking_mismatches}\n")
    out.write(f"gc_call_partition_mismatch_calls={call_partition_mismatches}\n")
    out.write(
        "gc_lock_held_partition_mismatch_calls="
        f"{held_partition_mismatches}\n"
    )


def emit_f2fs_gc_window_breakdown(
    out,
    records: List[Dict[str, object]],
    epoch_elapsed_us: Optional[int],
) -> None:
    """Merge demand-to-completion windows without double-counting overlap."""
    out.write("=== complete GC-related wall-clock window ===\n")
    out.write(f"gc_window_calls={len(records)}\n")
    if not records:
        out.write("gc_window_breakdown=unavailable\n")
        return

    demand_complete = [
        record
        for record in records
        if all(record.get(field) is not None for field in F2FS_GC_DEMAND_START_FIELDS)
        and int(record["gc_demand_tracked"]) == 1
        and int(record["gc_demand_to_call_us"]) >= 0
    ]
    demand_complete_ids = {int(record["call_id"]) for record in demand_complete}
    demand_missing = [
        record
        for record in records
        if int(record["call_id"]) not in demand_complete_ids
    ]

    call_windows = merge_windows(
        (int(record["start_t_us"]), int(record["end_t_us"]))
        for record in records
    )
    pre_call_windows = merge_windows(
        (
            int(record["start_t_us"])
            - int(record["gc_demand_to_call_us"]),
            int(record["start_t_us"]),
        )
        for record in demand_complete
    )
    raw_gc_windows = [
        (
            int(record["start_t_us"])
            - int(record["gc_demand_to_call_us"]),
            int(record["end_t_us"]),
        )
        for record in demand_complete
    ]
    lower_bound_windows = raw_gc_windows + [
        (int(record["start_t_us"]), int(record["end_t_us"]))
        for record in demand_missing
    ]
    merged_gc_windows = merge_windows(lower_bound_windows)
    gc_window_concurrency = build_window_concurrency(lower_bound_windows)

    call_union_us = merged_window_time(call_windows)
    pre_call_union_us = merged_window_time(pre_call_windows)
    gc_window_union_lower_bound_us = merged_window_time(merged_gc_windows)
    pre_call_overlapping_call_us = intersect_merged_windows(
        pre_call_windows, call_windows
    )
    gc_window_extra_outside_calls_us = max(
        0, gc_window_union_lower_bound_us - call_union_us
    )
    pre_call_weighted_us = sum(
        int(record["gc_demand_to_call_us"]) for record in demand_complete
    )
    demand_to_call_values = [
        int(record["gc_demand_to_call_us"]) for record in demand_complete
    ]
    demand_pre_lock_values: List[int] = []
    demand_pre_lock_mismatches = 0
    for record in demand_complete:
        if (
            record.get("gc_lock_wait_tracked") is None
            or int(record["gc_lock_wait_tracked"]) != 1
            or record.get("gc_lock_wait_us") is None
            or record.get("gc_lock_acquire_to_call_us") is None
        ):
            continue
        pre_lock_us = (
            int(record["gc_demand_to_call_us"])
            - int(record["gc_lock_wait_us"])
            - int(record["gc_lock_acquire_to_call_us"])
        )
        if pre_lock_us < -2:
            demand_pre_lock_mismatches += 1
        demand_pre_lock_values.append(max(0, pre_lock_us))

    first_window_start_us = min(start for start, _end in lower_bound_windows)
    last_window_end_us = max(end for _start, end in lower_bound_windows)
    gc_window_span_us = max(0, last_window_end_us - first_window_start_us)
    gc_window_busy_fraction_to_last_end = (
        gc_window_union_lower_bound_us / float(gc_window_span_us)
        if gc_window_span_us > 0
        else float("nan")
    )
    gc_window_complete = not demand_missing

    out.write(f"gc_window_demand_tracked_calls={len(demand_complete)}\n")
    out.write(f"gc_window_demand_untracked_calls={len(demand_missing)}\n")
    out.write(f"gc_window_complete={int(gc_window_complete)}\n")
    out.write(summarize_values("gc_demand_to_call_us", demand_to_call_values) + "\n")
    out.write(summarize_values("gc_demand_pre_lock_us", demand_pre_lock_values) + "\n")
    out.write(
        "gc_demand_pre_lock_partition_mismatch_calls="
        f"{demand_pre_lock_mismatches}\n"
    )
    out.write(f"gc_call_union_us={call_union_us}\n")
    out.write(f"gc_pre_call_union_us={pre_call_union_us}\n")
    out.write(f"gc_pre_call_weighted_us={pre_call_weighted_us}\n")
    out.write(
        "gc_pre_call_overlapping_gc_call_us="
        f"{pre_call_overlapping_call_us}\n"
    )
    out.write(
        "gc_window_extra_outside_gc_calls_us="
        f"{gc_window_extra_outside_calls_us}\n"
    )
    out.write(
        "gc_window_overlap_avoided_us="
        f"{max(0, call_union_us + pre_call_union_us - gc_window_union_lower_bound_us)}\n"
    )
    out.write(
        "gc_window_union_lower_bound_us="
        f"{gc_window_union_lower_bound_us}\n"
    )
    out.write(f"gc_window_lower_bound_first_t_us={first_window_start_us}\n")
    out.write(f"gc_window_lower_bound_last_t_us={last_window_end_us}\n")
    if gc_window_complete:
        out.write(f"gc_window_union_us={gc_window_union_lower_bound_us}\n")
    else:
        out.write("gc_window_union_us=unavailable\n")
        out.write("gc_window_first_demand_t_us=unavailable\n")
        out.write("gc_window_last_end_t_us=unavailable\n")
        out.write("gc_window_first_demand_to_last_end_us=unavailable\n")
        out.write(
            "gc_window_busy_fraction_first_demand_to_last_end=unavailable\n"
        )
    if gc_window_complete:
        out.write(f"gc_window_first_demand_t_us={first_window_start_us}\n")
        out.write(f"gc_window_last_end_t_us={last_window_end_us}\n")
        out.write(f"gc_window_first_demand_to_last_end_us={gc_window_span_us}\n")
        out.write(
            "gc_window_busy_fraction_first_demand_to_last_end="
            f"{format_float(gc_window_busy_fraction_to_last_end, 6)}\n"
        )

    if epoch_elapsed_us is not None and gc_window_complete:
        first_demand_to_epoch_end_us = epoch_elapsed_us - first_window_start_us
        out.write(f"gc_window_epoch_elapsed_us={epoch_elapsed_us}\n")
        if epoch_elapsed_us > 0:
            out.write(
                "gc_window_busy_fraction_epoch="
                f"{format_float(gc_window_union_lower_bound_us / float(epoch_elapsed_us), 6)}\n"
            )
            out.write(
                "gc_window_inactive_us_epoch="
                f"{max(0, epoch_elapsed_us - gc_window_union_lower_bound_us)}\n"
            )
        else:
            out.write("gc_window_busy_fraction_epoch=unavailable\n")
            out.write("gc_window_inactive_us_epoch=unavailable\n")
        out.write(
            "gc_window_first_demand_to_epoch_end_us="
            f"{max(0, first_demand_to_epoch_end_us)}\n"
        )
        if first_demand_to_epoch_end_us > 0:
            out.write(
                "gc_window_busy_fraction_first_demand_to_epoch_end="
                f"{format_float(gc_window_union_lower_bound_us / float(first_demand_to_epoch_end_us), 6)}\n"
            )
        else:
            out.write(
                "gc_window_busy_fraction_first_demand_to_epoch_end=unavailable\n"
            )
    else:
        out.write("gc_window_epoch_elapsed_us=unavailable\n")
        out.write("gc_window_busy_fraction_epoch=unavailable\n")
        out.write("gc_window_inactive_us_epoch=unavailable\n")
        out.write("gc_window_first_demand_to_epoch_end_us=unavailable\n")
        out.write("gc_window_busy_fraction_first_demand_to_epoch_end=unavailable\n")

    if gc_window_complete:
        out.write(
            "gc_window_request_weighted_us="
            f"{int(gc_window_concurrency['weighted_us'])}\n"
        )
        out.write(
            "gc_window_request_max_active="
            f"{int(gc_window_concurrency['max_active'])}\n"
        )
        for active, time_us in sorted(dict(gc_window_concurrency["bins"]).items()):
            out.write(f"gc_window_requests active={active} time_us={time_us}\n")
    else:
        out.write("gc_window_request_weighted_us=unavailable\n")
        out.write("gc_window_request_max_active=unavailable\n")


def emit_f2fs_gc_trigger_stats(
    out, trigger_stats: List[Dict[str, str]]
) -> None:
    """Aggregate per-source GC demand, lock, call, and victim counters."""
    out.write("=== f2fs_gc trigger source statistics ===\n")
    out.write(f"trigger_stat_lines={len(trigger_stats)}\n")
    if not trigger_stats:
        out.write("trigger_stats=unavailable\n")
        return

    by_source: DefaultDict[str, Counter] = defaultdict(Counter)
    invalid_rows = 0
    for row in trigger_stats:
        source = row.get("source", "unknown")
        values: Dict[str, int] = {}
        for field in F2FS_GC_TRIGGER_COUNTER_FIELDS:
            value = int_or_none(row.get(field))
            if value is None:
                invalid_rows += 1
                values = {}
                break
            values[field] = value
        if values:
            by_source[source].update(values)

    def emit_source(source: str, counters: Counter) -> None:
        def ratio(numerator: int, denominator: int) -> str:
            if not denominator:
                return "nan"
            return format_float(numerator / float(denominator), 6)

        values = {
            field: int(counters.get(field, 0))
            for field in F2FS_GC_TRIGGER_COUNTER_FIELDS
        }
        lock_attempts = (
            values["blocking_attempts"] + values["trylock_attempts"]
        )
        lock_acquired = (
            values["blocking_acquired"] + values["trylock_acquired"]
        )
        blocking_pending = (
            values["blocking_attempts"] - values["blocking_acquired"]
        )
        trylock_partition_delta = (
            values["trylock_attempts"]
            - values["trylock_acquired"]
            - values["trylock_failed"]
        )
        calls_pending = values["calls_started"] - values["calls_completed"]
        victim_partition_delta = (
            values["calls_completed"]
            - values["calls_with_victim"]
            - values["calls_no_victim"]
        )

        raw_fields = " ".join(
            f"{field}={values[field]}"
            for field in F2FS_GC_TRIGGER_COUNTER_FIELDS
        )
        out.write(f"source={source} {raw_fields}\n")
        out.write(
            f"source={source} lock_attempts={lock_attempts} "
            f"lock_acquired={lock_acquired} "
            f"demand_to_call_rate={ratio(values['calls_started'], values['demands'])} "
            f"delegated_fraction={ratio(values['delegated'], values['demands'])} "
            f"trylock_failure_rate={ratio(values['trylock_failed'], values['trylock_attempts'])} "
            f"lock_to_call_rate={ratio(values['calls_started'], lock_acquired)} "
            f"victim_hit_rate={ratio(values['calls_with_victim'], values['calls_completed'])} "
            "negative_return_rate="
            f"{ratio(values['calls_negative_ret'], values['calls_completed'])} "
            f"blocking_pending={blocking_pending} "
            f"trylock_partition_delta={trylock_partition_delta} "
            f"calls_pending={calls_pending} "
            f"victim_partition_delta={victim_partition_delta} "
            "lock_acquired_without_call="
            f"{lock_acquired - values['calls_started']}\n"
        )

    total = Counter()
    for source in sorted(by_source):
        emit_source(source, by_source[source])
        total.update(by_source[source])
    emit_source("all_stages", total)
    out.write(f"trigger_invalid_rows={invalid_rows}\n")
    out.write(
        "trigger_note=all_stages_demands_are_source_stage_observations_not_unique_logical_requests\n"
    )


def build_complete_segment_totals(
    intervals: Dict[str, Dict[Tuple[int, ...], List[int]]]
) -> List[int]:
    if not {"pre", "ssd", "post"}.issubset(intervals):
        return []
    keys = set(intervals["pre"]) & set(intervals["ssd"]) & set(intervals["post"])
    totals: List[int] = []
    for key in keys:
        if not intervals["pre"][key] or not intervals["ssd"][key] or not intervals["post"][key]:
            continue
        totals.append(
            intervals["pre"][key][0]
            + intervals["ssd"][key][0]
            + intervals["post"][key][0]
        )
    return totals


def build_phase_gaps(
    windows: List[Tuple[int, int]],
) -> Tuple[List[int], int, int]:
    """Return idle gaps plus overlap count/time for ordered phase windows."""
    if not windows:
        return [], 0, 0

    ordered = sorted(windows)
    frontier_end = ordered[0][1]
    gaps: List[int] = []
    overlap_count = 0
    overlap_us = 0

    for start, end in ordered[1:]:
        if start >= frontier_end:
            gaps.append(start - frontier_end)
        else:
            overlap_count += 1
            overlap_us += frontier_end - start
        frontier_end = max(frontier_end, end)

    return gaps, overlap_count, overlap_us


def reconstruct_active(
    traces: List[Trace],
    max_active_hint: int = 8,
) -> Dict[str, Dict[str, object]]:
    by_phase: Dict[str, List[Tuple[int, int, int]]] = {phase: [] for phase in PHASES}
    section_times = [
        trace_time_us(trace)
        for trace in traces
        if str(trace["event"]) in ("SECTION_START", "SECTION_END")
    ]
    section_first = min(section_times) if section_times else None
    section_last = max(section_times) if section_times else None
    event_to_phase_delta: DefaultDict[str, List[Tuple[str, int]]] = defaultdict(list)
    for phase, (start_event, end_event) in PHASE_START_END.items():
        event_to_phase_delta[start_event].append((phase, 1))
        event_to_phase_delta[end_event].append((phase, -1))

    for trace in traces:
        event = str(trace["event"])
        if event not in event_to_phase_delta:
            continue
        for phase, delta in event_to_phase_delta[event]:
            if not trace_matches_phase(phase, trace):
                continue
            event_order = 0 if delta < 0 else 1
            by_phase[phase].append((trace_time_us(trace), event_order, delta))

    result: Dict[str, Dict[str, object]] = {}
    for phase in PHASES:
        events = sorted(by_phase[phase])
        bins: DefaultDict[int, int] = defaultdict(int)
        active = 0
        max_active = 0
        negative_events = 0
        out_of_order_events = 0

        shared_section_window = (
            TRACE_KIND == "origc"
            and phase in ("data", "node")
            and section_first is not None
            and section_last is not None
        )

        if not events and not shared_section_window:
            result[phase] = {
                "bins": dict(bins),
                "span_us": 0,
                "busy_us": 0,
                "weighted_us": 0,
                "max_active": 0,
                "negative_events": 0,
                "out_of_order_events": 0,
            }
            continue

        first_t = int(section_first) if shared_section_window else events[0][0]
        last_t = first_t

        for t_us, _order, delta in events:
            if t_us < last_t:
                out_of_order_events += 1
                t_us = last_t
            bins[active] += t_us - last_t
            active += delta
            if active < 0:
                negative_events += 1
                active = 0
            max_active = max(max_active, active)
            last_t = t_us

        if shared_section_window and int(section_last) >= last_t:
            bins[active] += int(section_last) - last_t
            last_t = int(section_last)

        span_us = max(0, last_t - first_t)
        busy_us = sum(time for level, time in bins.items() if level > 0)
        weighted_us = sum(level * time for level, time in bins.items() if level > 0)

        for level in range(0, max(max_active_hint, max_active) + 1):
            bins[level] += 0

        result[phase] = {
            "bins": dict(sorted(bins.items())),
            "span_us": span_us,
            "busy_us": busy_us,
            "weighted_us": weighted_us,
            "max_active": max_active,
            "negative_events": negative_events,
            "out_of_order_events": out_of_order_events,
        }

    return result


def parse_kernel_stats(stats: List[Dict[str, str]]) -> Dict[str, object]:
    global_rows: List[Dict[str, str]] = []
    phase_active: DefaultDict[str, Dict[int, int]] = defaultdict(dict)
    phase_summary: DefaultDict[str, Dict[str, str]] = defaultdict(dict)
    other_rows: List[Dict[str, str]] = []

    for row in stats:
        if "since_first_gc_us" in row or (
            "epoch_elapsed_us" in row and "phase" not in row
        ):
            global_rows.append(row)
            continue
        phase = row.get("phase")
        if phase and "active" in row and "time_us" in row:
            active = int_or_none(row.get("active"))
            time_us = int_or_none(row.get("time_us"))
            if active is not None and time_us is not None:
                phase_active[phase][active] = time_us
            continue
        if phase:
            phase_summary[phase].update(row)
            continue
        other_rows.append(row)

    return {
        "global_rows": global_rows,
        "phase_active": dict(phase_active),
        "phase_summary": dict(phase_summary),
        "other_rows": other_rows,
    }


def emit_active_summary(out, active_result: Dict[str, Dict[str, object]]) -> None:
    for phase in PHASES:
        info = active_result[phase]
        bins = info["bins"]
        span_us = int(info["span_us"])
        busy_us = int(info["busy_us"])
        weighted_us = int(info["weighted_us"])
        max_active = int(info["max_active"])

        out.write(f"phase={phase} trace_span_us={span_us}\n")
        for active, time_us in sorted(bins.items()):
            out.write(f"phase={phase} active={active} time_us={time_us}\n")

        busy_fraction = busy_us / float(span_us) if span_us > 0 else float("nan")
        avg_parallelism_when_busy = (
            weighted_us / float(busy_us) if busy_us > 0 else float("nan")
        )
        avg_parallelism_over_span = (
            weighted_us / float(span_us) if span_us > 0 else float("nan")
        )
        max_active_time = int(bins.get(max_active, 0)) if max_active > 0 else 0
        max_active_key = (
            "f2fs_gc_call_max_active"
            if TRACE_KIND == "f2fs_gc" and phase == "gc_call"
            else "max_active"
        )
        observed_max_active_fraction_when_busy = (
            max_active_time / float(busy_us) if busy_us > 0 else float("nan")
        )
        active8_fraction_when_busy = (
            int(bins.get(8, 0)) / float(busy_us) if busy_us > 0 else float("nan")
        )
        active_ge2_fraction_when_busy = (
            sum(time for level, time in bins.items() if level >= 2) / float(busy_us)
            if busy_us > 0
            else float("nan")
        )
        active_ge4_fraction_when_busy = (
            sum(time for level, time in bins.items() if level >= 4) / float(busy_us)
            if busy_us > 0
            else float("nan")
        )

        out.write(
            f"phase={phase} busy_us={busy_us} busy_fraction={format_float(busy_fraction, 6)} "
            f"avg_parallelism_when_busy={format_float(avg_parallelism_when_busy)} "
            f"avg_parallelism_over_span={format_float(avg_parallelism_over_span)} "
            f"{max_active_key}={max_active} "
            f"observed_max_active_fraction_when_busy={format_float(observed_max_active_fraction_when_busy, 6)} "
            f"active8_fraction_when_busy={format_float(active8_fraction_when_busy, 6)} "
            f"active_ge2_fraction_when_busy={format_float(active_ge2_fraction_when_busy, 6)} "
            f"active_ge4_fraction_when_busy={format_float(active_ge4_fraction_when_busy, 6)} "
            f"negative_events={info['negative_events']} "
            f"out_of_order_events={info['out_of_order_events']}\n"
        )


def write_result(
    output_path: str,
    source_path: str,
    selected_epoch: int,
    selected_scope: str,
    available_epochs: List[int],
    traces: List[Trace],
    stats: List[Dict[str, str]],
    raw_stats: List[str],
    trigger_stats: List[Dict[str, str]],
    raw_trigger_stats: List[str],
    measurement_stats: List[Dict[str, str]],
    victim_stats: List[Dict[str, str]],
    measurement_boundaries: List[Dict[str, str]],
) -> None:
    intervals, windows, diagnostics = build_phase_intervals(traces)
    active_result = reconstruct_active(
        traces, max_active_hint=1 if TRACE_KIND in ("origc", "f2fs_gc") else 8
    )
    kernel_stats = parse_kernel_stats(stats)
    event_counts = Counter(str(trace["event"]) for trace in traces)
    complete_segment_totals = build_complete_segment_totals(intervals)
    gc_call_records = (
        build_f2fs_gc_call_records(traces) if TRACE_KIND == "f2fs_gc" else []
    )
    global_rows = kernel_stats["global_rows"]
    since_first_gc_us = (
        int_or_none(global_rows[-1].get("since_first_gc_us"))
        if global_rows
        else None
    )
    epoch_elapsed_us = (
        int_or_none(global_rows[-1].get("epoch_elapsed_us"))
        if global_rows
        else None
    )
    if epoch_elapsed_us is None:
        epoch_elapsed_us = since_first_gc_us
    first_t = min(trace_time_us(trace) for trace in traces) if traces else 0
    last_t = max(trace_time_us(trace) for trace in traces) if traces else 0

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as out:
        out.write(f"=== {TRACE_LABEL} heavy trace analysis ===\n")
        out.write(f"source_file={os.path.abspath(source_path)}\n")
        out.write(f"result_file={os.path.abspath(output_path)}\n")
        out.write(f"selected_epoch={selected_epoch}\n")
        out.write(f"selected_scope={selected_scope}\n")
        out.write(
            "available_epochs=" + ",".join(str(value) for value in available_epochs) + "\n"
        )
        out.write(f"trace_events={len(traces)}\n")
        out.write(f"kernel_stat_lines={len(stats)}\n")
        out.write(f"trigger_stat_lines={len(trigger_stats)}\n")
        out.write(f"first_trace_t_us={first_t}\n")
        out.write(f"last_trace_t_us={last_t}\n")
        out.write(f"trace_span_us={max(0, last_t - first_t)}\n")
        out.write("\n")

        out.write("=== measurement windows ===\n")
        out.write(f"trace_first_to_last_event_us={max(0, last_t - first_t)}\n")
        if global_rows:
            row = global_rows[-1]
            since_first = since_first_gc_us
            gc_active = int_or_none(row.get("gc_active_us"))
            epoch_elapsed = int_or_none(row.get("epoch_elapsed_us"))
            epoch_gc_active = int_or_none(row.get("epoch_gc_active_us"))
            epoch_gc_inactive = int_or_none(row.get("epoch_gc_inactive_us"))
            tail = int_or_none(row.get("tail_after_last_event_us"))
            if epoch_elapsed is not None:
                out.write(f"kernel_epoch_elapsed_us={epoch_elapsed}\n")
            if epoch_gc_active is not None:
                out.write(f"kernel_epoch_gc_active_us={epoch_gc_active}\n")
            if epoch_gc_inactive is not None:
                out.write(f"kernel_epoch_gc_inactive_us={epoch_gc_inactive}\n")
            if epoch_elapsed and epoch_gc_active is not None:
                out.write(
                    "kernel_gc_call_busy_fraction_epoch="
                    f"{format_float(epoch_gc_active / float(epoch_elapsed), 6)}\n"
                )
            if since_first is not None:
                out.write(f"kernel_first_gc_to_epoch_end_us={since_first}\n")
            if since_first and gc_active is not None:
                metric_name = (
                    "kernel_gc_call_busy_fraction_first_gc_to_epoch_end"
                    if TRACE_KIND == "f2fs_gc"
                    else "kernel_section_busy_fraction_first_gc_to_epoch_end"
                )
                out.write(
                    f"{metric_name}="
                    f"{format_float(gc_active / float(since_first), 6)}\n"
                )
            if tail is not None:
                out.write(f"kernel_tail_after_last_event_us={tail}\n")
        else:
            out.write("kernel_epoch_elapsed_us=unavailable\n")
            out.write("kernel_first_gc_to_epoch_end_us=unavailable\n")

        if measurement_stats:
            row = measurement_stats[-1]
            workload_closed_by_stop = (
                selected_scope != "workload" or row.get("reason") == "stop"
            )
            out.write(
                f"workload_epoch_closed_by_stop={int(workload_closed_by_stop)}\n"
            )
            out.write(
                "measurement_boundary_summary "
                + " ".join(f"{key}={value}" for key, value in row.items())
                + "\n"
            )
        elif selected_scope == "workload":
            out.write("workload_epoch_closed_by_stop=0\n")
        if victim_stats:
            row = victim_stats[-1]
            out.write(
                "foreground_victim_summary "
                + " ".join(f"{key}={value}" for key, value in row.items())
                + "\n"
            )
        out.write("\n")

        out.write("=== event counts ===\n")
        for event, count in sorted(event_counts.items()):
            out.write(f"{event}: count={count}\n")
        out.write("\n")

        out.write("=== measurement epoch diagnostics ===\n")
        out.write(f"measurement_boundary_count={len(measurement_boundaries)}\n")
        for boundary in measurement_boundaries:
            out.write(
                "boundary "
                + " ".join(f"{key}={value}" for key, value in boundary.items())
                + "\n"
            )
        if victim_stats:
            for row in victim_stats:
                total = int_or_none(row.get("fg_victim_starts"))
                csgc = int_or_none(row.get("fg_csgc_victim_starts"))
                origc = int_or_none(row.get("fg_origc_victim_starts"))
                invariant_ok = (
                    total is not None
                    and csgc is not None
                    and origc is not None
                    and total == csgc + origc
                )
                out.write(
                    f"victim_counter_invariant_ok={int(invariant_ok)} "
                    + " ".join(f"{key}={value}" for key, value in row.items())
                    + "\n"
                )
        else:
            out.write("victim_counter_summary=unavailable\n")
        out.write("\n")

        out.write("=== reconstructed interval statistics (microseconds) ===\n")
        for phase in PHASES:
            values = flatten_intervals(intervals[phase])
            out.write(summarize_values(f"{phase}_duration_us", values) + "\n")
        if TRACE_KIND == "csgc":
            out.write(
                summarize_values(
                    "complete_segment_pre_ssd_post_total_us",
                    complete_segment_totals,
                )
                + "\n"
            )
            out.write(
                f"complete_segment_phase_triplets={len(complete_segment_totals)}\n"
            )
        out.write("\n")

        if TRACE_KIND == "f2fs_gc":
            out.write("=== f2fs_gc call classification ===\n")
            for field in (
                "mode",
                "source",
                "path",
                "ret",
                "init_gc_type",
                "final_gc_type",
                "comm",
            ):
                counts = Counter(str(record.get(field)) for record in gc_call_records)
                for value, count in sorted(counts.items()):
                    out.write(f"{field}={value} count={count}\n")

            path_durations: DefaultDict[str, List[int]] = defaultdict(list)
            for record in gc_call_records:
                path_durations[str(record["path"])].append(
                    int(record["duration_us"])
                )
            for path, values in sorted(path_durations.items()):
                out.write(summarize_values(f"path_{path}_duration_us", values) + "\n")

            total_freed = sum(
                int(record["total_freed"])
                for record in gc_call_records
                if record.get("total_freed") is not None
            )
            sections_freed = sum(
                int(record["sec_freed"])
                for record in gc_call_records
                if record.get("sec_freed") is not None
            )
            out.write(f"completed_calls={len(gc_call_records)}\n")
            out.write(f"total_freed_sum={total_freed}\n")
            out.write(f"sections_freed_sum={sections_freed}\n")
            out.write("\n")
            emit_f2fs_gc_wait_outcome_breakdown(
                out, gc_call_records, epoch_elapsed_us
            )
            out.write("\n")
            emit_f2fs_gc_collector_breakdown(
                out, gc_call_records, epoch_elapsed_us
            )
            out.write("\n")
            emit_f2fs_gc_lock_breakdown(out, gc_call_records)
            out.write("\n")
            emit_f2fs_gc_window_breakdown(
                out, gc_call_records, epoch_elapsed_us
            )
            out.write("\n")
            emit_f2fs_gc_related_gap_breakdown(out, gc_call_records)
            out.write("\n")
            emit_f2fs_gc_trigger_stats(out, trigger_stats)
            out.write("\n")

        out.write("=== adjacent interval gaps (microseconds) ===\n")
        for phase in PHASES:
            gaps, overlap_count, overlap_us = build_phase_gaps(windows[phase])
            out.write(summarize_values(f"{phase}_gap_us", gaps) + "\n")
            out.write(
                f"phase={phase} overlap_count={overlap_count} "
                f"overlap_us={overlap_us}\n"
            )
        out.write("\n")

        out.write("=== interval pairing diagnostics ===\n")
        for phase in PHASES:
            values = flatten_intervals(intervals[phase])
            diag = diagnostics[phase]
            out.write(
                f"phase={phase} intervals={len(values)} "
                f"unmatched_starts={diag['unmatched_starts']} "
                f"unmatched_ends={diag['unmatched_ends']} "
                f"negative_durations={diag['negative_durations']}\n"
            )
        out.write("\n")

        out.write("=== reconstructed active timeline ===\n")
        emit_active_summary(out, active_result)
        out.write("\n")

        out.write(f"=== kernel {TRACE_LABEL}_HEAVY_STAT summary ===\n")
        global_rows = kernel_stats["global_rows"]
        if global_rows:
            for row in global_rows:
                ordered = " ".join(f"{k}={v}" for k, v in row.items())
                out.write(f"global {ordered}\n")
        else:
            out.write("global unavailable\n")

        phase_active = kernel_stats["phase_active"]
        phase_summary = kernel_stats["phase_summary"]
        for phase in PHASES:
            active_map = phase_active.get(phase, {})
            if active_map:
                for active, time_us in sorted(active_map.items()):
                    out.write(f"kernel phase={phase} active={active} time_us={time_us}\n")
            summary = phase_summary.get(phase, {})
            if summary:
                ordered = " ".join(f"{k}={v}" for k, v in summary.items())
                out.write(f"kernel {ordered}\n")

        other_rows = kernel_stats["other_rows"]
        for row in other_rows:
            ordered = " ".join(f"{k}={v}" for k, v in row.items())
            out.write(f"kernel_other {ordered}\n")

        if raw_stats:
            out.write("\n")
            out.write(
                f"=== raw kernel {TRACE_LABEL}_HEAVY_STAT lines (all epochs) ===\n"
            )
            for line in raw_stats:
                out.write(line + "\n")
        if raw_trigger_stats:
            out.write("\n")
            out.write("=== raw F2FS_GC_TRIGGER_STAT lines (all epochs) ===\n")
            for line in raw_trigger_stats:
                out.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Parse unified f2fs_gc, CSGC, or ORIGC heavy-trace lines "
            "from a dmesg log."
        )
    )
    parser.add_argument("logfile", help="path to the dmesg log file")
    parser.add_argument("output", help="path to the output .txt file")
    parser.add_argument(
        "--kind",
        choices=("csgc", "origc", "f2fs_gc"),
        default="csgc",
        help="heavy-trace implementation to parse (default: csgc)",
    )
    args = parser.parse_args()

    configure_trace_kind(args.kind)

    (
        traces,
        stats,
        raw_stats,
        trigger_stats,
        raw_trigger_stats,
        measurement_stats,
        victim_stats,
        measurement_boundaries,
    ) = parse_input(args.logfile)

    # Keep this script silent when the input log has no heavy-trace data.
    if (
        not traces
        and not stats
        and not trigger_stats
        and not measurement_stats
        and not victim_stats
    ):
        return 0

    (
        selected_epoch,
        selected_scope,
        available_epochs,
        traces,
        stats,
        trigger_stats,
        measurement_stats,
        victim_stats,
    ) = select_measurement_epoch(
        traces, stats, trigger_stats, measurement_stats, victim_stats
    )

    write_result(
        args.output,
        args.logfile,
        selected_epoch,
        selected_scope,
        available_epochs,
        traces,
        stats,
        raw_stats,
        trigger_stats,
        raw_trigger_stats,
        measurement_stats,
        victim_stats,
        measurement_boundaries,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
