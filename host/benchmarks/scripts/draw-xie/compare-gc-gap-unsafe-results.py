#!/usr/bin/env python3

"""Compare CSGC gap control, unsafe reclaim, and sequential refill runs."""

import argparse
import re
from pathlib import Path
from typing import Dict, Optional


STAT_RE = re.compile(
    r"^(?P<name>[^:]+): count=(?P<count>\d+) "
    r"mean=(?P<mean>-?[0-9.]+) median=(?P<median>-?[0-9.]+) "
    r"p95=(?P<p95>-?[0-9.]+) p99=(?P<p99>-?[0-9.]+) "
    r"min=(?P<min>-?[0-9.]+) max=(?P<max>-?[0-9.]+) "
    r"sum=(?P<sum>-?[0-9.]+)$"
)
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
FIO_WRITE_BW_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"bw_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
DEVICE_PATTERNS = {
    "logical_waf_cs": re.compile(r"logical WAF\(CS\):\s*(\d+)"),
    "physical_waf": re.compile(r"physical WAF:\s*(\d+)"),
    "host_write_bytes": re.compile(r"host_normal_write_bytes:\s*(\d+)"),
    "csgc_requests": re.compile(r"csgc_mp:\s+req=(\d+)"),
    "csgc_completed_moves": re.compile(r"csgc_mp:.*?done=(\d+)"),
    "csgc_completed_bytes": re.compile(r"csgc_mp:.*?bytes=(\d+)"),
}


def parse_args() -> argparse.Namespace:
    """Parse six result directories and the destination report path."""
    parser = argparse.ArgumentParser(description=__doc__)
    for workload in ("big", "small"):
        parser.add_argument(f"--control-{workload}", type=Path, required=True)
        parser.add_argument(f"--reclaim-{workload}", type=Path, required=True)
        parser.add_argument(f"--refill-{workload}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_fio_bandwidth(run_dir: Path) -> float:
    """Return the final fio write bandwidth in MiB/s."""
    text = (run_dir / "fio.log").read_text(errors="replace")
    json_matches = FIO_WRITE_BW_BYTES_RE.findall(text)
    if json_matches:
        return float(json_matches[-1]) / (1024.0 * 1024.0)
    matches = FIO_BW_RE.findall(text)
    if not matches:
        raise ValueError(f"no fio write bandwidth in {run_dir / 'fio.log'}")
    value_text, unit = matches[-1]
    value = float(value_text)
    if unit == "KiB/s":
        return value / 1024.0
    if unit == "GiB/s":
        return value * 1024.0
    return value


def parse_device_stats(run_dir: Path) -> Dict[str, float]:
    """Parse measured-window OpenSSD counters when available."""
    path = run_dir / "ssd-workload-stat.log"
    if not path.is_file():
        return {}
    text = path.read_bytes().replace(b"\0", b"").decode(errors="replace")
    values: Dict[str, float] = {}
    for name, pattern in DEVICE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            values[name] = float(match.group(1))
    for name in ("logical_waf_cs", "physical_waf"):
        if name in values:
            values[name] /= 1000.0
    return values


def parse_run(run_dir: Path) -> Dict[str, object]:
    """Parse one diagnostic result directory."""
    summary = run_dir / "gc-breakdown-diagnostic-result.txt"
    scalars: Dict[str, float] = {}
    stats: Dict[str, Dict[str, float]] = {}
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
            continue
    return {
        "run_dir": run_dir,
        "fio_bw_mib_s": parse_fio_bandwidth(run_dir),
        "scalars": scalars,
        "stats": stats,
        "device": parse_device_stats(run_dir),
    }


def scalar(run: Dict[str, object], name: str, default: float = 0.0) -> float:
    """Return one optional summary scalar."""
    return float(run["scalars"].get(name, default))


def stat(
    run: Dict[str, object], name: str, field: str, default: float = 0.0
) -> float:
    """Return one optional distribution field."""
    return float(run["stats"].get(name, {}).get(field, default))


def device(run: Dict[str, object], name: str) -> Optional[float]:
    """Return one optional device counter."""
    value = run["device"].get(name)
    return float(value) if value is not None else None


def percent_delta(reference: float, value: float) -> float:
    """Return value minus reference as a percentage of reference."""
    if reference == 0:
        return 0.0
    return (value - reference) * 100.0 / reference


def reduction(reference: float, value: float) -> float:
    """Return a reduction as a positive percentage."""
    return -percent_delta(reference, value)


def metric_values(run: Dict[str, object]) -> Dict[str, float]:
    """Build the comparable metrics used by the final report."""
    location1_calls = stat(run, "gc_call_checkpoint_location1_calls", "sum")
    location4_calls = stat(run, "gc_call_checkpoint_location4_calls", "sum")
    location1_us = stat(run, "gc_call_checkpoint_location1_us", "sum")
    location4_us = stat(run, "gc_call_checkpoint_location4_us", "sum")
    return {
        "fio_bw_mib_s": float(run["fio_bw_mib_s"]),
        "measured_duration_s": scalar(run, "measured_duration_us") / 1_000_000.0,
        "supply_coverage_pct": stat(
            run, "host_supply_coverage_permille", "mean"
        ) / 10.0,
        "internal_gap_count": scalar(run, "global_supply_internal_gaps"),
        "internal_gap_total_s": stat(
            run, "host_supply_internal_gap_us", "sum"
        ) / 1_000_000.0,
        "internal_gap_median_ms": stat(
            run, "host_supply_internal_gap_us", "median"
        ) / 1000.0,
        "internal_gap_p95_ms": stat(
            run, "host_supply_internal_gap_us", "p95"
        ) / 1000.0,
        "checkpoint_gap_count": scalar(run, "global_supply_checkpoint_gaps"),
        "checkpoint_gap_total_s": stat(
            run, "host_supply_checkpoint_gap_us", "sum"
        ) / 1_000_000.0,
        "same_gc_gap_count": scalar(run, "global_supply_same_gc_gaps"),
        "same_gc_gap_total_s": stat(
            run, "host_supply_same_gc_gap_us", "sum"
        ) / 1_000_000.0,
        "between_gc_gap_count": scalar(run, "global_supply_between_gc_gaps"),
        "between_gc_gap_total_s": stat(
            run, "host_supply_between_gc_gap_us", "sum"
        ) / 1_000_000.0,
        "location1_checkpoint_calls": location1_calls,
        "location1_checkpoint_s": location1_us / 1_000_000.0,
        "location4_checkpoint_calls": location4_calls,
        "location4_checkpoint_s": location4_us / 1_000_000.0,
        "location14_checkpoint_calls": location1_calls + location4_calls,
        "location14_checkpoint_s": (location1_us + location4_us) / 1_000_000.0,
        "unsafe_reclaim_calls": stat(run, "gc_unsafe_reclaim_duration_us", "count"),
        "unsafe_reclaim_segments": stat(run, "gc_unsafe_reclaim_segments", "sum"),
        "unsafe_reclaim_sections": stat(run, "gc_unsafe_reclaim_sections", "sum"),
        "unsafe_reclaim_skipped": stat(run, "gc_unsafe_reclaim_skipped", "sum"),
        "unsafe_reclaim_s": stat(
            run, "gc_unsafe_reclaim_duration_us", "sum"
        ) / 1_000_000.0,
        "f2fs_gc_calls": stat(run, "gc_call_duration_us", "count"),
        "csgc_collectors": stat(run, "gc_call_csgc_collectors", "sum"),
        "csgc_collectors_per_gc_mean": stat(
            run, "gc_call_csgc_collectors", "mean"
        ),
        "refill_sections": stat(run, "gc_call_refill_sections", "sum"),
        "timeline_sections": scalar(run, "timeline_sections"),
        "migrated_blocks": stat(run, "modern_section_critical_total_moves", "sum"),
        "ori_collectors": stat(run, "gc_call_origc_collectors", "sum"),
    }


def emit_workload(
    label: str,
    control: Dict[str, object],
    reclaim: Dict[str, object],
    refill: Dict[str, object],
) -> str:
    """Build one workload table and acceptance evaluation."""
    runs = (control, reclaim, refill)
    names = ("control", "reclaim", "refill")
    values = [metric_values(run) for run in runs]
    metrics = tuple(values[0].keys())
    output = [
        f"=== {label} ===",
        *(f"{name}_dir={run['run_dir']}" for name, run in zip(names, runs)),
        "metric\tcontrol\treclaim\trefill\treclaim_vs_control_pct\trefill_vs_control_pct",
    ]
    for metric in metrics:
        control_value, reclaim_value, refill_value = (
            item[metric] for item in values
        )
        output.append(
            f"{metric}\t{control_value:.6f}\t{reclaim_value:.6f}\t"
            f"{refill_value:.6f}\t{percent_delta(control_value, reclaim_value):.3f}\t"
            f"{percent_delta(control_value, refill_value):.3f}"
        )

    for counter in (
        "logical_waf_cs",
        "physical_waf",
        "host_write_bytes",
        "csgc_requests",
        "csgc_completed_moves",
        "csgc_completed_bytes",
    ):
        items = [device(run, counter) for run in runs]
        output.append(
            f"device_{counter}\t"
            + "\t".join("missing" if item is None else f"{item:.6f}" for item in items)
        )

    control_cp_calls = values[0]["location14_checkpoint_calls"]
    reclaim_cp_calls = values[1]["location14_checkpoint_calls"]
    refill_cp_calls = values[2]["location14_checkpoint_calls"]
    reclaim_cp_reduction = reduction(control_cp_calls, reclaim_cp_calls)
    refill_cp_reduction = reduction(control_cp_calls, refill_cp_calls)
    reclaim_gap_total = values[1]["internal_gap_total_s"]
    refill_gap_total = values[2]["internal_gap_total_s"]
    reclaim_gap_count = values[1]["internal_gap_count"]
    refill_gap_count = values[2]["internal_gap_count"]
    refill_gap_total_reduction = reduction(reclaim_gap_total, refill_gap_total)
    refill_gap_count_reduction = reduction(reclaim_gap_count, refill_gap_count)
    reclaim_fio_delta = percent_delta(
        values[0]["fio_bw_mib_s"], values[1]["fio_bw_mib_s"]
    )
    refill_fio_delta = percent_delta(
        values[0]["fio_bw_mib_s"], values[2]["fio_bw_mib_s"]
    )
    output.extend(
        [
            f"reclaim_location14_checkpoint_reduction_pct={reclaim_cp_reduction:.6f}",
            f"refill_location14_checkpoint_reduction_pct={refill_cp_reduction:.6f}",
            f"refill_vs_reclaim_gap_total_reduction_pct={refill_gap_total_reduction:.6f}",
            f"refill_vs_reclaim_gap_count_reduction_pct={refill_gap_count_reduction:.6f}",
            f"reclaim_fio_delta_pct={reclaim_fio_delta:.6f}",
            f"refill_fio_delta_pct={refill_fio_delta:.6f}",
            f"accept_reclaim_checkpoint_elimination={int(reclaim_cp_reduction >= 90.0)}",
            f"accept_refill_checkpoint_elimination={int(refill_cp_reduction >= 90.0)}",
            "accept_refill_gap_reduction="
            f"{int(max(refill_gap_total_reduction, refill_gap_count_reduction) >= 25.0)}",
            f"accept_reclaim_fio_gain={int(reclaim_fio_delta > 5.0)}",
            f"accept_refill_fio_gain={int(refill_fio_delta > 5.0)}",
            "",
        ]
    )
    return "\n".join(output)


def main() -> int:
    """Write one deterministic six-run comparison report."""
    args = parse_args()
    sections = [
        "CSGC unsafe prefree reclaim and sequential refill comparison",
        emit_workload(
            "bigfile",
            parse_run(args.control_big.resolve()),
            parse_run(args.reclaim_big.resolve()),
            parse_run(args.refill_big.resolve()),
        ),
        emit_workload(
            "smallfile",
            parse_run(args.control_small.resolve()),
            parse_run(args.reclaim_small.resolve()),
            parse_run(args.refill_small.resolve()),
        ),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections) + "\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
