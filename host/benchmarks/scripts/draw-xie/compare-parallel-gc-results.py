#!/usr/bin/env python3

"""Compare the control and shared-inode two-way CSGC experiments."""

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
    """Parse two workloads for both control and treatment."""
    parser = argparse.ArgumentParser(description=__doc__)
    for workload in ("big", "small"):
        parser.add_argument(f"--control-{workload}", type=Path, required=True)
        parser.add_argument(f"--treatment-{workload}", type=Path, required=True)
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
        text = device_path.read_bytes().replace(b"\0", b"").decode(
            errors="replace"
        )
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
    """Return percentage change from reference to value."""
    return (value - reference) * 100.0 / reference if reference else 0.0


def metrics(run: Dict[str, object]) -> Dict[str, float]:
    """Build end-to-end, supply, overlap, and integrity metrics."""
    return {
        "fio_bw_mib_s": float(run["bandwidth"]),
        "measured_duration_s": scalar(run, "measured_duration_us") / 1_000_000.0,
        "supply_coverage_pct": stat(
            run, "host_supply_coverage_permille", "mean"
        ) / 10.0,
        "supply_depth_ge_two_pct": stat(
            run, "host_supply_depth_ge_two_coverage_permille", "mean"
        ) / 10.0,
        "supply_peak_outstanding": stat(
            run, "host_supply_peak_outstanding", "max"
        ),
        "between_gc_gap_total_s": stat(
            run, "host_supply_between_gc_gap_us"
        ) / 1_000_000.0,
        "same_gc_gap_total_s": stat(
            run, "host_supply_same_gc_gap_us"
        ) / 1_000_000.0,
        "csgc_sections": scalar(run, "timeline_sections"),
        "migrated_blocks": stat(run, "modern_section_critical_total_moves"),
        "worker_overlap_pct": stat(
            run, "parallel_control_overlap_coverage_permille", "mean"
        ) / 10.0,
        "parallel_gc_records": scalar(run, "parallel_gc_records"),
        "parallel_active2_pct": stat(
            run, "parallel_gc_active2_coverage_permille", "mean"
        ) / 10.0,
        "shared_inode_pair_records": scalar(
            run, "parallel_gc_shared_inode_records"
        ),
        "shared_inode_overlap_pct": stat(
            run, "parallel_gc_shared_inode_overlap_fraction_permille", "mean"
        ) / 10.0,
        "exact_block_conflicts": stat(run, "parallel_gc_exact_block_conflicts"),
        "victim_claims": stat(run, "parallel_gc_victim_claims"),
        "victim_releases": stat(run, "parallel_gc_victim_releases"),
        "victim_collisions": stat(run, "parallel_gc_victim_collisions"),
        "victim_leaks": stat(run, "parallel_gc_victim_leaks"),
        "active_victim_residuals": stat(run, "parallel_gc_active_victims"),
        "inode_lease_new": stat(run, "parallel_gc_inode_lease_new"),
        "inode_lease_join": stat(run, "parallel_gc_inode_lease_join"),
        "inode_lease_release": stat(run, "parallel_gc_inode_lease_release"),
        "lease_residuals": stat(run, "parallel_gc_lease_residuals"),
        "invalid_parallel_records": scalar(run, "parallel_gc_invalid_records"),
        "logical_waf_cs": device(run, "logical_waf_cs"),
        "physical_waf": device(run, "physical_waf"),
        "device_csgc_requests": device(run, "csgc_requests"),
        "device_completed_moves": device(run, "csgc_completed_moves"),
    }


def emit_workload(
    label: str, control: Dict[str, object], treatment: Dict[str, object]
) -> str:
    """Emit one workload comparison and its acceptance checks."""
    control_values = metrics(control)
    treatment_values = metrics(treatment)
    lines = [
        f"=== {label} ===",
        f"control_dir={control['run_dir']}",
        f"treatment_dir={treatment['run_dir']}",
        "metric\tcontrol\ttreatment\ttreatment_vs_control_pct",
    ]
    for name, control_value in control_values.items():
        treatment_value = treatment_values[name]
        lines.append(
            f"{name}\t{control_value:.6f}\t{treatment_value:.6f}\t"
            f"{delta(control_value, treatment_value):.3f}"
        )

    bandwidth_gain = delta(
        control_values["fio_bw_mib_s"], treatment_values["fio_bw_mib_s"]
    )
    manager_valid = (
        treatment_values["parallel_gc_records"] > 0
        and treatment_values["invalid_parallel_records"] == 0
        and treatment_values["victim_claims"]
        == treatment_values["victim_releases"]
        and treatment_values["victim_leaks"] == 0
        and treatment_values["active_victim_residuals"] == 0
        and treatment_values["inode_lease_new"]
        == treatment_values["inode_lease_release"]
        and treatment_values["lease_residuals"] == 0
    )
    lines.extend(
        (
            f"fio_bandwidth_gain_pct={bandwidth_gain:.3f}",
            "bandwidth_within_5pct="
            f"{int(bandwidth_gain >= -5.0)}",
            "shared_inode_overlap_over_90pct="
            f"{int(treatment_values['shared_inode_overlap_pct'] > 90.0)}",
            "dual_worker_overlap_over_30pct="
            f"{int(treatment_values['parallel_active2_pct'] > 30.0)}",
            f"manager_integrity_pass={int(manager_valid)}",
            "",
        )
    )
    return "\n".join(lines)


def main() -> None:
    """Write the two-workload causal comparison report."""
    args = parse_args()
    control_big = parse_run(args.control_big)
    control_small = parse_run(args.control_small)
    treatment_big = parse_run(args.treatment_big)
    treatment_small = parse_run(args.treatment_small)
    big_gain = delta(
        float(control_big["bandwidth"]), float(treatment_big["bandwidth"])
    )
    small_gain = delta(
        float(control_small["bandwidth"]), float(treatment_small["bandwidth"])
    )
    big_values = metrics(treatment_big)
    small_values = metrics(treatment_small)
    all_parallel_records_valid = (
        big_values["parallel_gc_records"] > 0
        and small_values["parallel_gc_records"] > 0
        and big_values["invalid_parallel_records"] == 0
        and small_values["invalid_parallel_records"] == 0
    )
    report = "\n".join(
        (
            "Two-way shared-inode CSGC A/B",
            "",
            emit_workload("bigfile", control_big, treatment_big),
            emit_workload("smallfile", control_small, treatment_small),
            "=== overall acceptance ===",
            f"one_workload_gain_over_5pct={int(max(big_gain, small_gain) > 5.0)}",
            f"other_workload_within_5pct={int(min(big_gain, small_gain) >= -5.0)}",
            "bigfile_shared_inode_overlap_over_90pct="
            f"{int(big_values['shared_inode_overlap_pct'] > 90.0)}",
            "bigfile_dual_worker_overlap_over_30pct="
            f"{int(big_values['parallel_active2_pct'] > 30.0)}",
            "all_parallel_records_valid="
            f"{int(all_parallel_records_valid)}",
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
