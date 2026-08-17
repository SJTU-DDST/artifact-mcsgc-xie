#!/usr/bin/env python3

"""Compare control and batched-summary CSGC diagnostic runs."""

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
    "comparable_ssd_total_us",
    "comparable_post_total_work_us",
    "comparable_segment_total_us",
    "comparable_section_collector_us",
    "modern_post_detail_post_summary_commit_us",
    "modern_post_detail_post_summary_queue_wait_us",
    "modern_post_detail_post_summary_service_us",
    "modern_post_detail_post_summary_curseg_mutex_wait_us",
    "modern_post_detail_post_summary_resolve_us",
    "modern_post_detail_post_summary_entry_update_us",
    "summary_batch_batch_size",
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
    """Parse one diagnostic result directory into scalar and statistic maps."""

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
    """Return a required statistic or raise a descriptive error."""

    stats = run["stats"]
    if name not in stats:
        raise ValueError(f"missing statistic {name} in {run['run_dir']}")
    return stats[name]


def require_scalar(run: Dict[str, object], name: str) -> float:
    """Return a required scalar or raise a descriptive error."""

    scalars = run["scalars"]
    if name not in scalars:
        raise ValueError(f"missing scalar {name} in {run['run_dir']}")
    return scalars[name]


def emit_workload(label: str, control: Dict[str, object], treatment: Dict[str, object]) -> str:
    """Build one workload comparison and its acceptance checks."""

    output = [
        f"=== {label} ===",
        f"control_dir={control['run_dir']}",
        f"treatment_dir={treatment['run_dir']}",
        "metric\tcontrol\ttreatment\tdelta_pct",
    ]
    control_bw = float(control["fio_bw_mib_s"])
    treatment_bw = float(treatment["fio_bw_mib_s"])
    output.append(
        f"fio_bw_mib_s\t{control_bw:.3f}\t{treatment_bw:.3f}\t"
        f"{percent_delta(control_bw, treatment_bw):.3f}"
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

    reduction = require_scalar(treatment, "post_summary_commit_call_reduction_pct")
    commit_control = require_stat(control, "modern_post_detail_post_summary_commit_us")
    commit_treatment = require_stat(treatment, "modern_post_detail_post_summary_commit_us")
    mean_improvement = percent_improvement(commit_control["mean"], commit_treatment["mean"])
    p95_improvement = percent_improvement(commit_control["p95"], commit_treatment["p95"])
    batch_size_matches = require_scalar(treatment, "post_summary_batch_size_matches_segments") == 1
    batch_record_mismatches = require_scalar(treatment, "post_summary_batch_record_mismatches")
    move_sum = require_stat(treatment, "summary_batch_moves")["sum"]
    dnode_blocks = require_scalar(treatment, "post_dnode_total_blocks")

    output.extend(
        [
            f"commit_reduction_pct={reduction:.6f}",
            f"summary_mean_improvement_pct={mean_improvement:.6f}",
            f"summary_p95_improvement_pct={p95_improvement:.6f}",
            f"batch_size_matches_segments={int(batch_size_matches)}",
            f"batch_record_mismatches={int(batch_record_mismatches)}",
            f"batch_moves_match_dnode_blocks={int(move_sum == dnode_blocks)}",
            f"accept_commit_reduction={int(reduction >= 25.0)}",
            f"accept_summary_latency={int(max(mean_improvement, p95_improvement) >= 20.0)}",
            "",
        ]
    )
    return "\n".join(output)


def parse_args() -> argparse.Namespace:
    """Parse command-line paths for the four A/B result directories."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-big", type=Path, required=True)
    parser.add_argument("--control-small", type=Path, required=True)
    parser.add_argument("--treatment-big", type=Path, required=True)
    parser.add_argument("--treatment-small", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Compare both workloads and write a deterministic text report."""

    args = parse_args()
    control_big = parse_run(args.control_big.resolve())
    control_small = parse_run(args.control_small.resolve())
    treatment_big = parse_run(args.treatment_big.resolve())
    treatment_small = parse_run(args.treatment_small.resolve())
    sections = [
        "CSGC summary batch A/B comparison",
        emit_workload("bigfile", control_big, treatment_big),
        emit_workload("smallfile", control_small, treatment_small),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections) + "\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
