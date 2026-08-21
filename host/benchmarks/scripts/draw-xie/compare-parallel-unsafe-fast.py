#!/usr/bin/env python3

"""Summarize unsafe-fast two-way CSGC against fixed historical baselines."""

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
FAST_SUMMARY_RE = re.compile(
    r"CSGC_PARALLEL_FAST_SUMMARY pairs=(?P<pairs>\d+) "
    r"active0_us=(?P<active0_us>\d+) active1_us=(?P<active1_us>\d+) "
    r"active2_us=(?P<active2_us>\d+) victim_claims=(?P<victim_claims>\d+) "
    r"victim_releases=(?P<victim_releases>\d+) "
    r"victim_collisions=(?P<victim_collisions>\d+) "
    r"victim_leaks=(?P<victim_leaks>\d+) "
    r"inode_lease_new=(?P<inode_lease_new>\d+) "
    r"inode_lease_join=(?P<inode_lease_join>\d+) "
    r"inode_lease_release=(?P<inode_lease_release>\d+)"
)

BASELINES = {
    "bigfile": {
        "recommended_mib_s": 423.722676,
        "original_csgc_mib_s": 213.712,
    },
    "smallfile": {
        "recommended_mib_s": 426.853077,
        "original_csgc_mib_s": 276.110,
    },
}


def parse_args() -> argparse.Namespace:
    """Parse the two result directories and report destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--big", type=Path, required=True)
    parser.add_argument("--small", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def parse_bandwidth(run_dir: Path) -> float:
    """Return fio application-visible write bandwidth in MiB/s."""
    text = (run_dir / "fio.log").read_text(errors="replace")
    matches = FIO_BW_BYTES_RE.findall(text)
    if matches:
        return float(matches[-1]) / (1024.0 * 1024.0)
    matches = FIO_BW_RE.findall(text)
    if not matches:
        raise ValueError(f"no fio bandwidth in {run_dir / 'fio.log'}")
    value = float(matches[-1][0])
    unit = matches[-1][1]
    if unit == "KiB/s":
        return value / 1024.0
    if unit == "GiB/s":
        return value * 1024.0
    return value


def parse_run(run_dir: Path) -> Dict[str, float]:
    """Load end-to-end, supply, work, and aggregate parallel metrics."""
    scalars: Dict[str, float] = {}
    stats: Dict[str, Dict[str, float]] = {}
    summary_path = run_dir / "gc-breakdown-diagnostic-result.txt"
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

    dmesg_text = (run_dir / "external-dmesg.log").read_text(errors="replace")
    fast_matches = list(FAST_SUMMARY_RE.finditer(dmesg_text))
    if not fast_matches:
        raise ValueError(f"no CSGC_PARALLEL_FAST_SUMMARY in {run_dir}")
    fast = {name: float(value) for name, value in fast_matches[-1].groupdict().items()}
    active_total = fast["active0_us"] + fast["active1_us"] + fast["active2_us"]

    def stat(name: str, field: str = "sum") -> float:
        return stats.get(name, {}).get(field, 0.0)

    return {
        "fio_bw_mib_s": parse_bandwidth(run_dir),
        "measured_duration_s": scalars.get("measured_duration_us", 0.0) / 1_000_000.0,
        "supply_coverage_pct": stat("host_supply_coverage_permille", "mean") / 10.0,
        "between_gc_gap_total_s": stat("host_supply_between_gc_gap_us") / 1_000_000.0,
        "same_gc_gap_total_s": stat("host_supply_same_gc_gap_us") / 1_000_000.0,
        "sections": scalars.get("timeline_sections", 0.0),
        "migrated_blocks": stat("modern_section_critical_total_moves"),
        "pairs": fast["pairs"],
        "active0_s": fast["active0_us"] / 1_000_000.0,
        "active1_s": fast["active1_us"] / 1_000_000.0,
        "active2_s": fast["active2_us"] / 1_000_000.0,
        "active2_coverage_pct": 100.0 * fast["active2_us"] / active_total
        if active_total
        else 0.0,
        "victim_claims": fast["victim_claims"],
        "victim_releases": fast["victim_releases"],
        "victim_collisions": fast["victim_collisions"],
        "victim_leaks": fast["victim_leaks"],
        "inode_lease_new": fast["inode_lease_new"],
        "inode_lease_join": fast["inode_lease_join"],
        "inode_lease_release": fast["inode_lease_release"],
    }


def emit_workload(name: str, run_dir: Path) -> str:
    """Emit one workload result and the absolute-performance decision."""
    values = parse_run(run_dir)
    baseline = BASELINES[name]
    recommended = baseline["recommended_mib_s"]
    original = baseline["original_csgc_mib_s"]
    gain_pct = (values["fio_bw_mib_s"] / recommended - 1.0) * 100.0
    lines = [
        f"=== {name} ===",
        f"run_dir={run_dir}",
    ]
    lines.extend(f"{key}={value:.6f}" for key, value in values.items())
    lines.extend(
        (
            f"recommended_baseline_mib_s={recommended:.6f}",
            f"original_csgc_baseline_mib_s={original:.6f}",
            f"vs_recommended_ratio={values['fio_bw_mib_s'] / recommended:.6f}",
            f"vs_recommended_pct={gain_pct:.3f}",
            f"vs_original_csgc_ratio={values['fio_bw_mib_s'] / original:.6f}",
            f"exceeds_recommended_by_5pct={int(gain_pct > 5.0)}",
            f"victim_balance_pass={int(values['victim_claims'] == values['victim_releases'] and values['victim_leaks'] == 0)}",
            f"inode_lease_balance_pass={int(values['inode_lease_new'] == values['inode_lease_release'])}",
            "",
        )
    )
    return "\n".join(lines)


def main() -> None:
    """Write a reproducible two-workload report."""
    args = parse_args()
    report = "\n".join(
        (
            "Unsafe-fast two-way CSGC comparison",
            "Historical baselines are fixed single-run diagnostic results.",
            "",
            emit_workload("bigfile", args.big),
            emit_workload("smallfile", args.small),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")


if __name__ == "__main__":
    main()
