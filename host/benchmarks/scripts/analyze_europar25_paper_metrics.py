#!/usr/bin/env python3
"""Analyze Host-only paper metrics and strict Filebench timelines."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from analyze_europar25_original_matrix import parse_case


DEFAULT_QUIET_BATCH = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-mcsgc-nowait-reproduction/20260916_045438"
)
CASE_PATTERN = re.compile(
    r"^paper-metrics-(fio-section-(1|2|4|8|16)|filebench-period)-r([1-9][0-9]*)$"
)


def read_success_rows(batch: Path) -> Dict[str, Dict[str, str]]:
    """Return the latest successful result row for every case ID."""
    path = batch / "case-results.tsv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t") if row["status"] == "0"]
    return {row["case_id"]: row for row in rows}


def parse_key_values(path: Path) -> Dict[str, int]:
    """Parse the single-line gc_paper_metrics sysfs snapshot."""
    values: Dict[str, int] = {}
    for key, value in re.findall(r"([a-z0-9_]+)=([0-9]+)", path.read_text()):
        values[key] = int(value)
    required = {
        "active",
        "epoch",
        "csgc_sections",
        "csgc_time_ns",
        "csgc_blocks",
        "csgc_avg_ns_per_block",
        "origc_sections",
        "origc_time_ns",
        "origc_blocks",
        "origc_avg_ns_per_block",
    }
    missing = required - values.keys()
    if missing:
        raise ValueError(f"Missing metrics in {path}: {sorted(missing)}")
    if values["active"] != 0:
        raise ValueError(f"Metrics epoch is still active in {path}")
    return values


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    """Return arithmetic mean and sample standard deviation."""
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def read_quiet_section8(batch: Path) -> List[float]:
    """Read the three quiet section-size-eight throughput samples."""
    rows = read_success_rows(batch)
    samples: List[float] = []
    pattern = re.compile(r"^nowait-quiet-fio-section-8-r[1-9][0-9]*$")
    for case_id, row in rows.items():
        if not pattern.match(case_id):
            continue
        metrics = parse_case(row)
        samples.append(float(metrics["throughput_ops_s"]))
    if not samples:
        raise ValueError(f"No quiet section-size-eight samples in {batch}")
    return samples


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    """Write dictionaries with a stable column order."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def analyze(batch: Path, quiet_batch: Path) -> Dict[str, object]:
    """Validate all runs and emit structured summaries for plotting."""
    rows = read_success_rows(batch)
    section_runs: List[Dict[str, object]] = []
    timeline_runs: List[Dict[str, object]] = []

    for case_id, row in sorted(rows.items()):
        match = CASE_PATTERN.match(case_id)
        if not match:
            raise ValueError(f"Unexpected case ID: {case_id}")
        suffix, section_text, repetition_text = match.groups()
        repetition = int(repetition_text)
        output = Path(row["output_path"])
        parsed = parse_case(row)
        if section_text is not None:
            host = parse_key_values(output / "gc-paper-metrics.log")
            if host["csgc_blocks"] <= 0 or host["csgc_sections"] <= 0:
                raise ValueError(f"No measured CSGC work in {case_id}")
            section_runs.append(
                {
                    "case_id": case_id,
                    "section_size": int(section_text),
                    "repetition": repetition,
                    "throughput_ops_s": float(parsed["throughput_ops_s"]),
                    "bandwidth_mib_s": float(parsed["bandwidth_mib_s"]),
                    **host,
                    "output_path": str(output),
                }
            )
        else:
            timeline = parsed.get("timeline") or []
            if len(timeline) != 60:
                raise ValueError(f"Expected 60 timeline points for {case_id}, found {len(timeline)}")
            origin = float(timeline[0][0])
            for sample_index, (stamp, throughput) in enumerate(timeline, start=1):
                timeline_runs.append(
                    {
                        "case_id": case_id,
                        "repetition": repetition,
                        "sample_index": sample_index,
                        "elapsed_s": float(stamp) - origin + 5.0,
                        "throughput_ops_s": float(throughput),
                        "output_path": str(output),
                    }
                )

    repetitions = sorted(
        {int(row["repetition"]) for row in section_runs}
        | {int(row["repetition"]) for row in timeline_runs}
    )
    if repetitions != list(range(1, len(repetitions) + 1)):
        raise ValueError(f"Non-contiguous repetitions: {repetitions}")
    repetition_count = len(repetitions)
    expected_section_runs = 5 * repetition_count
    expected_timeline_rows = repetition_count * 60
    if len(section_runs) != expected_section_runs:
        raise ValueError(f"Expected {expected_section_runs} section runs, found {len(section_runs)}")
    if len(timeline_runs) != expected_timeline_rows:
        raise ValueError(f"Expected {expected_timeline_rows} timeline rows, found {len(timeline_runs)}")

    section_summary: List[Dict[str, object]] = []
    for section_size in (1, 2, 4, 8, 16):
        samples = [row for row in section_runs if row["section_size"] == section_size]
        throughput_mean, throughput_std = mean_std(
            [float(row["throughput_ops_s"]) for row in samples]
        )
        latency_mean, latency_std = mean_std(
            [float(row["csgc_avg_ns_per_block"]) / 1000.0 for row in samples]
        )
        blocks_mean, blocks_std = mean_std([float(row["csgc_blocks"]) for row in samples])
        section_summary.append(
            {
                "section_size": section_size,
                "samples": len(samples),
                "throughput_ops_s_mean": throughput_mean,
                "throughput_ops_s_std": throughput_std,
                "migration_us_mean": latency_mean,
                "migration_us_std": latency_std,
                "migrated_blocks_mean": blocks_mean,
                "migrated_blocks_std": blocks_std,
            }
        )

    timeline_summary: List[Dict[str, object]] = []
    for sample_index in range(1, 61):
        samples = [row for row in timeline_runs if row["sample_index"] == sample_index]
        throughput_mean, throughput_std = mean_std(
            [float(row["throughput_ops_s"]) for row in samples]
        )
        timeline_summary.append(
            {
                "sample_index": sample_index,
                "elapsed_s": statistics.fmean(float(row["elapsed_s"]) for row in samples),
                "throughput_ops_s_mean": throughput_mean,
                "throughput_ops_s_std": throughput_std,
            }
        )

    quiet_samples = read_quiet_section8(quiet_batch)
    metrics_samples = [
        float(row["throughput_ops_s"]) for row in section_runs if row["section_size"] == 8
    ]
    quiet_mean, quiet_std = mean_std(quiet_samples)
    metrics_mean, metrics_std = mean_std(metrics_samples)
    overhead_percent = (metrics_mean / quiet_mean - 1.0) * 100.0

    analysis_dir = batch / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    write_csv(analysis_dir / "section-metrics-runs.csv", section_runs[0].keys(), section_runs)
    write_csv(
        analysis_dir / "section-metrics-summary.csv", section_summary[0].keys(), section_summary
    )
    write_csv(analysis_dir / "filebench-timeline-runs.csv", timeline_runs[0].keys(), timeline_runs)
    write_csv(
        analysis_dir / "filebench-timeline-summary.csv",
        timeline_summary[0].keys(),
        timeline_summary,
    )

    summary: Dict[str, object] = {
        "batch": str(batch),
        "quiet_batch": str(quiet_batch),
        "section_runs": len(section_runs),
        "filebench_timeline_runs": repetition_count,
        "filebench_samples_per_run": 60,
        "section_summary": section_summary,
        "section8_overhead_check": {
            "quiet_samples_ops_s": quiet_samples,
            "metrics_samples_ops_s": metrics_samples,
            "quiet_mean_ops_s": quiet_mean,
            "quiet_std_ops_s": quiet_std,
            "metrics_mean_ops_s": metrics_mean,
            "metrics_std_ops_s": metrics_std,
            "difference_percent": overhead_percent,
        },
    }
    (analysis_dir / "paper-metrics-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    markdown = [
        "# NOWAIT paper metrics summary",
        "",
        f"- Batch: `{batch}`",
        f"- Section-size runs: {len(section_runs)}",
        f"- Strict Filebench timelines: {repetition_count} x 300 s, 60 samples per run",
        f"- Section-size-eight throughput difference from quiet build: {overhead_percent:+.2f}%",
        "",
        "| Section size | Throughput (kop/s) | Migration latency (us/block) |",
        "|---:|---:|---:|",
    ]
    for row in section_summary:
        markdown.append(
            f"| {row['section_size']} | {float(row['throughput_ops_s_mean']) / 1000:.3f} "
            f"+/- {float(row['throughput_ops_s_std']) / 1000:.3f} | "
            f"{float(row['migration_us_mean']):.3f} +/- "
            f"{float(row['migration_us_std']):.3f} |"
        )
    (analysis_dir / "paper-metrics-summary.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--quiet-batch", type=Path, default=DEFAULT_QUIET_BATCH)
    args = parser.parse_args()
    summary = analyze(args.batch.resolve(), args.quiet_batch.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
