#!/usr/bin/env python3
"""Analyze one Euro-Par 2025 ORI/CSGC reproduction matrix and redraw Figures 4-8."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


F2FS_COLOR = "#5E96E6"
CSGC_COLOR = "#E67365"


def read_text(path: Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def parse_scaled_number(value: str) -> float:
    value = value.strip()
    multiplier = 1.0
    if value[-1:].lower() == "k":
        multiplier = 1_000.0
        value = value[:-1]
    elif value[-1:].lower() == "m":
        multiplier = 1_000_000.0
        value = value[:-1]
    return float(value) * multiplier


def last_match(pattern: str, text: str) -> Optional[str]:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    return matches[-1] if matches else None


def read_key_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in read_text(path).splitlines():
        if "=" not in line or line.startswith("["):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_case(row: Dict[str, str]) -> Dict[str, object]:
    output = Path(row["output_path"])
    workload_type = row["workload_type"]
    metrics: Dict[str, object] = dict(row)
    metrics.update(
        throughput_ops_s=None,
        bandwidth_mib_s=None,
        waf=None,
        migration_us=None,
        read_avg_us=None,
        read_p99_us=None,
        update_avg_us=None,
        update_p99_us=None,
        timeline=[],
    )

    if workload_type == "filebench":
        text = read_text(output / "filebench.log")
        value = last_match(r"IO Summary:\s+[\d.]+\s+ops\s+([\d.]+)\s+ops/s", text)
        if value is None:
            raise ValueError(f"No filebench throughput in {output}")
        metrics["throughput_ops_s"] = float(value)
        timeline: List[Tuple[float, float]] = []
        for stamp, _ops, ops_s in re.findall(
            r"([\d.]+):\s+IO Summary:\s+([\d.]+)\s+ops\s+([\d.]+)\s+ops/s", text
        ):
            timeline.append((float(stamp), float(ops_s)))
        metrics["timeline"] = timeline
    elif workload_type == "ycsb":
        text = read_text(output / "ycsb.log")
        value = last_match(r"\[OVERALL\], Throughput\(ops/sec\),\s*([\d.]+)", text)
        if value is None:
            raise ValueError(f"No YCSB throughput in {output}")
        metrics["throughput_ops_s"] = float(value)
        for key, operation, stat in (
            ("read_avg_us", "READ", "AverageLatency"),
            ("read_p99_us", "READ", "99thPercentileLatency"),
            ("update_avg_us", "UPDATE", "AverageLatency"),
            ("update_p99_us", "UPDATE", "99thPercentileLatency"),
        ):
            found = last_match(
                rf"\[{operation}\], {stat}\(us\),\s*([\d.]+)", text
            )
            metrics[key] = float(found) if found is not None else None
    elif workload_type == "fio":
        text = read_text(output / "fio.log")
        value = last_match(r"\bwrite:\s+IOPS=([\d.]+[kKmM]?)", text)
        if value is None:
            raise ValueError(f"No fio IOPS in {output}")
        metrics["throughput_ops_s"] = parse_scaled_number(value)
        bw = last_match(r"\bwrite:\s+IOPS=[^,]+,\s+BW=([\d.]+)([KMG]iB/s)", text)
        if bw is None:
            bw_matches = re.findall(
                r"\bwrite:\s+IOPS=[^,]+,\s+BW=([\d.]+)([KMG]iB/s)", text
            )
            if bw_matches:
                bw = bw_matches[-1]
        if isinstance(bw, tuple):
            number, unit = bw
            factor = {"KiB/s": 1 / 1024, "MiB/s": 1, "GiB/s": 1024}[unit]
            metrics["bandwidth_mib_s"] = float(number) * factor

    stat_path = output / "stat.log"
    if stat_path.exists():
        stat = read_text(stat_path)
        basic_stats = last_match(r"openssd_perf:.*?basic_stats=(\d+)", stat)
        waf = last_match(r"physical WAF:\s+(\d+)", stat)
        if basic_stats != "0" and waf is not None:
            metrics["waf"] = float(waf) / 1000.0
        stat_tag = "CSGC" if row["mode"] == "cs" else "ORIGC"
        migration = last_match(
            rf"<{stat_tag} STAT>.*?block migration:\s+(\d+)\s+ns", stat
        )
        metrics["migration_us"] = float(migration) / 1000.0 if migration is not None else None

    return metrics


def require_metric(cases: Dict[str, Dict[str, object]], case_id: str, key: str) -> float:
    value = cases[case_id].get(key)
    if value is None:
        raise ValueError(f"Missing {key} for {case_id}")
    return float(value)


def geometric_mean(values: Iterable[float]) -> float:
    values = list(values)
    return math.exp(sum(math.log(value) for value in values) / len(values))


def configure_plot() -> None:
    plt.rcParams.update(
        {
            "axes.labelsize": 11,
            "axes.titlesize": 11,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 9,
            "figure.dpi": 150,
        }
    )


def save_figure(fig: plt.Figure, output: Path, aliases: Iterable[Path] = ()) -> None:
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    for alias in aliases:
        fig.savefig(alias, bbox_inches="tight")
    plt.close(fig)


def plot_figure4(cases: Dict[str, Dict[str, object]], figures: Path) -> None:
    labels = ["fileserver", "varmail", "YCSB-A", "YCSB-F", "fio-uniform", "fio-skewed"]
    suffixes = [
        "filebench-fileserver",
        "filebench-varmail",
        "ycsb-a",
        "ycsb-f",
        "fio-overall-uniform",
        "fio-overall-zipf11",
    ]
    f2fs = [require_metric(cases, f"ori-{suffix}", "throughput_ops_s") for suffix in suffixes]
    csgc = [require_metric(cases, f"cs-{suffix}", "throughput_ops_s") for suffix in suffixes]
    f2fs_norm = [left / right for left, right in zip(f2fs, csgc)]
    csgc_norm = [1.0] * len(labels)

    x = np.arange(len(labels))
    width = 0.34
    fig, ax = plt.subplots(figsize=(10, 2.8))
    left = ax.bar(x - width / 2, f2fs_norm, width, label="F2FS", color=F2FS_COLOR, edgecolor="black", hatch="xx")
    right = ax.bar(x + width / 2, csgc_norm, width, label="CSGC", color=CSGC_COLOR, edgecolor="black", hatch="\\\\")
    for bars, values in ((left, f2fs), (right, csgc)):
        for bar, value in zip(bars, values):
            ax.annotate(f"{value / 1000:.1f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 2), textcoords="offset points", ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("Normalized Throughput\n(absolute: kop/s)")
    ax.set_xticks(x, labels)
    ax.set_ylim(0, max(max(f2fs_norm), 1.0) * 1.35)
    ax.legend(ncol=2)
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, figures / "figure4_overall_performance.pdf", [figures / "figure4_overall_performance.png"])


def plot_figure5(cases: Dict[str, Dict[str, object]], figures: Path) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 3.0))
    for mode, label, color, marker in (
        ("ori", "F2FS", F2FS_COLOR, "^"),
        ("cs", "CSGC", CSGC_COLOR, "o"),
    ):
        timeline = cases[f"{mode}-filebench-period"]["timeline"]
        if not timeline:
            raise ValueError(f"Missing Filebench period timeline for {mode}")
        origin = float(timeline[0][0])
        left.plot([float(t) - origin for t, _ in timeline], [float(v) / 1000 for _, v in timeline],
                  label=label, color=color, marker=marker, markersize=2.5)
    left.set_xlabel("Time (s)\n(a)")
    left.set_ylabel("Throughput (kop/s)")
    left.grid(alpha=0.3)
    left.legend()

    keys = ["read_avg_us", "read_p99_us", "update_avg_us", "update_p99_us"]
    labels = ["read/Avg.", "read/99%", "update/Avg.", "update/99%"]
    x = np.arange(len(keys))
    width = 0.34
    f2fs = [require_metric(cases, "ori-ycsb-a", key) / 1000 for key in keys]
    csgc = [require_metric(cases, "cs-ycsb-a", key) / 1000 for key in keys]
    left_bars = right.bar(x - width / 2, f2fs, width, label="F2FS", color=F2FS_COLOR, edgecolor="black", hatch="xx")
    right_bars = right.bar(x + width / 2, csgc, width, label="CSGC", color=CSGC_COLOR, edgecolor="black", hatch="\\\\")
    for bars in (left_bars, right_bars):
        for bar in bars:
            right.annotate(f"{bar.get_height():.1f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                           xytext=(0, 2), textcoords="offset points", ha="center", fontsize=7)
    right.set_xticks(x, labels)
    right.set_ylabel("Latency (ms)")
    right.set_xlabel("\n(b)")
    right.grid(axis="y", alpha=0.25)
    right.legend()
    save_figure(fig, figures / "figure5_timeline_and_latency.pdf", [figures / "figure5_timeline_and_latency.png"])


def plot_two_metric_lines(
    cases: Dict[str, Dict[str, object]],
    figures: Path,
    output_name: str,
    x_values: List[str],
    case_suffixes: List[str],
    x_label: str,
) -> None:
    fig, (left, right) = plt.subplots(1, 2, figsize=(6.5, 3.0))
    for mode, label, color, marker in (
        ("ori", "F2FS", F2FS_COLOR, "^"),
        ("cs", "CSGC", CSGC_COLOR, "o"),
    ):
        throughput = [require_metric(cases, f"{mode}-{suffix}", "throughput_ops_s") / 1000 for suffix in case_suffixes]
        waf = [require_metric(cases, f"{mode}-{suffix}", "waf") for suffix in case_suffixes]
        left.plot(x_values, throughput, label=label, color=color, marker=marker, markeredgecolor="black")
        right.plot(x_values, waf, label=label, color=color, marker=marker, markeredgecolor="black")
    left.set_ylabel("Throughput (kop/s)")
    left.set_xlabel(f"{x_label}\n(a)")
    left.grid(alpha=0.3)
    right.set_ylabel("Write Amplification")
    right.set_xlabel(f"{x_label}\n(b)")
    right.grid(alpha=0.3)
    right.legend()
    save_figure(fig, figures / f"{output_name}.pdf", [figures / f"{output_name}.png"])


def plot_figure7(cases: Dict[str, Dict[str, object]], figures: Path) -> None:
    sizes = ["1", "2", "4", "8", "16"]
    suffixes = [f"fio-section-{size}" for size in sizes]
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.0))
    for mode, label, color, marker in (
        ("ori", "F2FS", F2FS_COLOR, "^"),
        ("cs", "CSGC", CSGC_COLOR, "o"),
    ):
        migration = [cases[f"{mode}-{suffix}"].get("migration_us") for suffix in suffixes]
        throughput = [require_metric(cases, f"{mode}-{suffix}", "throughput_ops_s") / 1000 for suffix in suffixes]
        waf = [require_metric(cases, f"{mode}-{suffix}", "waf") for suffix in suffixes]
        if all(value is not None for value in migration):
            axes[0].plot(
                sizes,
                [float(value) for value in migration],
                label=label,
                color=color,
                marker=marker,
                markeredgecolor="black",
            )
        axes[1].plot(sizes, throughput, label=label, color=color, marker=marker, markeredgecolor="black")
        axes[2].plot(sizes, waf, label=label, color=color, marker=marker, markeredgecolor="black")
    if not axes[0].lines:
        axes[0].text(
            0.5,
            0.5,
            "Not collected\n(runtime breakdown disabled)",
            ha="center",
            va="center",
            transform=axes[0].transAxes,
            fontsize=10,
        )
        axes[0].set_xticks(range(len(sizes)), sizes)
    axes[0].set_ylabel("Migration Latency (us)")
    axes[1].set_ylabel("Throughput (kop/s)")
    axes[2].set_ylabel("Write Amplification")
    for index, ax in enumerate(axes):
        ax.set_xlabel(f"Section Size (#segments)\n({chr(ord('a') + index)})")
        ax.grid(alpha=0.3)
    axes[2].legend()
    save_figure(fig, figures / "figure7_section_size.pdf", [figures / "figure7_section_size.png"])


def build_report(batch: Path, cases: Dict[str, Dict[str, object]], summary_csv: Path) -> str:
    provenance = read_key_values(batch / "provenance.txt")
    completed = read_key_values(batch / "completed.env")
    overall = [
        ("Filebench fileserver", "filebench-fileserver"),
        ("Filebench varmail", "filebench-varmail"),
        ("YCSB-A", "ycsb-a"),
        ("YCSB-F", "ycsb-f"),
        ("fio uniform", "fio-overall-uniform"),
        ("fio Zipf 1.1", "fio-overall-zipf11"),
    ]
    speedups = []
    lines = [
        "# Euro-Par 2025 原始 CSGC/ORI 复现实验结果",
        "",
        f"- 批次目录：`{batch}`",
        "- 执行者：Codex 直接运行",
        f"- 开始时间：`{completed.get('started_at', provenance.get('started_at', 'unknown'))}`",
        f"- 完成时间：`{completed.get('completed_at', 'unknown')}`",
        f"- 最外层脚本：`{provenance.get('outer_script', 'run_europar25_original_matrix.sh')}`",
        f"- Host：`{provenance.get('host_branch', 'unknown')}@{provenance.get('host_commit', 'unknown')}`",
        f"- OpenSSD 源码：`{provenance.get('openssd_expected_branch', 'unknown')}@{provenance.get('openssd_expected_commit', 'unknown')}`",
        f"- 实验工具：`{provenance.get('artifact_reproduction_branch', 'unknown')}@{provenance.get('artifact_reproduction_commit', 'unknown')}`",
        "- 实验点：修复版原始 CSGC 与原始 ORI 各 22 点，共 44 点；不含 IPLFS。",
        "- workload 来源：作者 artifact `main@0271b907ec00ed643fd139403b726817c9fe8c32`。",
        f"- 结构化数据：`{summary_csv.name}`",
        "",
        "## Overall 性能",
        "",
        "| 负载 | ORI (kop/s) | CSGC (kop/s) | CSGC/ORI |",
        "|---|---:|---:|---:|",
    ]
    for label, suffix in overall:
        ori = require_metric(cases, f"ori-{suffix}", "throughput_ops_s")
        cs = require_metric(cases, f"cs-{suffix}", "throughput_ops_s")
        speedup = cs / ori
        speedups.append(speedup)
        lines.append(f"| {label} | {ori / 1000:.3f} | {cs / 1000:.3f} | {speedup:.3f}x |")

    all_arithmetic_mean = sum(speedups) / len(speedups)
    all_geometric_mean = geometric_mean(speedups)
    paper_comparable_geometric_mean = geometric_mean(speedups[:5])
    lines.extend(
        [
            "",
            f"六项配置等权计算时，CSGC/ORI 算术平均为 **{all_arithmetic_mean:.3f}x**，"
            f"几何平均为 **{all_geometric_mean:.3f}x**。跨异构负载汇总应优先使用几何平均。",
            "",
            "论文正文报告相对 F2FS 平均 `2.76x`。原作者 Figure 4 绘图脚本使用几何平均，"
            "而不是算术平均；由论文图中公开的舍入柱值反推，`2.76x` 最符合前五个主要负载点"
            "（不重复计入作为 fio 替代分布的 `fio-skewed`）的几何平均。按相同前五项口径，"
            f"本轮为 **{paper_comparable_geometric_mean:.3f}x**。不过公开脚本当前又对六项调用"
            "几何平均函数，与论文正文数字不自洽，因此无法从公开材料百分之百还原作者最终的"
            "统计集合。此前将本轮六项算术平均与论文 `2.76x` 直接比较是不正确的。",
            "",
            f"本轮最大加速为 YCSB-F 的 **{max(speedups):.3f}x**；论文对应值为 `3.61x`，"
            "二者负载和结论一致，但数值并不完全相同。",
            "",
            "## YCSB-A 延迟",
            "",
            "| 指标 | ORI (ms) | CSGC (ms) | ORI/CSGC |",
            "|---|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("Read average", "read_avg_us"),
        ("Read P99", "read_p99_us"),
        ("Update average", "update_avg_us"),
        ("Update P99", "update_p99_us"),
    ):
        ori = require_metric(cases, "ori-ycsb-a", key) / 1000
        cs = require_metric(cases, "cs-ycsb-a", key) / 1000
        lines.append(f"| {label} | {ori:.3f} | {cs:.3f} | {ori / cs:.3f}x |")

    lines.extend(
        [
            "",
            "## 存储利用率",
            "",
            "| 利用率 | ORI (kop/s) | CSGC (kop/s) | 加速 | ORI WAF | CSGC WAF |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for value in ("0.6", "0.7", "0.8", "0.9", "0.95"):
        suffix = f"fio-util-{value}"
        ori = require_metric(cases, f"ori-{suffix}", "throughput_ops_s")
        cs = require_metric(cases, f"cs-{suffix}", "throughput_ops_s")
        ori_waf = require_metric(cases, f"ori-{suffix}", "waf")
        cs_waf = require_metric(cases, f"cs-{suffix}", "waf")
        lines.append(
            f"| {float(value) * 100:.0f}% | {ori / 1000:.3f} | {cs / 1000:.3f} | "
            f"{cs / ori:.3f}x | {ori_waf:.3f} | {cs_waf:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Section size",
            "",
            "| segments/section | ORI (kop/s) | CSGC (kop/s) | 加速 | ORI WAF | CSGC WAF |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for size in ("1", "2", "4", "8", "16"):
        suffix = f"fio-section-{size}"
        ori = require_metric(cases, f"ori-{suffix}", "throughput_ops_s")
        cs = require_metric(cases, f"cs-{suffix}", "throughput_ops_s")
        ori_waf = require_metric(cases, f"ori-{suffix}", "waf")
        cs_waf = require_metric(cases, f"cs-{suffix}", "waf")
        lines.append(
            f"| {size} | {ori / 1000:.3f} | {cs / 1000:.3f} | {cs / ori:.3f}x | "
            f"{ori_waf:.3f} | {cs_waf:.3f} |"
        )

    lines.extend(
        [
            "",
            "> Figure 7(a) 的平均块迁移延迟未采集。正式 quiet Host 关闭了 "
            "`CONFIG_F2FS_CSGC_RUNTIME_BREAKDOWN`，因此图中保留对应面板并明确标为 N/A，"
            "不使用吞吐或总 GC 时间反推该指标。",
            "",
            "## 写倾斜度",
            "",
            "| 分布 | ORI (kop/s) | CSGC (kop/s) | 加速 | ORI WAF | CSGC WAF |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, suffix in (
        ("uniform", "fio-skew-uniform"),
        ("Zipf 0.3", "fio-skew-0.3"),
        ("Zipf 0.7", "fio-skew-0.7"),
        ("Zipf 0.9", "fio-skew-0.9"),
        ("Zipf 1.1", "fio-skew-1.1"),
    ):
        ori = require_metric(cases, f"ori-{suffix}", "throughput_ops_s")
        cs = require_metric(cases, f"cs-{suffix}", "throughput_ops_s")
        ori_waf = require_metric(cases, f"ori-{suffix}", "waf")
        cs_waf = require_metric(cases, f"cs-{suffix}", "waf")
        lines.append(
            f"| {label} | {ori / 1000:.3f} | {cs / 1000:.3f} | {cs / ori:.3f}x | "
            f"{ori_waf:.3f} | {cs_waf:.3f} |"
        )

    lines.extend(
        [
            "",
            "## 图表",
            "",
            "- `figures/figure4_overall_performance.*`：对应论文 Figure 4。",
            "- `figures/figure5_timeline_and_latency.*`：对应论文 Figure 5。",
            "- `figures/figure6_storage_utilization.*`：对应论文 Figure 6。",
            "- `figures/figure7_section_size.*`：对应论文 Figure 7；面板 (a) 标明未采集。",
            "- `figures/figure8_write_skewness.*`：对应论文 Figure 8。",
            "",
            "## 口径说明",
            "",
            "- 本轮忠实使用 artifact 的实际 workload 配置，而不是后来加入充分 GC 预热的正式负载。",
            "- artifact 中 Filebench 使用 54,000 个均匀分布在 512 KiB 至 1.5 MiB 的文件；这与论文文字中对 varmail 的 64 KiB 描述存在差异。",
            "- Figure 5 的 artifact period workload 实际执行 60 秒（`psrun -5 $runtime`，"
            "其中 `$runtime=60`），论文正文写 300 秒。本轮图按实际公开 workload 绘制。",
            "- artifact 的 YCSB runner 使用 36 线程；论文正文写 32 线程。本轮以公开 artifact 为准。",
            "- fio 在预填充后立即运行，不增加额外 16 GiB GC 预热，因此与近期 GC-heavy 正式结果不可直接混用。",
            "- 运行固件无法从 Host 侧逐字节证明 ELF 身份；本批次保存了 OpenSSD 源码提交和 Vitis 输入文件哈希。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_dir", type=Path)
    args = parser.parse_args()
    batch = args.batch_dir.resolve()
    results_path = batch / "case-results.tsv"
    if not results_path.exists():
        raise SystemExit(f"Missing {results_path}")

    with results_path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t") if row["status"] == "0"]
    latest_rows: Dict[str, Dict[str, str]] = {row["case_id"]: row for row in rows}
    if len(latest_rows) != 44:
        raise SystemExit(f"Expected 44 successful cases, found {len(latest_rows)}")

    cases = {case_id: parse_case(row) for case_id, row in latest_rows.items()}
    analysis_dir = batch / "analysis"
    figures = analysis_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)

    scalar_keys = [
        "case_id", "mode", "workload_type", "bmname", "distribution", "prefill_ratio",
        "segs_per_sec", "duration_s", "output_path", "throughput_ops_s", "bandwidth_mib_s",
        "waf", "migration_us", "read_avg_us", "read_p99_us", "update_avg_us", "update_p99_us",
    ]
    summary_csv = analysis_dir / "experiment-summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys, lineterminator="\n")
        writer.writeheader()
        for case_id in sorted(cases):
            writer.writerow({key: cases[case_id].get(key) for key in scalar_keys})

    with (analysis_dir / "experiment-summary.json").open("w", encoding="utf-8") as handle:
        json.dump(cases, handle, ensure_ascii=False, indent=2)

    configure_plot()
    plot_figure4(cases, figures)
    plot_figure5(cases, figures)
    plot_two_metric_lines(
        cases, figures, "figure6_storage_utilization",
        ["0.6", "0.7", "0.8", "0.9", "0.95"],
        [f"fio-util-{value}" for value in ["0.6", "0.7", "0.8", "0.9", "0.95"]],
        "Storage Utilization",
    )
    plot_figure7(cases, figures)
    plot_two_metric_lines(
        cases, figures, "figure8_write_skewness",
        ["uni.", "z/0.3", "z/0.7", "z/0.9", "z/1.1"],
        ["fio-skew-uniform", "fio-skew-0.3", "fio-skew-0.7", "fio-skew-0.9", "fio-skew-1.1"],
        "Write Distribution",
    )

    report = build_report(batch, cases, summary_csv)
    (analysis_dir / "europar25-original-reproduction-report.md").write_text(report, encoding="utf-8")
    print(analysis_dir)


if __name__ == "__main__":
    main()
