#!/usr/bin/env python3

"""Compare off, moderate, and aggressive proactive CSGC profiles."""

import argparse
import re
from pathlib import Path
from typing import Dict


STAT_RE = re.compile(
    r"^(?P<name>[^:]+): count=(?P<count>\d+) "
    r"mean=(?P<mean>-?[0-9.]+) median=(?P<median>-?[0-9.]+) "
    r"p95=(?P<p95>-?[0-9.]+) p99=(?P<p99>-?[0-9.]+) "
    r"min=(?P<min>-?[0-9.]+) max=(?P<max>-?[0-9.]+) "
    r"sum=(?P<sum>-?[0-9.]+)$"
)
FIO_BW_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"bw_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
FIO_IO_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"io_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
DEVICE_PATTERNS = {
    "logical_waf_cs": re.compile(r"logical WAF\(CS\):\s*(\d+)"),
    "physical_waf": re.compile(r"physical WAF:\s*(\d+)"),
    "csgc_requests": re.compile(r"csgc_mp:\s+req=(\d+)"),
    "csgc_completed_moves": re.compile(r"csgc_mp:.*?done=(\d+)"),
}
REFERENCE_BW = {
    "bigfile": {"recommended": 423.723, "original_csgc": 213.712},
    "smallfile": {"recommended": 426.853, "original_csgc": 276.110},
}


def parse_args() -> argparse.Namespace:
    """Parse six result directories and one report path."""
    parser = argparse.ArgumentParser(description=__doc__)
    for workload in ("big", "small"):
        for profile in ("off", "moderate", "aggressive"):
            parser.add_argument(
                f"--{profile}-{workload}", type=Path, required=True
            )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_fio(run_dir: Path) -> Dict[str, float]:
    """Return application-visible write bandwidth and completed bytes."""
    text = (run_dir / "fio.log").read_text(errors="replace")
    bw_matches = FIO_BW_BYTES_RE.findall(text)
    io_matches = FIO_IO_BYTES_RE.findall(text)
    if bw_matches:
        bandwidth = float(bw_matches[-1]) / (1024.0 * 1024.0)
    else:
        text_matches = FIO_BW_RE.findall(text)
        if not text_matches:
            raise ValueError(f"no fio bandwidth in {run_dir / 'fio.log'}")
        value_text, unit = text_matches[-1]
        bandwidth = float(value_text)
        if unit == "KiB/s":
            bandwidth /= 1024.0
        elif unit == "GiB/s":
            bandwidth *= 1024.0
    return {
        "bandwidth": bandwidth,
        "io_bytes": float(io_matches[-1]) if io_matches else 0.0,
    }


def parse_run(run_dir: Path) -> Dict[str, object]:
    """Load fio, Host diagnostics, and optional device counters."""
    scalars: Dict[str, float] = {}
    stats: Dict[str, Dict[str, float]] = {}
    summary = run_dir / "gc-breakdown-diagnostic-result.txt"
    for line in summary.read_text(errors="replace").splitlines():
        match = STAT_RE.match(line)
        if match:
            fields = match.groupdict()
            name = fields.pop("name")
            stats[name] = {key: float(value) for key, value in fields.items()}
            continue
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        try:
            scalars[name] = float(value)
        except ValueError:
            pass

    device: Dict[str, float] = {}
    device_path = run_dir / "ssd-workload-stat.log"
    if device_path.is_file():
        text = device_path.read_bytes().replace(b"\0", b"").decode(errors="replace")
        for name, pattern in DEVICE_PATTERNS.items():
            match = pattern.search(text)
            if match:
                device[name] = float(match.group(1))
        for name in ("logical_waf_cs", "physical_waf"):
            if name in device:
                device[name] /= 1000.0

    fio = parse_fio(run_dir)
    return {
        "run_dir": run_dir,
        "bandwidth": fio["bandwidth"],
        "io_bytes": fio["io_bytes"],
        "scalars": scalars,
        "stats": stats,
        "device": device,
    }


def scalar(run: Dict[str, object], name: str) -> float:
    """Return one optional scalar."""
    return float(run["scalars"].get(name, 0.0))


def stat(run: Dict[str, object], name: str, field: str = "sum") -> float:
    """Return one optional distribution field."""
    return float(run["stats"].get(name, {}).get(field, 0.0))


def device(run: Dict[str, object], name: str) -> float:
    """Return one optional device counter."""
    return float(run["device"].get(name, 0.0))


def delta(reference: float, value: float) -> float:
    """Return percentage change relative to a nonzero reference."""
    return (value - reference) * 100.0 / reference if reference else 0.0


def ratio(reference: float, value: float) -> float:
    """Return a dimensionless speedup ratio."""
    return value / reference if reference else 0.0


def metrics(run: Dict[str, object]) -> Dict[str, float]:
    """Build end-to-end, supply, work, and producer metrics."""
    total_sections = scalar(run, "timeline_sections")
    total_moves = stat(run, "modern_section_critical_total_moves")
    proactive_sections = stat(run, "proactive_sections")
    proactive_moves = stat(run, "proactive_migrated_blocks")
    io_bytes = float(run["io_bytes"])
    return {
        "fio_bw_mib_s": float(run["bandwidth"]),
        "measured_duration_s": scalar(run, "measured_duration_us") / 1_000_000.0,
        "supply_coverage_pct": stat(run, "host_supply_coverage_permille", "mean") / 10.0,
        "depth_ge_two_coverage_pct": stat(
            run, "host_supply_depth_ge_two_coverage_permille", "mean"
        ) / 10.0,
        "peak_outstanding": stat(run, "host_supply_peak_outstanding", "mean"),
        "internal_gap_count": scalar(run, "global_supply_internal_gaps"),
        "internal_gap_total_s": stat(run, "host_supply_internal_gap_us") / 1_000_000.0,
        "between_gc_gap_total_s": stat(run, "host_supply_between_gc_gap_us") / 1_000_000.0,
        "between_gc_gap_median_ms": stat(
            run, "host_supply_between_gc_gap_us", "median"
        ) / 1000.0,
        "between_gc_gap_p95_ms": stat(
            run, "host_supply_between_gc_gap_us", "p95"
        ) / 1000.0,
        "f2fs_gc_calls": stat(run, "gc_call_duration_us", "count"),
        "proactive_gc_calls": stat(run, "gc_call_proactive"),
        "proactive_origc_collectors": stat(
            run, "proactive_gc_call_origc_collectors"
        ),
        "checkpoint_calls": stat(run, "gc_call_checkpoint_calls"),
        "unsafe_reclaim_calls": stat(run, "gc_call_unsafe_reclaim_calls"),
        "csgc_sections": total_sections,
        "proactive_sections": proactive_sections,
        "foreground_sections": max(0.0, total_sections - proactive_sections),
        "migrated_blocks": total_moves,
        "proactive_migrated_blocks": proactive_moves,
        "foreground_migrated_blocks": max(0.0, total_moves - proactive_moves),
        "host_migration_waf": total_moves * 4096.0 / io_bytes if io_bytes else 0.0,
        "producer_wakeups": stat(run, "proactive_wakeups"),
        "producer_triggers": stat(run, "proactive_triggers"),
        "producer_errors": stat(run, "proactive_errors"),
        "producer_lock_busy": stat(run, "proactive_lock_busy"),
        "producer_dirty_source_skips": stat(
            run, "proactive_dirty_source_skips"
        ),
        "producer_source_pre_checks": stat(run, "proactive_source_pre_checks"),
        "producer_source_pre_rejects": stat(run, "proactive_source_pre_rejects"),
        "producer_source_post_checks": stat(run, "proactive_source_post_checks"),
        "producer_source_post_rejects": stat(run, "proactive_source_post_rejects"),
        "producer_active_s": stat(run, "proactive_active_us") / 1_000_000.0,
        "producer_idle_s": stat(run, "proactive_idle_us") / 1_000_000.0,
        "producer_enabled_at_stop": stat(run, "proactive_enabled"),
        "producer_running_at_stop": stat(run, "proactive_running"),
        "free_sections_first": stat(run, "proactive_free_first"),
        "free_sections_last": stat(run, "proactive_free_last"),
        "free_sections_min": stat(run, "proactive_free_min"),
        "free_sections_max": stat(run, "proactive_free_max"),
        "logical_waf_cs": device(run, "logical_waf_cs"),
        "physical_waf": device(run, "physical_waf"),
        "device_csgc_requests": device(run, "csgc_requests"),
        "device_completed_moves": device(run, "csgc_completed_moves"),
    }


def emit_workload(label: str, runs: Dict[str, Dict[str, object]]) -> str:
    """Emit one workload table and acceptance checks."""
    values = {profile: metrics(run) for profile, run in runs.items()}
    lines = [
        f"=== {label} ===",
        *(f"{profile}_dir={runs[profile]['run_dir']}" for profile in runs),
        "metric\toff\tmoderate\taggressive\tmoderate_vs_off_pct\taggressive_vs_off_pct",
    ]
    for name in values["off"]:
        off_value = values["off"][name]
        moderate_value = values["moderate"][name]
        aggressive_value = values["aggressive"][name]
        lines.append(
            f"{name}\t{off_value:.6f}\t{moderate_value:.6f}\t{aggressive_value:.6f}\t"
            f"{delta(off_value, moderate_value):.3f}\t"
            f"{delta(off_value, aggressive_value):.3f}"
        )

    best_profile = max(runs, key=lambda item: float(runs[item]["bandwidth"]))
    best_bw = float(runs[best_profile]["bandwidth"])
    reference = REFERENCE_BW[label]
    off = values["off"]
    lines.extend(
        (
            f"best_profile={best_profile}",
            f"best_fio_bw_mib_s={best_bw:.6f}",
            f"best_vs_recommended={ratio(reference['recommended'], best_bw):.6f}",
            f"best_vs_original_csgc={ratio(reference['original_csgc'], best_bw):.6f}",
        )
    )
    for profile in ("moderate", "aggressive"):
        current = values[profile]
        coverage_gain = current["supply_coverage_pct"] - off["supply_coverage_pct"]
        gap_reduction = -delta(
            off["between_gc_gap_total_s"], current["between_gc_gap_total_s"]
        )
        bandwidth_gain = delta(off["fio_bw_mib_s"], current["fio_bw_mib_s"])
        lines.extend(
            (
                f"{profile}_coverage_gain_pp={coverage_gain:.3f}",
                f"{profile}_between_gc_gap_reduction_pct={gap_reduction:.3f}",
                f"{profile}_fio_gain_pct={bandwidth_gain:.3f}",
                f"{profile}_supply_acceptance={int(coverage_gain >= 20.0 or gap_reduction >= 25.0)}",
                f"{profile}_fio_acceptance={int(bandwidth_gain > 5.0)}",
                f"{profile}_producer_origc_zero={int(current['proactive_origc_collectors'] == 0.0)}",
                f"{profile}_producer_drained={int(current['producer_enabled_at_stop'] == 0.0 and current['producer_running_at_stop'] == 0.0)}",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    """Write the two-workload proactive profile comparison."""
    args = parse_args()
    workloads = {
        "bigfile": {
            "off": parse_run(args.off_big),
            "moderate": parse_run(args.moderate_big),
            "aggressive": parse_run(args.aggressive_big),
        },
        "smallfile": {
            "off": parse_run(args.off_small),
            "moderate": parse_run(args.moderate_small),
            "aggressive": parse_run(args.aggressive_small),
        },
    }
    report = "\n".join(
        (
            "Proactive CSGC supply matrix",
            "",
            emit_workload("bigfile", workloads["bigfile"]),
            emit_workload("smallfile", workloads["smallfile"]),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
