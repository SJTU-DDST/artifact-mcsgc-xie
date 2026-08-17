#!/usr/bin/env python3

"""Build multi-level GC breakdown tables from the August 12 diagnostic runs."""

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT = (
    ROOT
    / "doc_and_notes"
    / "exp_doc_and_notes"
    / "gc-breakdown-four-config-comparison-20260817-generated-en.md"
)

RUN_SPECS = (
    (
        "bigfile",
        "ORI",
        "host/benchmarks/scripts/outputs-ori-ssd1t/20260812_174901/"
        "fio_randwrite_s8_0.86_random",
    ),
    (
        "bigfile",
        "Original CSGC",
        "host/benchmarks/scripts/outputs-diagnostic-original-csgc-ssd1t/"
        "20260812_155526/fio_randwrite_s8_0.86_random",
    ),
    (
        "bigfile",
        "mCSGC8t no-pipeline",
        "host/benchmarks/scripts/outputs-diagnostic-mcsgc8t-nopipeline-csgc-ssd1t/"
        "20260812_221647/fio_randwrite_s8_0.86_random",
    ),
    (
        "bigfile",
        "mCSGC8t pipeline",
        "host/benchmarks/scripts/outputs-diagnostic-mcsgc8t-pipeline-csgc-ssd1t/"
        "20260812_224820/fio_randwrite_s8_0.86_random",
    ),
    (
        "smallfile",
        "ORI",
        "host/benchmarks/scripts/outputs-ori-ssd1t/20260812_192859/"
        "fio_rw16t26336file_s8_0.86_random",
    ),
    (
        "smallfile",
        "Original CSGC",
        "host/benchmarks/scripts/outputs-diagnostic-original-csgc-ssd1t/"
        "20260812_160657/fio_rw16t26336file_s8_0.86_random",
    ),
    (
        "smallfile",
        "mCSGC8t no-pipeline",
        "host/benchmarks/scripts/outputs-diagnostic-mcsgc8t-nopipeline-csgc-ssd1t/"
        "20260812_222648/fio_rw16t26336file_s8_0.86_random",
    ),
    (
        "smallfile",
        "mCSGC8t pipeline",
        "host/benchmarks/scripts/outputs-diagnostic-mcsgc8t-pipeline-csgc-ssd1t/"
        "20260812_225914/fio_rw16t26336file_s8_0.86_random",
    ),
)

CONFIG_ORDER = (
    "ORI",
    "Original CSGC",
    "mCSGC8t no-pipeline",
    "mCSGC8t pipeline",
)

METRIC_RE = re.compile(r"^([A-Za-z0-9_]+):\s+(.*)$")
FIELD_RE = re.compile(r"([A-Za-z0-9_]+)=(-?\d+(?:\.\d+)?)")
FIO_RE = re.compile(
    r"WRITE:\s+bw=(\d+(?:\.\d+)?)([KMG]iB/s).*?"
    r"io=(\d+(?:\.\d+)?)([KMG]iB).*?run=(\d+)-(\d+)msec"
)


@dataclass(frozen=True)
class Run:
    """Hold one workload/configuration diagnostic result."""

    workload: str
    label: str
    directory: Path
    header: Dict[str, float]
    metrics: Dict[str, Dict[str, float]]
    fio_bw_mib_s: float
    fio_io_gib: float
    fio_run_s: float

    def field(self, metric: str, name: str, default: float = math.nan) -> float:
        """Return one parsed metric field."""
        return self.metrics.get(metric, {}).get(name, default)

    def measured_s(self) -> float:
        """Return the marker-delimited workload duration."""
        return self.header["measured_duration_us"] / 1_000_000.0


def parse_args() -> argparse.Namespace:
    """Parse output selection."""
    parser = argparse.ArgumentParser(
        description="Build comparison tables for the paired GC diagnostic matrix."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Markdown output path",
    )
    return parser.parse_args()


def convert_to_mib(value: float, unit: str) -> float:
    """Convert one binary bandwidth or size value to MiB units."""
    factors = {"KiB": 1.0 / 1024.0, "MiB": 1.0, "GiB": 1024.0}
    base = unit.removesuffix("/s")
    return value * factors[base]


def parse_summary(path: Path) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """Parse scalar headers and distribution records from one summary."""
    header: Dict[str, float] = {}
    metrics: Dict[str, Dict[str, float]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = METRIC_RE.match(line)
        if match:
            fields = {
                item.group(1): float(item.group(2))
                for item in FIELD_RE.finditer(match.group(2))
            }
            if fields:
                metrics[match.group(1)] = fields
            continue
        if "=" not in line or line.startswith("input="):
            continue
        key, raw_value = line.split("=", 1)
        try:
            header[key] = float(raw_value)
        except ValueError:
            continue
    if "measured_duration_us" not in header:
        raise ValueError(f"missing measured_duration_us in {path}")
    return header, metrics


def parse_fio(path: Path) -> Tuple[float, float, float]:
    """Parse the final fio group bandwidth, amount, and runtime."""
    matches = list(FIO_RE.finditer(path.read_text(encoding="utf-8", errors="replace")))
    if not matches:
        raise ValueError(f"no final WRITE summary found in {path}")
    match = matches[-1]
    bandwidth = convert_to_mib(float(match.group(1)), match.group(2))
    io_gib = convert_to_mib(float(match.group(3)), match.group(4)) / 1024.0
    runtime = max(float(match.group(5)), float(match.group(6))) / 1000.0
    return bandwidth, io_gib, runtime


def load_runs() -> List[Run]:
    """Load and validate all paired diagnostic runs."""
    runs: List[Run] = []
    for workload, label, relative_directory in RUN_SPECS:
        directory = ROOT / relative_directory
        summary = directory / "gc-breakdown-diagnostic-result.txt"
        fio_log = directory / "fio.log"
        if not summary.is_file() or not fio_log.is_file():
            raise FileNotFoundError(f"incomplete run directory: {directory}")
        header, metrics = parse_summary(summary)
        bandwidth, io_gib, runtime = parse_fio(fio_log)
        runs.append(
            Run(
                workload=workload,
                label=label,
                directory=directory,
                header=header,
                metrics=metrics,
                fio_bw_mib_s=bandwidth,
                fio_io_gib=io_gib,
                fio_run_s=runtime,
            )
        )
    validate_runs(runs)
    return runs


def validate_runs(runs: Sequence[Run]) -> None:
    """Fail when the diagnostic matrix violates core accounting invariants."""
    if len(runs) != len(RUN_SPECS):
        raise ValueError(f"expected {len(RUN_SPECS)} runs, found {len(runs)}")
    for run in runs:
        calls = run.field("gc_call_duration_us", "count")
        work = run.field("gc_with_work_duration_us", "count")
        no_work = run.field("gc_no_work_duration_us", "count")
        if not all(finite(value) for value in (calls, work, no_work)):
            raise ValueError(f"missing GC call accounting in {run.directory}")
        if int(calls) != int(work + no_work):
            raise ValueError(f"GC outcome count mismatch in {run.directory}")

        duration = run.field("gc_call_duration_us", "sum")
        accounted = sum(
            run.field(metric, "sum", 0.0)
            for metric in (
                "gc_call_checkpoint_us",
                "gc_call_victim_select_us",
                "gc_call_collector_us",
                "gc_call_other_us",
            )
        )
        if abs(duration - accounted) > max(10_000.0, duration * 0.0001):
            raise ValueError(f"GC phase accounting mismatch in {run.directory}")
        if not finite(sections(run, "data")) or sections(run, "data") <= 0:
            raise ValueError(f"missing data section count in {run.directory}")


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    """Format a GitHub Markdown table."""
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    output.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(output)


def finite(value: float) -> bool:
    """Return whether a parsed value is available."""
    return not math.isnan(value)


def fmt(value: float, digits: int = 2) -> str:
    """Format one scalar while preserving unavailable values."""
    return f"{value:.{digits}f}" if finite(value) else "N/A"


def fmt_int(value: float) -> str:
    """Format one integer-valued metric."""
    return f"{int(round(value)):,}" if finite(value) else "N/A"


def fmt_ms(value_us: float, digits: int = 3) -> str:
    """Format microseconds as milliseconds."""
    return fmt(value_us / 1000.0, digits) if finite(value_us) else "N/A"


def distribution_cell(run: Run, metric: str) -> str:
    """Format mean, median, and p95 in milliseconds."""
    mean = run.field(metric, "mean")
    median = run.field(metric, "median")
    p95 = run.field(metric, "p95")
    if not finite(mean):
        return "N/A"
    return f"{fmt_ms(mean)} / {fmt_ms(median)} / {fmt_ms(p95)}"


def mean_share_cell(run: Run, metric: str, denominator: str) -> str:
    """Format a mean duration and its share of another mean duration."""
    value = run.field(metric, "mean")
    total = run.field(denominator, "mean")
    if not finite(value) or not finite(total) or total == 0:
        return "N/A"
    return f"{fmt_ms(value)} ({100.0 * value / total:.1f}%)"


def summed_phase_cell(run: Run, metric: str) -> str:
    """Format cumulative seconds and shares of window and GC call time."""
    value_us = run.field(metric, "sum")
    gc_us = run.field("gc_call_duration_us", "sum")
    if not finite(value_us) or not finite(gc_us) or gc_us == 0:
        return "N/A"
    seconds = value_us / 1_000_000.0
    window_share = 100.0 * seconds / run.measured_s()
    gc_share = 100.0 * value_us / gc_us
    return f"{seconds:.2f} s ({window_share:.1f}% win, {gc_share:.1f}% GC)"


def sections(run: Run, kind: str) -> float:
    """Return the number of processed data or node sections."""
    if kind == "data":
        key = (
            "collector_origc_data_sections"
            if run.label == "ORI"
            else "collector_csgc_data_sections"
        )
    else:
        key = "collector_origc_node_sections"
    count = run.field(key, "sum")
    if finite(count):
        return count

    # The original CSGC trace emits one collector record per section but does
    # not carry the newer explicit section-count field.
    if kind == "data" and run.label == "Original CSGC":
        return run.field("collector_csgc_data_duration_us", "count")
    if kind == "node" and run.label == "Original CSGC":
        return run.field("collector_origc_node_duration_us", "count")
    return math.nan


def weighted_collector_per_section_us(run: Run) -> float:
    """Normalize cumulative data collector time by processed sections."""
    if run.label == "ORI":
        duration_key = "collector_origc_data_duration_us"
    else:
        duration_key = "collector_csgc_data_duration_us"
    duration = run.field(duration_key, "sum")
    count = sections(run, "data")
    if not finite(duration) or not finite(count) or count == 0:
        return math.nan
    return duration / count


def runs_for_workload(runs: Sequence[Run], workload: str) -> List[Run]:
    """Return workload runs in the fixed configuration order."""
    by_label = {run.label: run for run in runs if run.workload == workload}
    return [by_label[label] for label in CONFIG_ORDER]


def source_table(runs: Sequence[Run]) -> str:
    """Build the run provenance table."""
    rows = []
    for run in runs:
        relative = run.directory.relative_to(ROOT)
        rows.append(
            (
                run.workload,
                run.label,
                f"[{relative.name}]({run.directory.as_posix()})",
                f"{run.measured_s():.3f}",
            )
        )
    return markdown_table(
        ("Workload", "Configuration", "Run directory", "Marker window (s)"),
        rows,
    )


def endpoint_table(group: Sequence[Run]) -> str:
    """Build the end-to-end and GC-pressure table."""
    ori_bw = group[0].fio_bw_mib_s
    rows = []
    for run in group:
        gc_sum = run.field("gc_call_duration_us", "sum") / 1_000_000.0
        data_count = sections(run, "data")
        rows.append(
            (
                run.label,
                fmt(run.fio_bw_mib_s, 1),
                fmt(run.fio_bw_mib_s / ori_bw, 3) + "x",
                fmt(run.fio_io_gib, 1),
                fmt(run.measured_s(), 3),
                fmt_int(run.field("gc_call_duration_us", "count")),
                fmt_int(run.field("gc_with_work_duration_us", "count")),
                fmt_int(run.field("gc_no_work_duration_us", "count")),
                fmt_int(data_count),
                fmt_int(sections(run, "node")),
                f"{gc_sum:.2f} ({100.0 * gc_sum / run.measured_s():.1f}%)",
                fmt(data_count / run.measured_s(), 2),
            )
        )
    return markdown_table(
        (
            "Configuration",
            "fio MiB/s",
            "vs ORI",
            "fio GiB",
            "Window s",
            "GC calls",
            "With work",
            "No work",
            "Data sections",
            "Node sections",
            "Sum GC s (% window)",
            "Data sec/s",
        ),
        rows,
    )


def gc_phase_table(group: Sequence[Run]) -> str:
    """Build the additive top-level f2fs_gc phase table."""
    phases = (
        ("Checkpoint", "gc_call_checkpoint_us"),
        ("Victim selection", "gc_call_victim_select_us"),
        ("Collector", "gc_call_collector_us"),
        ("Other", "gc_call_other_us"),
        ("Complete f2fs_gc", "gc_call_duration_us"),
    )
    rows = []
    for label, metric in phases:
        rows.append((label,) + tuple(summed_phase_cell(run, metric) for run in group))
    return markdown_table(("Phase",) + CONFIG_ORDER, rows)


def gc_outcome_table(group: Sequence[Run]) -> str:
    """Build the alternative with-work/no-work call decomposition."""
    outcomes = (
        ("With collector", "gc_with_work_duration_us"),
        ("No collector/work", "gc_no_work_duration_us"),
    )
    rows = []
    for label, metric in outcomes:
        row = [label]
        for run in group:
            count = run.field(metric, "count")
            total_s = run.field(metric, "sum") / 1_000_000.0
            mean_ms = run.field(metric, "mean") / 1000.0
            row.append(f"{fmt_int(count)} / {total_s:.2f} s / {mean_ms:.3f} ms")
        rows.append(tuple(row))
    return markdown_table(("Outcome",) + CONFIG_ORDER, rows)


def section_table(group: Sequence[Run]) -> str:
    """Build section-level means and distributions."""
    ori_collector = weighted_collector_per_section_us(group[0])
    rows = []
    for run in group:
        collector = weighted_collector_per_section_us(run)
        section_collector = run.field("comparable_section_collector_us", "mean")
        section_mean = run.field("comparable_section_section_gc_time_us", "mean")
        section_median = run.field("comparable_section_section_gc_time_us", "median")
        section_p95 = run.field("comparable_section_section_gc_time_us", "p95")
        section_p99 = run.field("comparable_section_section_gc_time_us", "p99")
        sync_mean = run.field("comparable_section_section_sync_us", "mean")
        pipeline_mean = run.field("pipeline_wall_per_section_us", "mean")
        primary = collector if run.label == "ORI" else section_mean
        speedup = ori_collector / primary if finite(primary) and primary > 0 else math.nan
        rows.append(
            (
                run.label,
                fmt_int(sections(run, "data")),
                fmt_ms(collector),
                fmt_ms(section_collector),
                fmt_ms(section_mean),
                fmt_ms(section_median),
                fmt_ms(section_p95),
                fmt_ms(section_p99),
                fmt_ms(sync_mean),
                fmt_ms(pipeline_mean),
                fmt(speedup, 2) + "x" if finite(speedup) else "N/A",
            )
        )
    return markdown_table(
        (
            "Configuration",
            "Data sections",
            "Top-level data collector/section ms",
            "Section collector body mean ms",
            "Section wall mean ms",
            "Section wall median ms",
            "Section wall p95 ms",
            "Section wall p99 ms",
            "Pre-section sync mean ms",
            "Pipeline wall/section ms",
            "ORI collector / section wall",
        ),
        rows,
    )


def segment_coarse_table(group: Sequence[Run]) -> str:
    """Build the additive CSGC segment lifetime decomposition."""
    phases = (
        ("Complete segment lifetime", "comparable_segment_total_us"),
        ("PRE work", "comparable_pre_work_total_us"),
        ("Host-visible SSD stage", "comparable_ssd_total_us"),
        ("POST queue wait", "comparable_post_queue_delay_us"),
        ("POST work", "comparable_post_total_work_us"),
    )
    csgc_runs = group[1:]
    rows = []
    for label, metric in phases:
        rows.append(
            (label,)
            + tuple(
                mean_share_cell(run, metric, "comparable_segment_total_us")
                for run in csgc_runs
            )
        )
    residual = ["Residual"]
    child_metrics = (
        "comparable_pre_work_total_us",
        "comparable_ssd_total_us",
        "comparable_post_queue_delay_us",
        "comparable_post_total_work_us",
    )
    for run in csgc_runs:
        total = run.field("comparable_segment_total_us", "mean")
        children = sum(run.field(metric, "mean", 0.0) for metric in child_metrics)
        residual.append(f"{fmt_ms(total - children)} ({100.0 * (total - children) / total:.1f}%)")
    rows.append(tuple(residual))
    rows.append(
        ("Segment total median / p95 ms",)
        + tuple(
            f"{fmt_ms(run.field('comparable_segment_total_us', 'median'))} / "
            f"{fmt_ms(run.field('comparable_segment_total_us', 'p95'))}"
            for run in csgc_runs
        )
    )
    return markdown_table(("Segment phase",) + CONFIG_ORDER[1:], rows)


def distribution_table(
    group: Sequence[Run],
    title_column: str,
    metrics: Sequence[Tuple[str, str]],
) -> str:
    """Build a CSGC-only fine-grained distribution table."""
    csgc_runs = group[1:]
    rows = [
        (label,) + tuple(distribution_cell(run, metric) for run in csgc_runs)
        for label, metric in metrics
    ]
    return markdown_table((title_column,) + CONFIG_ORDER[1:], rows)


PRE_METRICS = (
    ("PRE total", "comparable_pre_work_total_us"),
    ("Retry gap", "comparable_pre_retry_gap_us"),
    ("Data-page locking", "comparable_pre_data_lock_us"),
    ("Data revalidation", "comparable_pre_data_revalidate_us"),
    ("Build node list", "comparable_pre_node_list_us"),
    ("Inode locking", "comparable_pre_inode_lock_us"),
    ("Node-page locking", "comparable_pre_node_pages_lock_us"),
    ("Target preallocation", "comparable_pre_preallocate_us"),
    ("Request metadata/Move Plan", "comparable_pre_request_metadata_us"),
    ("cp_rwsem locking", "comparable_pre_cp_rwsem_lock_us"),
)

SSD_METRICS = (
    ("Host-visible SSD total", "comparable_ssd_total_us"),
    ("Trigger round trip", "comparable_ssd_trigger_roundtrip_us"),
    ("Inter-submit gap", "comparable_ssd_inter_submit_gap_us"),
    ("Completion wait", "comparable_ssd_completion_wait_us"),
)

POST_METRICS = (
    ("POST queue wait", "comparable_post_queue_delay_us"),
    ("POST work total", "comparable_post_total_work_us"),
    ("Metadata excluding page release", "comparable_post_metadata_without_page_release_us"),
    ("Result validation", "comparable_post_result_validation_us"),
    ("Segment/summary metadata", "comparable_post_segment_metadata_us"),
    ("Dnode update/commit", "comparable_post_dnode_update_us"),
    ("Unlock operation", "comparable_post_unlock_op_us"),
    ("Release data pages", "comparable_post_put_data_pages_us"),
    ("Cleanup", "comparable_post_cleanup_us"),
)

IMPLEMENTATION_METRICS = (
    ("Original: pack node", "original_detail_pre_pack_node_us"),
    ("Original: pack SIT", "original_detail_pre_pack_sit_us"),
    ("Modern: build valid offsets", "modern_detail_pre_build_valid_offsets_us"),
    ("Modern: dirty source scan", "modern_detail_pre_dirty_source_scan_us"),
    ("Modern: get valid blocks", "modern_detail_pre_get_valid_blocks_us"),
    ("Modern: check data validness", "modern_detail_pre_check_data_validness_us"),
    ("Modern: prepare Move Plan", "modern_detail_pre_prepare_move_plan_us"),
    ("Modern: finalize Move Plan", "modern_detail_pre_finalize_move_plan_us"),
    ("Modern: prealloc lock wait", "modern_detail_pre_prealloc_lock_wait_us"),
    ("Modern: prealloc sync", "modern_detail_pre_prealloc_sync_us"),
    ("Modern: prealloc wait sync", "modern_detail_pre_prealloc_wait_sync_us"),
    ("Modern: prealloc allocation", "modern_detail_pre_prealloc_alloc_us"),
    ("Modern: POST status", "modern_post_detail_post_result_status_us"),
    ("Modern: local commit validation", "modern_post_detail_post_local_commit_validate_us"),
    ("Modern: device result validation", "modern_post_detail_post_device_result_validate_us"),
    ("Modern: cache invalidation", "modern_post_detail_post_cache_invalidate_us"),
    ("Modern: summary commit", "modern_post_detail_post_summary_commit_us"),
    ("Modern: dnode commit", "modern_post_detail_post_dnode_commit_us"),
    ("Modern: rollback", "modern_post_detail_post_rollback_us"),
    ("Modern: release cleanup", "modern_release_post_release_cleanup_us"),
)


def implementation_table(group: Sequence[Run]) -> str:
    """Build the implementation-specific CSGC detail table."""
    csgc_runs = group[1:]
    rows = []
    for label, metric in IMPLEMENTATION_METRICS:
        row = [label]
        for run in csgc_runs:
            if label.startswith("Original:") and run.label != "Original CSGC":
                row.append("N/A")
            elif label.startswith("Modern:") and run.label == "Original CSGC":
                row.append("N/A")
            else:
                row.append(distribution_cell(run, metric))
        rows.append(tuple(row))
    return markdown_table(("Implementation-specific phase",) + CONFIG_ORDER[1:], rows)


def pipeline_table(group: Sequence[Run]) -> str:
    """Build pipeline-only batch statistics."""
    run = group[3]
    metrics = (
        ("Batches", "pipeline_wall_us", "count", "count"),
        ("Dual-section batch fraction", "pipeline_dual_batch_fraction_permille", "mean", "permille"),
        ("Sections per batch", "pipeline_sections", "mean", "scalar"),
        ("Wall per batch", "pipeline_wall_us", "mean", "ms"),
        ("Amortized wall per section", "pipeline_wall_per_section_us", "mean", "ms"),
        ("Individual section sum per section", "pipeline_section_sum_per_section_us", "mean", "ms"),
        ("Overlap per batch", "pipeline_overlap_us", "mean", "ms"),
        ("Overlap fraction", "pipeline_overlap_fraction_permille", "mean", "permille"),
        ("Effective parallelism", "pipeline_effective_parallelism_milli", "mean", "milli"),
        ("Net saved per batch", "pipeline_net_saved_us", "mean", "ms"),
        ("Second-section launch gap", "pipeline_launch_gap_us", "mean", "ms"),
        ("Second victim selection", "pipeline_second_victim_select_us", "mean", "ms"),
        ("Outer PRE", "pipeline_outer_pre_us", "mean", "ms"),
        ("Outer POST", "pipeline_outer_post_us", "mean", "ms"),
    )
    rows = []
    for label, metric, field, unit in metrics:
        value = run.field(metric, field)
        if unit == "count":
            rendered = fmt_int(value)
        elif unit == "permille":
            rendered = f"{value / 10.0:.1f}%" if finite(value) else "N/A"
        elif unit == "milli":
            rendered = f"{value / 1000.0:.3f}x" if finite(value) else "N/A"
        elif unit == "ms":
            rendered = fmt_ms(value)
        else:
            rendered = fmt(value, 3)
        rows.append((label, rendered))
    return markdown_table(("Pipeline metric", run.label), rows)


def workload_section(runs: Sequence[Run], workload: str) -> str:
    """Build all hierarchy levels for one workload."""
    group = runs_for_workload(runs, workload)
    name = "Large-file workload" if workload == "bigfile" else "Partitioned small-file workload"
    sections_text = [
        f"## {name}",
        "",
        "### Level 0: end-to-end throughput and GC pressure",
        "",
        endpoint_table(group),
        "",
        "### Level 1A: additive top-level f2fs_gc phase time",
        "",
        "Each cell is `cumulative seconds (% of measured window, % of summed f2fs_gc time)`.",
        "",
        gc_phase_table(group),
        "",
        "### Level 1B: f2fs_gc call outcomes",
        "",
        "Each cell is `count / cumulative time / mean per call`. This is an alternative decomposition to Level 1A and must not be added to it.",
        "",
        gc_outcome_table(group),
        "",
        "### Level 2: data-section service time",
        "",
        section_table(group),
        "",
        "ORI has no CSGC section trace, so its data collector time per section is shown as the closest comparison. It excludes top-level checkpoint and victim-selection cost. Pipeline wall/section is batch wall time divided by actual sections and is the pipeline throughput-oriented denominator.",
        "",
        "### Level 3: coarse CSGC segment lifetime",
        "",
        "Each phase cell is `mean ms (% of mean segment lifetime)`. Segment lifetimes overlap within a section, especially in mCSGC8t; they must not be summed across segments to derive section wall-clock time.",
        "",
        segment_coarse_table(group),
        "",
        "### Level 4A: comparable PRE breakdown",
        "",
        "Each cell is `mean / median / p95` in milliseconds.",
        "",
        distribution_table(group, "PRE phase", PRE_METRICS),
        "",
        "### Level 4B: Host-visible SSD lifecycle",
        "",
        "Each cell is `mean / median / p95` in milliseconds. These are Host-observed request intervals, not pure device execution time.",
        "",
        distribution_table(group, "SSD-stage phase", SSD_METRICS),
        "",
        "### Level 4C: comparable POST breakdown",
        "",
        "Each cell is `mean / median / p95` in milliseconds.",
        "",
        distribution_table(group, "POST phase", POST_METRICS),
        "",
        "### Level 5: implementation-specific PRE/POST details",
        "",
        "Each cell is `mean / median / p95` in milliseconds. N/A means that the phase does not exist in that implementation or is represented by a different comparable phase above.",
        "",
        implementation_table(group),
        "",
        "### Level 6: cross-section pipeline behavior",
        "",
        pipeline_table(group),
        "",
    ]
    return "\n".join(sections_text)


def build_report(runs: Sequence[Run]) -> str:
    """Build the complete Markdown report."""
    return "\n".join(
        (
            "# Four-configuration GC breakdown comparison",
            "",
            "This report compares the paired August 12, 2026 diagnostic runs: ORI, original CSGC, mCSGC8t without cross-section pipeline, and mCSGC8t with pipeline. Every table is generated directly from `fio.log` and `gc-breakdown-diagnostic-result.txt`.",
            "",
            "## Interpretation rules",
            "",
            "- Level 1 phase sums are additive within each configuration. Level 1 call outcomes are a separate view of the same f2fs_gc calls.",
            "- Section wall-clock is the primary latency denominator for one data section. Pipeline wall/section is the throughput-oriented denominator for a two-section batch.",
            "- Segment PRE/SSD/POST times describe concurrent segment lifetimes. Their per-segment means cannot be multiplied by segment count to obtain section or fio wall-clock time.",
            "- Host-visible SSD time includes request submission, queueing, device work, and result return. It is not the OpenSSD core-only execution time.",
            "- These diagnostic builds have a strong observer effect and should be used for structural attribution, not as quiet-build performance baselines.",
            "",
            "## Data sources",
            "",
            source_table(runs),
            "",
            workload_section(runs, "bigfile"),
            workload_section(runs, "smallfile"),
        )
    )


def main() -> None:
    """Load the matrix and write the generated Markdown report."""
    args = parse_args()
    runs = load_runs()
    report = build_report(runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
