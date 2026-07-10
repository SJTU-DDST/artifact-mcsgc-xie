#!/usr/bin/env python3
import argparse
import math
import os
import re
import sys
from collections import Counter, defaultdict
from typing import DefaultDict, Dict, Iterable, List, Optional, Tuple


RE_TRACE = re.compile(r"CSGC_HEAVY_TRACE\s+(?P<kv>.*)$")
RE_STAT = re.compile(r"CSGC_HEAVY_STAT\s+(?P<kv>.*)$")

PHASES = ("section", "pre", "ssd", "post")
EVENT_PHASE_DELTA = {
    "SECTION_START": ("section", 1),
    "SECTION_END": ("section", -1),
    "PRE_START": ("pre", 1),
    "PRE_END": ("pre", -1),
    "SSD_START": ("ssd", 1),
    "SSD_END": ("ssd", -1),
    "POST_START": ("post", 1),
    "POST_END": ("post", -1),
}
PHASE_START_END = {
    "section": ("SECTION_START", "SECTION_END"),
    "pre": ("PRE_START", "PRE_END"),
    "ssd": ("SSD_START", "SSD_END"),
    "post": ("POST_START", "POST_END"),
}


Trace = Dict[str, object]


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


def parse_input(path: str) -> Tuple[List[Trace], List[Dict[str, str]], List[str]]:
    traces: List[Trace] = []
    stats: List[Dict[str, str]] = []
    raw_stats: List[str] = []

    with open(path, "r", encoding="utf-8", errors="replace") as fp:
        for lineno, line in enumerate(fp, 1):
            line = line.rstrip("\n")

            match = RE_TRACE.search(line)
            if match:
                kv = parse_kv_blob(match.group("kv"))
                t_us = int_or_none(kv.get("t_us"))
                event = kv.get("event")
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
                        "event": event,
                        "section": section,
                        "segno": segno,
                        "req_idx": req_idx,
                        "pid": int_or_none(kv.get("pid")),
                        "comm": kv.get("comm", ""),
                        "cpu": int_or_none(kv.get("cpu")),
                    }
                )
                continue

            match = RE_STAT.search(line)
            if match:
                kv = parse_kv_blob(match.group("kv"))
                if kv:
                    stats.append(kv)
                    raw_stats.append(match.group(0))

    return traces, stats, raw_stats


def phase_key(phase: str, trace: Trace) -> Tuple[int, ...]:
    if phase == "section":
        return (int(trace["section"]),)
    return (
        int(trace["section"]),
        int(trace["segno"]),
        int(trace["req_idx"]),
    )


def build_phase_intervals(
    traces: List[Trace],
) -> Tuple[Dict[str, Dict[Tuple[int, ...], List[int]]], Dict[str, Dict[str, int]]]:
    starts: Dict[str, DefaultDict[Tuple[int, ...], List[int]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    intervals: Dict[str, DefaultDict[Tuple[int, ...], List[int]]] = {
        phase: defaultdict(list) for phase in PHASES
    }
    diagnostics: Dict[str, Dict[str, int]] = {
        phase: {
            "unmatched_starts": 0,
            "unmatched_ends": 0,
            "negative_durations": 0,
        }
        for phase in PHASES
    }

    event_to_phase: Dict[str, Tuple[str, str]] = {}
    for phase, (start_event, end_event) in PHASE_START_END.items():
        event_to_phase[start_event] = (phase, "start")
        event_to_phase[end_event] = (phase, "end")

    for trace in traces:
        event = str(trace["event"])
        if event not in event_to_phase:
            continue
        phase, kind = event_to_phase[event]
        key = phase_key(phase, trace)
        t_us = int(trace["t_us"])

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

    for phase in PHASES:
        diagnostics[phase]["unmatched_starts"] = sum(
            len(v) for v in starts[phase].values()
        )

    return (
        {phase: dict(intervals[phase]) for phase in PHASES},
        diagnostics,
    )


def flatten_intervals(intervals: Dict[Tuple[int, ...], List[int]]) -> List[int]:
    values: List[int] = []
    for durations in intervals.values():
        values.extend(durations)
    return values


def build_complete_segment_totals(
    intervals: Dict[str, Dict[Tuple[int, ...], List[int]]]
) -> List[int]:
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


def reconstruct_active(
    traces: List[Trace],
    max_active_hint: int = 8,
) -> Dict[str, Dict[str, object]]:
    by_phase: Dict[str, List[Tuple[int, int, int]]] = {phase: [] for phase in PHASES}
    event_order = {
        "SECTION_END": 0,
        "PRE_END": 0,
        "SSD_END": 0,
        "POST_END": 0,
        "SECTION_START": 1,
        "PRE_START": 1,
        "SSD_START": 1,
        "POST_START": 1,
    }

    for index, trace in enumerate(traces):
        event = str(trace["event"])
        if event not in EVENT_PHASE_DELTA:
            continue
        phase, delta = EVENT_PHASE_DELTA[event]
        by_phase[phase].append((int(trace["t_us"]), event_order.get(event, 1), delta))

    result: Dict[str, Dict[str, object]] = {}
    for phase in PHASES:
        events = sorted(by_phase[phase])
        bins: DefaultDict[int, int] = defaultdict(int)
        active = 0
        max_active = 0
        negative_events = 0
        out_of_order_events = 0

        if not events:
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

        first_t = events[0][0]
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
        if "since_first_gc_us" in row:
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
            f"max_active={max_active} "
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
    traces: List[Trace],
    stats: List[Dict[str, str]],
    raw_stats: List[str],
) -> None:
    intervals, diagnostics = build_phase_intervals(traces)
    active_result = reconstruct_active(traces)
    kernel_stats = parse_kernel_stats(stats)
    event_counts = Counter(str(trace["event"]) for trace in traces)
    complete_segment_totals = build_complete_segment_totals(intervals)
    first_t = min(int(trace["t_us"]) for trace in traces) if traces else 0
    last_t = max(int(trace["t_us"]) for trace in traces) if traces else 0

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as out:
        out.write("=== CSGC heavy trace analysis ===\n")
        out.write(f"source_file={os.path.abspath(source_path)}\n")
        out.write(f"result_file={os.path.abspath(output_path)}\n")
        out.write(f"trace_events={len(traces)}\n")
        out.write(f"kernel_stat_lines={len(stats)}\n")
        out.write(f"first_trace_t_us={first_t}\n")
        out.write(f"last_trace_t_us={last_t}\n")
        out.write(f"trace_span_us={max(0, last_t - first_t)}\n")
        out.write("\n")

        out.write("=== event counts ===\n")
        for event, count in sorted(event_counts.items()):
            out.write(f"{event}: count={count}\n")
        out.write("\n")

        out.write("=== reconstructed interval statistics (microseconds) ===\n")
        for phase in PHASES:
            values = flatten_intervals(intervals[phase])
            out.write(summarize_values(f"{phase}_duration_us", values) + "\n")
        out.write(summarize_values("complete_segment_pre_ssd_post_total_us", complete_segment_totals) + "\n")
        out.write(f"complete_segment_phase_triplets={len(complete_segment_totals)}\n")
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

        out.write("=== kernel CSGC_HEAVY_STAT summary ===\n")
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
            out.write("=== raw kernel CSGC_HEAVY_STAT lines ===\n")
            for line in raw_stats:
                out.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse CSGC_HEAVY_TRACE and CSGC_HEAVY_STAT lines from a dmesg log."
    )
    parser.add_argument("logfile", help="path to the dmesg log file")
    parser.add_argument("output", help="path to the output .txt file")
    args = parser.parse_args()

    traces, stats, raw_stats = parse_input(args.logfile)

    # Keep this script silent when the input log has no heavy-trace data.
    if not traces and not stats:
        return 0

    write_result(args.output, args.logfile, traces, stats, raw_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
