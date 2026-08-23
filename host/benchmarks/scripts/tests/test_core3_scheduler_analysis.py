#!/usr/bin/env python3
"""Synthetic validation and comparison checks for Core3 scheduler runs."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
VALIDATOR = SCRIPT_DIR / "validate-core3-scheduler-run.py"
COMPARATOR = SCRIPT_DIR / "draw-xie/compare-core3-scheduler-results.py"


def distribution(mean_us: float = 10.0) -> dict[str, object]:
    return {
        "count": 10,
        "sum": int(mean_us * 10_000),
        "min": int(mean_us * 500),
        "max": int(mean_us * 2_000),
        "mean": mean_us * 1_000,
        "histogram": [0] * 32,
        "histogram_unit": "us",
        "total_ms": mean_us / 100,
        "mean_us": mean_us,
        "min_us": mean_us / 2,
        "max_us": mean_us * 2,
        "p50_us": int(mean_us),
        "p95_us": int(mean_us * 2),
        "p99_us": int(mean_us * 2),
    }


def batch_distribution(mean: float = 4.0) -> dict[str, object]:
    result = distribution(mean)
    result.update({
        "histogram_unit": "requests",
        "mean_requests": mean,
        "min_requests": 1,
        "max_requests": 8,
        "p50_requests": 3,
        "p95_requests": 7,
        "p99_requests": 7,
    })
    return result


def build_run(path: Path, budget: int, bandwidth: float) -> None:
    path.mkdir(parents=True)
    distributions = {
        "normal_sq_batch_size": batch_distribution(),
        "normal_sq_batch_ns": distribution(),
        "normal_cq_batch_size": batch_distribution(),
        "normal_cq_batch_ns": distribution(),
        "csgc_sq_batch_size": batch_distribution(2.0),
        "csgc_sq_batch_ns": distribution(),
        "csio_poll_gap_ns": distribution(20.0 if budget == 0 else 10.0),
        "csgc_pending_wait_ns": distribution(),
        "other_pending_wait_ns": distribution(),
    }
    analysis = {
        "host": {
            "supply_coverage_pct": 70.0,
            "outstanding_ge_2_coverage_pct": 20.0,
            "gap_dropped": 0,
            "request_dropped": 0,
        },
        "device": {
            "timeline_overflow_count": 0,
            "request_overflow_count": 0,
            "scheduler": {
                "normal_budget": budget,
                "normal_sq_yield_count": 0 if budget == 0 else 5,
                "normal_cq_yield_count": 0 if budget == 0 else 2,
                "csio_poll_long_count": 4,
                "csio_poll_long_ns": 4_000_000 if budget == 0 else 2_000_000,
                "distributions": distributions,
            },
            "timeline_summary": {
                "core3_budget_values": [budget],
                "core3_state_pct": {
                    "C3_NORMAL_EMU_IO": 20.0,
                    "C3_NORMAL_CQ": 5.0,
                    "C3_CSIO_SCHED": 10.0,
                    "C3_CDMA": 15.0,
                },
                "cdma_busy_pct": 15.0,
                "normal_io_pending_pct": 50.0,
                "normal_io_active_pct": 20.0,
            },
            "request_lifecycle": {
                name: {"mean_us": value}
                for name, value in {
                    "enqueue_wait_ns": 100.0,
                    "worker_start_wait_ns": 5.0,
                    "leader_ns": 900.0,
                    "total_ns": 1100.0,
                }.items()
            },
            "channel_at_freeze": {
                name: 0 for name in (
                    "cs_queue_depth", "csgc_sq_depth", "normal_sq_depth",
                    "normal_cq_depth", "csgc_csio_pending_depth",
                    "other_csio_pending_depth", "csio_outstanding_depth",
                    "active_workers", "cdma_busy",
                )
            },
        },
        "clock_mapping": {
            "reliable": True,
            "matched_request_count": 10,
            "valid_host_request_count": 10,
            "valid_device_request_count": 10,
            "unmatched_host_request_count": 0,
            "unmatched_device_request_count": 0,
            "unmatched_device_interior_request_count": 0,
            "unmatched_device_boundary_request_count": 0,
            "unmatched_device_unmapped_request_count": 0,
        },
    }
    (path / "csgc-supply-analysis.json").write_text(
        json.dumps(analysis), encoding="ascii"
    )
    (path / "fio.log").write_text(
        '{"jobs":[{"error":0,"write":{"bw_bytes":'
        f'{bandwidth * 1024 * 1024},"io_bytes":1073741824,'
        '"slat_ns":{"mean":1000},"clat_ns":{"mean":2000},'
        '"lat_ns":{"mean":3000}}}]}\n', encoding="ascii"
    )
    (path / "ssd-workload-stat.log").write_text(
        "csgc_mp: req=10 ok=10 fail=0 decl=100 sub=100 done=100 bytes=409600 tx=10\n"
        "csgc_mp_life_us: rx=1/1/1 q=100/10/20 exec=900/90/100 rwait=1/1/1 tx=1/1/1 total=1000/100/120\n"
        "csgc_mp_phase_us: parse=1/1/1 init=1/1/1 submit=1/1/1 flush=100/10/20 pack=1/1/1 clean=1/1/1 other=1/1/1\n"
        "csgc_mp_channel_x10000: supply=5000 service=6000 dma=7000 srv_mib_s=3000 good_mib_s=500\n"
        "logical WAF(CS): 1000\nphysical WAF: 1100\n",
        encoding="ascii",
    )
    (path / "gc-breakdown-diagnostic-result.txt").write_text(
        "timeline_sections=10\n"
        "modern_section_critical_total_moves: count=10 mean=10 median=10 "
        "p95=10 p99=10 min=10 max=10 sum=100\n",
        encoding="ascii",
    )
    (path / "core3-scheduler-provenance.log").write_text(
        f"core3_normal_budget={budget}\nsupply_trace_abi=2\n", encoding="ascii"
    )
    (path / "core3-scheduler-config.log").write_text(
        f"core3_normal_budget={budget}\n", encoding="ascii"
    )
    (path / "host-csgc-supply-trace.bin").write_bytes(b"host")
    (path / "ssd-csgc-supply-trace.bin").write_bytes(b"device")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="core3-scheduler-test-") as directory:
        root = Path(directory)
        runs: dict[tuple[str, int], Path] = {}
        for workload in ("big", "small"):
            for budget in (0, 4, 8):
                path = root / f"{workload}-{budget}"
                build_run(path, budget, 300.0 + budget)
                runs[(workload, budget)] = path
                subprocess.run(
                    [str(VALIDATOR), str(path), str(budget)], check=True,
                    stdout=subprocess.DEVNULL,
                )

        report = root / "report.md"
        command = [str(COMPARATOR)]
        for workload in ("big", "small"):
            for budget in (0, 4, 8):
                command.extend((f"--b{budget}-{workload}",
                                str(runs[(workload, budget)])))
        command.extend(("--output", str(report)))
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
        assert report.is_file()
        comparison = json.loads(report.with_suffix(".json").read_text())
        assert comparison["大文件"]["best_budget"] == 8
    print("synthetic Core3 scheduler analysis checks passed")


if __name__ == "__main__":
    main()
