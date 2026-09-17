#!/usr/bin/env python3
"""Analyze low-overhead NOWAIT WAF runs for the Euro-Par fio sweeps."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analyze_europar25_original_matrix import parse_case


SYSTEM_STYLE = {
    "ori": ("F2FS", "#5E96E6", "^"),
    "original-csgc": ("CSGC", "#E67365", "o"),
    "nowait": ("mCSGC NOWAIT", "#47A66B", "s"),
}
SWEEPS = (
    (
        "figure6_storage_utilization_waf",
        "Storage utilization",
        ("60%", "70%", "80%", "90%", "95%"),
        tuple(f"fio-util-{value}" for value in ("0.6", "0.7", "0.8", "0.9", "0.95")),
    ),
    (
        "figure7_section_size_waf",
        "Section size (#segments)",
        ("1", "2", "4", "8", "16"),
        tuple(f"fio-section-{value}" for value in ("1", "2", "4", "8", "16")),
    ),
    (
        "figure8_write_skewness_waf",
        "Write distribution",
        ("uniform", "z/0.3", "z/0.7", "z/0.9", "z/1.1"),
        ("fio-skew-uniform", "fio-skew-0.3", "fio-skew-0.7", "fio-skew-0.9", "fio-skew-1.1"),
    ),
)
EXPECTED_SUFFIXES = {suffix for _, _, _, suffixes in SWEEPS for suffix in suffixes}
CASE_PATTERN = re.compile(r"^nowait-paper-waf-(.+)-r([1-9][0-9]*)$")


# Return the final integer value associated with one SSD log field.
def last_integer(text: str, pattern: str) -> int:
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if not matches:
        raise ValueError(f"Missing SSD statistic matching {pattern!r}")
    return int(matches[-1])


# Parse and cross-check the low-overhead WAF fields in one SSD statistics log.
def parse_waf_log(path: Path) -> Dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    enabled = last_integer(text, r"paper_waf_stats:\s*enabled=(\d+)")
    host_write = last_integer(text, r"host_normal_write_bytes:\s*(\d+)")
    nand_write = last_integer(text, r"(?m)^.*nand_write_bytes:\s*(\d+)")
    csgc_write = last_integer(text, r"nand_cs_write_bytes:\s*(\d+)")
    reported = last_integer(text, r"physical WAF:\s*(\d+)")
    computed = nand_write * 1000 // (1 + host_write)
    if enabled != 1:
        raise ValueError(f"Paper WAF statistics are not enabled in {path}")
    if host_write <= 0 or nand_write < host_write:
        raise ValueError(f"Invalid Host/NAND write counters in {path}")
    if csgc_write <= 0 or csgc_write > nand_write:
        raise ValueError(f"Invalid CSGC write counter in {path}")
    if reported != computed:
        raise ValueError(
            f"WAF mismatch in {path}: reported={reported}, computed={computed}"
        )
    return {
        "host_normal_write_bytes": host_write,
        "nand_write_bytes": nand_write,
        "nand_cs_write_bytes": csgc_write,
        "reported_waf_x1000": reported,
        "computed_waf_x1000": computed,
    }


# Read the latest successful result row for every case in a batch.
def read_success_rows(batch: Path) -> Dict[str, Dict[str, str]]:
    path = batch / "case-results.tsv"
    if not path.exists():
        raise ValueError(f"Missing {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle, delimiter="\t") if row["status"] == "0"]
    latest = {row["case_id"]: row for row in rows}
    if not latest:
        raise ValueError(f"No successful cases in {path}")
    return latest


# Parse the paired one-run ORI and original-CSGC baseline WAF values.
def read_baseline(batch: Path) -> Dict[str, Dict[str, Mapping[str, object]]]:
    systems: Dict[str, Dict[str, Mapping[str, object]]] = {
        "ori": {},
        "original-csgc": {},
    }
    for case_id, row in read_success_rows(batch).items():
        if case_id.startswith("ori-"):
            systems["ori"][case_id[4:]] = parse_case(row)
        elif case_id.startswith("cs-"):
            systems["original-csgc"][case_id[3:]] = parse_case(row)
    for system in systems:
        missing = EXPECTED_SUFFIXES - systems[system].keys()
        if missing:
            raise ValueError(f"Baseline {system} is missing cases: {sorted(missing)}")
        for suffix in EXPECTED_SUFFIXES:
            if systems[system][suffix].get("waf") is None:
                raise ValueError(f"Baseline {system}/{suffix} has no physical WAF")
    return systems


# Parse all candidate runs and require a complete, balanced repetition set.
def read_candidate(batch: Path) -> Tuple[Dict[str, List[Dict[str, object]]], int]:
    grouped: Dict[str, List[Tuple[int, Dict[str, object]]]] = {}
    for case_id, row in read_success_rows(batch).items():
        match = CASE_PATTERN.match(case_id)
        if not match:
            raise ValueError(f"Unexpected candidate case ID: {case_id}")
        suffix, repetition_text = match.groups()
        if suffix not in EXPECTED_SUFFIXES:
            raise ValueError(f"Unexpected WAF case suffix: {suffix}")
        output = Path(row["output_path"])
        metrics = dict(parse_case(row))
        counters = parse_waf_log(output / "stat.log")
        metrics.update(counters)
        metrics["case_id"] = case_id
        metrics["output_path"] = str(output)
        grouped.setdefault(suffix, []).append((int(repetition_text), metrics))
    if set(grouped) != EXPECTED_SUFFIXES:
        raise ValueError(f"Candidate suffix mismatch: {sorted(set(grouped) ^ EXPECTED_SUFFIXES)}")
    repetition_sets = [{number for number, _ in values} for values in grouped.values()]
    expected_repetitions = repetition_sets[0]
    if not expected_repetitions or expected_repetitions != set(
        range(1, max(expected_repetitions) + 1)
    ):
        raise ValueError(f"Non-contiguous repetitions: {sorted(expected_repetitions)}")
    if any(numbers != expected_repetitions for numbers in repetition_sets):
        raise ValueError("Candidate cases have unbalanced repetition sets")
    ordered = {
        suffix: [metrics for _, metrics in sorted(values)]
        for suffix, values in grouped.items()
    }
    return ordered, len(expected_repetitions)


# Return one required numeric metric.
def metric(record: Mapping[str, object], key: str) -> float:
    value = record.get(key)
    if value is None:
        raise ValueError(f"Missing metric {key}")
    return float(value)


# Build per-point aggregate rows for CSV, JSON, plots, and the report.
def build_summary(
    baseline: Mapping[str, Mapping[str, Mapping[str, object]]],
    candidate: Mapping[str, Sequence[Mapping[str, object]]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for figure, _, labels, suffixes in SWEEPS:
        for label, suffix in zip(labels, suffixes):
            runs = candidate[suffix]
            waf_values = [metric(run, "waf") for run in runs]
            throughput_values = [metric(run, "throughput_ops_s") for run in runs]
            bandwidth_values = [metric(run, "bandwidth_mib_s") for run in runs]
            csgc_shares = [
                100.0
                * metric(run, "nand_cs_write_bytes")
                / metric(run, "nand_write_bytes")
                for run in runs
            ]
            ori_waf = metric(baseline["ori"][suffix], "waf")
            csgc_waf = metric(baseline["original-csgc"][suffix], "waf")
            mean_waf = statistics.fmean(waf_values)
            rows.append(
                {
                    "figure": figure,
                    "point_label": label,
                    "suffix": suffix,
                    "sample_count": len(runs),
                    "ori_waf": ori_waf,
                    "original_csgc_waf": csgc_waf,
                    "nowait_waf_mean": mean_waf,
                    "nowait_waf_stdev": statistics.stdev(waf_values) if len(waf_values) > 1 else 0.0,
                    "nowait_waf_min": min(waf_values),
                    "nowait_waf_max": max(waf_values),
                    "nowait_over_original_csgc": mean_waf / csgc_waf,
                    "nowait_throughput_ops_s_mean": statistics.fmean(
                        throughput_values
                    ),
                    "nowait_throughput_ops_s_stdev": statistics.stdev(
                        throughput_values
                    )
                    if len(throughput_values) > 1
                    else 0.0,
                    "nowait_throughput_ops_s_min": min(throughput_values),
                    "nowait_throughput_ops_s_max": max(throughput_values),
                    "nowait_bandwidth_mib_s_mean": statistics.fmean(bandwidth_values),
                    "csgc_fraction_of_nand_percent_mean": statistics.fmean(csgc_shares),
                }
            )
    return rows


# Write one CSV row for every physical experiment run.
def write_runs_csv(
    candidate: Mapping[str, Sequence[Mapping[str, object]]], path: Path
) -> None:
    fields = (
        "case_id",
        "suffix",
        "output_path",
        "throughput_ops_s",
        "bandwidth_mib_s",
        "host_normal_write_bytes",
        "nand_write_bytes",
        "nand_cs_write_bytes",
        "reported_waf_x1000",
        "computed_waf_x1000",
        "waf",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for suffix in sorted(candidate):
            for run in candidate[suffix]:
                writer.writerow({key: suffix if key == "suffix" else run.get(key) for key in fields})


# Write the compact aggregate CSV used by the final paper figure generator.
def write_summary_csv(rows: Sequence[Mapping[str, object]], path: Path) -> None:
    if not rows:
        raise ValueError("Cannot write an empty WAF summary")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Draw one WAF-only three-system panel in the Euro-Par color and marker style.
def plot_sweep(
    rows: Sequence[Mapping[str, object]],
    figure: str,
    x_label: str,
    output_dir: Path,
) -> None:
    selected = [row for row in rows if row["figure"] == figure]
    labels = [str(row["point_label"]) for row in selected]
    fig, axis = plt.subplots(figsize=(3.3, 2.8))
    for system, key in (
        ("ori", "ori_waf"),
        ("original-csgc", "original_csgc_waf"),
        ("nowait", "nowait_waf_mean"),
    ):
        name, color, marker = SYSTEM_STYLE[system]
        values = [float(row[key]) for row in selected]
        if system == "nowait":
            errors = [float(row["nowait_waf_stdev"]) for row in selected]
            axis.errorbar(
                labels,
                values,
                yerr=errors,
                label=name,
                color=color,
                marker=marker,
                markeredgecolor="black",
                capsize=2,
            )
        else:
            axis.plot(
                labels,
                values,
                label=name,
                color=color,
                marker=marker,
                markeredgecolor="black",
            )
    axis.set_xlabel(x_label)
    axis.set_ylabel("Write Amplification")
    axis.grid(alpha=0.3)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output_dir / f"{figure}.pdf", bbox_inches="tight")
    fig.savefig(output_dir / f"{figure}.png", bbox_inches="tight")
    plt.close(fig)


# Build a concise measurement report without treating diagnostic throughput as final.
def build_report(
    batch: Path,
    baseline_batch: Path,
    repetitions: int,
    rows: Sequence[Mapping[str, object]],
) -> str:
    lines = [
        "# mCSGC NOWAIT Paper WAF Results",
        "",
        f"- Candidate batch: `{batch}`",
        f"- Paired ORI/CSGC baseline: `{baseline_batch}`",
        f"- Repetitions per point: `{repetitions}`",
        "- Scope: the 15 fio points used by Euro-Par Figures 6 through 8.",
        "- Metric: physical WAF = NAND write bytes / Host normal write bytes.",
        "- The diagnostic-build throughput is a sanity check; final throughput must remain from the quiet build.",
        "",
        "| Sweep | Point | F2FS WAF | Original CSGC WAF | NOWAIT WAF | Stddev | NOWAIT / CSGC |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['figure']} | {row['point_label']} | {float(row['ori_waf']):.3f} "
            f"| {float(row['original_csgc_waf']):.3f} | {float(row['nowait_waf_mean']):.3f} "
            f"| {float(row['nowait_waf_stdev']):.3f} | {float(row['nowait_over_original_csgc']):.3f}x |"
        )
    lines.extend(
        [
            "",
            "Every candidate point passed three independent checks: the firmware feature marker was enabled, "
            "the three byte counters satisfied their containment relationships, and the reported integer WAF "
            "matched a recomputation from raw bytes.",
            "",
        ]
    )
    return "\n".join(lines)


# Exercise the low-level log parser with synthetic counter data.
def self_test() -> None:
    content = """paper_waf_stats: enabled=1
host_normal_write_bytes: 1000
nand_cs_write_bytes: 500
nand_write_bytes: 1500
physical WAF: 1498
"""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "stat.log"
        path.write_text(content, encoding="utf-8")
        parsed = parse_waf_log(path)
    assert parsed["host_normal_write_bytes"] == 1000
    assert parsed["nand_write_bytes"] == 1500
    assert parsed["nand_cs_write_bytes"] == 500
    assert parsed["computed_waf_x1000"] == 1498
    match = CASE_PATTERN.match("nowait-paper-waf-fio-section-8-r3")
    assert match is not None and match.groups() == ("fio-section-8", "3")
    baseline = {
        system: {
            suffix: {"waf": 2.0 if system == "ori" else 1.5}
            for suffix in EXPECTED_SUFFIXES
        }
        for system in ("ori", "original-csgc")
    }
    candidate = {
        suffix: [
            {
                "waf": 1.2 + repetition / 100,
                "throughput_ops_s": 6400.0 + repetition,
                "bandwidth_mib_s": 400.0 + repetition,
                "nand_cs_write_bytes": 250,
                "nand_write_bytes": 1250,
            }
            for repetition in range(3)
        ]
        for suffix in EXPECTED_SUFFIXES
    }
    rows = build_summary(baseline, candidate)
    assert len(rows) == 15
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        write_summary_csv(rows, output / "summary.csv")
        plot_sweep(rows, SWEEPS[0][0], SWEEPS[0][1], output)
        assert (output / "summary.csv").is_file()
        assert (output / f"{SWEEPS[0][0]}.pdf").is_file()
    print("paper WAF analyzer self-test passed")


# Parse arguments and generate all WAF artifacts for one completed batch.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_batch", nargs="?", type=Path)
    parser.add_argument("--baseline-batch", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.candidate_batch is None or args.baseline_batch is None:
        parser.error("candidate_batch and --baseline-batch are required")

    batch = args.candidate_batch.resolve()
    baseline_batch = args.baseline_batch.resolve()
    baseline = read_baseline(baseline_batch)
    candidate, repetitions = read_candidate(batch)
    rows = build_summary(baseline, candidate)
    analysis = batch / "analysis-paper-waf"
    figures = analysis / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    write_runs_csv(candidate, analysis / "waf-runs.csv")
    write_summary_csv(rows, analysis / "waf-summary.csv")
    payload = {
        "candidate_batch": str(batch),
        "baseline_batch": str(baseline_batch),
        "repetitions": repetitions,
        "summary": rows,
        "candidate_runs": candidate,
    }
    (analysis / "paper-waf-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "axes.labelsize": 10,
            "xtick.labelsize": 8,
            "ytick.labelsize": 9,
            "legend.fontsize": 8,
        }
    )
    for figure, x_label, _, _ in SWEEPS:
        plot_sweep(rows, figure, x_label, figures)
    (analysis / "paper-waf-summary.md").write_text(
        build_report(batch, baseline_batch, repetitions, rows), encoding="utf-8"
    )
    print(analysis)


if __name__ == "__main__":
    main()
