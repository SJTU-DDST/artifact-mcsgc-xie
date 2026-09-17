#!/usr/bin/env python3
"""Summarize corrected section-size-16 quiet throughput runs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from analyze_europar25_original_matrix import parse_case


DEFAULT_METRICS_BATCH = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-nowait-section16-paper-metrics/20260918_025234"
)
CASE_PATTERN = re.compile(r"^nowait-section16-quiet-fio-section-16-r([1-9][0-9]*)$")


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    """Return arithmetic mean and sample standard deviation."""
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def read_success_rows(batch: Path) -> Dict[str, Dict[str, str]]:
    """Return one successful result row for every scheduled case."""
    with (batch / "case-results.tsv").open(newline="", encoding="utf-8") as handle:
        successful = {
            row["case_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
            if row["status"] == "0"
        }
    with (batch / "schedule.tsv").open(newline="", encoding="utf-8") as handle:
        scheduled = [row["case_id"] for row in csv.DictReader(handle, delimiter="\t")]
    missing = sorted(set(scheduled) - successful.keys())
    unexpected = sorted(successful.keys() - set(scheduled))
    if missing or unexpected:
        raise ValueError(f"Result mismatch: missing={missing}, unexpected={unexpected}")
    return {case_id: successful[case_id] for case_id in scheduled}


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Write dictionaries with a stable field order."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def read_metrics_throughput(batch: Path) -> List[float]:
    """Read matching throughput samples from the low-overhead metrics build."""
    path = batch / "analysis" / "section-metrics-runs.csv"
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            float(row["throughput_ops_s"])
            for row in csv.DictReader(handle)
            if int(row["section_size"]) == 16
        ]


def analyze(batch: Path, metrics_batch: Path) -> Dict[str, object]:
    """Validate three quiet runs and compare them with the metrics build."""
    rows = read_success_rows(batch)
    runs: List[Dict[str, object]] = []
    for case_id, row in rows.items():
        match = CASE_PATTERN.match(case_id)
        if not match:
            raise ValueError(f"Unexpected case ID: {case_id}")
        parsed = parse_case(row)
        runs.append(
            {
                "case_id": case_id,
                "repetition": int(match.group(1)),
                "throughput_ops_s": float(parsed["throughput_ops_s"]),
                "bandwidth_mib_s": float(parsed["bandwidth_mib_s"]),
                "output_path": row["output_path"],
            }
        )
    runs.sort(key=lambda row: int(row["repetition"]))
    repetitions = [int(row["repetition"]) for row in runs]
    if repetitions != list(range(1, len(runs) + 1)):
        raise ValueError(f"Non-contiguous repetitions: {repetitions}")

    quiet_values = [float(row["throughput_ops_s"]) for row in runs]
    quiet_mean, quiet_std = mean_std(quiet_values)
    metrics_values = read_metrics_throughput(metrics_batch)
    comparison: Dict[str, object]
    if metrics_values:
        metrics_mean, metrics_std = mean_std(metrics_values)
        comparison = {
            "status": "measured",
            "metrics_batch": str(metrics_batch),
            "metrics_samples_ops_s": metrics_values,
            "metrics_mean_ops_s": metrics_mean,
            "metrics_std_ops_s": metrics_std,
            "quiet_vs_metrics_percent": (quiet_mean / metrics_mean - 1.0) * 100.0,
        }
    else:
        comparison = {
            "status": "unavailable",
            "metrics_batch": str(metrics_batch),
        }

    summary: Dict[str, object] = {
        "batch": str(batch),
        "samples": len(runs),
        "throughput_ops_s_mean": quiet_mean,
        "throughput_ops_s_std": quiet_std,
        "throughput_ops_s_min": min(quiet_values),
        "throughput_ops_s_max": max(quiet_values),
        "metrics_build_comparison": comparison,
    }
    analysis_dir = batch / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    write_csv(analysis_dir / "section16-quiet-runs.csv", runs)
    (analysis_dir / "section16-quiet-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown = [
        "# Corrected section-size-16 quiet summary",
        "",
        f"- Batch: `{batch}`",
        f"- Samples: {len(runs)}",
        f"- Throughput: {quiet_mean / 1000:.3f} +/- {quiet_std / 1000:.3f} kop/s",
        f"- Range: {min(quiet_values) / 1000:.3f} to {max(quiet_values) / 1000:.3f} kop/s",
    ]
    if comparison["status"] == "measured":
        markdown.append(
            "- Difference from the matching metrics build: "
            f"{float(comparison['quiet_vs_metrics_percent']):+.2f}%"
        )
    (analysis_dir / "section16-quiet-summary.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--metrics-batch", type=Path, default=DEFAULT_METRICS_BATCH)
    args = parser.parse_args()
    summary = analyze(args.batch.resolve(), args.metrics_batch.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
