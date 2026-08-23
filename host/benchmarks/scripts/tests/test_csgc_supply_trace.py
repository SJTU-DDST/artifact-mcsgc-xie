#!/usr/bin/env python3
"""Synthetic ABI, overflow, and clock-correlation checks."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from csgc_supply_trace import (  # noqa: E402
    DEVICE_HEADER_SIZE,
    DEVICE_MAGIC,
    DEVICE_REQUEST_STRUCT,
    DEVICE_TIMELINE_STRUCT,
    HOST_GAP_SIZE,
    HOST_HEADER_SIZE,
    HOST_MAGIC,
    HOST_REQUEST_STRUCT,
    PAGE_SIZE,
    REASONS,
    analyze_traces,
    decode_device_trace,
    decode_host_trace,
)


def build_host_trace(path: Path) -> None:
    gap_count = 1
    request_count = 2
    data = bytearray(HOST_HEADER_SIZE + gap_count * HOST_GAP_SIZE +
                     request_count * HOST_REQUEST_STRUCT.size)
    struct.pack_into("<IHHIIII", data, 0, HOST_MAGIC, 1, HOST_HEADER_SIZE,
                     HOST_GAP_SIZE, HOST_REQUEST_STRUCT.size, len(REASONS), 1)
    struct.pack_into("<QQQ", data, 24, 7, 2_000_000_000, 2_010_000_000)
    struct.pack_into("<6I", data, 320, gap_count, request_count,
                     65536, 262144, 1, 0)
    struct.pack_into("<Q", data, 400, 11)

    gap = HOST_HEADER_SIZE
    struct.pack_into("<QQQQ", data, gap, 2_004_000_000, 2_006_000_000, 1, 2)
    inclusive = [0] * len(REASONS)
    dominant = [0] * len(REASONS)
    inclusive[1] = dominant[1] = 2_000_000
    struct.pack_into(f"<{len(REASONS)}Q", data, gap + 32, *inclusive)
    struct.pack_into(f"<{len(REASONS)}Q", data, gap + 32 + 8 * len(REASONS),
                     *dominant)
    struct.pack_into("<II", data, gap + HOST_GAP_SIZE - 8, 1, 0)

    request = HOST_HEADER_SIZE + HOST_GAP_SIZE
    HOST_REQUEST_STRUCT.pack_into(
        data, request, 1, 2_000_900_000, 2_001_000_000,
        2_004_000_000, 100, 800, 0, 7)
    HOST_REQUEST_STRUCT.pack_into(
        data, request + HOST_REQUEST_STRUCT.size, 2,
        2_005_900_000, 2_006_000_000, 2_009_000_000,
        101, 808, 0, 7)
    path.write_bytes(data)


def build_device_trace(path: Path) -> None:
    timeline_count = 10
    request_count = 2
    timeline_offset = PAGE_SIZE
    request_offset = PAGE_SIZE * 2
    total_size = PAGE_SIZE * 3
    data = bytearray(total_size)
    struct.pack_into("<IHHII", data, 0, DEVICE_MAGIC, 1,
                     DEVICE_HEADER_SIZE, PAGE_SIZE, 0x7)
    struct.pack_into("<5Q", data, 16, 9, 1_000_000_000,
                     1_010_000_000, total_size, timeline_offset)
    struct.pack_into("<II", data, 56, DEVICE_TIMELINE_STRUCT.size,
                     timeline_count)
    struct.pack_into("<Q", data, 64, request_offset)
    struct.pack_into("<4I", data, 72, DEVICE_REQUEST_STRUCT.size,
                     request_count, 600064, 262144)
    struct.pack_into("<3Q", data, 88, 3, 2, 16)

    for index in range(timeline_count):
        DEVICE_TIMELINE_STRUCT.pack_into(
            data, timeline_offset + index * DEVICE_TIMELINE_STRUCT.size,
            0, 6, 0, 12, 0, 0, 0, 1, 0, 0, 0, 0, index + 1)

    DEVICE_REQUEST_STRUCT.pack_into(
        data, request_offset, 1,
        1_001_050_000, 1_001_100_000, 1_001_110_000, 1_001_120_000,
        1_001_130_000, 1_001_140_000, 1_003_700_000, 1_003_800_000,
        1_003_900_000, 0, 64, 0, 1, 3)
    DEVICE_REQUEST_STRUCT.pack_into(
        data, request_offset + DEVICE_REQUEST_STRUCT.size, 2,
        1_006_050_000, 1_006_100_000, 1_006_110_000, 1_006_120_000,
        1_006_130_000, 1_006_140_000, 1_008_700_000, 1_008_800_000,
        1_008_900_000, 0, 64, 0, 1, 4)
    path.write_bytes(data)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="csgc-supply-test-") as directory:
        root = Path(directory)
        host_path = root / "host.bin"
        device_path = root / "device.bin"
        build_host_trace(host_path)
        build_device_trace(device_path)
        host = decode_host_trace(host_path)
        device = decode_device_trace(device_path)
        assert host["header"]["timestamp_reorders"] == 11
        assert device["header"]["timeline_overflow_count"] == 3
        assert device["header"]["request_overflow_count"] == 2

        samples = []
        for phase, device_ns in (("pre", 1_000_000_000),
                                 ("post", 1_010_000_000)):
            host_mid = device_ns + 1_000_000_000
            samples.append({
                "phase": phase,
                "epoch": 9,
                "device_time_ns": device_ns,
                "host_before_ns": host_mid - 50_000,
                "host_after_ns": host_mid + 50_000,
            })
        result = analyze_traces(host, device, samples, root)
        assert result["clock_mapping"]["reliable"]
        assert result["clock_mapping"]["matched_request_count"] == 2
        assert result["joint_attribution_emitted"]
        assert (root / "csgc-supply-gaps.csv").is_file()
        assert json.loads((root / "csgc-supply-analysis.json").read_text())["device"][
            "timeline_overflow_count"] == 3

        bad_samples = [dict(sample) for sample in samples]
        bad_samples[-1]["host_before_ns"] += 20_000_000
        bad_samples[-1]["host_after_ns"] += 20_000_000
        bad_result = analyze_traces(host, device, bad_samples, root)
        assert not bad_result["clock_mapping"]["reliable"]
        assert not bad_result["joint_attribution_emitted"]
        timeline_lines = (root / "csgc-device-timeline.csv").read_text().splitlines()
        assert len(timeline_lines) == len(device["timeline"]) + 1
    print("synthetic CSGC supply trace checks passed")


if __name__ == "__main__":
    main()
