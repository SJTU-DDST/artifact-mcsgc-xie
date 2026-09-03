#!/usr/bin/env python3
"""Analyze the strict Filebench mCSGC single-variable A/B matrix."""

from __future__ import annotations

import bisect
import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

from analyze_europar25_original_matrix import parse_case, read_text


LABELS = {
    "control": "A: Conflict-aware control",
    "standard-prefree": "B: standard prefree checkpoint",
    "pre-sync": "C: B plus pre-CSGC sync",
    "single-section": "D: sequential sections",
    "node-checkpoint": "E: checkpoint node victim prefree space",
    "allocator-first": "F: allocate before writeback balance",
    "segment-window1": "E1: one active segment",
    "segment-window2": "E2: two active segments",
    "segment-window4": "E4: four active segments",
}
WORKLOAD_LABELS = {
    "filebench-fileserver": "fileserver",
    "filebench-varmail": "varmail",
}


def parse_filebench_details(path: Path) -> Tuple[Dict[str, object], List[Dict[str, float]]]:
    """Extract operation latency and periodic throughput from Filebench output."""
    if not path.exists():
        return {}, []
    text = read_text(path)
    summaries = [
        {
            "timestamp_s": float(stamp),
            "operations": float(operations),
            "throughput_ops_s": float(throughput),
            "latency_ms": float(latency),
        }
        for stamp, operations, throughput, latency in re.findall(
            r"^([\d.]+):\s+IO Summary:\s+([\d.]+)\s+ops\s+"
            r"([\d.]+)\s+ops/s.*?([\d.]+)ms/op$",
            text,
            flags=re.MULTILINE,
        )
    ]
    if not summaries:
        return {}, []

    running = re.search(r"^([\d.]+):\s+Running\.\.\.$", text, flags=re.MULTILINE)
    run_start_s = float(running.group(1)) if running else None
    first_timestamp = summaries[0]["timestamp_s"]
    for index, item in enumerate(summaries):
        item["sample_index"] = float(index)
        item["elapsed_s"] = item["timestamp_s"] - first_timestamp
        if run_start_s is not None:
            item["run_elapsed_s"] = item["timestamp_s"] - run_start_s

    result: Dict[str, object] = {
        "filebench_summary_count": len(summaries),
        "filebench_summary_latency_ms": (
            sum(item["latency_ms"] * item["operations"] for item in summaries)
            / sum(item["operations"] for item in summaries)
        ),
    }
    if run_start_s is not None:
        result["filebench_run_start_s"] = run_start_s
    if len(summaries) > 1:
        width = max(1, len(summaries) // 4)
        early = [item["throughput_ops_s"] for item in summaries[:width]]
        late = [item["throughput_ops_s"] for item in summaries[-width:]]
        early_mean = statistics.fmean(early)
        late_mean = statistics.fmean(late)
        result.update(
            timeline_early_mean_ops_s=early_mean,
            timeline_late_mean_ops_s=late_mean,
            timeline_late_to_early_ratio=(
                late_mean / early_mean if early_mean else float("nan")
            ),
            timeline_min_ops_s=min(
                item["throughput_ops_s"] for item in summaries
            ),
            timeline_max_ops_s=max(
                item["throughput_ops_s"] for item in summaries
            ),
        )

    operations: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"operations": 0.0, "latency_weighted_ms": 0.0, "max_ms": 0.0}
    )
    for name, count, latency, maximum in re.findall(
        r"^(\S+)\s+(\d+)ops\s+[\d.]+ops/s\s+[\d.]+mb/s\s+"
        r"([\d.]+)ms/op\s+\[[\d.]+ms\s+-\s+([\d.]+)ms\]$",
        text,
        flags=re.MULTILINE,
    ):
        count_value = float(count)
        latency_value = float(latency)
        operation = operations[name]
        operation["operations"] += count_value
        operation["latency_weighted_ms"] += latency_value * count_value
        operation["max_ms"] = max(operation["max_ms"], float(maximum))
    for name in ("wrtfile1", "writefile2", "readfile1", "deletefile1"):
        operation = operations.get(name)
        if not operation or not operation["operations"]:
            continue
        result[f"{name}_latency_ms"] = (
            operation["latency_weighted_ms"] / operation["operations"]
        )
        result[f"{name}_max_ms"] = operation["max_ms"]
    return result, summaries


def parse_kernel_lifecycle(output: Path) -> Dict[str, object]:
    """Count writeback stalls and fatal signatures in one saved kernel log."""
    candidates = [output / "dmesg.log", output / "dmesg.old"]
    texts = [read_text(path) for path in candidates if path.exists()]
    if not texts:
        return {}
    text = texts[0]
    if "blocked for more than" not in text and len(texts) > 1:
        text = texts[1]
    blocked = re.findall(
        r"INFO: task\s+([^:\s]+):\d+\s+blocked for more than", text
    )
    fatal_patterns = (
        r"\bOops:",
        r"\bBUG:",
        r"kernel NULL pointer dereference",
        r"F2FS-fs.*(?:EUCLEAN|inconsisten)",
        r"nvme.*(?:timeout|I/O error)",
    )
    return {
        "kernel_hung_task_reports": len(blocked),
        "kernel_hung_sync_reports": sum(command == "sync" for command in blocked),
        "kernel_hung_umount_reports": sum(
            command in {"umount", "umount2"} for command in blocked
        ),
        "kernel_fatal_signature_count": sum(
            len(re.findall(pattern, text, flags=re.IGNORECASE))
            for pattern in fatal_patterns
        ),
    }


def parse_status_samples(path: Path) -> List[Dict[str, object]]:
    """Parse low-frequency snapshots emitted by run_filebench.sh."""
    if not path.exists():
        return []
    records: List[Dict[str, object]] = []
    current: Dict[str, object] | None = None
    patterns = {
        "utilization_pct": re.compile(r"^Utilization:\s+(\d+)%"),
        "valid_segments": re.compile(r"^\s+- Valid:\s+(\d+)"),
        "dirty_segments": re.compile(r"^\s+- Dirty:\s+(\d+)"),
        "prefree_segments": re.compile(r"^\s+- Prefree:\s+(\d+)"),
        "free_segments": re.compile(r"^\s+- Free:\s+(\d+)\s+\((\d+)\)"),
        "cp_calls": re.compile(r"^CP calls:\s+(\d+)"),
        "gc_calls": re.compile(r"^GC calls:\s+(\d+)"),
    }
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = re.match(
            r"^=== F2FS_STATUS_SAMPLE wall=(\S+) realtime_ns=(\d+) ===$", line
        )
        if marker:
            current = {
                "wall_time": marker.group(1),
                "realtime_ns": int(marker.group(2)),
            }
            continue
        if line == "=== F2FS_STATUS_SAMPLE_END ===":
            if current and "free_sections" in current:
                records.append(current)
            current = None
            continue
        if current is None:
            continue
        for name, pattern in patterns.items():
            match = pattern.match(line)
            if not match:
                continue
            if name == "free_segments":
                current[name] = int(match.group(1))
                current["free_sections"] = int(match.group(2))
            else:
                current[name] = int(match.group(1))
            break
    if records:
        first_ns = int(records[0]["realtime_ns"])
        for record in records:
            record["elapsed_s"] = (int(record["realtime_ns"]) - first_ns) / 1e9
    return records


def summarize_status_samples(records: List[Dict[str, object]]) -> Dict[str, float]:
    """Summarize allocator pressure and GC activity for one benchmark case."""
    if not records:
        return {}

    def values(name: str) -> List[int]:
        return [int(record[name]) for record in records if name in record]

    result: Dict[str, float] = {"status_samples": float(len(records))}
    for name, reducer, output_name in (
        ("free_sections", min, "min_free_sections"),
        ("free_segments", min, "min_free_segments"),
        ("prefree_segments", max, "max_prefree_segments"),
        ("dirty_segments", max, "max_dirty_segments"),
    ):
        items = values(name)
        if items:
            result[output_name] = float(reducer(items))
    for name, output_name in (("cp_calls", "cp_calls_delta"), ("gc_calls", "gc_calls_delta")):
        items = values(name)
        if len(items) >= 2:
            result[output_name] = float(items[-1] - items[0])
    return result


def parse_phase_times(path: Path) -> Dict[str, float]:
    """Calculate the measured command and post-command teardown durations."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        phases = {
            row["phase"]: int(row["realtime_ns"])
            for row in csv.DictReader(handle, delimiter="\t")
        }
    result: Dict[str, float] = {}
    for start, end, name in (
        ("filebench_start", "filebench_end", "filebench_wall_s"),
        ("teardown_start", "teardown_end", "post_filebench_teardown_s"),
    ):
        if start in phases and end in phases:
            result[name] = (phases[end] - phases[start]) / 1e9
    return result


def parse_phase_markers(path: Path) -> Dict[str, int]:
    """Read absolute phase timestamps for cross-source timeline alignment."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["phase"]: int(row["realtime_ns"])
            for row in csv.DictReader(handle, delimiter="\t")
        }


def align_filebench_status(
    summaries: List[Dict[str, float]],
    status_records: List[Dict[str, object]],
    filebench_start_ns: int,
) -> List[Dict[str, object]]:
    """Match each periodic Filebench report to the nearest F2FS snapshot."""
    if not summaries or not status_records:
        return []

    status_times = [int(record["realtime_ns"]) for record in status_records]
    aligned: List[Dict[str, object]] = []
    previous_status_index: int | None = None
    for summary in summaries:
        target_ns = filebench_start_ns + int(summary["timestamp_s"] * 1e9)
        insertion = bisect.bisect_left(status_times, target_ns)
        candidates = [
            index
            for index in (insertion - 1, insertion)
            if 0 <= index < len(status_records)
        ]
        if not candidates:
            continue
        status_index = min(
            candidates,
            key=lambda index: abs(status_times[index] - target_ns),
        )
        status = status_records[status_index]
        item: Dict[str, object] = {
            **summary,
            "status_offset_s": (status_times[status_index] - target_ns) / 1e9,
        }
        for name in (
            "utilization_pct",
            "valid_segments",
            "dirty_segments",
            "prefree_segments",
            "free_segments",
            "free_sections",
            "cp_calls",
            "gc_calls",
        ):
            if name in status:
                item[name] = status[name]
        if previous_status_index is not None and status_index != previous_status_index:
            previous = status_records[previous_status_index]
            for name in ("cp_calls", "gc_calls"):
                if name in item and name in previous:
                    item[f"{name}_interval_delta"] = (
                        int(item[name]) - int(previous[name])
                    )
        previous_status_index = status_index
        aligned.append(item)
    return aligned


def summarize_collapse(
    timeline: List[Dict[str, object]],
) -> Dict[str, object]:
    """Locate the first transient and sustained half-throughput collapse."""
    if len(timeline) < 3:
        return {}
    baseline_width = min(3, len(timeline))
    baseline = statistics.median(
        float(item["throughput_ops_s"])
        for item in timeline[:baseline_width]
    )
    threshold = baseline * 0.5
    first_index = next(
        (
            index
            for index, item in enumerate(timeline)
            if float(item["throughput_ops_s"]) < threshold
        ),
        None,
    )
    sustained_index = next(
        (
            index
            for index in range(len(timeline) - 2)
            if all(
                float(timeline[offset]["throughput_ops_s"]) < threshold
                for offset in range(index, index + 3)
            )
        ),
        None,
    )
    result: Dict[str, object] = {
        "collapse_baseline_ops_s": baseline,
        "collapse_threshold_ops_s": threshold,
    }
    for prefix, index in (
        ("first_half", first_index),
        ("sustained_half", sustained_index),
    ):
        if index is None:
            continue
        item = timeline[index]
        result[f"{prefix}_elapsed_s"] = float(
            item.get("run_elapsed_s", item["timestamp_s"])
        )
        result[f"{prefix}_ops_s"] = float(item["throughput_ops_s"])
        for name in (
            "free_sections",
            "free_segments",
            "prefree_segments",
            "dirty_segments",
            "cp_calls",
            "gc_calls",
        ):
            if name in item:
                result[f"{prefix}_{name}"] = item[name]
    return result


def read_rows(path: Path) -> List[Dict[str, str]]:
    """Read the latest successful result row for each scheduled case."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle, delimiter="\t")
            if row["status"] == "0"
        ]
    latest = {row["case_id"]: row for row in rows}
    return list(latest.values())


def read_schedule(path: Path) -> Dict[str, Dict[str, str]]:
    """Index the immutable schedule by case ID."""
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["case_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
        }


def summarize(values: List[float]) -> Dict[str, float]:
    """Return stable descriptive statistics for one configuration/workload."""
    result = {
        "count": len(values),
        "mean_ops_s": statistics.fmean(values),
        "median_ops_s": statistics.median(values),
        "min_ops_s": min(values),
        "max_ops_s": max(values),
        "stdev_ops_s": statistics.stdev(values) if len(values) > 1 else 0.0,
    }
    return result


def geometric_mean(values: List[float]) -> float:
    """Return the multiplicative mean of positive ratios."""
    return math.exp(statistics.fmean([math.log(value) for value in values]))


def format_metric(item: Mapping[str, object], name: str) -> str:
    """Format an optional numeric metric for Markdown tables."""
    value = item.get(name)
    return "-" if value is None else f"{float(value):.3f}"


def write_report(
    batch: Path,
    stats: Mapping[str, Mapping[str, Mapping[str, float]]],
    workloads: List[str],
    status_summaries: List[Mapping[str, object]],
    samples: List[Mapping[str, object]],
) -> str:
    """Build a concise report with explicit A/B denominators."""
    configurations = sorted(
        stats,
        key=lambda item: (
            item != "control",
            list(LABELS).index(item) if item in LABELS else 99,
        ),
    )
    has_control = "control" in stats
    mean_ratio_heading = "Mean vs A" if has_control else "Mean vs A (not available)"
    median_ratio_heading = (
        "Median vs A" if has_control else "Median vs A (not available)"
    )
    lines = [
        "# mCSGC Filebench Single-Variable A/B Results",
        "",
        f"- Batch: `{batch}`",
        "- Firmware is fixed to SSD1t; all configurations use identical workloads.",
        "- A is the current Conflict-aware candidate; B restores only standard",
        "  prefree checkpoints; C adds pre-CSGC sync to B; D retains unsafe",
        "  reclaim but processes sections sequentially. E1/E2/E4 additionally",
        "  limit active segment collectors within each section.",
        "",
        "## Throughput",
        "",
        f"| Configuration | Workload | Samples | Mean ops/s | Median | Min | Max | Stddev | {mean_ratio_heading} | {median_ratio_heading} |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for configuration in configurations:
        for workload in workloads:
            item = stats[configuration][workload]
            mean_ratio = "-"
            median_ratio = "-"
            if has_control:
                control_mean = stats["control"][workload]["mean_ops_s"]
                control_median = stats["control"][workload]["median_ops_s"]
                mean_ratio = f"{item['mean_ops_s'] / control_mean:.3f}x"
                median_ratio = f"{item['median_ops_s'] / control_median:.3f}x"
            lines.append(
                f"| {LABELS.get(configuration, configuration)} "
                f"| {WORKLOAD_LABELS[workload]} | {int(item['count'])} "
                f"| {item['mean_ops_s']:.3f} | {item['median_ops_s']:.3f} "
                f"| {item['min_ops_s']:.3f} | {item['max_ops_s']:.3f} "
                f"| {item['stdev_ops_s']:.3f} | {mean_ratio} | {median_ratio} |"
            )

    lines.extend(["", "## Single-Variable Comparisons", ""])
    if has_control:
        for configuration in configurations:
            if configuration == "control":
                continue
            mean_ratios = [
                stats[configuration][workload]["mean_ops_s"]
                / stats["control"][workload]["mean_ops_s"]
                for workload in workloads
            ]
            median_ratios = [
                stats[configuration][workload]["median_ops_s"]
                / stats["control"][workload]["median_ops_s"]
                for workload in workloads
            ]
            details = ", ".join(
                f"{WORKLOAD_LABELS[workload]} mean {mean_ratio:.3f}x, "
                f"median {median_ratio:.3f}x"
                for workload, mean_ratio, median_ratio in zip(
                    workloads, mean_ratios, median_ratios
                )
            )
            lines.append(
                f"- **{LABELS.get(configuration, configuration)}**: "
                f"{details}; geometric mean of mean ratios "
                f"{geometric_mean(mean_ratios):.3f}x, geometric mean of "
                f"median ratios {geometric_mean(median_ratios):.3f}x."
            )
    else:
        lines.append(
            "- This batch has no A/control cases. Absolute results are valid, "
            "but relative A/B ratios require a separate matching control batch."
        )

    lines.extend(
        [
            "",
            "Interpretation limit: a single-sample screen can identify only large",
            "effects. Key configurations require three repetitions before a final",
            "conclusion.",
            "",
        ]
    )
    detail_samples = [
        item for item in samples if "filebench_summary_latency_ms" in item
    ]
    if detail_samples:
        lines.extend(
            [
                "## Application Latency",
                "",
                "| Case | Throughput (ops/s) | IO latency (ms) | "
                "wrtfile1 mean (ms) | wrtfile1 max (ms) | readfile1 mean (ms) |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for item in sorted(detail_samples, key=lambda value: str(value["case_id"])):
            lines.append(
                f"| {item['case_id']} | {item['throughput_ops_s']:.3f} "
                f"| {item['filebench_summary_latency_ms']:.3f} "
                f"| {format_metric(item, 'wrtfile1_latency_ms')} "
                f"| {format_metric(item, 'wrtfile1_max_ms')} "
                f"| {format_metric(item, 'readfile1_latency_ms')} |"
            )
        lines.extend(
            [
                "",
                "Operation means are weighted by operation count when Filebench emits",
                "periodic summaries. They cover application-visible buffered operations,",
                "not the later filesystem sync and unmount.",
                "",
            ]
        )

    timeline_samples = [
        item for item in samples if "timeline_late_to_early_ratio" in item
    ]
    if timeline_samples:
        lines.extend(
            [
                "## Throughput Evolution",
                "",
                "| Case | Intervals | Early mean | Late mean | Late/Early | Min | Max |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in sorted(timeline_samples, key=lambda value: str(value["case_id"])):
            lines.append(
                f"| {item['case_id']} | {item['filebench_summary_count']} "
                f"| {item['timeline_early_mean_ops_s']:.3f} "
                f"| {item['timeline_late_mean_ops_s']:.3f} "
                f"| {item['timeline_late_to_early_ratio']:.3f}x "
                f"| {item['timeline_min_ops_s']:.3f} "
                f"| {item['timeline_max_ops_s']:.3f} |"
            )
        lines.extend(
            [
                "",
                "Early and late means use the first and last quarter of complete",
                "Filebench reporting intervals. A single final summary cannot establish",
                "when throughput changed.",
                "",
            ]
        )

    collapse_samples = [
        item for item in samples if "collapse_baseline_ops_s" in item
    ]
    if collapse_samples:
        lines.extend(
            [
                "## Throughput Collapse And Space State",
                "",
                "| Case | Early baseline | First below 50% (s) | "
                "Sustained below 50% (s) | Free sections | Prefree segments | "
                "Dirty segments | CP calls | GC calls |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in sorted(
            collapse_samples, key=lambda value: str(value["case_id"])
        ):
            state_prefix = (
                "sustained_half"
                if "sustained_half_elapsed_s" in item
                else "first_half"
            )
            lines.append(
                f"| {item['case_id']} "
                f"| {format_metric(item, 'collapse_baseline_ops_s')} "
                f"| {format_metric(item, 'first_half_elapsed_s')} "
                f"| {format_metric(item, 'sustained_half_elapsed_s')} "
                f"| {format_metric(item, f'{state_prefix}_free_sections')} "
                f"| {format_metric(item, f'{state_prefix}_prefree_segments')} "
                f"| {format_metric(item, f'{state_prefix}_dirty_segments')} "
                f"| {format_metric(item, f'{state_prefix}_cp_calls')} "
                f"| {format_metric(item, f'{state_prefix}_gc_calls')} |"
            )
        lines.extend(
            [
                "",
                "The early baseline is the median of the first three complete",
                "reporting intervals. Sustained collapse requires three consecutive",
                "intervals below half that baseline. Space state is matched from the",
                "nearest low-frequency F2FS snapshot and is diagnostic only.",
                "",
            ]
        )

    kernel_samples = [
        item
        for item in samples
        if int(item.get("kernel_hung_task_reports", 0))
        or int(item.get("kernel_fatal_signature_count", 0))
    ]
    if kernel_samples:
        lines.extend(
            [
                "## Kernel Lifecycle Warnings",
                "",
                "| Case | Hung-task reports | sync | umount | Fatal signatures |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for item in sorted(kernel_samples, key=lambda value: str(value["case_id"])):
            lines.append(
                f"| {item['case_id']} | {item['kernel_hung_task_reports']} "
                f"| {item['kernel_hung_sync_reports']} "
                f"| {item['kernel_hung_umount_reports']} "
                f"| {item['kernel_fatal_signature_count']} |"
            )
        lines.extend(
            [
                "",
                "A zero process exit status does not make a case healthy when sync or",
                "unmount exceeds the kernel hung-task threshold. Such cases must not be",
                "used as correctness evidence.",
                "",
            ]
        )

    if status_summaries:
        lines.extend(
            [
                "## Low-Space Timeline",
                "",
                "| Case | Samples | Min free sections | Max prefree segments | Max dirty segments | CP delta | GC delta |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for item in sorted(status_summaries, key=lambda value: str(value["case_id"])):
            lines.append(
                f"| {item['case_id']} | {int(item.get('status_samples', 0))} "
                f"| {item.get('min_free_sections', '-')} "
                f"| {item.get('max_prefree_segments', '-')} "
                f"| {item.get('max_dirty_segments', '-')} "
                f"| {item.get('cp_calls_delta', '-')} "
                f"| {item.get('gc_calls_delta', '-')} |"
            )
        lines.extend(
            [
                "",
                "The raw five-second snapshots are preserved beside each Filebench log;",
                "this table is diagnostic and is not part of the throughput denominator.",
                "",
            ]
        )
    timed_samples = [
        item for item in samples if "post_filebench_teardown_s" in item
    ]
    if timed_samples:
        lines.extend(
            [
                "## Lifecycle Tail",
                "",
                "| Case | Filebench wall time (s) | Post-Filebench teardown (s) |",
                "|---|---:|---:|",
            ]
        )
        for item in sorted(timed_samples, key=lambda value: str(value["case_id"])):
            lines.append(
                f"| {item['case_id']} | {item.get('filebench_wall_s', '-')} "
                f"| {item['post_filebench_teardown_s']:.3f} |"
            )
        lines.extend(
            [
                "",
                "The teardown interval starts after Filebench exits and includes the fixed",
                "five-second delay, filesystem sync, unmount, and statistics collection.",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    """Parse one completed batch and write CSV, JSON, and Markdown outputs."""
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {Path(sys.argv[0]).name} BATCH_DIR")
    batch = Path(sys.argv[1]).resolve()
    schedule = read_schedule(batch / "schedule.tsv")
    rows = read_rows(batch / "case-results.tsv")
    if len(rows) != len(schedule):
        raise SystemExit(
            f"Expected {len(schedule)} successful cases, found {len(rows)}"
        )

    samples: List[Dict[str, object]] = []
    status_records: List[Dict[str, object]] = []
    status_summaries: List[Dict[str, object]] = []
    timeline_records: List[Dict[str, object]] = []
    space_timeline_records: List[Dict[str, object]] = []
    grouped: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        case_id = row["case_id"]
        scheduled = schedule[case_id]
        parsed = parse_case(row)
        throughput = float(parsed["throughput_ops_s"])
        configuration = scheduled["configuration"]
        workload = next(
            suffix
            for suffix in WORKLOAD_LABELS
            if f"-{suffix}-" in case_id
        )
        repetition = int(case_id.rsplit("-r", 1)[1])
        case_status_records = parse_status_samples(
            Path(row["output_path"]) / "f2fs-status-timeline.log"
        )
        status_summary: Dict[str, object] = {
            "case_id": case_id,
            **summarize_status_samples(case_status_records),
        }
        phase_path = Path(row["output_path"]) / "filebench-phase-times.tsv"
        phase_times = parse_phase_times(phase_path)
        phase_markers = parse_phase_markers(phase_path)
        filebench_details, case_timeline = parse_filebench_details(
            Path(row["output_path"]) / "filebench.log"
        )
        case_space_timeline: List[Dict[str, object]] = []
        if "filebench_start" in phase_markers:
            filebench_start_ns = phase_markers["filebench_start"]
            for record in case_status_records:
                record["filebench_elapsed_s"] = (
                    int(record["realtime_ns"]) - filebench_start_ns
                ) / 1e9
            case_space_timeline = align_filebench_status(
                case_timeline, case_status_records, filebench_start_ns
            )
            for record in case_space_timeline:
                space_timeline_records.append(
                    {
                        "case_id": case_id,
                        "configuration": configuration,
                        "workload": workload,
                        "repetition": repetition,
                        **record,
                    }
                )
        collapse = summarize_collapse(case_space_timeline or case_timeline)
        kernel_lifecycle = parse_kernel_lifecycle(Path(row["output_path"]))
        if case_status_records:
            status_summaries.append(status_summary)
            for record in case_status_records:
                status_records.append(
                    {
                        "case_id": case_id,
                        "configuration": configuration,
                        "workload": workload,
                        "repetition": repetition,
                        **record,
                    }
                )
        for record in case_timeline:
            timeline_records.append(
                {
                    "case_id": case_id,
                    "configuration": configuration,
                    "workload": workload,
                    "repetition": repetition,
                    **record,
                }
            )
        grouped[configuration][workload].append(throughput)
        samples.append(
            {
                "case_id": case_id,
                "configuration": configuration,
                "workload": workload,
                "repetition": repetition,
                "throughput_ops_s": throughput,
                "duration_s": float(row["duration_s"]),
                "output_path": row["output_path"],
                **phase_times,
                **filebench_details,
                **collapse,
                **kernel_lifecycle,
                **{key: value for key, value in status_summary.items() if key != "case_id"},
            }
        )

    required_workloads = {
        next(suffix for suffix in WORKLOAD_LABELS if f"-{suffix}-" in case_id)
        for case_id in schedule
    }
    for configuration, workloads in grouped.items():
        if set(workloads) != required_workloads:
            raise SystemExit(f"Incomplete workloads for {configuration}")
    ordered_workloads = [
        workload for workload in WORKLOAD_LABELS if workload in required_workloads
    ]

    stats = {
        configuration: {
            workload: summarize(values)
            for workload, values in workloads.items()
        }
        for configuration, workloads in grouped.items()
    }
    analysis = batch / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    sample_fields = [
        "case_id",
        "configuration",
        "workload",
        "repetition",
        "throughput_ops_s",
        "duration_s",
        "filebench_wall_s",
        "post_filebench_teardown_s",
        "filebench_summary_count",
        "filebench_run_start_s",
        "filebench_summary_latency_ms",
        "wrtfile1_latency_ms",
        "wrtfile1_max_ms",
        "writefile2_latency_ms",
        "writefile2_max_ms",
        "readfile1_latency_ms",
        "readfile1_max_ms",
        "deletefile1_latency_ms",
        "deletefile1_max_ms",
        "timeline_early_mean_ops_s",
        "timeline_late_mean_ops_s",
        "timeline_late_to_early_ratio",
        "timeline_min_ops_s",
        "timeline_max_ops_s",
        "kernel_hung_task_reports",
        "kernel_hung_sync_reports",
        "kernel_hung_umount_reports",
        "kernel_fatal_signature_count",
        "output_path",
        "status_samples",
        "min_free_sections",
        "min_free_segments",
        "max_prefree_segments",
        "max_dirty_segments",
        "cp_calls_delta",
        "gc_calls_delta",
        "collapse_baseline_ops_s",
        "collapse_threshold_ops_s",
        "first_half_elapsed_s",
        "first_half_ops_s",
        "first_half_free_sections",
        "first_half_free_segments",
        "first_half_prefree_segments",
        "first_half_dirty_segments",
        "first_half_cp_calls",
        "first_half_gc_calls",
        "sustained_half_elapsed_s",
        "sustained_half_ops_s",
        "sustained_half_free_sections",
        "sustained_half_free_segments",
        "sustained_half_prefree_segments",
        "sustained_half_dirty_segments",
        "sustained_half_cp_calls",
        "sustained_half_gc_calls",
    ]
    with (analysis / "samples.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=sample_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(samples, key=lambda item: item["case_id"]))

    if status_records:
        status_fields = [
            "case_id",
            "configuration",
            "workload",
            "repetition",
            "wall_time",
            "realtime_ns",
            "elapsed_s",
            "filebench_elapsed_s",
            "utilization_pct",
            "valid_segments",
            "dirty_segments",
            "prefree_segments",
            "free_segments",
            "free_sections",
            "cp_calls",
            "gc_calls",
        ]
        with (analysis / "f2fs-status-samples.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=status_fields, extrasaction="ignore", lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(status_records)

    if timeline_records:
        timeline_fields = [
            "case_id",
            "configuration",
            "workload",
            "repetition",
            "sample_index",
            "elapsed_s",
            "run_elapsed_s",
            "timestamp_s",
            "operations",
            "throughput_ops_s",
            "latency_ms",
        ]
        with (analysis / "filebench-timeline.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle, fieldnames=timeline_fields, lineterminator="\n"
            )
            writer.writeheader()
            writer.writerows(timeline_records)

    if space_timeline_records:
        space_timeline_fields = [
            "case_id",
            "configuration",
            "workload",
            "repetition",
            "sample_index",
            "elapsed_s",
            "run_elapsed_s",
            "timestamp_s",
            "operations",
            "throughput_ops_s",
            "latency_ms",
            "status_offset_s",
            "utilization_pct",
            "valid_segments",
            "dirty_segments",
            "prefree_segments",
            "free_segments",
            "free_sections",
            "cp_calls",
            "gc_calls",
            "cp_calls_interval_delta",
            "gc_calls_interval_delta",
        ]
        with (analysis / "filebench-space-timeline.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=space_timeline_fields,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(space_timeline_records)

    payload = {
        "batch": str(batch),
        "control_available": "control" in grouped,
        "workloads": ordered_workloads,
        "samples": samples,
        "statistics": stats,
    }
    (analysis / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = write_report(batch, stats, ordered_workloads, status_summaries, samples)
    (analysis / "filebench-mcsgc-ab-report.md").write_text(
        report, encoding="utf-8"
    )
    print(report)


if __name__ == "__main__":
    main()
