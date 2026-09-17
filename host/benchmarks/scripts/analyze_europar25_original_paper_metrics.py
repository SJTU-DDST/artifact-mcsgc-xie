#!/usr/bin/env python3
"""Analyze original ORI/CSGC migration metrics and strict Filebench traces."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

from analyze_europar25_original_matrix import parse_case


DEFAULT_BASELINE_BATCH = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-original-reproduction/20260828_185514"
)
CASE_PATTERN = re.compile(
    r"^original-paper-metrics-(cs|ori)-"
    r"(fio-section-(1|2|4|8|16)|filebench-period)-r([1-9][0-9]*)$"
)


def read_success_rows(batch: Path) -> Dict[str, Dict[str, str]]:
    """Return one successful result row for every scheduled case."""
    results_path = batch / "case-results.tsv"
    schedule_path = batch / "schedule.tsv"
    with results_path.open(newline="", encoding="utf-8") as handle:
        successful = {
            row["case_id"]: row
            for row in csv.DictReader(handle, delimiter="\t")
            if row["status"] == "0"
        }
    with schedule_path.open(newline="", encoding="utf-8") as handle:
        scheduled = [row["case_id"] for row in csv.DictReader(handle, delimiter="\t")]
    missing = sorted(set(scheduled) - successful.keys())
    unexpected = sorted(successful.keys() - set(scheduled))
    if missing or unexpected:
        raise ValueError(f"Result mismatch: missing={missing}, unexpected={unexpected}")
    return {case_id: successful[case_id] for case_id in scheduled}


def parse_key_values(path: Path) -> Dict[str, int]:
    """Parse and validate one frozen gc_paper_metrics snapshot."""
    values = {
        key: int(value)
        for key, value in re.findall(r"([a-z0-9_]+)=([0-9]+)", path.read_text())
    }
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


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Write dictionaries with the first row's stable field order."""
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def read_baseline_throughput(batch: Path, mode: str, section_size: int) -> float:
    """Read the matching quiet original-system throughput sample."""
    case_id = f"{mode}-fio-section-{section_size}"
    row = read_success_rows(batch).get(case_id)
    if row is None:
        raise ValueError(f"Missing baseline case {case_id} in {batch}")
    return float(parse_case(row)["throughput_ops_s"])


def analyze(batch: Path, baseline_batch: Path) -> Dict[str, object]:
    """Validate the matrix and emit plot-ready ORI/CSGC summaries."""
    rows = read_success_rows(batch)
    section_runs: List[Dict[str, object]] = []
    timeline_runs: List[Dict[str, object]] = []
    filebench_runs: List[Dict[str, object]] = []

    for case_id, row in rows.items():
        match = CASE_PATTERN.match(case_id)
        if not match:
            raise ValueError(f"Unexpected case ID: {case_id}")
        mode, _, section_text, repetition_text = match.groups()
        repetition = int(repetition_text)
        output = Path(row["output_path"])
        parsed = parse_case(row)
        if section_text is not None:
            host = parse_key_values(output / "gc-paper-metrics.log")
            metric_prefix = "csgc" if mode == "cs" else "origc"
            other_prefix = "origc" if mode == "cs" else "csgc"
            blocks = host[f"{metric_prefix}_blocks"]
            time_ns = host[f"{metric_prefix}_time_ns"]
            sections = host[f"{metric_prefix}_sections"]
            if blocks <= 0 or sections <= 0 or time_ns <= 0:
                raise ValueError(
                    f"No complete {metric_prefix} migration work in {case_id}: {host}"
                )
            section_runs.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "section_size": int(section_text),
                    "repetition": repetition,
                    "throughput_ops_s": float(parsed["throughput_ops_s"]),
                    "bandwidth_mib_s": float(parsed["bandwidth_mib_s"]),
                    "metric_path": metric_prefix,
                    "measured_sections": sections,
                    "measured_blocks": blocks,
                    "measured_time_ns": time_ns,
                    "migration_ns_per_block": time_ns / blocks,
                    "other_path_sections": host[f"{other_prefix}_sections"],
                    "other_path_blocks": host[f"{other_prefix}_blocks"],
                    "output_path": str(output),
                }
            )
            continue

        timeline = parsed.get("timeline") or []
        if len(timeline) != 60:
            raise ValueError(f"Expected 60 timeline points for {case_id}, found {len(timeline)}")
        filebench_runs.append(
            {
                "case_id": case_id,
                "mode": mode,
                "repetition": repetition,
                "throughput_ops_s": float(parsed["throughput_ops_s"]),
                "output_path": str(output),
            }
        )
        origin = float(timeline[0][0])
        for sample_index, (stamp, throughput) in enumerate(timeline, start=1):
            timeline_runs.append(
                {
                    "case_id": case_id,
                    "mode": mode,
                    "repetition": repetition,
                    "sample_index": sample_index,
                    "elapsed_s": float(stamp) - origin + 5.0,
                    "throughput_ops_s": float(throughput),
                    "output_path": str(output),
                }
            )

    repetitions = sorted({int(row["repetition"]) for row in section_runs})
    modes = sorted({str(row["mode"]) for row in section_runs})
    section_sizes = sorted({int(row["section_size"]) for row in section_runs})
    if modes != ["cs", "ori"]:
        raise ValueError(f"Expected cs and ori modes, found {modes}")
    if section_sizes != [1, 2, 4, 8, 16]:
        raise ValueError(f"Unexpected section sizes: {section_sizes}")
    if repetitions != list(range(1, len(repetitions) + 1)):
        raise ValueError(f"Non-contiguous repetitions: {repetitions}")
    expected_section_runs = len(modes) * len(section_sizes) * len(repetitions)
    expected_filebench_runs = len(modes) * len(repetitions)
    if len(section_runs) != expected_section_runs:
        raise ValueError(f"Expected {expected_section_runs} section runs, found {len(section_runs)}")
    if len(filebench_runs) != expected_filebench_runs:
        raise ValueError(
            f"Expected {expected_filebench_runs} Filebench runs, found {len(filebench_runs)}"
        )
    if len(timeline_runs) != expected_filebench_runs * 60:
        raise ValueError(f"Expected {expected_filebench_runs * 60} timeline rows")

    section_summary: List[Dict[str, object]] = []
    for mode in modes:
        for section_size in section_sizes:
            samples = [
                row
                for row in section_runs
                if row["mode"] == mode and row["section_size"] == section_size
            ]
            observed = sorted(int(row["repetition"]) for row in samples)
            if observed != repetitions:
                raise ValueError(f"Unbalanced {mode}/s{section_size} repetitions: {observed}")
            throughput_mean, throughput_std = mean_std(
                [float(row["throughput_ops_s"]) for row in samples]
            )
            latency_mean, latency_std = mean_std(
                [float(row["migration_ns_per_block"]) / 1000.0 for row in samples]
            )
            blocks_mean, blocks_std = mean_std(
                [float(row["measured_blocks"]) for row in samples]
            )
            baseline = read_baseline_throughput(baseline_batch, mode, section_size)
            section_summary.append(
                {
                    "mode": mode,
                    "section_size": section_size,
                    "samples": len(samples),
                    "throughput_ops_s_mean": throughput_mean,
                    "throughput_ops_s_std": throughput_std,
                    "baseline_quiet_ops_s": baseline,
                    "metrics_vs_quiet_percent": (throughput_mean / baseline - 1.0) * 100.0,
                    "migration_us_mean": latency_mean,
                    "migration_us_std": latency_std,
                    "migrated_blocks_mean": blocks_mean,
                    "migrated_blocks_std": blocks_std,
                    "other_path_blocks_total": sum(
                        int(row["other_path_blocks"]) for row in samples
                    ),
                }
            )

    timeline_summary: List[Dict[str, object]] = []
    for mode in modes:
        for sample_index in range(1, 61):
            samples = [
                row
                for row in timeline_runs
                if row["mode"] == mode and row["sample_index"] == sample_index
            ]
            throughput_mean, throughput_std = mean_std(
                [float(row["throughput_ops_s"]) for row in samples]
            )
            timeline_summary.append(
                {
                    "mode": mode,
                    "sample_index": sample_index,
                    "elapsed_s": statistics.fmean(float(row["elapsed_s"]) for row in samples),
                    "throughput_ops_s_mean": throughput_mean,
                    "throughput_ops_s_std": throughput_std,
                }
            )

    filebench_summary: List[Dict[str, object]] = []
    for mode in modes:
        samples = [float(row["throughput_ops_s"]) for row in filebench_runs if row["mode"] == mode]
        mean, std = mean_std(samples)
        filebench_summary.append(
            {
                "mode": mode,
                "samples": len(samples),
                "throughput_ops_s_mean": mean,
                "throughput_ops_s_std": std,
            }
        )

    analysis_dir = batch / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    write_csv(analysis_dir / "section-metrics-runs.csv", section_runs)
    write_csv(analysis_dir / "section-metrics-summary.csv", section_summary)
    write_csv(analysis_dir / "filebench-runs.csv", filebench_runs)
    write_csv(analysis_dir / "filebench-summary.csv", filebench_summary)
    write_csv(analysis_dir / "filebench-timeline-runs.csv", timeline_runs)
    write_csv(analysis_dir / "filebench-timeline-summary.csv", timeline_summary)

    summary: Dict[str, object] = {
        "batch": str(batch),
        "baseline_batch": str(baseline_batch),
        "repetitions": len(repetitions),
        "section_runs": len(section_runs),
        "filebench_runs": len(filebench_runs),
        "filebench_samples_per_run": 60,
        "section_summary": section_summary,
        "filebench_summary": filebench_summary,
    }
    (analysis_dir / "original-paper-metrics-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    markdown = [
        "# Original ORI/CSGC paper metrics summary",
        "",
        f"- Batch: `{batch}`",
        f"- Repetitions: {len(repetitions)}",
        f"- Strict Filebench timelines: {len(filebench_runs)} x 300 s, 60 samples per run",
        "- Migration latency uses only the selected mode's complete GC sections.",
        "",
        "| Mode | Section size | Throughput (kop/s) | Metrics vs quiet | Migration latency (us/block) |",
        "|:---|---:|---:|---:|---:|",
    ]
    for row in section_summary:
        markdown.append(
            f"| {row['mode']} | {row['section_size']} | "
            f"{float(row['throughput_ops_s_mean']) / 1000:.3f} +/- "
            f"{float(row['throughput_ops_s_std']) / 1000:.3f} | "
            f"{float(row['metrics_vs_quiet_percent']):+.2f}% | "
            f"{float(row['migration_us_mean']):.3f} +/- "
            f"{float(row['migration_us_std']):.3f} |"
        )
    markdown.extend(
        [
            "",
            "| Mode | Filebench throughput (kop/s) |",
            "|:---|---:|",
        ]
    )
    for row in filebench_summary:
        markdown.append(
            f"| {row['mode']} | {float(row['throughput_ops_s_mean']) / 1000:.3f} +/- "
            f"{float(row['throughput_ops_s_std']) / 1000:.3f} |"
        )
    (analysis_dir / "original-paper-metrics-summary.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--baseline-batch", type=Path, default=DEFAULT_BASELINE_BATCH)
    args = parser.parse_args()
    summary = analyze(args.batch.resolve(), args.baseline_batch.resolve())
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
