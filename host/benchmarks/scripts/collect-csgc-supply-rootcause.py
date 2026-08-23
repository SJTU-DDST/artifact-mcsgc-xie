#!/usr/bin/env python3
"""Start, collect, and analyze one CSGC supply root-cause trace."""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from csgc_supply_trace import (
    DEVICE_HEADER_SIZE,
    DEVICE_MAGIC,
    PAGE_SIZE,
    analyze_traces,
    decode_device_trace,
    decode_host_trace,
    decode_sync_page,
)


def privileged_command(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    prefix = [] if os.geteuid() == 0 else ["sudo"]
    return subprocess.run(prefix + command, check=True, **kwargs)


def write_control(path: Path, command: str, retries: int = 1) -> None:
    last_error: subprocess.CalledProcessError | None = None
    for _ in range(retries):
        try:
            privileged_command(
                ["tee", str(path)], input=f"{command}\n", text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            time.sleep(0.05)
    detail = last_error.stderr.strip() if last_error and last_error.stderr else "unknown error"
    raise RuntimeError(f"Failed to write '{command}' to {path}: {detail}")


def read_privileged(path: Path) -> bytes:
    result = privileged_command(["cat", str(path)], stdout=subprocess.PIPE)
    return result.stdout


def nvme_read(nvme: Path, device: Path, start_lba: int, pages: int) -> bytes:
    if pages < 1 or pages > 32:
        raise ValueError("A diagnostic NVMe read must contain 1..32 pages")
    with tempfile.TemporaryDirectory(prefix="csgc-supply-read-") as directory:
        output = Path(directory) / "payload.bin"
        command = [
            str(nvme), "read", str(device),
            "-s", str(start_lba),
            "-c", str(pages - 1),
            "-z", str(pages * PAGE_SIZE),
            "-L", "-d", str(output),
        ]
        privileged_command(command, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
        payload = output.read_bytes()
    expected = pages * PAGE_SIZE
    if len(payload) != expected:
        raise RuntimeError(f"NVMe diagnostic read returned {len(payload)} bytes, expected {expected}")
    return payload


def collect_sync_samples(nvme: Path, device: Path, phase: str,
                         count: int) -> list[dict[str, int | str]]:
    samples = []
    for _ in range(count):
        before = time.monotonic_ns()
        payload = nvme_read(nvme, device, 124, 1)
        after = time.monotonic_ns()
        sample = decode_sync_page(payload)
        sample.update({
            "phase": phase,
            "host_before_ns": before,
            "host_after_ns": after,
        })
        samples.append(sample)
    return samples


def collect_device_dump(nvme: Path, device: Path, output: Path) -> None:
    first = nvme_read(nvme, device, 0x10000, 1)
    magic, _, header_size = struct.unpack_from("<IHH", first, 0)
    if magic != DEVICE_MAGIC or header_size != DEVICE_HEADER_SIZE:
        raise RuntimeError("OpenSSD did not return a compatible supply trace header")
    total_size = struct.unpack_from("<Q", first, 40)[0]
    if total_size < PAGE_SIZE or total_size % PAGE_SIZE:
        raise RuntimeError(f"OpenSSD returned an invalid dump size: {total_size}")
    total_pages = total_size // PAGE_SIZE
    chunks = [first]
    page = 1
    while page < total_pages:
        count = min(32, total_pages - page)
        chunks.append(nvme_read(nvme, device, 0x10000 + page, count))
        page += count
    output.write_bytes(b"".join(chunks))


def command_start(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    control = args.debugfs_dir / "csgc_supply_control"
    # Keep synchronization commands outside the Host measurement epoch.
    samples = collect_sync_samples(args.nvme, args.device, "pre", args.samples)
    (args.output_dir / "csgc-time-sync.json").write_text(
        json.dumps(samples, indent=2, sort_keys=True) + "\n", encoding="ascii")
    write_control(control, "start", retries=200)


def command_finish(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    control = args.debugfs_dir / "csgc_supply_control"
    write_control(control, "stop", retries=200)

    # Freeze the device snapshot immediately after the Host epoch.  The time
    # synchronization page remains live after the diagnostic dump is frozen.
    collect_device_dump(args.nvme, args.device,
                        args.output_dir / "ssd-csgc-supply-trace.bin")

    sync_path = args.output_dir / "csgc-time-sync.json"
    samples = json.loads(sync_path.read_text(encoding="ascii"))
    samples.extend(collect_sync_samples(args.nvme, args.device, "post", args.samples))
    sync_path.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n",
                         encoding="ascii")

    (args.output_dir / "host-csgc-supply-summary.log").write_bytes(
        read_privileged(args.debugfs_dir / "csgc_supply_summary"))
    (args.output_dir / "host-csgc-supply-trace.bin").write_bytes(
        read_privileged(args.debugfs_dir / "csgc_supply_trace"))
    command_analyze(args)


def command_analyze(args: argparse.Namespace) -> None:
    host = decode_host_trace(args.output_dir / "host-csgc-supply-trace.bin")
    device = decode_device_trace(args.output_dir / "ssd-csgc-supply-trace.bin")
    samples = json.loads(
        (args.output_dir / "csgc-time-sync.json").read_text(encoding="ascii"))
    result = analyze_traces(host, device, samples, args.output_dir)
    print(json.dumps({
        "supply_coverage_pct": result["host"]["supply_coverage_pct"],
        "outstanding_ge_2_coverage_pct": result["host"]["outstanding_ge_2_coverage_pct"],
        "clock_mapping_reliable": result["clock_mapping"]["reliable"],
        "matched_request_count": result["clock_mapping"]["matched_request_count"],
        "boundary_device_request_count": result["clock_mapping"][
            "unmatched_device_boundary_request_count"],
    }, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "finish", "analyze"):
        child = subparsers.add_parser(name)
        child.add_argument("--output-dir", required=True, type=Path)
        child.add_argument("--device", type=Path, default=Path("/dev/nvme0n1"))
        child.add_argument("--nvme", type=Path,
                           default=Path(__file__).resolve().parents[2] /
                           "src/nvme-cli/nvme")
        child.add_argument("--debugfs-dir", type=Path,
                           default=Path("/sys/kernel/debug/f2fs/nvme0n1"))
        child.add_argument("--samples", type=int, default=8)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command in {"start", "finish"}:
        if not args.nvme.is_file():
            raise SystemExit(f"NVMe CLI is unavailable: {args.nvme}")
        if not args.device.exists():
            raise SystemExit(f"NVMe device is unavailable: {args.device}")
        if not args.debugfs_dir.is_dir():
            raise SystemExit(f"Host CSGC debugfs directory is unavailable: {args.debugfs_dir}")
    if args.samples < 2:
        raise SystemExit("At least two time synchronization samples are required")
    {"start": command_start, "finish": command_finish,
     "analyze": command_analyze}[args.command](args)


if __name__ == "__main__":
    main()
