#!/usr/bin/env python3
"""Decode and correlate Host and OpenSSD CSGC supply diagnostics."""

from __future__ import annotations

import bisect
import csv
import json
import math
import statistics
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


HOST_MAGIC = 0x48534743
DEVICE_MAGIC = 0x43535254
SYNC_MAGIC = 0x43535453
HOST_TRACE_VERSION = 1
DEVICE_TRACE_VERSION = 2
PAGE_SIZE = 4096
TIMELINE_INTERVAL_NS = 1_000_000

REASONS = [
    "IDLE_SPACE_OK",
    "IDLE_NO_GC_CALL",
    "WAIT_GC_LOCK",
    "CHECKPOINT",
    "VICTIM_SELECT",
    "NO_VICTIM_RETRY",
    "PRE_DISCOVERY",
    "PRE_LOCK_VALIDATE",
    "DIRTY_WRITEBACK",
    "PREALLOCATE",
    "REQUEST_SUBMIT",
    "POST_COMMIT",
    "PREFREE_RECLAIM",
    "OTHER_GC",
]

CORE_STATES = [
    "IDLE",
    "C0_NORMAL_NVME",
    "C0_CSGC_ARG",
    "C0_GET_LOG",
    "C0_ADMIN",
    "C12_QUEUE",
    "C12_CSGC_EXEC",
    "C12_WAIT_CSIO",
    "C12_PACK",
    "C3_NORMAL_EMU_IO",
    "C3_NORMAL_CQ",
    "C3_CSGC_SQ",
    "C3_CSIO_SCHED",
    "C3_CDMA",
    "C3_ADMIN",
]

HOST_HEADER_SIZE = 408
HOST_GAP_SIZE = 264
HOST_REQUEST_SIZE = 48
DEVICE_HEADER_SIZE = 3376
DEVICE_TIMELINE_SIZE = 32
DEVICE_REQUEST_SIZE = 96
SYNC_RECORD_SIZE = 64
DEVICE_DISTRIBUTION_SIZE = 288
DEVICE_SCHEDULER_SIZE = 2632
DEVICE_CHANNEL_SIZE = 56
DEVICE_HISTOGRAM_BUCKETS = 32

HOST_REQUEST_SUBMITTED = 1 << 1

HOST_REQUEST_STRUCT = struct.Struct("<QQQQIIiI")
DEVICE_TIMELINE_STRUCT = struct.Struct("<4B6H8BII")
DEVICE_REQUEST_STRUCT = struct.Struct("<10QiIHHI")
SYNC_STRUCT = struct.Struct("<IHHQQQQQIIQ")
DEVICE_DISTRIBUTION_STRUCT = struct.Struct("<4Q32Q")
DEVICE_SCHEDULER_PREFIX_STRUCT = struct.Struct("<II4Q")
DEVICE_CHANNEL_STRUCT = struct.Struct("<14I")

DEVICE_DISTRIBUTIONS = (
    ("normal_sq_batch_size", "requests"),
    ("normal_sq_batch_ns", "us"),
    ("normal_cq_batch_size", "requests"),
    ("normal_cq_batch_ns", "us"),
    ("csgc_sq_batch_size", "requests"),
    ("csgc_sq_batch_ns", "us"),
    ("csio_poll_gap_ns", "us"),
    ("csgc_pending_wait_ns", "us"),
    ("other_pending_wait_ns", "us"),
)

CSIO_OWNERS = ("NONE", "CSGC", "OTHER")
CDMA_OWNERS = ("NONE", "CSGC", "OTHER")


def percentile(values: Iterable[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return float(ordered[rank])


def summarize_ns(values: Iterable[int]) -> dict[str, float | int]:
    data = list(values)
    return {
        "count": len(data),
        "total_ms": sum(data) / 1_000_000,
        "mean_us": statistics.fmean(data) / 1_000 if data else 0.0,
        "median_us": statistics.median(data) / 1_000 if data else 0.0,
        "p95_us": percentile(data, 0.95) / 1_000,
        "p99_us": percentile(data, 0.99) / 1_000,
        "min_us": min(data) / 1_000 if data else 0.0,
        "max_us": max(data) / 1_000 if data else 0.0,
    }


def histogram_percentile(histogram: list[int], pct: float) -> int:
    """Return the inclusive upper bound of a base-two histogram percentile."""
    total = sum(histogram)
    if total == 0:
        return 0
    target = max(1, math.ceil(total * pct))
    cumulative = 0
    for bucket, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return 0 if bucket == 0 else (1 << bucket) - 1
    return (1 << (len(histogram) - 1)) - 1


def decode_distribution(data: bytes, offset: int, histogram_unit: str) -> dict[str, Any]:
    """Decode one fixed-width device distribution and approximate percentiles."""
    values = DEVICE_DISTRIBUTION_STRUCT.unpack_from(data, offset)
    count, total, minimum, maximum = values[:4]
    histogram = list(values[4:])
    if sum(histogram) != count:
        raise ValueError("Device scheduler histogram count is inconsistent")
    result: dict[str, Any] = {
        "count": count,
        "sum": total,
        "min": minimum if count else 0,
        "max": maximum if count else 0,
        "mean": total / count if count else 0.0,
        "histogram": histogram,
        "histogram_unit": histogram_unit,
    }
    if histogram_unit == "us":
        result.update({
            "total_ms": total / 1_000_000,
            "mean_us": total / count / 1_000 if count else 0.0,
            "min_us": minimum / 1_000 if count else 0.0,
            "max_us": maximum / 1_000 if count else 0.0,
        })
    elif histogram_unit == "requests":
        result.update({
            "mean_requests": total / count if count else 0.0,
            "min_requests": minimum if count else 0,
            "max_requests": maximum if count else 0,
        })
    histogram_max = maximum // 1_000 if histogram_unit == "us" else maximum
    for label, pct in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99)):
        upper_bound = histogram_percentile(histogram, pct)
        result[f"{label}_{histogram_unit}"] = min(upper_bound, histogram_max)
    return result


def decode_host_trace(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < HOST_HEADER_SIZE:
        raise ValueError("Host trace is shorter than its fixed header")

    magic, version, header_size, gap_size, request_size, reason_count, flags = \
        struct.unpack_from("<IHHIIII", data, 0)
    if magic != HOST_MAGIC or version != HOST_TRACE_VERSION:
        raise ValueError(f"Unsupported Host trace magic/version: {magic:#x}/{version}")
    if (header_size, gap_size, request_size, reason_count) != (
            HOST_HEADER_SIZE, HOST_GAP_SIZE, HOST_REQUEST_SIZE, len(REASONS)):
        raise ValueError("Host trace ABI does not match the decoder")

    epoch, start_ns, end_ns = struct.unpack_from("<QQQ", data, 24)
    inclusive = list(struct.unpack_from(f"<{len(REASONS)}Q", data, 48))
    dominant = list(struct.unpack_from(f"<{len(REASONS)}Q", data, 160))
    counters = struct.unpack_from("<6Q", data, 272)
    counts = struct.unpack_from("<6I", data, 320)
    active = list(struct.unpack_from(f"<{len(REASONS)}I", data, 344))
    timestamp_reorders, = struct.unpack_from("<Q", data, 400)
    gap_count, request_count, gap_capacity, request_capacity, max_outstanding, outstanding = counts

    expected = header_size + gap_count * gap_size + request_count * request_size
    if len(data) != expected:
        raise ValueError(f"Host trace size mismatch: expected {expected}, got {len(data)}")

    gaps = []
    offset = header_size
    gap_prefix = struct.Struct("<QQQQ")
    reason_struct = struct.Struct(f"<{len(REASONS)}Q")
    for index in range(gap_count):
        base = offset + index * gap_size
        start, end, previous_id, next_id = gap_prefix.unpack_from(data, base)
        inclusive_reason = list(reason_struct.unpack_from(data, base + 32))
        dominant_reason = list(reason_struct.unpack_from(data, base + 32 + 8 * len(REASONS)))
        dominant_id, _ = struct.unpack_from("<II", data, base + gap_size - 8)
        gaps.append({
            "index": index,
            "start_ns": start,
            "end_ns": end,
            "previous_completion_request_id": previous_id,
            "next_submit_request_id": next_id,
            "inclusive_reason_ns": inclusive_reason,
            "dominant_reason_ns": dominant_reason,
            "dominant_reason_id": dominant_id,
            "dominant_reason": REASONS[dominant_id] if dominant_id < len(REASONS) else "INVALID",
        })

    requests = []
    offset += gap_count * gap_size
    for index in range(request_count):
        values = HOST_REQUEST_STRUCT.unpack_from(data, offset + index * request_size)
        requests.append(dict(zip((
            "request_id", "submit_start_ns", "submit_done_ns", "completion_ns",
            "section", "segment", "status", "flags"), values)))

    return {
        "header": {
            "flags": flags,
            "epoch": epoch,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "inclusive_ns": dict(zip(REASONS, inclusive)),
            "dominant_ns": dict(zip(REASONS, dominant)),
            "gap_dropped": counters[0],
            "request_dropped": counters[1],
            "transition_errors": counters[2],
            "timestamp_reorders": timestamp_reorders,
            "dirty_victims": counters[3],
            "no_victims": counters[4],
            "retries": counters[5],
            "gap_capacity": gap_capacity,
            "request_capacity": request_capacity,
            "max_outstanding": max_outstanding,
            "outstanding": outstanding,
            "active": dict(zip(REASONS, active)),
        },
        "gaps": gaps,
        "requests": requests,
    }


def decode_device_trace(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < PAGE_SIZE:
        raise ValueError("Device dump is shorter than one page")
    magic, version, header_size, page_size, flags = struct.unpack_from("<IHHII", data, 0)
    if magic != DEVICE_MAGIC or version != DEVICE_TRACE_VERSION:
        raise ValueError(f"Unsupported device trace magic/version: {magic:#x}/{version}")
    if header_size != DEVICE_HEADER_SIZE or page_size != PAGE_SIZE:
        raise ValueError("Device dump header ABI does not match the decoder")

    epoch, start_ns, freeze_ns, total_size, timeline_offset = \
        struct.unpack_from("<5Q", data, 16)
    timeline_size, timeline_count = struct.unpack_from("<II", data, 56)
    request_offset = struct.unpack_from("<Q", data, 64)[0]
    request_size, request_count, timeline_capacity, request_capacity = \
        struct.unpack_from("<4I", data, 72)
    timeline_overflow, request_overflow, sync_sequence = \
        struct.unpack_from("<3Q", data, 88)
    if timeline_size != DEVICE_TIMELINE_SIZE or request_size != DEVICE_REQUEST_SIZE:
        raise ValueError("Device record ABI does not match the decoder")
    if total_size > len(data):
        raise ValueError(f"Device dump is truncated: expected {total_size}, got {len(data)}")
    if timeline_count > timeline_capacity or request_count > request_capacity:
        raise ValueError("Device dump count exceeds advertised capacity")
    if timeline_offset < PAGE_SIZE or timeline_offset % PAGE_SIZE or \
            request_offset < timeline_offset or request_offset % PAGE_SIZE:
        raise ValueError("Device dump contains invalid region offsets")
    if timeline_offset + timeline_count * timeline_size > request_offset or \
            request_offset + request_count * request_size > total_size:
        raise ValueError("Device dump regions exceed their advertised bounds")

    state_count = len(CORE_STATES)
    core_values = struct.unpack_from(f"<{4 * state_count}Q", data, 112)
    core_state_ns = [
        list(core_values[core * state_count:(core + 1) * state_count])
        for core in range(4)
    ]
    current_states = list(struct.unpack_from("<4I", data, 592))
    transitions = list(struct.unpack_from("<4I", data, 608))
    channel_values = DEVICE_CHANNEL_STRUCT.unpack_from(data, 624)
    scheduler_offset = 624 + DEVICE_CHANNEL_SIZE
    scheduler_prefix = DEVICE_SCHEDULER_PREFIX_STRUCT.unpack_from(
        data, scheduler_offset)
    scheduler = {
        "normal_budget": scheduler_prefix[0],
        "normal_sq_yield_count": scheduler_prefix[2],
        "normal_cq_yield_count": scheduler_prefix[3],
        "csio_poll_long_count": scheduler_prefix[4],
        "csio_poll_long_ns": scheduler_prefix[5],
        "distributions": {},
    }
    distribution_offset = scheduler_offset + DEVICE_SCHEDULER_PREFIX_STRUCT.size
    for name, unit in DEVICE_DISTRIBUTIONS:
        scheduler["distributions"][name] = decode_distribution(
            data, distribution_offset, unit)
        distribution_offset += DEVICE_DISTRIBUTION_SIZE
    if distribution_offset != scheduler_offset + DEVICE_SCHEDULER_SIZE:
        raise ValueError("Device scheduler ABI size does not match the decoder")

    timelines = []
    previous_interval = -1
    for index in range(timeline_count):
        values = DEVICE_TIMELINE_STRUCT.unpack_from(
            data, timeline_offset + index * timeline_size)
        if any(state >= len(CORE_STATES) for state in values[0:4]):
            raise ValueError("Device timeline contains an invalid core state")
        if values[12] >= len(CSIO_OWNERS) or values[14] >= len(CDMA_OWNERS):
            raise ValueError("Device timeline contains an invalid owner")
        if values[17] not in (0, 4, 8):
            raise ValueError("Device timeline contains an invalid Core3 budget")
        if values[18] <= previous_interval:
            raise ValueError("Device timeline interval indexes are not increasing")
        previous_interval = values[18]
        timelines.append({
            "index": index,
            "device_interval": values[18],
            "device_ns": start_ns + values[18] * TIMELINE_INTERVAL_NS,
            "core_state": list(values[0:4]),
            "cs_queue_depth": values[4],
            "csgc_sq_depth": values[5],
            "normal_sq_depth": values[6],
            "normal_cq_depth": values[7],
            "csgc_csio_pending_depth": values[8],
            "other_csio_pending_depth": values[9],
            "csio_outstanding_depth": values[10],
            "active_workers": values[11],
            "csio_owner": values[12],
            "cdma_busy": values[13],
            "cdma_owner": values[14],
            "normal_io_pending": values[15],
            "normal_io_active": values[16],
            "core3_normal_budget": values[17],
        })

    requests = []
    for index in range(request_count):
        values = DEVICE_REQUEST_STRUCT.unpack_from(
            data, request_offset + index * request_size)
        requests.append(dict(zip((
            "request_id", "rx_cmd_ns", "rx_done_ns", "enqueue_ns", "dequeue_ns",
            "worker_start_ns", "leader_start_ns", "leader_end_ns", "slot_done_ns",
            "tx_done_ns", "status", "moves", "worker_id", "flags", "slot_id"), values)))

    return {
        "header": {
            "flags": flags,
            "epoch": epoch,
            "start_ns": start_ns,
            "freeze_ns": freeze_ns,
            "total_size": total_size,
            "timeline_count": timeline_count,
            "request_count": request_count,
            "timeline_capacity": timeline_capacity,
            "request_capacity": request_capacity,
            "timeline_overflow_count": timeline_overflow,
            "request_overflow_count": request_overflow,
            "sync_sequence": sync_sequence,
            "core_state_ns": core_state_ns,
            "core_current_state": current_states,
            "core_transitions": transitions,
            "channel": dict(zip((
                "cs_queue_depth", "csgc_sq_depth", "normal_sq_depth",
                "normal_cq_depth", "csgc_csio_pending_depth",
                "other_csio_pending_depth", "csio_outstanding_depth",
                "active_workers", "csio_owner", "cdma_busy", "cdma_owner",
                "normal_io_pending", "normal_io_active",
                "core3_normal_budget"), channel_values)),
            "scheduler": scheduler,
        },
        "timeline": timelines,
        "requests": requests,
    }


def decode_sync_page(data: bytes) -> dict[str, int]:
    if len(data) < SYNC_RECORD_SIZE:
        raise ValueError("Time synchronization page is truncated")
    values = SYNC_STRUCT.unpack_from(data)
    keys = (
        "magic", "version", "record_size", "device_time_ns", "epoch",
        "epoch_start_ns", "timeline_count", "request_count", "flags",
        "reserved", "sequence",
    )
    sample = dict(zip(keys, values))
    if sample["magic"] != SYNC_MAGIC or \
            sample["version"] != DEVICE_TRACE_VERSION or \
            sample["record_size"] != SYNC_RECORD_SIZE:
        raise ValueError("Time synchronization page has an incompatible ABI")
    sample["core3_normal_budget"] = sample["reserved"]
    return sample


def build_clock_mapping(samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_phase: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        sample["rtt_ns"] = sample["host_after_ns"] - sample["host_before_ns"]
        sample["host_mid_ns"] = (sample["host_before_ns"] + sample["host_after_ns"]) / 2
        by_phase[sample["phase"]].append(sample)

    result: dict[str, Any] = {
        "reliable": False,
        "sample_count": len(samples),
        "reason": "need pre and post synchronization samples",
    }
    if not by_phase["pre"] or not by_phase["post"]:
        return result
    epochs = {item["epoch"] for item in samples}
    if len(epochs) != 1 or not next(iter(epochs)):
        result["reason"] = "time samples do not belong to one enabled device epoch"
        return result
    pre = min(by_phase["pre"], key=lambda item: item["rtt_ns"])
    post = min(by_phase["post"], key=lambda item: item["rtt_ns"])
    device_delta = post["device_time_ns"] - pre["device_time_ns"]
    if device_delta <= 0:
        result["reason"] = "device clock did not advance"
        return result

    scale = (post["host_mid_ns"] - pre["host_mid_ns"]) / device_delta
    offset = pre["host_mid_ns"] - scale * pre["device_time_ns"]
    mapped_times = [scale * item["device_time_ns"] + offset for item in samples]
    residuals = [
        abs(mapped - item["host_mid_ns"])
        for mapped, item in zip(mapped_times, samples)
    ]
    interval_errors = [
        max(item["host_before_ns"] - mapped,
            mapped - item["host_after_ns"], 0)
        for mapped, item in zip(mapped_times, samples)
    ]
    tolerance = 2_000_000
    reliable = 0.995 <= scale <= 1.005 and \
        max(interval_errors, default=0) <= tolerance
    result.update({
        "reliable": reliable,
        "reason": "ok" if reliable else "clock fit exceeds drift or host interval limit",
        "scale": scale,
        "offset_ns": offset,
        "best_pre_rtt_ns": pre["rtt_ns"],
        "best_post_rtt_ns": post["rtt_ns"],
        "max_residual_ns": max(residuals, default=0),
        "median_residual_ns": statistics.median(residuals) if residuals else 0,
        "max_interval_error_ns": max(interval_errors, default=0),
    })
    return result


def map_device_time(mapping: dict[str, Any], device_ns: int) -> int | None:
    if not mapping.get("reliable"):
        return None
    return round(mapping["scale"] * device_ns + mapping["offset_ns"])


def classify_device_request_epoch(mapping: dict[str, Any],
                                  request: dict[str, Any],
                                  start_ns: int, end_ns: int) -> str:
    """Classify a device request relative to the frozen Host epoch."""
    mapped_rx = map_device_time(mapping, request["rx_cmd_ns"])
    mapped_tx = map_device_time(mapping, request["tx_done_ns"])
    if mapped_rx is None or mapped_tx is None:
        return "UNMAPPED"
    if mapped_tx < start_ns:
        return "BEFORE_EPOCH"
    if mapped_rx > end_ns:
        return "AFTER_EPOCH"
    if mapped_rx < start_ns and mapped_tx > end_ns:
        return "SPANS_EPOCH"
    if mapped_rx < start_ns:
        return "CROSSES_START"
    if mapped_tx > end_ns:
        return "CROSSES_END"
    return "INSIDE_EPOCH"


def coverage_at_least(requests: list[dict[str, Any]], start_ns: int,
                      end_ns: int, depth: int) -> float:
    if end_ns <= start_ns:
        return 0.0
    events: list[tuple[int, int]] = []
    for request in requests:
        if not request["flags"] & HOST_REQUEST_SUBMITTED:
            continue
        begin = max(start_ns, request["submit_done_ns"])
        end = min(end_ns, request["completion_ns"])
        if begin and end > begin:
            events.extend(((begin, 1), (end, -1)))
    events.sort()
    active = 0
    previous = start_ns
    covered = 0
    index = 0
    while index < len(events):
        timestamp = events[index][0]
        if active >= depth:
            covered += max(0, timestamp - previous)
        while index < len(events) and events[index][0] == timestamp:
            active += events[index][1]
            index += 1
        previous = timestamp
    if active >= depth:
        covered += max(0, end_ns - previous)
    return covered / (end_ns - start_ns)


def analyze_traces(host: dict[str, Any], device: dict[str, Any],
                   sync_samples: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mapping = build_clock_mapping(sync_samples)
    host_header = host["header"]
    mapped_device_freeze_ns = map_device_time(
        mapping, device["header"]["freeze_ns"])
    mapping["mapped_device_freeze_host_ns"] = mapped_device_freeze_ns
    mapping["device_freeze_after_host_epoch_ns"] = \
        None if mapped_device_freeze_ns is None else \
        mapped_device_freeze_ns - host_header["end_ns"]
    epoch_ns = host_header["end_ns"] - host_header["start_ns"]
    gap_total_ns = sum(max(0, gap["end_ns"] - gap["start_ns"])
                       for gap in host["gaps"])
    by_reason: dict[str, list[int]] = defaultdict(list)
    reason_slices: dict[str, list[int]] = defaultdict(list)
    for gap in host["gaps"]:
        by_reason[gap["dominant_reason"]].append(gap["end_ns"] - gap["start_ns"])
        for index, reason in enumerate(REASONS):
            duration = gap["dominant_reason_ns"][index]
            if duration:
                reason_slices[reason].append(duration)

    valid_host_requests = [
        item for item in host["requests"]
        if item["request_id"] and item["flags"] & HOST_REQUEST_SUBMITTED and
        item["submit_start_ns"] and item["submit_done_ns"] and item["completion_ns"]
    ]
    valid_device_requests = [
        item for item in device["requests"]
        if item["request_id"] and item["flags"] & 1 and item["tx_done_ns"]
    ]
    host_duplicate_ids = len(valid_host_requests) - len({
        item["request_id"] for item in valid_host_requests
    })
    device_duplicate_ids = len(valid_device_requests) - len({
        item["request_id"] for item in valid_device_requests
    })
    host_requests = {item["request_id"]: item for item in valid_host_requests}
    device_requests = {item["request_id"]: item for item in valid_device_requests}
    matched_ids = sorted(host_requests.keys() & device_requests.keys())
    device_epoch_class = {
        item["request_id"]: classify_device_request_epoch(
            mapping, item, host_header["start_ns"], host_header["end_ns"])
        for item in valid_device_requests
    }
    device_epoch_class_counts = Counter(device_epoch_class.values())
    unmatched_host_ids = sorted(host_requests.keys() - device_requests.keys())
    unmatched_device_ids = sorted(device_requests.keys() - host_requests.keys())
    unmatched_device_interior_ids = [
        request_id for request_id in unmatched_device_ids
        if device_epoch_class[request_id] == "INSIDE_EPOCH"
    ]
    unmatched_device_boundary_ids = [
        request_id for request_id in unmatched_device_ids
        if device_epoch_class[request_id] not in {"INSIDE_EPOCH", "UNMAPPED"}
    ]
    unmatched_device_unmapped_ids = [
        request_id for request_id in unmatched_device_ids
        if device_epoch_class[request_id] == "UNMAPPED"
    ]
    ordering_violations = 0
    matched_rows = []
    for request_id in matched_ids:
        host_request = host_requests[request_id]
        device_request = device_requests[request_id]
        mapped_rx = map_device_time(mapping, device_request["rx_cmd_ns"])
        mapped_tx = map_device_time(mapping, device_request["tx_done_ns"])
        begin_to_rx = None if mapped_rx is None else \
            mapped_rx - host_request["submit_start_ns"]
        done_to_rx = None if mapped_rx is None else \
            mapped_rx - host_request["submit_done_ns"]
        tx_to_completion = None if mapped_tx is None else host_request["completion_ns"] - mapped_tx
        device_times = [
            device_request[key] for key in (
                "rx_cmd_ns", "rx_done_ns", "enqueue_ns", "dequeue_ns",
                "worker_start_ns", "leader_start_ns", "leader_end_ns",
                "slot_done_ns", "tx_done_ns")
        ]
        device_order_valid = all(
            earlier and later and earlier <= later
            for earlier, later in zip(device_times, device_times[1:]))
        if begin_to_rx is not None and (
                begin_to_rx < -2_000_000 or tx_to_completion < -2_000_000 or
                not device_order_valid):
            ordering_violations += 1
        matched_rows.append({
            "request_id": request_id,
            "section": host_request["section"],
            "segment": host_request["segment"],
            "host_submit_start_ns": host_request["submit_start_ns"],
            "host_submit_done_ns": host_request["submit_done_ns"],
            "device_rx_cmd_ns": device_request["rx_cmd_ns"],
            "device_worker_start_ns": device_request["worker_start_ns"],
            "device_leader_start_ns": device_request["leader_start_ns"],
            "device_leader_end_ns": device_request["leader_end_ns"],
            "device_tx_done_ns": device_request["tx_done_ns"],
            "host_completion_ns": host_request["completion_ns"],
            "mapped_device_rx_host_ns": mapped_rx,
            "mapped_device_tx_host_ns": mapped_tx,
            "host_submit_start_to_device_rx_ns": begin_to_rx,
            "host_submit_done_to_device_rx_ns": done_to_rx,
            "device_tx_to_host_completion_ns": tx_to_completion,
            "device_lifecycle_order_valid": device_order_valid,
            "host_status": host_request["status"],
            "device_status": device_request["status"],
            "moves": device_request["moves"],
            "worker_id": device_request["worker_id"],
        })

    violation_ratio = ordering_violations / len(matched_ids) if matched_ids else 0.0
    mapping["matched_request_count"] = len(matched_ids)
    mapping["valid_host_request_count"] = len(valid_host_requests)
    mapping["valid_device_request_count"] = len(valid_device_requests)
    mapping["unmatched_host_request_count"] = len(unmatched_host_ids)
    mapping["unmatched_device_request_count"] = len(unmatched_device_ids)
    mapping["device_request_epoch_class_counts"] = dict(
        sorted(device_epoch_class_counts.items()))
    mapping["unmatched_host_request_ids"] = unmatched_host_ids
    mapping["unmatched_device_request_ids"] = unmatched_device_ids
    mapping["unmatched_device_interior_request_count"] = len(
        unmatched_device_interior_ids)
    mapping["unmatched_device_boundary_request_count"] = len(
        unmatched_device_boundary_ids)
    mapping["unmatched_device_unmapped_request_count"] = len(
        unmatched_device_unmapped_ids)
    mapping["unmatched_device_interior_request_ids"] = \
        unmatched_device_interior_ids
    mapping["unmatched_device_boundary_request_ids"] = \
        unmatched_device_boundary_ids
    mapping["unmatched_device_unmapped_request_ids"] = \
        unmatched_device_unmapped_ids
    mapping["host_duplicate_request_ids"] = host_duplicate_ids
    mapping["device_duplicate_request_ids"] = device_duplicate_ids
    mapping["request_ordering_violations"] = ordering_violations
    mapping["request_ordering_violation_ratio"] = violation_ratio
    if mapping.get("reliable") and (
            host_duplicate_ids or device_duplicate_ids or
            (len(matched_ids) >= 20 and violation_ratio > 0.10)):
        mapping["reliable"] = False
        mapping["reason"] = "request_id uniqueness or ordering validation failed"
        for row in matched_rows:
            row["mapped_device_rx_host_ns"] = None
            row["mapped_device_tx_host_ns"] = None
            row["host_submit_start_to_device_rx_ns"] = None
            row["host_submit_done_to_device_rx_ns"] = None
            row["device_tx_to_host_completion_ns"] = None

    timeline_rows = []
    timeline_host_ns = []
    for record in device["timeline"]:
        host_ns = map_device_time(mapping, record["device_ns"])
        if host_ns is not None:
            timeline_host_ns.append(host_ns)
        timeline_rows.append({
            "index": record["index"],
            "device_interval": record["device_interval"],
            "device_ns": record["device_ns"],
            "host_ns": host_ns,
            "core0_state": CORE_STATES[record["core_state"][0]],
            "core1_state": CORE_STATES[record["core_state"][1]],
            "core2_state": CORE_STATES[record["core_state"][2]],
            "core3_state": CORE_STATES[record["core_state"][3]],
            "csio_owner_name": CSIO_OWNERS[record["csio_owner"]],
            "cdma_owner_name": CDMA_OWNERS[record["cdma_owner"]],
            **{key: value for key, value in record.items()
               if key not in {"index", "device_interval", "device_ns", "core_state"}},
        })

    gap_rows = []
    for gap in host["gaps"]:
        row: dict[str, Any] = {
            "gap_index": gap["index"],
            "start_ns": gap["start_ns"],
            "end_ns": gap["end_ns"],
            "duration_us": (gap["end_ns"] - gap["start_ns"]) / 1_000,
            "previous_request_id": gap["previous_completion_request_id"],
            "next_request_id": gap["next_submit_request_id"],
            "dominant_reason": gap["dominant_reason"],
        }
        for index, reason in enumerate(REASONS):
            row[f"inclusive_{reason.lower()}_us"] = gap["inclusive_reason_ns"][index] / 1_000
            row[f"dominant_{reason.lower()}_us"] = gap["dominant_reason_ns"][index] / 1_000
        if mapping.get("reliable"):
            begin = bisect.bisect_left(timeline_host_ns, gap["start_ns"])
            end = bisect.bisect_right(timeline_host_ns, gap["end_ns"])
            records = device["timeline"][begin:end]
            row["device_samples"] = len(records)
            expected_samples = max(1, math.ceil(
                (gap["end_ns"] - gap["start_ns"]) / TIMELINE_INTERVAL_NS))
            row["device_sample_coverage_pct"] = min(
                100.0, 100.0 * len(records) / expected_samples)
            if records:
                for core in range(4):
                    states = Counter(record["core_state"][core] for record in records)
                    for state_id, count in states.items():
                        row[f"core{core}_{CORE_STATES[state_id].lower()}_pct"] = \
                            100.0 * count / len(records)
                row["cs_queue_empty_pct"] = 100.0 * sum(
                    record["cs_queue_depth"] == 0 for record in records) / len(records)
                row["csio_empty_pct"] = 100.0 * sum(
                    record["csgc_csio_pending_depth"] == 0 and
                    record["other_csio_pending_depth"] == 0 and
                    record["csio_outstanding_depth"] == 0 for record in records) / len(records)
                row["cdma_idle_pct"] = 100.0 * sum(
                    not record["cdma_busy"] for record in records) / len(records)
                row["normal_io_pending_pct"] = 100.0 * sum(
                    record["normal_io_pending"] for record in records) / len(records)
                row["normal_io_active_pct"] = 100.0 * sum(
                    record["normal_io_active"] for record in records) / len(records)
        gap_rows.append(row)

    def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="ascii")
            return
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", newline="", encoding="ascii") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    write_csv(output_dir / "csgc-supply-gaps.csv", gap_rows)
    write_csv(output_dir / "csgc-device-timeline.csv", timeline_rows)
    write_csv(output_dir / "csgc-matched-requests.csv", matched_rows)

    timeline_count = len(device["timeline"])
    timeline_summary = {
        "sample_count": timeline_count,
        "normal_sq_nonempty_pct": 100.0 * sum(
            record["normal_sq_depth"] > 0 for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "normal_cq_nonempty_pct": 100.0 * sum(
            record["normal_cq_depth"] > 0 for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "csgc_sq_nonempty_pct": 100.0 * sum(
            record["csgc_sq_depth"] > 0 for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "csgc_csio_pending_pct": 100.0 * sum(
            record["csgc_csio_pending_depth"] > 0 for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "other_csio_pending_pct": 100.0 * sum(
            record["other_csio_pending_depth"] > 0 for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "cdma_busy_pct": 100.0 * sum(
            record["cdma_busy"] for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "normal_io_pending_pct": 100.0 * sum(
            record["normal_io_pending"] for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "normal_io_active_pct": 100.0 * sum(
            record["normal_io_active"] for record in device["timeline"]
        ) / timeline_count if timeline_count else 0.0,
        "core3_budget_values": sorted({
            record["core3_normal_budget"] for record in device["timeline"]
        }),
        "core3_state_pct": {},
        "csio_owner_pct": {},
        "cdma_owner_pct": {},
    }
    if timeline_count:
        core3_counts = Counter(record["core_state"][3] for record in device["timeline"])
        timeline_summary["core3_state_pct"] = {
            CORE_STATES[state]: 100.0 * count / timeline_count
            for state, count in sorted(core3_counts.items())
        }
        csio_counts = Counter(record["csio_owner"] for record in device["timeline"])
        timeline_summary["csio_owner_pct"] = {
            CSIO_OWNERS[owner]: 100.0 * count / timeline_count
            for owner, count in sorted(csio_counts.items())
        }
        cdma_counts = Counter(record["cdma_owner"] for record in device["timeline"])
        timeline_summary["cdma_owner_pct"] = {
            CDMA_OWNERS[owner]: 100.0 * count / timeline_count
            for owner, count in sorted(cdma_counts.items())
        }

    lifecycle_phases = {
        "rx_payload_ns": [],
        "enqueue_wait_ns": [],
        "worker_start_wait_ns": [],
        "worker_to_leader_ns": [],
        "leader_ns": [],
        "result_pack_ns": [],
        "result_tx_ns": [],
        "total_ns": [],
    }
    for request in valid_device_requests:
        phase_bounds = (
            ("rx_payload_ns", "rx_cmd_ns", "rx_done_ns"),
            ("enqueue_wait_ns", "enqueue_ns", "dequeue_ns"),
            ("worker_start_wait_ns", "dequeue_ns", "worker_start_ns"),
            ("worker_to_leader_ns", "worker_start_ns", "leader_start_ns"),
            ("leader_ns", "leader_start_ns", "leader_end_ns"),
            ("result_pack_ns", "leader_end_ns", "slot_done_ns"),
            ("result_tx_ns", "slot_done_ns", "tx_done_ns"),
            ("total_ns", "rx_cmd_ns", "tx_done_ns"),
        )
        for phase, begin_key, end_key in phase_bounds:
            begin = request[begin_key]
            end = request[end_key]
            if begin and end >= begin:
                lifecycle_phases[phase].append(end - begin)

    result = {
        "host": {
            "epoch_ns": epoch_ns,
            "gap_total_ns": gap_total_ns,
            "supply_coverage_pct": 100.0 * (1.0 - gap_total_ns / epoch_ns) if epoch_ns > 0 else 0.0,
            "outstanding_ge_2_coverage_pct": 100.0 * coverage_at_least(
                host["requests"], host_header["start_ns"], host_header["end_ns"], 2),
            "max_outstanding": host_header["max_outstanding"],
            "gap_count": len(host["gaps"]),
            "request_count": len(host["requests"]),
            "gap_dropped": host_header["gap_dropped"],
            "request_dropped": host_header["request_dropped"],
            "transition_errors": host_header["transition_errors"],
            "timestamp_reorders": host_header["timestamp_reorders"],
            "gap_by_dominant_reason": {
                reason: summarize_ns(values) for reason, values in by_reason.items()
            },
            "dominant_reason_slices": {
                reason: summarize_ns(reason_slices.get(reason, []))
                for reason in REASONS
            },
            "inclusive_reason_total_ns": host_header["inclusive_ns"],
            "dominant_reason_total_ns": host_header["dominant_ns"],
        },
        "device": {
            "timeline_count": timeline_count,
            "request_count": len(device["requests"]),
            "timeline_overflow_count": device["header"]["timeline_overflow_count"],
            "request_overflow_count": device["header"]["request_overflow_count"],
            "core_state_ns": {
                f"core{core}": dict(zip(CORE_STATES, values))
                for core, values in enumerate(device["header"]["core_state_ns"])
            },
            "channel_at_freeze": device["header"]["channel"],
            "scheduler": device["header"]["scheduler"],
            "timeline_summary": timeline_summary,
            "request_lifecycle": {
                phase: summarize_ns(values)
                for phase, values in lifecycle_phases.items()
            },
        },
        "clock_mapping": mapping,
        "joint_attribution_emitted": bool(mapping.get("reliable")),
    }
    (output_dir / "csgc-supply-analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return result
