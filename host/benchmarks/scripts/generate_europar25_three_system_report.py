#!/usr/bin/env python3
"""Generate paper-style three-system figures and auditable source tables."""

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


SYSTEMS = ("ori", "original-csgc", "nowait-quiet")
LABELS = {
    "ori": "F2FS (ORI)",
    "original-csgc": "Original CSGC",
    "nowait-quiet": "mCSGC NOWAIT",
}
COLORS = {
    "ori": "#5E96E6",
    "original-csgc": "#E67365",
    "nowait-quiet": "#47A66B",
}
MARKERS = {"ori": "^", "original-csgc": "o", "nowait-quiet": "s"}
HATCHES = {"ori": "xx", "original-csgc": "\\\\", "nowait-quiet": "//"}

OVERALL = (
    ("fileserver", "filebench-fileserver"),
    ("varmail", "filebench-varmail"),
    ("YCSB-A", "ycsb-a"),
    ("YCSB-F", "ycsb-f"),
    ("fio-uniform", "fio-overall-uniform"),
    ("fio-skewed", "fio-overall-zipf11"),
)

DEFAULT_COMBINED = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-mcsgc-nowait-reproduction/20260916_045438/"
    "analysis/combined-results.json"
)
DEFAULT_PAPER_METRICS = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-nowait-paper-metrics/20260917_030310/analysis"
)
DEFAULT_SECTION16_METRICS = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-nowait-section16-paper-metrics/20260918_025234/analysis"
)
DEFAULT_WAF_ANALYSIS = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/"
    "outputs-europar25-nowait-paper-waf/20260917_220250/analysis-paper-waf"
)
DEFAULT_HISTORICAL_CSGC = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-cs"
)
DEFAULT_HISTORICAL_ORI_STAT = Path(
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-ori/"
    "20250512_162204/fio_randwrite_s8_0.86_random/stat.log"
)
DEFAULT_NOWAIT_GC_HEAVY = (
    Path(
        "/home/xin/artifact-csgc-nowait-gcheavy-20260916/host/benchmarks/scripts/"
        "outputs-formal-mcsgc8t-nowait-quiet-gcheavy-3x/20260916_191008/results.tsv"
    ),
    Path(
        "/home/xin/artifact-csgc-nowait-gcheavy-20260916/host/benchmarks/scripts/"
        "outputs-formal-mcsgc8t-nowait-quiet-gcheavy-3x/20260916_230306/results.tsv"
    ),
)
DEFAULT_ORIGINAL_GC_HEAVY = (
    Path(
        "/home/xin/artifact-csgc/host/benchmarks/scripts/"
        "outputs-formal-original-matrix-3x/20260826_185714/results.tsv"
    ),
    Path(
        "/home/xin/artifact-csgc/host/benchmarks/scripts/"
        "outputs-formal-original-matrix-3x/20260827_120731/results.tsv"
    ),
)
DEFAULT_OUTPUT = Path(
    "/home/xin/artifact-csgc/doc_and_notes/exp_doc_and_notes/"
    "europar25-three-system-evaluation-20260917"
)


# Return a multiplicative mean for independent normalized performance ratios.
def geometric_mean(values: Iterable[float]) -> float:
    samples = list(values)
    if not samples or any(value <= 0 for value in samples):
        raise ValueError("Geometric mean requires positive values")
    return math.exp(statistics.fmean(math.log(value) for value in samples))


# Load one JSON document and require an object at the top level.
def load_json(path: Path) -> Dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


# Read a CSV file into a list of dictionaries.
def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Add measured NOWAIT WAF to the in-memory quiet-performance result set.
def apply_nowait_waf(
    combined: Dict[str, object], waf_rows: Sequence[Mapping[str, str]]
) -> Dict[str, Mapping[str, str]]:
    waf_by_suffix = {row["suffix"]: row for row in waf_rows}
    expected = {
        *(f"fio-util-{value}" for value in ("0.6", "0.7", "0.8", "0.9", "0.95")),
        *(f"fio-section-{value}" for value in ("1", "2", "4", "8", "16")),
        "fio-skew-uniform",
        *(f"fio-skew-{value}" for value in ("0.3", "0.7", "0.9", "1.1")),
    }
    if set(waf_by_suffix) != expected:
        raise ValueError(
            f"NOWAIT WAF suffix mismatch: {sorted(set(waf_by_suffix) ^ expected)}"
        )
    systems = combined["systems"]
    for suffix, row in waf_by_suffix.items():
        systems["nowait-quiet"][suffix]["waf"] = float(row["nowait_waf_mean"])
    return waf_by_suffix


# Replace the invalid wide-section fallback point with corrected CSGC runs.
def apply_corrected_section16(
    combined: Dict[str, object], base_metrics_dir: Path, metrics_dir: Path
) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    summary_rows = read_csv(metrics_dir / "section-metrics-summary.csv")
    run_rows = read_csv(metrics_dir / "section-metrics-runs.csv")
    selected_summary = [row for row in summary_rows if row["section_size"] == "16"]
    selected_runs = [row for row in run_rows if row["section_size"] == "16"]
    if len(selected_summary) != 1 or len(selected_runs) < 2:
        raise ValueError("Corrected section-size-16 metrics are incomplete")
    if any(int(row["csgc_blocks"]) <= 0 for row in selected_runs):
        raise ValueError("Corrected section-size-16 run did not execute CSGC")
    summary = selected_summary[0]
    systems = combined["systems"]
    repetitions = combined["nowait_repetitions"]
    systems["nowait-quiet"]["fio-section-16"].update(
        {
            "sample_count": len(selected_runs),
            "throughput_ops_s": float(summary["throughput_ops_s_mean"]),
            "bandwidth_mib_s": statistics.fmean(
                float(row["bandwidth_mib_s"]) for row in selected_runs
            ),
        }
    )
    repetitions["fio-section-16"] = [dict(row) for row in selected_runs]
    base_rows = read_csv(base_metrics_dir / "section-metrics-summary.csv")
    merged_rows = [row for row in base_rows if row["section_size"] != "16"]
    merged_rows.append(summary)
    merged_rows.sort(key=lambda row: int(row["section_size"]))
    return merged_rows, summary


# Write dictionaries with a stable field order.
def write_csv(path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# Apply the typography, colors, and compact geometry used by the Euro-Par plots.
def configure_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Liberation Serif", "Times New Roman", "DejaVu Serif"],
            "font.size": 10,
            "axes.labelsize": 13,
            "axes.titlesize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9,
            "axes.linewidth": 0.8,
            "lines.linewidth": 1.4,
            "lines.markersize": 5.0,
            "grid.color": "#B8B8B8",
            "grid.alpha": 0.42,
            "grid.linewidth": 0.6,
            "figure.dpi": 160,
            "savefig.dpi": 220,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


# Save every figure as both vector PDF and high-resolution PNG.
def save_figure(fig: plt.Figure, output: Path) -> None:
    fig.tight_layout()
    fig.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output.with_suffix(".png"), bbox_inches="tight")
    plt.close(fig)


# Return one scalar metric from the combined result object.
def metric(
    systems: Mapping[str, Mapping[str, Mapping[str, object]]],
    system: str,
    suffix: str,
    key: str,
) -> float:
    value = systems[system][suffix].get(key)
    if value is None:
        raise ValueError(f"Missing {key} for {system}/{suffix}")
    return float(value)


# Return sample standard deviation for a repeated NOWAIT metric.
def nowait_std(
    repetitions: Mapping[str, Sequence[Mapping[str, object]]], suffix: str, key: str
) -> float:
    values = [float(run[key]) for run in repetitions[suffix] if run.get(key) is not None]
    return statistics.stdev(values) if len(values) > 1 else 0.0


# Draw the six headline workloads using absolute throughput as in the paper.
def plot_overall(
    systems: Mapping[str, Mapping[str, Mapping[str, object]]],
    repetitions: Mapping[str, Sequence[Mapping[str, object]]],
    output: Path,
) -> List[Dict[str, object]]:
    x = np.arange(len(OVERALL))
    width = 0.24
    fig, ax = plt.subplots(figsize=(10.0, 2.8))
    rows: List[Dict[str, object]] = []
    for index, system in enumerate(SYSTEMS):
        values = [metric(systems, system, suffix, "throughput_ops_s") / 1000 for _, suffix in OVERALL]
        errors = [
            nowait_std(repetitions, suffix, "throughput_ops_s") / 1000
            if system == "nowait-quiet"
            else 0.0
            for _, suffix in OVERALL
        ]
        bars = ax.bar(
            x + (index - 1) * width,
            values,
            width,
            yerr=errors,
            capsize=2.5 if system == "nowait-quiet" else 0,
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.55,
            hatch=HATCHES[system],
            label=LABELS[system],
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.22,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                rotation=90,
            )
        for (workload, suffix), value, error in zip(OVERALL, values, errors):
            rows.append(
                {
                    "workload": workload,
                    "case_suffix": suffix,
                    "system": LABELS[system],
                    "throughput_kops": value,
                    "sample_stddev_kops": error,
                }
            )
    ax.set_ylabel("Throughput (kop/s)")
    ax.set_xticks(x, [label for label, _ in OVERALL])
    ax.set_ylim(0, max(metric(systems, system, suffix, "throughput_ops_s") / 1000 for system in SYSTEMS for _, suffix in OVERALL) * 1.20)
    ax.grid(axis="y")
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.22), frameon=False)
    save_figure(fig, output)
    return rows


# Normalize an original timeline so each 5-second interval ends at 5, 10, ... seconds.
def normalize_legacy_timeline(timeline: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    if not timeline:
        return []
    origin = float(timeline[0][0])
    return [
        (float(stamp) - origin + 5.0, float(value) / 1000.0)
        for stamp, value in timeline
    ]


# Draw the common 60-second fileserver window and YCSB-A latency panel.
def plot_timeline_and_latency(
    systems: Mapping[str, Mapping[str, Mapping[str, object]]],
    repetitions: Mapping[str, Sequence[Mapping[str, object]]],
    strict_timeline: Sequence[Mapping[str, str]],
    output: Path,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10.0, 2.8))
    timeline_rows: List[Dict[str, object]] = []
    for system in ("ori", "original-csgc"):
        timeline = normalize_legacy_timeline(
            systems[system]["filebench-period"].get("timeline") or []
        )
        for elapsed, throughput in timeline:
            timeline_rows.append(
                {
                    "elapsed_s": elapsed,
                    "system": LABELS[system],
                    "throughput_kops": throughput,
                    "sample_stddev_kops": 0.0,
                    "window": "legacy-60s",
                }
            )
        left.plot(
            [item[0] for item in timeline],
            [item[1] for item in timeline],
            color=COLORS[system],
            marker=MARKERS[system],
            label=LABELS[system],
        )

    strict_points = [
        (
            float(row["elapsed_s"]),
            float(row["throughput_ops_s_mean"]) / 1000,
            float(row["throughput_ops_s_std"]) / 1000,
        )
        for row in strict_timeline
    ]
    common_points = [point for point in strict_points if point[0] <= 60.1]
    left.errorbar(
        [point[0] for point in common_points],
        [point[1] for point in common_points],
        yerr=[point[2] for point in common_points],
        color=COLORS["nowait-quiet"],
        marker=MARKERS["nowait-quiet"],
        capsize=2,
        label=LABELS["nowait-quiet"],
    )
    for elapsed, throughput, error in strict_points:
        timeline_rows.append(
            {
                "elapsed_s": elapsed,
                "system": LABELS["nowait-quiet"],
                "throughput_kops": throughput,
                "sample_stddev_kops": error,
                "window": "strict-300s",
            }
        )
    left.set_xlim(0, 62)
    left.set_ylim(bottom=0)
    left.set_xlabel("Time (s)\n(a)")
    left.set_ylabel("Throughput (kop/s)")
    left.grid()
    left.legend(frameon=False)

    latency_keys = ("read_avg_us", "read_p99_us", "update_avg_us", "update_p99_us")
    latency_labels = ("read/Avg.", "read/99%", "update/Avg.", "update/99%")
    x = np.arange(len(latency_keys))
    width = 0.24
    latency_rows: List[Dict[str, object]] = []
    for index, system in enumerate(SYSTEMS):
        values = [metric(systems, system, "ycsb-a", key) / 1000 for key in latency_keys]
        errors = [
            nowait_std(repetitions, "ycsb-a", key) / 1000
            if system == "nowait-quiet"
            else 0.0
            for key in latency_keys
        ]
        right.bar(
            x + (index - 1) * width,
            values,
            width,
            yerr=errors,
            capsize=2.5 if system == "nowait-quiet" else 0,
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.55,
            hatch=HATCHES[system],
            label=LABELS[system],
        )
        for label, value, error in zip(latency_labels, values, errors):
            latency_rows.append(
                {
                    "metric": label,
                    "system": LABELS[system],
                    "latency_ms": value,
                    "sample_stddev_ms": error,
                }
            )
    right.set_xticks(x, latency_labels)
    right.set_ylim(bottom=0)
    right.set_xlabel("\n(b)")
    right.set_ylabel("Latency (ms)")
    right.grid(axis="y")
    right.legend(
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.20),
        frameon=False,
        fontsize=8,
    )
    save_figure(fig, output)
    return timeline_rows, latency_rows


# Draw the complete 300-second NOWAIT fileserver trace as a supplementary figure.
def plot_full_nowait_timeline(
    strict_timeline: Sequence[Mapping[str, str]], output: Path
) -> None:
    elapsed = [float(row["elapsed_s"]) for row in strict_timeline]
    mean = [float(row["throughput_ops_s_mean"]) / 1000 for row in strict_timeline]
    stddev = [float(row["throughput_ops_s_std"]) / 1000 for row in strict_timeline]
    lower = [max(0.0, value - error) for value, error in zip(mean, stddev)]
    upper = [value + error for value, error in zip(mean, stddev)]
    fig, ax = plt.subplots(figsize=(6.0, 2.6))
    ax.plot(
        elapsed,
        mean,
        color=COLORS["nowait-quiet"],
        marker=MARKERS["nowait-quiet"],
        markersize=3.0,
        label=LABELS["nowait-quiet"],
    )
    ax.fill_between(elapsed, lower, upper, color=COLORS["nowait-quiet"], alpha=0.18)
    ax.set_xlim(0, 305)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Throughput (kop/s)")
    ax.grid()
    ax.legend(frameon=False)
    save_figure(fig, output)


# Draw one throughput/WAF sweep with low-overhead NOWAIT WAF measurements.
def plot_sweep(
    systems: Mapping[str, Mapping[str, Mapping[str, object]]],
    repetitions: Mapping[str, Sequence[Mapping[str, object]]],
    nowait_waf: Mapping[str, Mapping[str, str]],
    x_labels: Sequence[str],
    suffixes: Sequence[str],
    x_axis_label: str,
    output: Path,
) -> List[Dict[str, object]]:
    fig, (left, right) = plt.subplots(1, 2, figsize=(6.4, 2.6))
    rows: List[Dict[str, object]] = []
    x = np.arange(len(x_labels))
    for system in SYSTEMS:
        throughput = [metric(systems, system, suffix, "throughput_ops_s") / 1000 for suffix in suffixes]
        errors = [
            nowait_std(repetitions, suffix, "throughput_ops_s") / 1000
            if system == "nowait-quiet"
            else 0.0
            for suffix in suffixes
        ]
        style = {
            "color": COLORS[system],
            "marker": MARKERS[system],
            "label": LABELS[system],
            "markeredgecolor": "black",
            "markeredgewidth": 0.4,
        }
        if system == "nowait-quiet":
            left.errorbar(x, throughput, yerr=errors, capsize=2.5, **style)
        else:
            left.plot(x, throughput, **style)
        waf_values = [systems[system][suffix].get("waf") for suffix in suffixes]
        if system == "nowait-quiet":
            waf_errors = [float(nowait_waf[suffix]["nowait_waf_stdev"]) for suffix in suffixes]
            right.errorbar(
                x,
                [float(value) for value in waf_values],
                yerr=waf_errors,
                capsize=2.5,
                **style,
            )
        else:
            waf_errors = [0.0] * len(suffixes)
            right.plot(x, [float(value) for value in waf_values], **style)
        for label, suffix, value, error, waf, waf_error in zip(
            x_labels, suffixes, throughput, errors, waf_values, waf_errors
        ):
            rows.append(
                {
                    "x": label,
                    "case_suffix": suffix,
                    "system": LABELS[system],
                    "throughput_kops": value,
                    "throughput_stddev_kops": error,
                    "physical_waf": float(waf),
                    "physical_waf_stddev": waf_error,
                }
            )
    left.set_xticks(x, x_labels)
    right.set_xticks(x, x_labels)
    left.set_ylim(bottom=0)
    measured_waf = [
        float(value)
        for system in SYSTEMS
        for suffix in suffixes
        if (value := systems[system][suffix].get("waf")) is not None
    ]
    right.set_ylim(bottom=1.0, top=max(measured_waf) + 0.15 * (max(measured_waf) - 1.0))
    left.set_xlabel(f"{x_axis_label}\n(a)")
    right.set_xlabel(f"{x_axis_label}\n(b)")
    left.set_ylabel("Throughput (kop/s)")
    right.set_ylabel("Write amplification")
    left.grid()
    right.grid()
    handles, labels = left.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.04),
        frameon=False,
        fontsize=8,
    )
    save_figure(fig, output)
    return rows


# Extract the last paper-era CSGC migration statistic for each section size.
def historical_csgc_migration(root: Path) -> Tuple[Dict[int, float], Dict[int, str]]:
    values: Dict[int, Tuple[str, float, str]] = {}
    pattern = re.compile(r"<CSGC STAT>.*block migration:\s+(\d+)\s+ns")
    date_pattern = re.compile(r"/(\d{8}_\d{6})/")
    for size in (1, 2, 4, 8, 16):
        for path in root.glob(f"*/fio_randwrite_s{size}_0.86_random/stat.log"):
            match = pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
            if not match:
                continue
            date_match = date_pattern.search(str(path))
            stamp = date_match.group(1) if date_match else ""
            value = float(match.group(1)) / 1000.0
            previous = values.get(size)
            if previous is None or stamp > previous[0]:
                values[size] = (stamp, value, str(path))
    return (
        {size: item[1] for size, item in values.items()},
        {size: item[2] for size, item in values.items()},
    )


# Extract one legacy ORI block-migration statistic from a selected raw log.
def historical_ori_migration(path: Path) -> float:
    pattern = re.compile(r"<ORIGC STAT>.*block migration:\s+(\d+)\s+ns")
    match = pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    if not match:
        raise ValueError(f"Missing ORIGC migration statistic in {path}")
    return float(match.group(1)) / 1000.0


# Draw section-size sensitivity, including provenance-aware migration latency.
def plot_section_size(
    systems: Mapping[str, Mapping[str, Mapping[str, object]]],
    repetitions: Mapping[str, Sequence[Mapping[str, object]]],
    nowait_waf: Mapping[str, Mapping[str, str]],
    section_metrics: Sequence[Mapping[str, str]],
    csgc_migration: Mapping[int, float],
    ori_s8_migration: float,
    output: Path,
) -> List[Dict[str, object]]:
    sizes = (1, 2, 4, 8, 16)
    x = np.arange(len(sizes))
    suffixes = [f"fio-section-{size}" for size in sizes]
    nowait_by_size = {int(row["section_size"]): row for row in section_metrics}
    fig, axes = plt.subplots(1, 3, figsize=(9.5, 2.6))

    axes[0].plot(
        x,
        [csgc_migration.get(size, math.nan) for size in sizes],
        color=COLORS["original-csgc"],
        marker=MARKERS["original-csgc"],
        markeredgecolor="black",
        markeredgewidth=0.4,
        label=LABELS["original-csgc"],
    )
    axes[0].plot(
        x,
        [ori_s8_migration if size == 8 else math.nan for size in sizes],
        color=COLORS["ori"],
        marker=MARKERS["ori"],
        markeredgecolor="black",
        markeredgewidth=0.4,
        linestyle="none",
        label=f"{LABELS['ori']} (s=8 only)",
    )
    nowait_migration = [float(nowait_by_size[size]["migration_us_mean"]) for size in sizes]
    nowait_migration_error = [float(nowait_by_size[size]["migration_us_std"]) for size in sizes]
    axes[0].errorbar(
        x,
        nowait_migration,
        yerr=nowait_migration_error,
        color=COLORS["nowait-quiet"],
        marker=MARKERS["nowait-quiet"],
        markeredgecolor="black",
        markeredgewidth=0.4,
        capsize=2.5,
        label=LABELS["nowait-quiet"],
    )
    for system in SYSTEMS:
        throughput = [metric(systems, system, suffix, "throughput_ops_s") / 1000 for suffix in suffixes]
        style = {
            "color": COLORS[system],
            "marker": MARKERS[system],
            "markeredgecolor": "black",
            "markeredgewidth": 0.4,
            "label": LABELS[system],
        }
        if system == "nowait-quiet":
            errors = [nowait_std(repetitions, suffix, "throughput_ops_s") / 1000 for suffix in suffixes]
            axes[1].errorbar(x, throughput, yerr=errors, capsize=2.5, **style)
        else:
            axes[1].plot(x, throughput, **style)
        waf = [float(systems[system][suffix]["waf"]) for suffix in suffixes]
        if system == "nowait-quiet":
            waf_errors = [
                float(nowait_waf[suffix]["nowait_waf_stdev"])
                for suffix in suffixes
            ]
            axes[2].errorbar(x, waf, yerr=waf_errors, capsize=2.5, **style)
        else:
            axes[2].plot(x, waf, **style)
    ylabels = ("Migration latency (us)", "Throughput (kop/s)", "Write amplification")
    for index, (axis, ylabel) in enumerate(zip(axes, ylabels)):
        axis.set_xticks(x, [str(size) for size in sizes])
        axis.set_xlabel(f"Section size (#segments)\n({chr(ord('a') + index)})")
        axis.set_ylabel(ylabel)
        axis.set_ylim(bottom=0 if index < 2 else 1.0)
        axis.grid()
    axes[0].legend(frameon=False, fontsize=7)
    save_figure(fig, output)

    rows: List[Dict[str, object]] = []
    for size, suffix in zip(sizes, suffixes):
        nowait_row = nowait_by_size[size]
        rows.append(
            {
                "section_size": size,
                "ori_migration_us": ori_s8_migration if size == 8 else "",
                "original_csgc_migration_us": csgc_migration.get(size, ""),
                "nowait_migration_us": float(nowait_row["migration_us_mean"]),
                "nowait_migration_stddev_us": float(nowait_row["migration_us_std"]),
                "nowait_gc_path": nowait_row["gc_paths"],
                "ori_throughput_kops": metric(systems, "ori", suffix, "throughput_ops_s") / 1000,
                "original_csgc_throughput_kops": metric(
                    systems, "original-csgc", suffix, "throughput_ops_s"
                )
                / 1000,
                "nowait_throughput_kops": metric(
                    systems, "nowait-quiet", suffix, "throughput_ops_s"
                )
                / 1000,
                "nowait_throughput_stddev_kops": nowait_std(
                    repetitions, suffix, "throughput_ops_s"
                )
                / 1000,
                "ori_physical_waf": systems["ori"][suffix].get("waf"),
                "original_csgc_physical_waf": systems["original-csgc"][suffix].get("waf"),
                "nowait_physical_waf": systems["nowait-quiet"][suffix].get("waf"),
                "nowait_physical_waf_stddev": float(
                    nowait_waf[suffix]["nowait_waf_stdev"]
                ),
            }
        )
    return rows


# Read GC-heavy rows from the historical strict reproduction batches.
def load_gc_heavy(
    nowait_paths: Sequence[Path], original_paths: Sequence[Path]
) -> Dict[str, Dict[str, List[float]]]:
    samples: Dict[str, Dict[str, List[float]]] = {
        "ori": {"bigfile": [], "smallfile": []},
        "original-csgc": {"bigfile": [], "smallfile": []},
        "nowait-quiet": {"bigfile": [], "smallfile": []},
    }
    for path in nowait_paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("fio_error") == "0":
                    samples["nowait-quiet"][row["workload"]].append(float(row["bw_mib_s"]))
    for path in original_paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if row.get("fio_error") != "0":
                    continue
                system = "original-csgc" if row["configuration"] == "original-csgc" else "ori"
                samples[system][row["workload"]].append(float(row["bw_mib_s"]))
    for system in SYSTEMS:
        for workload in ("bigfile", "smallfile"):
            if len(samples[system][workload]) != 6:
                raise ValueError(
                    f"Expected six GC-heavy samples for {system}/{workload}, "
                    f"found {len(samples[system][workload])}"
                )
    return samples


# Draw absolute and original-CSGC-normalized GC-heavy throughput.
def plot_gc_heavy(
    samples: Mapping[str, Mapping[str, Sequence[float]]], output: Path
) -> List[Dict[str, object]]:
    workloads = ("bigfile", "smallfile")
    labels = ("Large file", "Small files")
    x = np.arange(len(workloads))
    width = 0.24
    fig, (left, right) = plt.subplots(1, 2, figsize=(7.2, 2.8))
    rows: List[Dict[str, object]] = []
    for index, system in enumerate(SYSTEMS):
        means = [statistics.fmean(samples[system][workload]) for workload in workloads]
        stddev = [statistics.stdev(samples[system][workload]) for workload in workloads]
        baseline = [statistics.fmean(samples["original-csgc"][workload]) for workload in workloads]
        ratios = [value / reference for value, reference in zip(means, baseline)]
        ratio_error = [error / reference for error, reference in zip(stddev, baseline)]
        offset = x + (index - 1) * width
        bars = left.bar(
            offset,
            means,
            width,
            yerr=stddev,
            capsize=2.5,
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.55,
            hatch=HATCHES[system],
            label=LABELS[system],
        )
        right.bar(
            offset,
            ratios,
            width,
            yerr=ratio_error,
            capsize=2.5,
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.55,
            hatch=HATCHES[system],
            label=LABELS[system],
        )
        for bar, value in zip(bars, means):
            left.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 13,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                rotation=90,
            )
        for workload, mean, error, ratio in zip(workloads, means, stddev, ratios):
            rows.append(
                {
                    "workload": workload,
                    "system": LABELS[system],
                    "samples_mib_s": ";".join(
                        f"{value:.6f}" for value in samples[system][workload]
                    ),
                    "mean_mib_s": mean,
                    "sample_stddev_mib_s": error,
                    "normalized_to_original_csgc": ratio,
                }
            )
    left.set_xticks(x, labels)
    right.set_xticks(x, labels)
    left.set_ylim(bottom=0)
    right.set_ylim(bottom=0)
    left.set_ylabel("Write bandwidth (MiB/s)")
    right.set_ylabel("Normalized throughput\n(original CSGC = 1)")
    left.set_xlabel("Fully preconditioned GC-heavy workload\n(a)")
    right.set_xlabel("Fully preconditioned GC-heavy workload\n(b)")
    left.grid(axis="y")
    right.grid(axis="y")
    right.axhline(1.0, color="black", linewidth=0.7, linestyle="--")
    handles, labels = right.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        ncol=3,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.04),
        frameon=False,
        fontsize=8,
    )
    save_figure(fig, output)
    return rows


# Build a machine-readable summary for the bilingual narrative reports.
def build_summary(
    combined: Mapping[str, object],
    strict_timeline: Sequence[Mapping[str, str]],
    section_rows: Sequence[Mapping[str, object]],
    gc_heavy_rows: Sequence[Mapping[str, object]],
    source_paths: Mapping[str, object],
) -> Dict[str, object]:
    systems = combined["systems"]
    repetitions = combined["nowait_repetitions"]
    headline: List[Dict[str, object]] = []
    ratios_original: List[float] = []
    ratios_ori: List[float] = []
    for label, suffix in OVERALL:
        ori = metric(systems, "ori", suffix, "throughput_ops_s")
        original = metric(systems, "original-csgc", suffix, "throughput_ops_s")
        nowait = metric(systems, "nowait-quiet", suffix, "throughput_ops_s")
        ratios_original.append(nowait / original)
        ratios_ori.append(nowait / ori)
        headline.append(
            {
                "workload": label,
                "ori_kops": ori / 1000,
                "original_csgc_kops": original / 1000,
                "nowait_kops": nowait / 1000,
                "nowait_stddev_kops": nowait_std(repetitions, suffix, "throughput_ops_s")
                / 1000,
                "nowait_vs_original_csgc": nowait / original,
                "nowait_vs_ori": nowait / ori,
            }
        )
    strict_values = [float(row["throughput_ops_s_mean"]) for row in strict_timeline]
    gc_index = {
        (row["system"], row["workload"]): row for row in gc_heavy_rows
    }
    summary: Dict[str, object] = {
        "headline": headline,
        "headline_geomean_nowait_vs_original_csgc": geometric_mean(ratios_original),
        "headline_geomean_nowait_vs_ori": geometric_mean(ratios_ori),
        "strict_fileserver_300s": {
            "sample_count": len(strict_values),
            "interval_s": 5,
            "mean_kops": statistics.fmean(strict_values) / 1000,
            "minimum_kops": min(strict_values) / 1000,
            "maximum_kops": max(strict_values) / 1000,
        },
        "section_size": list(section_rows),
        "gc_heavy": list(gc_heavy_rows),
        "gc_heavy_key_ratios": {
            "bigfile_nowait_vs_original_csgc": gc_index[
                (LABELS["nowait-quiet"], "bigfile")
            ]["normalized_to_original_csgc"],
            "smallfile_nowait_vs_original_csgc": gc_index[
                (LABELS["nowait-quiet"], "smallfile")
            ]["normalized_to_original_csgc"],
            "bigfile_nowait_vs_ori": gc_index[
                (LABELS["nowait-quiet"], "bigfile")
            ]["mean_mib_s"]
            / gc_index[(LABELS["ori"], "bigfile")]["mean_mib_s"],
            "smallfile_nowait_vs_ori": gc_index[
                (LABELS["nowait-quiet"], "smallfile")
            ]["mean_mib_s"]
            / gc_index[(LABELS["ori"], "smallfile")]["mean_mib_s"],
        },
        "measurement_limits": {
            "nowait_physical_waf": (
                "measured separately with the low-overhead OpenSSD paper-WAF build; "
                "throughput remains sourced from quiet runs except corrected section-size 16"
            ),
            "nowait_section16": (
                "corrected CSGC point measured with low-overhead Host and OpenSSD metrics; "
                "a formal quiet-build confirmation remains pending"
            ),
            "strict_300s_baseline_timeline": "ORI and original CSGC are available for 60 seconds only",
            "ori_section_migration": "only the s=8 historical raw statistic is available",
            "original_csgc_section_migration": "historical paper-era raw logs, not the 2026 paired baseline",
            "iplfs": "not evaluated in the current three-system study",
        },
        "sources": source_paths,
    }
    return summary


# Parse arguments, generate all figures, and export auditable source tables.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--combined-json", type=Path, default=DEFAULT_COMBINED)
    parser.add_argument("--paper-metrics", type=Path, default=DEFAULT_PAPER_METRICS)
    parser.add_argument(
        "--section16-metrics", type=Path, default=DEFAULT_SECTION16_METRICS
    )
    parser.add_argument("--waf-analysis", type=Path, default=DEFAULT_WAF_ANALYSIS)
    parser.add_argument("--historical-csgc-root", type=Path, default=DEFAULT_HISTORICAL_CSGC)
    parser.add_argument("--historical-ori-stat", type=Path, default=DEFAULT_HISTORICAL_ORI_STAT)
    parser.add_argument(
        "--nowait-gc-heavy", type=Path, nargs="+", default=list(DEFAULT_NOWAIT_GC_HEAVY)
    )
    parser.add_argument(
        "--original-gc-heavy", type=Path, nargs="+", default=list(DEFAULT_ORIGINAL_GC_HEAVY)
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    combined = load_json(args.combined_json.resolve())
    paper_metrics = args.paper_metrics.resolve()
    section16_metrics = args.section16_metrics.resolve()
    waf_analysis = args.waf_analysis.resolve()
    nowait_waf = apply_nowait_waf(combined, read_csv(waf_analysis / "waf-summary.csv"))
    section_metrics, section16_summary = apply_corrected_section16(
        combined, paper_metrics, section16_metrics
    )
    systems = combined["systems"]
    repetitions = combined["nowait_repetitions"]
    output = args.output.resolve()
    figures = output / "figures"
    data = output / "data"
    figures.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    section_metrics_path = paper_metrics / "section-metrics-summary.csv"
    timeline_path = paper_metrics / "filebench-timeline-summary.csv"
    strict_timeline = read_csv(timeline_path)
    csgc_migration, csgc_migration_sources = historical_csgc_migration(
        args.historical_csgc_root.resolve()
    )
    ori_s8_migration = historical_ori_migration(args.historical_ori_stat.resolve())
    if set(csgc_migration) != {1, 2, 4, 8, 16}:
        raise ValueError(f"Incomplete historical CSGC migration series: {csgc_migration}")

    configure_plot_style()
    overall_rows = plot_overall(systems, repetitions, figures / "figure4_performance_overview")
    timeline_rows, latency_rows = plot_timeline_and_latency(
        systems,
        repetitions,
        strict_timeline,
        figures / "figure5_timeline_and_latency",
    )
    plot_full_nowait_timeline(strict_timeline, figures / "figure5a_nowait_strict_300s")
    utilization_rows = plot_sweep(
        systems,
        repetitions,
        nowait_waf,
        ("0.60", "0.70", "0.80", "0.90", "0.95"),
        tuple(f"fio-util-{value}" for value in ("0.6", "0.7", "0.8", "0.9", "0.95")),
        "Storage utilization",
        figures / "figure6_storage_utilization",
    )
    section_rows = plot_section_size(
        systems,
        repetitions,
        nowait_waf,
        section_metrics,
        csgc_migration,
        ori_s8_migration,
        figures / "figure7_section_size",
    )
    skew_rows = plot_sweep(
        systems,
        repetitions,
        nowait_waf,
        ("uni.", "z/0.3", "z/0.7", "z/0.9", "z/1.1"),
        ("fio-skew-uniform", "fio-skew-0.3", "fio-skew-0.7", "fio-skew-0.9", "fio-skew-1.1"),
        "Write distribution",
        figures / "figure8_write_skewness",
    )
    gc_heavy_samples = load_gc_heavy(args.nowait_gc_heavy, args.original_gc_heavy)
    gc_heavy_rows = plot_gc_heavy(gc_heavy_samples, figures / "figure9_gc_heavy")

    write_csv(
        data / "figure4_performance_overview.csv",
        ("workload", "case_suffix", "system", "throughput_kops", "sample_stddev_kops"),
        overall_rows,
    )
    write_csv(
        data / "figure5a_fileserver_timeline.csv",
        ("elapsed_s", "system", "throughput_kops", "sample_stddev_kops", "window"),
        timeline_rows,
    )
    write_csv(
        data / "figure5b_ycsb_a_latency.csv",
        ("metric", "system", "latency_ms", "sample_stddev_ms"),
        latency_rows,
    )
    write_csv(
        data / "figure6_storage_utilization.csv",
        (
            "x",
            "case_suffix",
            "system",
            "throughput_kops",
            "throughput_stddev_kops",
            "physical_waf",
            "physical_waf_stddev",
        ),
        utilization_rows,
    )
    write_csv(
        data / "figure7_section_size.csv",
        (
            "section_size",
            "ori_migration_us",
            "original_csgc_migration_us",
            "nowait_migration_us",
            "nowait_migration_stddev_us",
            "nowait_gc_path",
            "ori_throughput_kops",
            "original_csgc_throughput_kops",
            "nowait_throughput_kops",
            "nowait_throughput_stddev_kops",
            "ori_physical_waf",
            "original_csgc_physical_waf",
            "nowait_physical_waf",
            "nowait_physical_waf_stddev",
        ),
        section_rows,
    )
    write_csv(
        data / "figure8_write_skewness.csv",
        (
            "x",
            "case_suffix",
            "system",
            "throughput_kops",
            "throughput_stddev_kops",
            "physical_waf",
            "physical_waf_stddev",
        ),
        skew_rows,
    )
    write_csv(
        data / "figure9_gc_heavy.csv",
        (
            "workload",
            "system",
            "samples_mib_s",
            "mean_mib_s",
            "sample_stddev_mib_s",
            "normalized_to_original_csgc",
        ),
        gc_heavy_rows,
    )

    source_paths: Dict[str, object] = {
        "combined_results": str(args.combined_json.resolve()),
        "paper_metrics_section": str(section_metrics_path.resolve()),
        "paper_metrics_section16_corrected": str(
            (section16_metrics / "section-metrics-summary.csv").resolve()
        ),
        "paper_metrics_section16_summary": section16_summary,
        "paper_metrics_timeline": str(timeline_path.resolve()),
        "paper_waf_summary": str((waf_analysis / "waf-summary.csv").resolve()),
        "paper_waf_runs": str((waf_analysis / "waf-runs.csv").resolve()),
        "historical_csgc_migration": csgc_migration_sources,
        "historical_ori_s8_migration": str(args.historical_ori_stat.resolve()),
        "nowait_gc_heavy": [str(path.resolve()) for path in args.nowait_gc_heavy],
        "original_gc_heavy": [str(path.resolve()) for path in args.original_gc_heavy],
    }
    summary = build_summary(
        combined, strict_timeline, section_rows, gc_heavy_rows, source_paths
    )
    (data / "analysis-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (data / "source-manifest.json").write_text(
        json.dumps(source_paths, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
