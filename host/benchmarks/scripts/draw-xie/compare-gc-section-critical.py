#!/usr/bin/env python3

"""Compare section critical paths for matched mCSGC8t diagnostic runs."""

import argparse
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Optional, Tuple


START_MARKER = "MEASURED_FIO_START"
END_MARKER = "MEASURED_FIO_END"
KV_RE = re.compile(r"([A-Za-z0-9_]+)=([^\s,]+)")
FIO_BW_RE = re.compile(r"WRITE: bw=([0-9.]+)([KMG]iB/s)")
FIO_WRITE_BW_BYTES_RE = re.compile(
    r'"write"\s*:\s*\{.*?"bw_bytes"\s*:\s*([0-9.]+)', re.DOTALL
)
PRE_STAGE_KEYS = (
    "pre_build_valid_offsets_us",
    "pre_sum_us",
    "pre_node_list_us",
    "pre_inode_lock_us",
    "pre_data_lock_us",
    "pre_dirty_source_scan_us",
    "pre_cp_rwsem_lock_us",
    "pre_node_pages_lock_us",
    "pre_get_valid_blocks_us",
    "pre_check_data_validness_us",
    "pre_prepare_move_plan_us",
    "pre_preallocate_us",
    "pre_finalize_move_plan_us",
    "pre_tail_us",
)
METRICS = (
    "pre_span_us",
    "pre_tail_us",
    "submit_span_us",
    "ssd_drain_us",
    "last_completion_from_start_us",
    "post_drain_us",
    "section_us",
    "ssd_busy_union_us",
    "ssd_idle_us",
    "supply_coverage_permille",
    "peak_outstanding",
    "ns_per_move",
)


def parse_args() -> argparse.Namespace:
    """Parse control/treatment result directories and report path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--treatment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--move-bucket-blocks", type=int, default=256)
    return parser.parse_args()


def parse_kv(line: str) -> Dict[str, str]:
    """Extract whitespace-delimited key/value fields."""
    return {match.group(1): match.group(2) for match in KV_RE.finditer(line)}


def int_value(values: Dict[str, str], key: str) -> Optional[int]:
    """Return one decimal integer field when present."""
    try:
        return int(values[key], 10)
    except (KeyError, ValueError):
        return None


def measured_window(lines: List[str]) -> List[str]:
    """Return the latest complete measured-fio window."""
    start: Optional[int] = None
    complete: Optional[Tuple[int, int]] = None
    for index, line in enumerate(lines):
        if START_MARKER in line:
            start = index
        if END_MARKER in line and start is not None:
            complete = (start, index)
            start = None
    if complete is None:
        raise ValueError("no complete measured fio window")
    return lines[complete[0] : complete[1] + 1]


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


def percentile(values: List[float], percentile_value: float) -> float:
    """Calculate one interpolated percentile."""
    ordered = sorted(values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def peak_outstanding(records: List[Dict[str, int]]) -> int:
    """Calculate peak Host-visible CSGC requests for one section."""
    events: List[Tuple[int, int]] = []
    for record in records:
        events.append((record["pre_ready_ns"], 1))
        events.append((record["ssd_completion_ns"], -1))
    current = 0
    peak = 0
    for _, delta in sorted(events, key=lambda event: (event[0], event[1])):
        current += delta
        peak = max(peak, current)
    return peak


def busy_union_ns(records: List[Dict[str, int]], start_ns: int, end_ns: int) -> int:
    """Calculate the union of Host-visible SSD request intervals."""
    intervals = sorted(
        (max(start_ns, record["pre_ready_ns"]),
         min(end_ns, record["ssd_completion_ns"]))
        for record in records
    )
    total = 0
    current_start = 0
    current_end = 0
    for interval_start, interval_end in intervals:
        if interval_end < interval_start:
            continue
        if current_end == 0 or interval_start > current_end:
            if current_end:
                total += current_end - current_start
            current_start = interval_start
            current_end = interval_end
        else:
            current_end = max(current_end, interval_end)
    if current_end:
        total += current_end - current_start
    return total


def parse_run(run_dir: Path, bucket_blocks: int) -> Dict[str, object]:
    """Build complete section samples from one diagnostic result."""
    lines = measured_window(
        (run_dir / "external-dmesg.log")
        .read_text(errors="replace")
        .splitlines()
    )
    sections: Dict[Tuple[int, int], Dict[str, int]] = {}
    timelines: DefaultDict[Tuple[int, int], List[Dict[str, int]]] = defaultdict(list)
    pre_details: DefaultDict[Tuple[int, int], Dict[str, int]] = defaultdict(dict)

    for line in lines:
        if "MCSGC_SECTION " in line:
            values = parse_kv(line)
            section = int_value(values, "section")
            start_ns = int_value(values, "start_ns")
            end_ns = int_value(values, "end_ns")
            submitted = int_value(values, "submitted")
            if None not in (section, start_ns, end_ns, submitted):
                sections[(section, start_ns)] = {
                    "start_ns": start_ns,
                    "end_ns": end_ns,
                    "submitted": submitted,
                }
            continue

        if "MCSGC_SEGMENT_TIMELINE " in line:
            values = parse_kv(line)
            keys = (
                "section", "segno", "req_idx", "valid", "ret", "moves",
                "section_start_ns", "segment_start_ns", "pre_start_ns",
                "pre_ready_ns", "ssd_completion_ns", "post_done_ns",
            )
            parsed = {key: int_value(values, key) for key in keys}
            if any(value is None for value in parsed.values()):
                continue
            record = {key: value for key, value in parsed.items() if value is not None}
            timelines[(record["section"], record["section_start_ns"])].append(record)
            continue

        if "MCSGC_SEGMENT_PRE_DETAIL " in line or "MCSGC_SEGMENT_MOVE_DETAIL " in line:
            values = parse_kv(line)
            segno = int_value(values, "segno")
            start_ns = int_value(values, "start_ns")
            if segno is None or start_ns is None:
                continue
            for stage in PRE_STAGE_KEYS:
                value = int_value(values, stage)
                if value is not None and value >= 0:
                    pre_details[(segno, start_ns)][stage] = value

    samples: List[Dict[str, object]] = []
    incomplete_sections = 0
    invalid_records = 0
    for section_key, section in sections.items():
        records = timelines.get(section_key, [])
        valid_records = [
            record
            for record in records
            if record["valid"] == 1 and record["ret"] == 0
        ]
        invalid_records += len(records) - len(valid_records)
        if not valid_records or len(valid_records) != section["submitted"]:
            incomplete_sections += 1
            continue

        start_ns = section["start_ns"]
        end_ns = section["end_ns"]
        first_pre_start = min(record["pre_start_ns"] for record in valid_records)
        last_pre = max(valid_records, key=lambda record: record["pre_ready_ns"])
        first_submit = min(record["pre_ready_ns"] for record in valid_records)
        last_submit = max(record["pre_ready_ns"] for record in valid_records)
        last_completion = max(record["ssd_completion_ns"] for record in valid_records)
        last_post = max(record["post_done_ns"] for record in valid_records)
        total_moves = sum(record["moves"] for record in valid_records)
        section_ns = end_ns - start_ns
        busy_ns = busy_union_ns(valid_records, start_ns, end_ns)
        detail = pre_details.get((last_pre["segno"], last_pre["segment_start_ns"]), {})
        dominant_stage = "unknown"
        dominant_us = -1
        if detail:
            dominant_stage, dominant_us = max(detail.items(), key=lambda item: item[1])

        metrics = {
            "pre_span_us": (last_pre["pre_ready_ns"] - first_pre_start) / 1000.0,
            "pre_tail_us": (last_pre["pre_ready_ns"] - start_ns) / 1000.0,
            "submit_span_us": (last_submit - first_submit) / 1000.0,
            "ssd_drain_us": (last_completion - first_submit) / 1000.0,
            "last_completion_from_start_us": (last_completion - start_ns) / 1000.0,
            "post_drain_us": (end_ns - last_completion) / 1000.0,
            "section_us": section_ns / 1000.0,
            "ssd_busy_union_us": busy_ns / 1000.0,
            "ssd_idle_us": max(0, section_ns - busy_ns) / 1000.0,
            "supply_coverage_permille": busy_ns * 1000.0 / section_ns,
            "peak_outstanding": float(peak_outstanding(valid_records)),
            "ns_per_move": section_ns / total_moves if total_moves else math.nan,
        }
        samples.append(
            {
                "active_segments": len(valid_records),
                "total_moves": total_moves,
                "bucket": (len(valid_records), total_moves // bucket_blocks),
                "last_pre_stage": dominant_stage,
                "last_pre_stage_us": dominant_us,
                "metrics": metrics,
            }
        )

    return {
        "run_dir": run_dir,
        "fio_bw_mib_s": parse_fio_bandwidth(run_dir),
        "samples": samples,
        "timeline_records": sum(len(records) for records in timelines.values()),
        "invalid_records": invalid_records,
        "incomplete_sections": incomplete_sections,
    }


def values_for(run: Dict[str, object], metric: str) -> List[float]:
    """Return finite values for one section metric."""
    values = [sample["metrics"][metric] for sample in run["samples"]]
    return [float(value) for value in values if math.isfinite(float(value))]


def percent_delta(control: float, treatment: float) -> float:
    """Return treatment minus control as a percentage of control."""
    return 0.0 if control == 0 else (treatment - control) * 100.0 / control


def matched_means(
    control: Dict[str, object],
    treatment: Dict[str, object],
    metric: str,
) -> Tuple[float, float, int]:
    """Compare bucket means with equal weights for common physical work."""
    grouped: List[DefaultDict[Tuple[int, int], List[float]]] = [
        defaultdict(list), defaultdict(list)
    ]
    for group, run in zip(grouped, (control, treatment)):
        for sample in run["samples"]:
            value = float(sample["metrics"][metric])
            if math.isfinite(value):
                group[sample["bucket"]].append(value)

    control_sum = 0.0
    treatment_sum = 0.0
    matched = 0
    for bucket in grouped[0].keys() & grouped[1].keys():
        weight = min(len(grouped[0][bucket]), len(grouped[1][bucket]))
        control_sum += statistics.fmean(grouped[0][bucket]) * weight
        treatment_sum += statistics.fmean(grouped[1][bucket]) * weight
        matched += weight
    if matched == 0:
        return math.nan, math.nan, 0
    return control_sum / matched, treatment_sum / matched, matched


def emit_stage_distribution(label: str, run: Dict[str, object]) -> List[str]:
    """Format dominant PRE stages for the last-ready worker."""
    counts = Counter(sample["last_pre_stage"] for sample in run["samples"])
    total = sum(counts.values())
    output = [f"[{label}_last_pre_stage]"]
    for stage, count in counts.most_common():
        output.append(
            f"{stage}\tcount={count}\tfraction_pct={count * 100.0 / total:.3f}"
        )
    return output


def main() -> int:
    """Write overall and physically matched section comparisons."""
    args = parse_args()
    if args.move_bucket_blocks <= 0:
        raise SystemExit("ERROR: --move-bucket-blocks must be positive")
    control = parse_run(args.control.resolve(), args.move_bucket_blocks)
    treatment = parse_run(args.treatment.resolve(), args.move_bucket_blocks)

    output = [
        "mCSGC8t section critical-path comparison",
        f"move_bucket_blocks={args.move_bucket_blocks}",
        f"control_dir={control['run_dir']}",
        f"treatment_dir={treatment['run_dir']}",
        f"control_fio_bw_mib_s={control['fio_bw_mib_s']:.3f}",
        f"treatment_fio_bw_mib_s={treatment['fio_bw_mib_s']:.3f}",
        f"fio_delta_pct={percent_delta(control['fio_bw_mib_s'], treatment['fio_bw_mib_s']):.3f}",
        f"control_sections={len(control['samples'])}",
        f"treatment_sections={len(treatment['samples'])}",
        f"control_timeline_records={control['timeline_records']}",
        f"treatment_timeline_records={treatment['timeline_records']}",
        f"control_invalid_records={control['invalid_records']}",
        f"treatment_invalid_records={treatment['invalid_records']}",
        f"control_incomplete_sections={control['incomplete_sections']}",
        f"treatment_incomplete_sections={treatment['incomplete_sections']}",
        "",
        "metric\tcontrol_mean\ttreatment_mean\tdelta_pct\tcontrol_median\t"
        "treatment_median\tcontrol_p95\ttreatment_p95",
    ]
    for metric in METRICS:
        control_values = values_for(control, metric)
        treatment_values = values_for(treatment, metric)
        control_mean = statistics.fmean(control_values)
        treatment_mean = statistics.fmean(treatment_values)
        output.append(
            f"{metric}\t{control_mean:.3f}\t{treatment_mean:.3f}\t"
            f"{percent_delta(control_mean, treatment_mean):.3f}\t"
            f"{statistics.median(control_values):.3f}\t"
            f"{statistics.median(treatment_values):.3f}\t"
            f"{percentile(control_values, 95):.3f}\t"
            f"{percentile(treatment_values, 95):.3f}"
        )

    output.extend(["", "[matched_work_means]", "metric\tcontrol\ttreatment\tdelta_pct\tmatched_sections"])
    for metric in METRICS:
        control_mean, treatment_mean, matched = matched_means(
            control, treatment, metric
        )
        output.append(
            f"{metric}\t{control_mean:.3f}\t{treatment_mean:.3f}\t"
            f"{percent_delta(control_mean, treatment_mean):.3f}\t{matched}"
        )

    output.append("")
    output.extend(emit_stage_distribution("control", control))
    output.append("")
    output.extend(emit_stage_distribution("treatment", treatment))
    output.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(output), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
