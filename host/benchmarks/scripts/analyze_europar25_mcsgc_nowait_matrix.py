#!/usr/bin/env python3
"""Analyze repeated quiet NOWAIT runs against the paired ORI/CSGC baseline."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_europar25_original_matrix import parse_case


SYSTEMS = ("ori", "original-csgc", "nowait-quiet")
LABELS = {
    "ori": "ORI",
    "original-csgc": "Original CSGC",
    "nowait-quiet": "mCSGC NOWAIT quiet",
}
COLORS = {
    "ori": "#5E96E6",
    "original-csgc": "#E67365",
    "nowait-quiet": "#47A66B",
}
MARKERS = {"ori": "^", "original-csgc": "o", "nowait-quiet": "s"}
OVERALL = (
    ("Filebench fileserver", "filebench-fileserver"),
    ("Filebench varmail", "filebench-varmail"),
    ("YCSB-A", "ycsb-a"),
    ("YCSB-F", "ycsb-f"),
    ("fio uniform", "fio-overall-uniform"),
    ("fio Zipf 1.1", "fio-overall-zipf11"),
)
SCALAR_METRICS = (
    "throughput_ops_s",
    "bandwidth_mib_s",
    "waf",
    "migration_us",
    "read_avg_us",
    "read_p99_us",
    "update_avg_us",
    "update_p99_us",
)


# Return a multiplicative mean suitable for normalized throughput ratios.
def geometric_mean(values: Iterable[float]) -> float:
    samples = list(values)
    if not samples or any(value <= 0 for value in samples):
        raise ValueError("Geometric mean requires positive samples")
    return math.exp(sum(math.log(value) for value in samples) / len(samples))


# Read simple key=value metadata emitted by the matrix launcher.
def read_key_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.startswith("["):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'")
    return values


# Read the latest successful row for every case and parse its raw output.
def read_batch_cases(batch: Path, expected: Optional[int] = None) -> Dict[str, Dict[str, object]]:
    results = batch / "case-results.tsv"
    if not results.exists():
        raise ValueError(f"Missing {results}")
    with results.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t") if row["status"] == "0"]
    latest = {row["case_id"]: row for row in rows}
    if expected is not None and len(latest) != expected:
        raise ValueError(f"Expected {expected} successful cases in {batch}, found {len(latest)}")
    return {case_id: parse_case(row) for case_id, row in latest.items()}


# Average equal-position periodic samples after converting timestamps to elapsed time.
def aggregate_timeline(samples: Sequence[Mapping[str, object]]) -> List[Tuple[float, float]]:
    timelines = [sample.get("timeline") or [] for sample in samples]
    if not timelines or any(not timeline for timeline in timelines):
        return []
    length = min(len(timeline) for timeline in timelines)
    normalized: List[List[Tuple[float, float]]] = []
    for timeline in timelines:
        origin = float(timeline[0][0])
        normalized.append(
            [(float(stamp) - origin, float(value)) for stamp, value in timeline[:length]]
        )
    return [
        (
            statistics.fmean(timeline[index][0] for timeline in normalized),
            statistics.fmean(timeline[index][1] for timeline in normalized),
        )
        for index in range(length)
    ]


# Build one representative metrics record from repeated runs.
def aggregate_metrics(samples: Sequence[Mapping[str, object]]) -> Dict[str, object]:
    if not samples:
        raise ValueError("Cannot aggregate an empty sample set")
    aggregate: Dict[str, object] = {
        "workload_type": samples[0]["workload_type"],
        "bmname": samples[0]["bmname"],
        "distribution": samples[0]["distribution"],
        "prefill_ratio": samples[0]["prefill_ratio"],
        "segs_per_sec": samples[0]["segs_per_sec"],
        "timeline": aggregate_timeline(samples),
        "sample_count": len(samples),
    }
    for key in SCALAR_METRICS:
        values = [float(sample[key]) for sample in samples if sample.get(key) is not None]
        aggregate[key] = statistics.fmean(values) if len(values) == len(samples) else None
    return aggregate


# Convert batch-specific case IDs into a shared system/suffix namespace.
def combine_cases(
    baseline: Mapping[str, Mapping[str, object]],
    candidates: Mapping[str, Mapping[str, object]],
) -> Tuple[
    Dict[str, Dict[str, Dict[str, object]]],
    Dict[str, List[Dict[str, object]]],
    int,
]:
    combined: Dict[str, Dict[str, Dict[str, object]]] = {system: {} for system in SYSTEMS}
    for case_id, metrics in baseline.items():
        if case_id.startswith("ori-"):
            combined["ori"][case_id[4:]] = dict(metrics)
        elif case_id.startswith("cs-"):
            combined["original-csgc"][case_id[3:]] = dict(metrics)
    candidate_runs: Dict[str, List[Tuple[int, Dict[str, object]]]] = {}
    pattern = re.compile(r"^nowait-quiet-(.+)-r([1-9][0-9]*)$")
    for case_id, metrics in candidates.items():
        match = pattern.match(case_id)
        if not match:
            raise ValueError(f"Unexpected candidate case ID: {case_id}")
        suffix, repetition_text = match.groups()
        candidate_runs.setdefault(suffix, []).append((int(repetition_text), dict(metrics)))

    expected_suffixes = set(combined["ori"])
    if len(expected_suffixes) != 22 or set(combined["original-csgc"]) != expected_suffixes:
        raise ValueError("The baseline does not contain paired ORI/CSGC results for 22 cases")
    if set(candidate_runs) != expected_suffixes:
        missing = sorted(expected_suffixes - set(candidate_runs))
        extra = sorted(set(candidate_runs) - expected_suffixes)
        raise ValueError(f"Candidate case set mismatch: missing={missing}, extra={extra}")

    repetitions = {len(values) for values in candidate_runs.values()}
    if len(repetitions) != 1:
        raise ValueError(f"Candidate repetition counts differ by case: {sorted(repetitions)}")
    repetition_count = repetitions.pop()
    if repetition_count < 1:
        raise ValueError("No candidate repetitions were found")

    ordered_runs: Dict[str, List[Dict[str, object]]] = {}
    for suffix, values in candidate_runs.items():
        values.sort(key=lambda item: item[0])
        indexes = [index for index, _metrics in values]
        if indexes != list(range(1, repetition_count + 1)):
            raise ValueError(f"Non-contiguous repetitions for {suffix}: {indexes}")
        ordered_runs[suffix] = [metrics for _index, metrics in values]
        combined["nowait-quiet"][suffix] = aggregate_metrics(ordered_runs[suffix])

    return combined, ordered_runs, repetition_count


# Require one scalar metric from a system and case suffix.
def metric(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    system: str,
    suffix: str,
    key: str,
) -> float:
    value = cases[system][suffix].get(key)
    if value is None:
        raise ValueError(f"Missing {key} for {system}-{suffix}")
    return float(value)


# Return the sample standard deviation for one repeated candidate metric.
def candidate_std(
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]], suffix: str, key: str
) -> float:
    values = [float(sample[key]) for sample in candidate_runs[suffix] if sample.get(key) is not None]
    return statistics.stdev(values) if len(values) > 1 else 0.0


# Save one figure in both vector and raster formats.
def save_figure(fig: plt.Figure, output: Path) -> None:
    fig.tight_layout()
    fig.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output.with_suffix(".png"), dpi=180, bbox_inches="tight")
    plt.close(fig)


# Draw the six headline workloads normalized to the original CSGC result.
def plot_overall(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    figures: Path,
) -> None:
    x = np.arange(len(OVERALL))
    width = 0.25
    fig, ax = plt.subplots(figsize=(11.5, 3.4))
    for index, system in enumerate(SYSTEMS):
        values = []
        errors = []
        for _, suffix in OVERALL:
            base = metric(cases, "original-csgc", suffix, "throughput_ops_s")
            values.append(metric(cases, system, suffix, "throughput_ops_s") / base)
            errors.append(
                candidate_std(candidate_runs, suffix, "throughput_ops_s") / base
                if system == "nowait-quiet"
                else 0.0
            )
        ax.bar(
            x + (index - 1) * width,
            values,
            width,
            yerr=errors,
            capsize=3 if system == "nowait-quiet" else 0,
            label=LABELS[system],
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.5,
        )
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_ylabel("Normalized throughput\n(original CSGC = 1)")
    ax.set_xticks(x, [label for label, _ in OVERALL])
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.20))
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, figures / "figure4_three_system_overall")


# Draw the period trace and YCSB-A latency for all four systems.
def plot_timeline_latency(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    figures: Path,
) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(11.5, 3.3))
    for system in SYSTEMS:
        timeline = cases[system]["filebench-period"].get("timeline") or []
        if not timeline:
            raise ValueError(f"Missing timeline for {system}")
        origin = float(timeline[0][0])
        left.plot(
            [float(stamp) - origin for stamp, _ in timeline],
            [float(value) / 1000 for _, value in timeline],
            label=LABELS[system],
            color=COLORS[system],
            marker=MARKERS[system],
            markersize=2.0,
        )
    left.set_xlabel("Time (s)")
    left.set_ylabel("Throughput (kop/s)")
    left.grid(alpha=0.3)

    keys = ("read_avg_us", "read_p99_us", "update_avg_us", "update_p99_us")
    labels = ("read avg", "read P99", "update avg", "update P99")
    x = np.arange(len(keys))
    width = 0.25
    for index, system in enumerate(SYSTEMS):
        values = [metric(cases, system, "ycsb-a", key) / 1000 for key in keys]
        errors = [
            candidate_std(candidate_runs, "ycsb-a", key) / 1000
            if system == "nowait-quiet"
            else 0.0
            for key in keys
        ]
        right.bar(
            x + (index - 1) * width,
            values,
            width,
            yerr=errors,
            capsize=3 if system == "nowait-quiet" else 0,
            label=LABELS[system],
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.5,
        )
    right.set_xticks(x, labels)
    right.set_ylabel("Latency (ms)")
    right.grid(axis="y", alpha=0.25)
    right.legend(ncol=1)
    save_figure(fig, figures / "figure5_three_system_timeline_latency")


# Draw throughput and WAF trends for one parameter sweep.
def plot_sweep(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    figures: Path,
    name: str,
    x_values: List[str],
    suffixes: List[str],
    x_label: str,
) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(8.0, 3.2))
    for system in SYSTEMS:
        throughput = [metric(cases, system, suffix, "throughput_ops_s") / 1000 for suffix in suffixes]
        style = dict(label=LABELS[system], color=COLORS[system], marker=MARKERS[system])
        if system == "nowait-quiet":
            errors = [
                candidate_std(candidate_runs, suffix, "throughput_ops_s") / 1000
                for suffix in suffixes
            ]
            left.errorbar(x_values, throughput, yerr=errors, capsize=3, **style)
        else:
            left.plot(x_values, throughput, **style)
        waf = [cases[system][suffix].get("waf") for suffix in suffixes]
        if any(value is not None for value in waf):
            right.plot(
                x_values,
                [float(value) if value is not None else math.nan for value in waf],
                **style,
            )
    left.set_xlabel(x_label)
    left.set_ylabel("Throughput (kop/s)")
    left.grid(alpha=0.3)
    left.legend(ncol=1)
    right.set_xlabel(x_label)
    right.set_ylabel("Write amplification")
    if not any(cases["nowait-quiet"][suffix].get("waf") is not None for suffix in suffixes):
        right.set_title("NOWAIT WAF not collected", fontsize=9)
    right.grid(alpha=0.3)
    right.legend(ncol=1)
    save_figure(fig, figures / name)


# Draw all three original Figure 7 panels without inventing missing measurements.
def plot_section_size(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    figures: Path,
) -> None:
    sizes = ["1", "2", "4", "8", "16"]
    suffixes = [f"fio-section-{size}" for size in sizes]
    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.2))
    for system in SYSTEMS:
        style = dict(label=LABELS[system], color=COLORS[system], marker=MARKERS[system])
        migration = [cases[system][suffix].get("migration_us") for suffix in suffixes]
        if all(value is not None for value in migration):
            axes[0].plot(sizes, [float(value) for value in migration], **style)
        throughput = [metric(cases, system, suffix, "throughput_ops_s") / 1000 for suffix in suffixes]
        if system == "nowait-quiet":
            errors = [
                candidate_std(candidate_runs, suffix, "throughput_ops_s") / 1000
                for suffix in suffixes
            ]
            axes[1].errorbar(sizes, throughput, yerr=errors, capsize=3, **style)
        else:
            axes[1].plot(sizes, throughput, **style)
        waf = [cases[system][suffix].get("waf") for suffix in suffixes]
        if all(value is not None for value in waf):
            axes[2].plot(sizes, [float(value) for value in waf], **style)
    if not axes[0].lines:
        axes[0].text(
            0.5,
            0.5,
            "Not collected",
            ha="center",
            va="center",
            transform=axes[0].transAxes,
        )
    axes[0].set_ylabel("Migration latency (us)")
    axes[1].set_ylabel("Throughput (kop/s)")
    axes[2].set_ylabel("Write amplification")
    if not any(cases["nowait-quiet"][suffix].get("waf") is not None for suffix in suffixes):
        axes[2].set_title("NOWAIT WAF not collected", fontsize=9)
    for index, axis in enumerate(axes):
        axis.set_xlabel(f"Segments per section\n({chr(ord('a') + index)})")
        axis.grid(alpha=0.3)
    axes[2].legend(ncol=1)
    save_figure(fig, figures / "figure7_three_system_section_size")


# Write one row per matched workload point, including every candidate repetition.
def write_comparison_csv(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    output: Path,
) -> None:
    fields = [
        "case_suffix",
        "workload_type",
        "ori_ops_s",
        "original_csgc_ops_s",
        "nowait_samples_ops_s",
        "nowait_mean_ops_s",
        "nowait_median_ops_s",
        "nowait_min_ops_s",
        "nowait_max_ops_s",
        "nowait_sample_stddev_ops_s",
        "nowait_mean_vs_original",
        "nowait_mean_vs_ori",
        "ori_waf",
        "original_csgc_waf",
        "nowait_waf",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for suffix in sorted(cases["ori"]):
            ori = metric(cases, "ori", suffix, "throughput_ops_s")
            original = metric(cases, "original-csgc", suffix, "throughput_ops_s")
            samples = [float(run["throughput_ops_s"]) for run in candidate_runs[suffix]]
            mean = statistics.fmean(samples)
            writer.writerow(
                {
                    "case_suffix": suffix,
                    "workload_type": cases["ori"][suffix]["workload_type"],
                    "ori_ops_s": ori,
                    "original_csgc_ops_s": original,
                    "nowait_samples_ops_s": ";".join(f"{value:.9g}" for value in samples),
                    "nowait_mean_ops_s": mean,
                    "nowait_median_ops_s": statistics.median(samples),
                    "nowait_min_ops_s": min(samples),
                    "nowait_max_ops_s": max(samples),
                    "nowait_sample_stddev_ops_s": statistics.stdev(samples) if len(samples) > 1 else 0.0,
                    "nowait_mean_vs_original": mean / original,
                    "nowait_mean_vs_ori": mean / ori,
                    "ori_waf": cases["ori"][suffix].get("waf"),
                    "original_csgc_waf": cases["original-csgc"][suffix].get("waf"),
                    "nowait_waf": cases["nowait-quiet"][suffix].get("waf"),
                }
            )


# Build a concise report with explicit denominators and measurement limits.
def build_report(
    candidate_batch: Path,
    baseline_batch: Path,
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate_runs: Mapping[str, Sequence[Mapping[str, object]]],
    repetition_count: int,
) -> str:
    provenance = read_key_values(candidate_batch / "provenance.txt")
    completed = read_key_values(candidate_batch / "completed.env")
    candidate_waf_available = all(
        cases["nowait-quiet"][suffix].get("waf") is not None
        for suffix in cases["nowait-quiet"]
    )
    candidate_migration_available = all(
        cases["nowait-quiet"][suffix].get("migration_us") is not None
        for suffix in cases["nowait-quiet"]
    )
    lines = [
        "# Euro-Par Workloads: Quiet NOWAIT Candidate",
        "",
        f"- Candidate batch: `{candidate_batch}`",
        f"- Original-system baseline: `{baseline_batch}`",
        f"- Candidate repetitions per workload: `{repetition_count}`",
        f"- Started: `{completed.get('started_at', provenance.get('started_at', 'unknown'))}`",
        f"- Completed: `{completed.get('completed_at', 'unknown')}`",
        f"- Host commit: `{provenance.get('host_commit', 'see provenance.txt')}`",
        f"- OpenSSD source commit: `{provenance.get('openssd_expected_commit', 'unknown')}`",
        "- Ratios use the candidate arithmetic mean divided by the paired baseline result.",
        "",
        "## Headline Throughput",
        "",
        "| Workload | ORI (kop/s) | Original CSGC | NOWAIT mean | NOWAIT min-max | NOWAIT/original | NOWAIT/ORI |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    ratios_original: List[float] = []
    ratios_ori: List[float] = []
    for label, suffix in OVERALL:
        ori = metric(cases, "ori", suffix, "throughput_ops_s")
        original = metric(cases, "original-csgc", suffix, "throughput_ops_s")
        values = [float(run["throughput_ops_s"]) for run in candidate_runs[suffix]]
        mean = statistics.fmean(values)
        ratios_original.append(mean / original)
        ratios_ori.append(mean / ori)
        lines.append(
            f"| {label} | {ori / 1000:.3f} | {original / 1000:.3f} | {mean / 1000:.3f} "
            f"| {min(values) / 1000:.3f}-{max(values) / 1000:.3f} "
            f"| {mean / original:.3f}x | {mean / ori:.3f}x |"
        )
    lines.extend(
        [
            "",
            f"Equal-weight geometric mean across the six headline workloads: "
            f"**{geometric_mean(ratios_original):.3f}x** over original CSGC and "
            f"**{geometric_mean(ratios_ori):.3f}x** over ORI.",
            "",
            "## Generated Artifacts",
            "",
            "- `comparison.csv`: paired throughput, all repetitions, dispersion, and ratios.",
            "- `combined-results.json`: baseline metrics, candidate aggregates, and raw repetitions.",
            "- `figures/`: three-system extensions of the original Figure 4 through Figure 8 panels.",
            "",
            "## Measurement Limits",
            "",
            "- ORI and original CSGC are reused paired baseline measurements with one run per case; NOWAIT has repeated runs.",
            "- Candidate WAF is available."
            if candidate_waf_available
            else "- Candidate WAF was not collected by the quiet OpenSSD build.",
            "- Average block migration latency is available."
            if candidate_migration_available
            else "- Average block migration latency was not collected by the quiet Host build.",
            "- Source revisions and Vitis input hashes do not prove byte-identical identity of the running firmware ELF.",
        ]
    )
    return "\n".join(lines) + "\n"


# Parse both batches, validate repetition completeness, and produce all outputs.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_batch", type=Path)
    parser.add_argument("--baseline-batch", required=True, type=Path)
    args = parser.parse_args()
    candidate_batch = args.candidate_batch.resolve()
    baseline_batch = args.baseline_batch.resolve()

    candidates = read_batch_cases(candidate_batch)
    baseline = read_batch_cases(baseline_batch, 44)
    cases, candidate_runs, repetition_count = combine_cases(baseline, candidates)
    expected_text = read_key_values(candidate_batch / "state.env").get("expected_cases")
    if expected_text is not None and len(candidates) != int(expected_text):
        raise ValueError(f"Expected {expected_text} candidate cases, found {len(candidates)}")

    analysis = candidate_batch / "analysis"
    figures = analysis / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    write_comparison_csv(cases, candidate_runs, analysis / "comparison.csv")
    payload = {
        "systems": cases,
        "nowait_repetitions": candidate_runs,
        "repetition_count": repetition_count,
    }
    with (analysis / "combined-results.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    plt.rcParams.update({"figure.dpi": 150, "axes.labelsize": 10, "legend.fontsize": 8})
    plot_overall(cases, candidate_runs, figures)
    plot_timeline_latency(cases, candidate_runs, figures)
    plot_sweep(
        cases,
        candidate_runs,
        figures,
        "figure6_three_system_storage_utilization",
        ["60%", "70%", "80%", "90%", "95%"],
        [f"fio-util-{value}" for value in ("0.6", "0.7", "0.8", "0.9", "0.95")],
        "Storage utilization",
    )
    plot_section_size(cases, candidate_runs, figures)
    plot_sweep(
        cases,
        candidate_runs,
        figures,
        "figure8_three_system_write_skewness",
        ["uniform", "z/0.3", "z/0.7", "z/0.9", "z/1.1"],
        ["fio-skew-uniform", "fio-skew-0.3", "fio-skew-0.7", "fio-skew-0.9", "fio-skew-1.1"],
        "Write distribution",
    )
    report = build_report(
        candidate_batch, baseline_batch, cases, candidate_runs, repetition_count
    )
    (analysis / "mcsgc-nowait-quiet-comparison.md").write_text(report, encoding="utf-8")
    print(analysis)


if __name__ == "__main__":
    main()
