#!/usr/bin/env python3
"""Analyze the strict Filebench mCSGC single-variable A/B matrix."""

from __future__ import annotations

import csv
import json
import math
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
}
WORKLOAD_LABELS = {
    "filebench-fileserver": "fileserver",
    "filebench-varmail": "varmail",
}


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
    ratio_heading = "Mean vs A" if has_control else "Mean vs A (not available)"
    lines = [
        "# mCSGC Filebench Single-Variable A/B Results",
        "",
        f"- Batch: `{batch}`",
        "- Firmware is fixed to SSD1t; all configurations use identical workloads.",
        "- A is the current Conflict-aware candidate; B restores only standard",
        "  prefree checkpoints; C adds pre-CSGC sync to B; D retains unsafe",
        "  reclaim but processes sections sequentially.",
        "",
        "## Throughput",
        "",
        f"| Configuration | Workload | Samples | Mean ops/s | Median | Min | Max | Stddev | {ratio_heading} |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for configuration in configurations:
        for workload in workloads:
            item = stats[configuration][workload]
            ratio = "-"
            if has_control:
                control = stats["control"][workload]["mean_ops_s"]
                ratio = f"{item['mean_ops_s'] / control:.3f}x"
            lines.append(
                f"| {LABELS.get(configuration, configuration)} "
                f"| {WORKLOAD_LABELS[workload]} | {int(item['count'])} "
                f"| {item['mean_ops_s']:.3f} | {item['median_ops_s']:.3f} "
                f"| {item['min_ops_s']:.3f} | {item['max_ops_s']:.3f} "
                f"| {item['stdev_ops_s']:.3f} | {ratio} |"
            )

    lines.extend(["", "## Single-Variable Comparisons", ""])
    if has_control:
        for configuration in configurations:
            if configuration == "control":
                continue
            ratios = [
                stats[configuration][workload]["mean_ops_s"]
                / stats["control"][workload]["mean_ops_s"]
                for workload in workloads
            ]
            details = ", ".join(
                f"{WORKLOAD_LABELS[workload]} {ratio:.3f}x"
                for workload, ratio in zip(workloads, ratios)
            )
            lines.append(
                f"- **{LABELS.get(configuration, configuration)}**: "
                f"{details}, selected-workload geometric mean "
                f"{geometric_mean(ratios):.3f}x."
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
    ]
    with (analysis / "samples.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=sample_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(samples, key=lambda item: item["case_id"]))

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
    report = write_report(batch, stats, ordered_workloads)
    (analysis / "filebench-mcsgc-ab-report.md").write_text(
        report, encoding="utf-8"
    )
    print(report)


if __name__ == "__main__":
    main()
