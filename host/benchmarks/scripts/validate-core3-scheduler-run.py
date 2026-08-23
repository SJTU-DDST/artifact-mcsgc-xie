#!/usr/bin/env python3
"""Fail closed when one Core3 scheduler diagnostic run is incomplete."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("budget", type=int, choices=(0, 4, 8))
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"ERROR: {message}")


def main() -> None:
    args = parse_args()
    required = (
        "fio.log",
        "host-csgc-supply-trace.bin",
        "ssd-csgc-supply-trace.bin",
        "csgc-supply-analysis.json",
        "core3-scheduler-config.log",
        "core3-scheduler-provenance.log",
    )
    for name in required:
        require((args.run_dir / name).is_file(), f"missing {name}")

    fio_text = (args.run_dir / "fio.log").read_text(errors="replace")
    fio_errors = [int(value) for value in re.findall(r'"error"\s*:\s*(\d+)', fio_text)]
    require(fio_errors and all(value == 0 for value in fio_errors),
            "fio did not report error=0 for every job")

    provenance = dict(
        line.split("=", 1)
        for line in (args.run_dir / "core3-scheduler-provenance.log")
        .read_text(errors="replace").splitlines()
        if "=" in line
    )
    require(provenance.get("core3_normal_budget") == str(args.budget),
            "recorded Core3 budget differs from the requested value")
    require(provenance.get("supply_trace_abi") == "2",
            "recorded supply trace ABI is not v2")

    analysis = json.loads(
        (args.run_dir / "csgc-supply-analysis.json").read_text(encoding="ascii")
    )
    host = analysis["host"]
    device = analysis["device"]
    mapping = analysis["clock_mapping"]
    require(mapping.get("reliable") is True, "Host/device clock mapping is unreliable")
    matched = int(mapping.get("matched_request_count", 0))
    host_count = int(mapping.get("valid_host_request_count", -1))
    require(matched > 0 and matched == host_count,
            "not every valid Host request is associated with the device")
    require(int(mapping.get("unmatched_host_request_count", -1)) == 0,
            "unmatched Host request IDs remain")
    require(int(mapping.get("unmatched_device_interior_request_count", -1)) == 0,
            "an unmatched device request remains inside the Host epoch")
    require(int(mapping.get("unmatched_device_unmapped_request_count", -1)) == 0,
            "an unmatched device request could not be placed on the Host clock")
    require(int(host.get("gap_dropped", -1)) == 0 and
            int(host.get("request_dropped", -1)) == 0,
            "Host trace dropped records")
    require(int(device.get("timeline_overflow_count", -1)) == 0 and
            int(device.get("request_overflow_count", -1)) == 0,
            "device trace overflowed")

    scheduler = device["scheduler"]
    require(int(scheduler["normal_budget"]) == args.budget,
            "device trace header contains the wrong Core3 budget")
    require(device["timeline_summary"]["core3_budget_values"] == [args.budget],
            "timeline contains a budget change during the measured epoch")
    if args.budget:
        yields = int(scheduler["normal_sq_yield_count"]) + \
            int(scheduler["normal_cq_yield_count"])
        require(yields > 0, "bounded scheduler never yielded to the main loop")

    normal_sq_time = scheduler["distributions"]["normal_sq_batch_ns"]
    require(int(normal_sq_time["max"]) < 10_000_000_000,
            "normal SQ batch timing contains an invalid uptime-sized sample")

    channel = device["channel_at_freeze"]
    for name in (
            "csgc_sq_depth", "csgc_csio_pending_depth", "active_workers"):
        require(int(channel[name]) == 0,
                f"CSGC device channel was not drained at freeze: {name}={channel[name]}")
    require(not (int(channel["csio_outstanding_depth"]) and
                 int(channel["csio_owner"]) == 1),
            "a CSGC-owned CSIO request remained active at freeze")
    require(not (int(channel["cdma_busy"]) and
                 int(channel["cdma_owner"]) == 1),
            "a CSGC-owned CDMA transfer remained active at freeze")

    print(
        f"validated Core3 scheduler run: budget={args.budget} "
        f"matched_requests={matched} "
        f"boundary_device_requests="
        f"{mapping.get('unmatched_device_boundary_request_count', 0)} "
        f"csio_owner_at_freeze={channel['csio_owner']} "
        f"cdma_owner_at_freeze={channel['cdma_owner']}"
    )


if __name__ == "__main__":
    main()
