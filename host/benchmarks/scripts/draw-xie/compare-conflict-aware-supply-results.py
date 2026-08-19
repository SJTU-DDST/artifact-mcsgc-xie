#!/usr/bin/env python3

"""Compare conflict-aware pair scheduling with rolling CSGC supply."""

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
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
DEVICE_PATTERNS = {
    "logical_waf_cs": re.compile(r"logical WAF\(CS\):\s*(\d+)"),
    "physical_waf": re.compile(r"physical WAF:\s*(\d+)"),
    "csgc_requests": re.compile(r"csgc_mp:\s+req=(\d+)"),
    "csgc_completed_moves": re.compile(r"csgc_mp:.*?done=(\d+)"),
}


def parse_args() -> argparse.Namespace:
    """Parse four result directories and one output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    for workload in ("big", "small"):
        parser.add_argument(f"--control-{workload}", type=Path, required=True)
        parser.add_argument(f"--rolling-{workload}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_bandwidth(run_dir: Path) -> float:
    """Return application-visible fio write bandwidth in MiB/s."""
    text = (run_dir / "fio.log").read_text(errors="replace")
    matches = FIO_BW_BYTES_RE.findall(text)
    if matches:
        return float(matches[-1]) / (1024.0 * 1024.0)
    text_matches = FIO_BW_RE.findall(text)
    if not text_matches:
        raise ValueError(f"no fio bandwidth in {run_dir / 'fio.log'}")
    value_text, unit = text_matches[-1]
    value = float(value_text)
    if unit == "KiB/s":
        return value / 1024.0
    if unit == "GiB/s":
        return value * 1024.0
    return value


def parse_run(run_dir: Path) -> Dict[str, object]:
    """Load one fio result, analyzer summary, and optional device counters."""
    summary_path = run_dir / "gc-breakdown-diagnostic-result.txt"
    scalars: Dict[str, float] = {}
    stats: Dict[str, Dict[str, float]] = {}
    for line in summary_path.read_text(errors="replace").splitlines():
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

    return {
        "run_dir": run_dir,
        "bandwidth": parse_bandwidth(run_dir),
        "scalars": scalars,
        "stats": stats,
        "device": device,
    }


def scalar(run: Dict[str, object], name: str) -> float:
    """Return one optional analyzer scalar."""
    return float(run["scalars"].get(name, 0.0))


def stat(run: Dict[str, object], name: str, field: str = "sum") -> float:
    """Return one optional distribution field."""
    return float(run["stats"].get(name, {}).get(field, 0.0))


def device(run: Dict[str, object], name: str) -> float:
    """Return one optional device counter."""
    return float(run["device"].get(name, 0.0))


def delta(reference: float, value: float) -> float:
    """Return the percentage change from reference to value."""
    return (value - reference) * 100.0 / reference if reference else 0.0


def metrics(run: Dict[str, object]) -> Dict[str, float]:
    """Build the scheduler and end-to-end metrics used by the report."""
    return {
        "fio_bw_mib_s": float(run["bandwidth"]),
        "measured_duration_s": scalar(run, "measured_duration_us") / 1_000_000.0,
        "supply_coverage_pct": stat(
            run, "host_supply_coverage_permille", "mean"
        ) / 10.0,
        "internal_gap_count": scalar(run, "global_supply_internal_gaps"),
        "internal_gap_total_s": stat(
            run, "host_supply_internal_gap_us"
        ) / 1_000_000.0,
        "same_gc_gap_total_s": stat(
            run, "host_supply_same_gc_gap_us"
        ) / 1_000_000.0,
        "between_gc_gap_total_s": stat(
            run, "host_supply_between_gc_gap_us"
        ) / 1_000_000.0,
        "between_gc_gap_median_ms": stat(
            run, "host_supply_between_gc_gap_us", "median"
        ) / 1000.0,
        "between_gc_gap_p95_ms": stat(
            run, "host_supply_between_gc_gap_us", "p95"
        ) / 1000.0,
        "f2fs_gc_calls": stat(run, "gc_call_duration_us", "count"),
        "csgc_sections": scalar(run, "timeline_sections"),
        "migrated_blocks": stat(run, "modern_section_critical_total_moves"),
        "location23_checkpoint_calls": (
            stat(run, "gc_call_checkpoint_location2_calls")
            + stat(run, "gc_call_checkpoint_location3_calls")
        ),
        "conflict_deferred_fraction_pct": stat(
            run, "conflict_supply_deferred_fraction_permille", "mean"
        ) / 10.0,
        "rolling_records": scalar(run, "rolling_supply_records"),
        "rolling_sections_started": stat(run, "rolling_supply_sections_started"),
        "rolling_sections_completed": stat(
            run, "rolling_supply_sections_completed"
        ),
        "rolling_sections_per_call": stat(
            run, "rolling_supply_sections_started", "mean"
        ),
        "rolling_active_peak": stat(run, "rolling_supply_active_peak", "mean"),
        "rolling_successor_submitted": stat(
            run, "rolling_supply_successor_submitted"
        ),
        "logical_waf_cs": device(run, "logical_waf_cs"),
        "physical_waf": device(run, "physical_waf"),
        "device_csgc_requests": device(run, "csgc_requests"),
        "device_completed_moves": device(run, "csgc_completed_moves"),
    }


def emit_workload(
    label: str, control: Dict[str, object], rolling: Dict[str, object]
) -> str:
    """Emit one workload comparison and its acceptance checks."""
    control_values = metrics(control)
    rolling_values = metrics(rolling)
    lines = [
        f"=== {label} ===",
        f"control_dir={control['run_dir']}",
        f"rolling_dir={rolling['run_dir']}",
        "metric\tcontrol\trolling\trolling_vs_control_pct",
    ]
    for name, control_value in control_values.items():
        rolling_value = rolling_values[name]
        lines.append(
            f"{name}\t{control_value:.6f}\t{rolling_value:.6f}\t"
            f"{delta(control_value, rolling_value):.3f}"
        )

    gap_reference = control_values["between_gc_gap_total_s"]
    gap_value = rolling_values["between_gc_gap_total_s"]
    gap_reduction = -delta(gap_reference, gap_value)
    bandwidth_gain = delta(
        control_values["fio_bw_mib_s"], rolling_values["fio_bw_mib_s"]
    )
    lines.extend(
        (
            f"between_gc_gap_reduction_pct={gap_reduction:.3f}",
            f"fio_bandwidth_gain_pct={bandwidth_gain:.3f}",
            f"gap_acceptance_25pct={int(gap_reduction >= 25.0)}",
            f"bandwidth_gain_5pct={int(bandwidth_gain > 5.0)}",
            f"bandwidth_regression_over_5pct={int(bandwidth_gain < -5.0)}",
            "",
        )
    )
    return "\n".join(lines)


def main() -> None:
    """Write the two-workload causal comparison report."""
    args = parse_args()
    control_big = parse_run(args.control_big)
    control_small = parse_run(args.control_small)
    rolling_big = parse_run(args.rolling_big)
    rolling_small = parse_run(args.rolling_small)
    big_gain = delta(float(control_big["bandwidth"]), float(rolling_big["bandwidth"]))
    small_gain = delta(
        float(control_small["bandwidth"]), float(rolling_small["bandwidth"])
    )
    report = "\n".join(
        (
            "Conflict-aware versus rolling CSGC supply",
            "",
            emit_workload("bigfile", control_big, rolling_big),
            emit_workload("smallfile", control_small, rolling_small),
            "=== overall acceptance ===",
            f"one_workload_gain_over_5pct={int(max(big_gain, small_gain) > 5.0)}",
            f"other_workload_within_5pct={int(min(big_gain, small_gain) >= -5.0)}",
            f"bigfile_gain_pct={big_gain:.3f}",
            f"smallfile_gain_pct={small_gain:.3f}",
            "",
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
