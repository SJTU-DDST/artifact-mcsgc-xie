#!/usr/bin/env python3
"""Compare two mCSGC candidates with the paired original CSGC/ORI baseline."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_europar25_original_matrix import parse_case


SYSTEMS = ("ori", "original-csgc", "conflict-aware", "rolling-final")
LABELS = {
    "ori": "ORI",
    "original-csgc": "Original CSGC",
    "conflict-aware": "Conflict-aware",
    "rolling-final": "Rolling-final",
}
COLORS = {
    "ori": "#5E96E6",
    "original-csgc": "#E67365",
    "conflict-aware": "#47A66B",
    "rolling-final": "#B36AB3",
}
MARKERS = {"ori": "^", "original-csgc": "o", "conflict-aware": "s", "rolling-final": "D"}
OVERALL = (
    ("Filebench fileserver", "filebench-fileserver"),
    ("Filebench varmail", "filebench-varmail"),
    ("YCSB-A", "ycsb-a"),
    ("YCSB-F", "ycsb-f"),
    ("fio uniform", "fio-overall-uniform"),
    ("fio Zipf 1.1", "fio-overall-zipf11"),
)


# Return a multiplicative mean suitable for normalized throughput ratios.
def geometric_mean(values: Iterable[float]) -> float:
    values = list(values)
    return math.exp(sum(math.log(value) for value in values) / len(values))


# Read the latest successful row for every case and parse its raw output.
def read_batch_cases(batch: Path, expected: int) -> Dict[str, Dict[str, object]]:
    results = batch / "case-results.tsv"
    if not results.exists():
        raise ValueError(f"Missing {results}")
    with results.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t") if row["status"] == "0"]
    latest = {row["case_id"]: row for row in rows}
    if len(latest) != expected:
        raise ValueError(f"Expected {expected} successful cases in {batch}, found {len(latest)}")
    return {case_id: parse_case(row) for case_id, row in latest.items()}


# Convert batch-specific case IDs into one common system/suffix namespace.
def combine_cases(
    baseline: Mapping[str, Mapping[str, object]],
    candidates: Mapping[str, Mapping[str, object]],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    combined: Dict[str, Dict[str, Dict[str, object]]] = {system: {} for system in SYSTEMS}
    for case_id, metrics in baseline.items():
        if case_id.startswith("ori-"):
            combined["ori"][case_id[4:]] = dict(metrics)
        elif case_id.startswith("cs-"):
            combined["original-csgc"][case_id[3:]] = dict(metrics)
    for case_id, metrics in candidates.items():
        for system in ("conflict-aware", "rolling-final"):
            prefix = f"{system}-"
            if case_id.startswith(prefix):
                combined[system][case_id[len(prefix):]] = dict(metrics)
                break
    expected_suffixes = set(combined["ori"])
    for system in SYSTEMS:
        if set(combined[system]) != expected_suffixes or len(combined[system]) != 22:
            raise ValueError(f"System {system} does not contain the same 22 cases")
    return combined


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


# Save one figure in both vector and raster formats.
def save_figure(fig: plt.Figure, output: Path) -> None:
    fig.tight_layout()
    fig.savefig(output.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(output.with_suffix(".png"), dpi=180, bbox_inches="tight")
    plt.close(fig)


# Draw the six headline workloads normalized to the original CSGC result.
def plot_overall(cases: Mapping[str, Mapping[str, Mapping[str, object]]], figures: Path) -> None:
    x = np.arange(len(OVERALL))
    width = 0.19
    fig, ax = plt.subplots(figsize=(11.5, 3.4))
    for index, system in enumerate(SYSTEMS):
        values = []
        for _, suffix in OVERALL:
            base = metric(cases, "original-csgc", suffix, "throughput_ops_s")
            values.append(metric(cases, system, suffix, "throughput_ops_s") / base)
        ax.bar(
            x + (index - 1.5) * width,
            values,
            width,
            label=LABELS[system],
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.5,
        )
    ax.axhline(1.0, color="black", linewidth=0.8, linestyle="--")
    ax.set_ylabel("Normalized throughput\n(original CSGC = 1)")
    ax.set_xticks(x, [label for label, _ in OVERALL])
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.20))
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, figures / "figure4_four_system_overall")


# Draw the period trace and YCSB-A latency for all four systems.
def plot_timeline_latency(cases: Mapping[str, Mapping[str, Mapping[str, object]]], figures: Path) -> None:
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
    width = 0.19
    for index, system in enumerate(SYSTEMS):
        values = [metric(cases, system, "ycsb-a", key) / 1000 for key in keys]
        right.bar(
            x + (index - 1.5) * width,
            values,
            width,
            label=LABELS[system],
            color=COLORS[system],
            edgecolor="black",
            linewidth=0.5,
        )
    right.set_xticks(x, labels)
    right.set_ylabel("Latency (ms)")
    right.grid(axis="y", alpha=0.25)
    right.legend(ncol=2)
    save_figure(fig, figures / "figure5_four_system_timeline_latency")


# Draw throughput and WAF trends for one parameter sweep.
def plot_sweep(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
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
    right.set_xlabel(x_label)
    right.set_ylabel("Write amplification")
    right.grid(alpha=0.3)
    right.legend(ncol=2)
    right.text(
        0.02,
        0.04,
        "Candidate WAF: N/A",
        transform=right.transAxes,
        fontsize=8,
        color="#555555",
    )
    save_figure(fig, figures / name)


# Write one row per matched workload point for later statistical analysis.
def write_comparison_csv(
    cases: Mapping[str, Mapping[str, Mapping[str, object]]], output: Path
) -> None:
    fields = [
        "case_suffix",
        "workload_type",
        "ori_ops_s",
        "original_csgc_ops_s",
        "conflict_aware_ops_s",
        "rolling_final_ops_s",
        "conflict_vs_original",
        "rolling_vs_original",
        "conflict_vs_ori",
        "rolling_vs_ori",
        "rolling_vs_conflict",
        "ori_waf",
        "original_csgc_waf",
        "conflict_aware_waf",
        "rolling_final_waf",
    ]
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for suffix in sorted(cases["ori"]):
            values = {system: metric(cases, system, suffix, "throughput_ops_s") for system in SYSTEMS}
            writer.writerow(
                {
                    "case_suffix": suffix,
                    "workload_type": cases["ori"][suffix]["workload_type"],
                    "ori_ops_s": values["ori"],
                    "original_csgc_ops_s": values["original-csgc"],
                    "conflict_aware_ops_s": values["conflict-aware"],
                    "rolling_final_ops_s": values["rolling-final"],
                    "conflict_vs_original": values["conflict-aware"] / values["original-csgc"],
                    "rolling_vs_original": values["rolling-final"] / values["original-csgc"],
                    "conflict_vs_ori": values["conflict-aware"] / values["ori"],
                    "rolling_vs_ori": values["rolling-final"] / values["ori"],
                    "rolling_vs_conflict": values["rolling-final"] / values["conflict-aware"],
                    "ori_waf": cases["ori"][suffix].get("waf"),
                    "original_csgc_waf": cases["original-csgc"][suffix].get("waf"),
                    "conflict_aware_waf": cases["conflict-aware"][suffix].get("waf"),
                    "rolling_final_waf": cases["rolling-final"][suffix].get("waf"),
                }
            )


# Build a concise Chinese report with explicit comparison denominators.
def build_report(
    candidate_batch: Path,
    baseline_batch: Path,
    cases: Mapping[str, Mapping[str, Mapping[str, object]]],
) -> str:
    lines = [
        "# Euro-Par 负载上的 mCSGC 最佳候选对比",
        "",
        f"- 候选批次：`{candidate_batch}`",
        f"- 原始系统基线批次：`{baseline_batch}`",
        "- 候选系统：生命周期修复后的 quiet Conflict-aware 与 Rolling-final。",
        "- 每个候选运行与原论文 artifact 相同的 22 个 case，共 44 轮。",
        "- 所有倍率均按同名 case 直接配对，不混用后来的 GC-heavy 工作负载。",
        "",
        "## 六项总体性能",
        "",
        "| 负载 | ORI (kop/s) | 原始 CSGC | Conflict-aware | Rolling-final | Conflict/原始 | Rolling/原始 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    conflict_ratios: List[float] = []
    rolling_ratios: List[float] = []
    conflict_ori: List[float] = []
    rolling_ori: List[float] = []
    for label, suffix in OVERALL:
        values = {system: metric(cases, system, suffix, "throughput_ops_s") for system in SYSTEMS}
        conflict_ratios.append(values["conflict-aware"] / values["original-csgc"])
        rolling_ratios.append(values["rolling-final"] / values["original-csgc"])
        conflict_ori.append(values["conflict-aware"] / values["ori"])
        rolling_ori.append(values["rolling-final"] / values["ori"])
        lines.append(
            f"| {label} | {values['ori'] / 1000:.3f} | {values['original-csgc'] / 1000:.3f} "
            f"| {values['conflict-aware'] / 1000:.3f} | {values['rolling-final'] / 1000:.3f} "
            f"| {values['conflict-aware'] / values['original-csgc']:.3f}x "
            f"| {values['rolling-final'] / values['original-csgc']:.3f}x |"
        )
    lines.extend(
        [
            "",
            f"六项等权几何平均：Conflict-aware/原始 CSGC 为 **{geometric_mean(conflict_ratios):.3f}x**，"
            f"Rolling-final/原始 CSGC 为 **{geometric_mean(rolling_ratios):.3f}x**；"
            f"相对 ORI 分别为 **{geometric_mean(conflict_ori):.3f}x** 和 "
            f"**{geometric_mean(rolling_ori):.3f}x**。",
            "",
            "分开看负载类型更准确：两个 Filebench 点的几何平均仅为 "
            f"**{geometric_mean(conflict_ratios[:2]):.3f}x** 和 "
            f"**{geometric_mean(rolling_ratios[:2]):.3f}x**；后四个 YCSB/fio 点则为 "
            f"**{geometric_mean(conflict_ratios[2:]):.3f}x** 和 "
            f"**{geometric_mean(rolling_ratios[2:]):.3f}x**。因此六项总几何平均主要反映 "
            "Filebench 严重退化与其余负载稳定加速的混合结果，不能把它解释为候选版本在所有负载上都更慢。",
            "",
            "## 输出文件",
            "",
            "- `comparison.csv`：22 个 case 的四系统吞吐、WAF 和配对倍率。",
            "- `combined-results.json`：完整结构化指标。",
            "- `figures/`：对应原论文 Figure 4 至 Figure 8 的四系统扩展图。",
            "",
            "## 口径限制",
            "",
            "- 每个候选每个 case 只运行一次，因此可用于整体趋势和论文图，不足以单独给出置信区间。",
            "- Host 候选代码移除了专有诊断开销；通用 `F2FS_STAT_FS` 与原始系统基线一致地启用。",
            "- 原版 ORI/CSGC 的 WAF 来自设备统计；候选固件记录 "
            "`basic_stats=0`，其占位零已按缺失值处理，不能用于 WAF 对比。",
            "- OpenSSD 源码提交和 Vitis 输入哈希会被记录，但 Host 无法证明当前运行 ELF 的逐字节身份。",
        ]
    )
    return "\n".join(lines) + "\n"


# Parse both batches, produce tables, and draw the four-system figure set.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_batch", type=Path)
    parser.add_argument("--baseline-batch", required=True, type=Path)
    args = parser.parse_args()
    candidate_batch = args.candidate_batch.resolve()
    baseline_batch = args.baseline_batch.resolve()

    candidates = read_batch_cases(candidate_batch, 44)
    baseline = read_batch_cases(baseline_batch, 44)
    cases = combine_cases(baseline, candidates)
    analysis = candidate_batch / "analysis"
    figures = analysis / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    write_comparison_csv(cases, analysis / "comparison.csv")
    with (analysis / "combined-results.json").open("w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False, indent=2)

    plt.rcParams.update({"figure.dpi": 150, "axes.labelsize": 10, "legend.fontsize": 8})
    plot_overall(cases, figures)
    plot_timeline_latency(cases, figures)
    plot_sweep(
        cases,
        figures,
        "figure6_four_system_storage_utilization",
        ["60%", "70%", "80%", "90%", "95%"],
        [f"fio-util-{value}" for value in ("0.6", "0.7", "0.8", "0.9", "0.95")],
        "Storage utilization",
    )
    plot_sweep(
        cases,
        figures,
        "figure7_four_system_section_size",
        ["1", "2", "4", "8", "16"],
        [f"fio-section-{value}" for value in ("1", "2", "4", "8", "16")],
        "Segments per section",
    )
    plot_sweep(
        cases,
        figures,
        "figure8_four_system_write_skewness",
        ["uniform", "z/0.3", "z/0.7", "z/0.9", "z/1.1"],
        ["fio-skew-uniform", "fio-skew-0.3", "fio-skew-0.7", "fio-skew-0.9", "fio-skew-1.1"],
        "Write distribution",
    )
    report = build_report(candidate_batch, baseline_batch, cases)
    (analysis / "mcsgc-candidate-comparison.md").write_text(report, encoding="utf-8")
    print(analysis)


if __name__ == "__main__":
    main()
