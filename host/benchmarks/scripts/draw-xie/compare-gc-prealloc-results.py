#!/usr/bin/env python3

"""Compare control and dirty-batched CSGC PRE allocation diagnostics."""

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
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
FIO_WRITE_BW_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"bw_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)

COMPARABLE_METRICS = (
    "comparable_pre_work_total_us",
    "comparable_pre_preallocate_us",
    "modern_detail_pre_prealloc_lock_wait_us",
    "modern_detail_pre_prealloc_alloc_us",
    "modern_prealloc_lock_hold_us",
    "modern_prealloc_discard_estimated_us",
    "modern_prealloc_curseg_advance_estimated_us",
    "modern_prealloc_block_stat_estimated_us",
    "modern_prealloc_mtime_estimated_us",
    "modern_prealloc_sit_estimated_us",
    "modern_prealloc_dirty_locate_estimated_us",
    "modern_prealloc_dirty_batch_us",
    "comparable_post_total_work_us",
    "comparable_segment_total_us",
    "comparable_section_collector_us",
)


def parse_fio_bandwidth(run_dir: Path) -> float:
    """Return the final group write bandwidth in MiB/s."""
    fio_text = (run_dir / "fio.log").read_text(errors="replace")
    json_matches = FIO_WRITE_BW_BYTES_RE.findall(fio_text)
    if json_matches:
        return float(json_matches[-1]) / (1024.0 * 1024.0)

    matches = FIO_BW_RE.findall(fio_text)
    if not matches:
        raise ValueError(f"no fio group bandwidth found in {run_dir / 'fio.log'}")
    value_text, unit = matches[-1]
    value = float(value_text)
    if unit == "KiB/s":
        return value / 1024.0
    if unit == "GiB/s":
        return value * 1024.0
    return value


def parse_run(run_dir: Path) -> Dict[str, object]:
    """Parse one diagnostic result directory."""
    summary_path = run_dir / "gc-breakdown-diagnostic-result.txt"
    scalars: Dict[str, float] = {}
    stats: Dict[str, Dict[str, float]] = {}

    for line in summary_path.read_text(errors="replace").splitlines():
        stat_match = STAT_RE.match(line)
        if stat_match:
            fields = stat_match.groupdict()
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
    }


def percent_delta(control: float, treatment: float) -> float:
    """Return treatment minus control as a percentage of control."""
    if control == 0:
        return 0.0
    return (treatment - control) * 100.0 / control


def percent_improvement(control: float, treatment: float) -> float:
    """Return latency reduction as a positive percentage."""
    return -percent_delta(control, treatment)


def require_stat(run: Dict[str, object], name: str) -> Dict[str, float]:
    """Return one required distribution."""
    stats = run["stats"]
    if name not in stats:
        raise ValueError(f"missing statistic {name} in {run['run_dir']}")
    return stats[name]


def require_scalar(run: Dict[str, object], name: str) -> float:
    """Return one required scalar."""
    scalars = run["scalars"]
    if name not in scalars:
        raise ValueError(f"missing scalar {name} in {run['run_dir']}")
    return scalars[name]


def emit_workload(label: str, control: Dict[str, object], treatment: Dict[str, object]) -> str:
    """Build one workload comparison and acceptance summary."""
    output = [
        f"=== {label} ===",
        f"control_dir={control['run_dir']}",
        f"treatment_dir={treatment['run_dir']}",
        "metric\tcontrol\ttreatment\tdelta_pct",
    ]
    control_bw = float(control["fio_bw_mib_s"])
    treatment_bw = float(treatment["fio_bw_mib_s"])
    fio_delta = percent_delta(control_bw, treatment_bw)
    output.append(
        f"fio_bw_mib_s\t{control_bw:.3f}\t{treatment_bw:.3f}\t{fio_delta:.3f}"
    )

    for metric in COMPARABLE_METRICS:
        control_stat = require_stat(control, metric)
        treatment_stat = require_stat(treatment, metric)
        for field in ("mean", "median", "p95", "p99"):
            control_value = control_stat[field]
            treatment_value = treatment_stat[field]
            output.append(
                f"{metric}_{field}\t{control_value:.3f}\t{treatment_value:.3f}\t"
                f"{percent_delta(control_value, treatment_value):.3f}"
            )

    dirty_reduction = require_scalar(
        treatment, "prealloc_dirty_call_reduction_pct"
    )
    mismatches = require_scalar(treatment, "prealloc_record_mismatches")
    lock_control = require_stat(control, "modern_prealloc_lock_hold_us")
    lock_treatment = require_stat(treatment, "modern_prealloc_lock_hold_us")
    alloc_control = require_stat(control, "modern_detail_pre_prealloc_alloc_us")
    alloc_treatment = require_stat(treatment, "modern_detail_pre_prealloc_alloc_us")
    section_control = require_stat(control, "comparable_section_collector_us")
    section_treatment = require_stat(treatment, "comparable_section_collector_us")
    lock_improvement = percent_improvement(lock_control["mean"], lock_treatment["mean"])
    alloc_improvement = percent_improvement(
        alloc_control["mean"], alloc_treatment["mean"]
    )
    section_improvement = percent_improvement(
        section_control["mean"], section_treatment["mean"]
    )

    output.extend(
        [
            f"dirty_call_reduction_pct={dirty_reduction:.6f}",
            f"prealloc_lock_hold_improvement_pct={lock_improvement:.6f}",
            f"prealloc_allocation_improvement_pct={alloc_improvement:.6f}",
            f"section_collector_improvement_pct={section_improvement:.6f}",
            f"fio_delta_pct={fio_delta:.6f}",
            f"prealloc_record_mismatches={int(mismatches)}",
            f"accept_dirty_call_reduction={int(dirty_reduction >= 90.0)}",
            f"accept_prealloc_service={int(max(lock_improvement, alloc_improvement) >= 15.0)}",
            f"accept_section_collector={int(section_improvement >= 3.0)}",
            f"accept_fio_no_regression={int(fio_delta >= -3.0)}",
            f"accept_invariants={int(mismatches == 0)}",
            "",
        ]
    )
    return "\n".join(output)


def parse_args() -> argparse.Namespace:
    """Parse paths for both workloads and configurations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-big", type=Path, required=True)
    parser.add_argument("--control-small", type=Path, required=True)
    parser.add_argument("--treatment-big", type=Path, required=True)
    parser.add_argument("--treatment-small", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Write one deterministic PRE allocation comparison report."""
    args = parse_args()
    sections = [
        "CSGC PRE allocation dirty-batch comparison",
        emit_workload(
            "bigfile",
            parse_run(args.control_big.resolve()),
            parse_run(args.treatment_big.resolve()),
        ),
        emit_workload(
            "smallfile",
            parse_run(args.control_small.resolve()),
            parse_run(args.treatment_small.resolve()),
        ),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections) + "\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
