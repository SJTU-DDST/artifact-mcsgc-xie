#!/usr/bin/env python3
"""Analyze the strict Filebench mCSGC single-variable A/B matrix."""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping

from analyze_europar25_original_matrix import parse_case


LABELS = {
    "control": "A: Conflict-aware control",
    "standard-prefree": "B: standard prefree checkpoint",
    "pre-sync": "C: B plus pre-CSGC sync",
    "single-section": "D: sequential sections",
    "segment-window1": "E1: one active segment",
    "segment-window2": "E2: two active segments",
    "segment-window4": "E4: four active segments",
}
WORKLOAD_LABELS = {
    "filebench-fileserver": "fileserver",
    "filebench-varmail": "varmail",
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


def write_report(
    batch: Path,
    stats: Mapping[str, Mapping[str, Mapping[str, float]]],
    workloads: List[str],
    status_summaries: List[Mapping[str, object]],
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
        "output_path",
        "status_samples",
        "min_free_sections",
        "min_free_segments",
        "max_prefree_segments",
        "max_dirty_segments",
        "cp_calls_delta",
        "gc_calls_delta",
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
    report = write_report(batch, stats, ordered_workloads, status_summaries)
    (analysis / "filebench-mcsgc-ab-report.md").write_text(
        report, encoding="utf-8"
    )
    print(report)


if __name__ == "__main__":
    main()
