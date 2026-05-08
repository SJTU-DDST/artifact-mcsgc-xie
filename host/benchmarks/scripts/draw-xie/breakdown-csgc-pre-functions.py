#!/usr/bin/env python3
import argparse
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, TextIO, Tuple

import numpy as np


BREAKDOWN_PREFIX = "BREAKDOWN_M"

STAT_PREFIXES = [
    "mCSGCv2_STAT",
    "mCSGCv2_STAT without wait",
    "CSGC-va_STAT",
    "mCSGCv2_STAT 2thread without wait",
    "mCSGC8t_STAT without wait",
    "mCSGC2t_STAT without wait",
]

STAT_PREFIX_PATTERN = "|".join(
    re.escape(p) for p in sorted(STAT_PREFIXES, key=len, reverse=True)
)

TS_PATTERN = r"\[\s*(?P<ts>\d+\.\d+)\s*\]\s+"

RE_BREAKDOWN = re.compile(
    rf"""
    ^{TS_PATTERN}
    {BREAKDOWN_PREFIX}\[(?P<tag>[A-Z_]+)\]<pid=(?P<pid>\d+)\s+comm=(?P<comm>[^>]+)>:
    (?P<body>.*)$
    """,
    re.VERBOSE,
)

RE_OUTER_STAT = re.compile(
    rf"""
    ^{TS_PATTERN}
    (?P<prefix>{STAT_PREFIX_PATTERN})\s+
    segno=(?P<segno>\d+)\s+
    (?P<kv>.*)$
    """,
    re.VERBOSE,
)

RE_KV = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")


@dataclass(frozen=True)
class FunctionSpec:
    tag: str
    function_name: str
    outer_metric: str
    required_keys: Tuple[str, ...]
    base_time_metrics: Tuple[str, ...]
    nested_time_metrics: Tuple[str, ...]
    count_metrics: Tuple[str, ...]
    loop_component_metrics: Tuple[str, ...]
    optional_time_metrics: Tuple[str, ...] = ()


FUNCTION_SPECS: Dict[str, FunctionSpec] = {
    "NODE_LIST": FunctionSpec(
        tag="NODE_LIST",
        function_name="get_gc_node_list",
        outer_metric="pre_node_list_us",
        required_keys=(
            "segno",
            "req_idx",
            "function_total_us",
            "covered_total_us",
            "get_sum_page_us",
            "loop_total_us",
            "check_valid_map_us",
            "summary_decode_us",
            "get_node_info_us",
            "iget_us",
            "add_gc_dnode_us",
            "add_gc_inode_us",
            "clear_rollback_us",
            "valid_blocks",
            "invalid_blocks",
            "node_info_errs",
            "iget_enoent",
            "iget_errs",
            "ret",
        ),
        base_time_metrics=(
            "function_total_us",
            "covered_total_us",
            "function_uncovered_us",
            "get_sum_page_us",
            "loop_total_us",
            "loop_main_path_us",
            "loop_other_us",
            "check_valid_map_us",
            "summary_decode_us",
            "get_node_info_us",
            "iget_us",
            "add_gc_dnode_us",
            "add_gc_inode_us",
            "clear_rollback_us",
        ),
        nested_time_metrics=(),
        count_metrics=(
            "valid_blocks",
            "invalid_blocks",
            "node_info_errs",
            "iget_enoent",
            "iget_errs",
            "ret",
        ),
        loop_component_metrics=(
            "check_valid_map_us",
            "summary_decode_us",
            "get_node_info_us",
            "iget_us",
            "add_gc_dnode_us",
            "add_gc_inode_us",
        ),
    ),
    "LOCK_NPAGE": FunctionSpec(
        tag="LOCK_NPAGE",
        function_name="get_lock_gc_node_pages",
        outer_metric="pre_node_pages_lock_us",
        required_keys=(
            "segno",
            "req_idx",
            "function_total_us",
            "covered_total_us",
            "main_path_us",
            "loop_total_us",
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
            "nid_equal_path_us",
            "nid_not_equal_path_us",
            "inode_down_write_us",
            "dnode_down_write_us",
            "inode_get_node_page_us",
            "dnode_get_node_page_us",
            "inode_wait_writeback_us",
            "dnode_wait_writeback_us",
            "inc_inode_ref_us",
            "inc_dnode_ref_us",
            "valid_blocks",
            "invalid_blocks",
            "nid_equal_blocks",
            "nid_not_equal_blocks",
            "inode_page_gets",
            "inode_page_reuses",
            "dnode_page_gets",
            "dnode_page_reuses",
            "dnode_page_aliases",
            "ret",
        ),
        base_time_metrics=(
            "function_total_us",
            "covered_total_us",
            "function_uncovered_us",
            "loop_total_us",
            "main_path_us",
            "loop_other_us",
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
            "nid_equal_path_us",
            "nid_not_equal_path_us",
        ),
        nested_time_metrics=(
            "inode_down_write_us",
            "dnode_down_write_us",
            "inode_get_node_page_us",
            "dnode_get_node_page_us",
            "inode_wait_writeback_us",
            "dnode_wait_writeback_us",
            "inc_inode_ref_us",
            "inc_dnode_ref_us",
        ),
        count_metrics=(
            "valid_blocks",
            "invalid_blocks",
            "nid_equal_blocks",
            "nid_not_equal_blocks",
            "inode_page_gets",
            "inode_page_reuses",
            "dnode_page_gets",
            "dnode_page_reuses",
            "dnode_page_aliases",
            "ret",
        ),
        loop_component_metrics=(
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
            "nid_equal_path_us",
            "nid_not_equal_path_us",
        ),
        optional_time_metrics=(
            "get_sum_page_us",
        ),
    ),
    "DATA_VALIDNESS": FunctionSpec(
        tag="DATA_VALIDNESS",
        function_name="check_gc_data_validness",
        outer_metric="pre_check_data_validness_us",
        required_keys=(
            "segno",
            "function_total_us",
            "covered_total_us",
            "get_sum_page_us",
            "loop_total_us",
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
            "valid_blocks",
            "invalid_blocks",
            "missing_dnodes",
            "missing_inodes",
            "ret",
        ),
        base_time_metrics=(
            "function_total_us",
            "covered_total_us",
            "function_uncovered_us",
            "get_sum_page_us",
            "loop_total_us",
            "loop_main_path_us",
            "loop_other_us",
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
        ),
        nested_time_metrics=(),
        count_metrics=(
            "valid_blocks",
            "invalid_blocks",
            "missing_dnodes",
            "missing_inodes",
            "ret",
        ),
        loop_component_metrics=(
            "check_valid_map_us",
            "summary_decode_us",
            "find_gc_dnode_us",
            "find_gc_inode_us",
        ),
    ),
}


class Tee:
    def __init__(self, *streams: TextIO):
        self.streams = streams

    def write(self, text: str) -> None:
        for s in self.streams:
            s.write(text)
            s.flush()

    def writeln(self, text: str = "") -> None:
        self.write(text + "\n")


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def parse_kv_pairs(text: str) -> Dict[str, str]:
    return {k: v for k, v in RE_KV.findall(text)}


def to_int(value: str) -> int:
    return int(value.rstrip(","))


def maybe_int(value: str):
    try:
        return to_int(value)
    except ValueError:
        return value


def safe_float_array(xs: Iterable[float]) -> np.ndarray:
    arr = np.array(list(xs), dtype=float)
    arr = arr[np.isfinite(arr)]
    return arr


def percentile(arr: np.ndarray, p: float) -> float:
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, p))


def top_k_mean(arr: np.ndarray, frac: float) -> float:
    if arr.size == 0:
        return float("nan")
    k = max(1, int(math.ceil(arr.size * frac)))
    return float(np.mean(np.sort(arr)[-k:]))


def summarize_numeric(arr: np.ndarray) -> Dict[str, float]:
    if arr.size == 0:
        return {
            "count": 0.0,
            "mean": float("nan"),
            "min": float("nan"),
            "max": float("nan"),
            "median": float("nan"),
            "p80": float("nan"),
            "top20_mean": float("nan"),
        }
    return {
        "count": float(arr.size),
        "mean": float(np.mean(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "median": float(np.median(arr)),
        "p80": percentile(arr, 80.0),
        "top20_mean": top_k_mean(arr, 0.20),
    }


def format_stats(name: str, arr: np.ndarray) -> str:
    s = summarize_numeric(arr)
    return (
        f"{name}: n={int(s['count'])} mean={s['mean']:.3f} min={s['min']:.3f} "
        f"max={s['max']:.3f} median={s['median']:.3f} p80={s['p80']:.3f} "
        f"top20_mean={s['top20_mean']:.3f}"
    )


def ratio_array(numerators: Iterable[float], denominators: Iterable[float]) -> np.ndarray:
    num = np.array(list(numerators), dtype=float)
    den = np.array(list(denominators), dtype=float)
    mask = np.isfinite(num) & np.isfinite(den) & (den > 0.0)
    if not np.any(mask):
        return np.array([], dtype=float)
    ratio = num[mask] / den[mask]
    return ratio[np.isfinite(ratio)]


def pearson_corr(x: np.ndarray, y: np.ndarray) -> float:
    if x.size != y.size:
        raise RuntimeError(f"Correlation input size mismatch: {x.size} != {y.size}")
    mask = np.isfinite(x) & np.isfinite(y)
    x2 = x[mask]
    y2 = y[mask]
    if x2.size < 3:
        return float("nan")
    if float(np.std(x2)) == 0.0 or float(np.std(y2)) == 0.0:
        return float("nan")
    return float(np.corrcoef(x2, y2)[0, 1])


def parse_function_row(spec: FunctionSpec, kv: Dict[str, str], line_no: int) -> Dict[str, object]:
    missing = [key for key in spec.required_keys if key not in kv]
    if missing:
        raise RuntimeError(
            f"Missing keys in {spec.tag} line at line {line_no}: {', '.join(missing)}"
        )

    row: Dict[str, object] = {}
    for key, value in kv.items():
        row[key] = maybe_int(value)

    loop_main_path_us = sum(int(row[key]) for key in spec.loop_component_metrics)
    row["loop_main_path_us"] = loop_main_path_us
    row["loop_other_us"] = int(row["loop_total_us"]) - loop_main_path_us
    row["function_uncovered_us"] = int(row["function_total_us"]) - int(row["covered_total_us"])
    return row


def paired_arrays_for_metric(
    rows: Iterable[Dict[str, object]],
    metric: str,
    reference_metric: str,
) -> Tuple[np.ndarray, np.ndarray]:
    vals: List[float] = []
    refs: List[float] = []

    for row in rows:
        if metric not in row or reference_metric not in row:
            continue
        vals.append(float(row[metric]))
        refs.append(float(row[reference_metric]))

    return safe_float_array(vals), safe_float_array(refs)


def outer_key(row: Dict[str, object]) -> Tuple[int, Optional[int]]:
    segno = int(row["segno"])
    req_idx = row.get("req_idx")
    if req_idx is None:
        return (segno, None)
    return (segno, int(req_idx))


def find_outer_pair(
    row: Dict[str, object],
    outer_rows: List[Dict[str, object]],
    used_indices: set,
) -> Optional[Dict[str, object]]:
    row_key = outer_key(row)
    fallback_key = (row_key[0], None)

    for idx, outer in enumerate(outer_rows):
        if idx in used_indices:
            continue
        if int(outer["_line_no"]) <= int(row["_line_no"]):
            continue
        if outer_key(outer) == row_key:
            used_indices.add(idx)
            return outer

    for idx, outer in enumerate(outer_rows):
        if idx in used_indices:
            continue
        if int(outer["_line_no"]) <= int(row["_line_no"]):
            continue
        if outer_key(outer) == fallback_key or (outer_key(outer)[0], None) == fallback_key:
            used_indices.add(idx)
            return outer

    return None


def paired_with_outer(
    rows: List[Dict[str, object]],
    outer_rows: List[Dict[str, object]],
    outer_metric: str,
) -> List[Tuple[Dict[str, object], Dict[str, object]]]:
    candidates = [r for r in outer_rows if outer_metric in r]
    used_indices: set = set()
    pairs: List[Tuple[Dict[str, object], Dict[str, object]]] = []

    for row in sorted(rows, key=lambda r: int(r["_line_no"])):
        outer = find_outer_pair(row, candidates, used_indices)
        if outer is not None:
            pairs.append((row, outer))
    return pairs


def print_metric_group(out: Tee, title: str, rows: List[Dict[str, object]], metrics: Iterable[str]) -> None:
    out.writeln("")
    out.writeln(title)
    for metric in metrics:
        arr = safe_float_array(float(r[metric]) for r in rows if metric in r)
        out.writeln(format_stats(metric, arr))


def print_ratio_percentiles(
    out: Tee,
    title: str,
    rows: List[Dict[str, object]],
    numerators: Iterable[str],
    denominator: str,
) -> None:
    out.writeln("")
    out.writeln(title)
    for metric in numerators:
        ratio = ratio_array(
            [float(r[metric]) for r in rows if metric in r and denominator in r],
            [float(r[denominator]) for r in rows if metric in r and denominator in r],
        )
        out.writeln(
            f"{metric}_ratio: n={ratio.size} "
            f"p50={percentile(ratio, 50.0):.6f} "
            f"p90={percentile(ratio, 90.0):.6f} "
            f"p99={percentile(ratio, 99.0):.6f}"
        )


def print_top_rows(out: Tee, spec: FunctionSpec, rows: List[Dict[str, object]]) -> None:
    out.writeln("")
    out.writeln(f"=== {spec.tag}: top slow samples by function_total_us ===")
    sorted_rows = sorted(rows, key=lambda r: float(r["function_total_us"]), reverse=True)
    for rank, row in enumerate(sorted_rows[:10], start=1):
        fields = [
            f"rank={rank}",
            f"segno={row.get('segno', -1)}",
            f"req_idx={row.get('req_idx', -1)}",
            f"function_total_us={row.get('function_total_us', 'nan')}",
            f"loop_total_us={row.get('loop_total_us', 'nan')}",
            f"loop_other_us={row.get('loop_other_us', 'nan')}",
            f"valid_blocks={row.get('valid_blocks', 'nan')}",
            f"ret={row.get('ret', 'nan')}",
        ]

        for metric in spec.loop_component_metrics:
            fields.append(f"{metric}={row.get(metric, 'nan')}")
        for metric in spec.optional_time_metrics:
            fields.append(f"{metric}={row.get(metric, 'nan')}")
        for metric in spec.nested_time_metrics:
            fields.append(f"{metric}={row.get(metric, 'nan')}")

        out.writeln(" ".join(fields))


def print_function_report(
    out: Tee,
    spec: FunctionSpec,
    rows: List[Dict[str, object]],
    outer_rows: List[Dict[str, object]],
) -> None:
    out.writeln("")
    out.writeln(f"=== {spec.tag}: {spec.function_name} ===")
    out.writeln(f"records={len(rows)}")
    out.writeln(f"outer_metric={spec.outer_metric}")

    if not rows:
        out.writeln("status=no records found; the corresponding macro may be disabled")
        return

    time_metrics = spec.base_time_metrics + spec.optional_time_metrics

    print_metric_group(out, f"=== {spec.tag}: basic time statistics (microseconds) ===",
                       rows, time_metrics)

    if spec.nested_time_metrics:
        print_metric_group(out, f"=== {spec.tag}: nested hotspot statistics (microseconds) ===",
                           rows, spec.nested_time_metrics)

    print_metric_group(out, f"=== {spec.tag}: count statistics ===", rows, spec.count_metrics)

    if any("valid_blocks" in r and float(r["valid_blocks"]) > 0.0 for r in rows):
        out.writeln("")
        out.writeln(f"=== {spec.tag}: normalized by valid_blocks (us per valid block) ===")
        for metric in time_metrics + spec.nested_time_metrics:
            metric_rows = [
                r for r in rows
                if metric in r and "valid_blocks" in r and float(r["valid_blocks"]) > 0.0
            ]
            vals = [float(r[metric]) for r in metric_rows]
            valid_blocks = [float(r["valid_blocks"]) for r in metric_rows]
            out.writeln(format_stats(f"{metric}_per_valid_block_us",
                                     ratio_array(vals, valid_blocks)))

    print_ratio_percentiles(
        out,
        f"=== {spec.tag}: loop decomposition ratio (phase / loop_total_us) ===",
        rows,
        list(spec.loop_component_metrics) + ["loop_other_us"],
        "loop_total_us",
    )

    print_ratio_percentiles(
        out,
        f"=== {spec.tag}: function coverage ratio (phase / function_total_us) ===",
        rows,
        ["covered_total_us", "function_uncovered_us"],
        "function_total_us",
    )

    if spec.nested_time_metrics:
        print_ratio_percentiles(
            out,
            f"=== {spec.tag}: nested hotspot ratio (overlapping / loop_total_us) ===",
            rows,
            spec.nested_time_metrics,
            "loop_total_us",
        )

    out.writeln("")
    out.writeln(f"=== {spec.tag}: correlation with function_total_us ===")
    for metric in time_metrics + spec.nested_time_metrics + spec.count_metrics:
        if metric == "function_total_us":
            continue
        vals, function_total = paired_arrays_for_metric(rows, metric, "function_total_us")
        corr = pearson_corr(vals, function_total)
        out.writeln(f"{metric}: corr_with_function_total_us={corr:.6f}")

    pairs = paired_with_outer(rows, outer_rows, spec.outer_metric)
    out.writeln("")
    out.writeln(f"=== {spec.tag}: coverage vs outer {spec.outer_metric} ===")
    out.writeln(f"outer_rows_with_metric={sum(1 for r in outer_rows if spec.outer_metric in r)}")
    out.writeln(f"paired_rows={len(pairs)}")
    if not pairs:
        out.writeln("status=skipped; no matching outer stat rows were found")
    else:
        inner = [float(row["function_total_us"]) for row, _ in pairs]
        outer = [float(outer_row[spec.outer_metric]) for _, outer_row in pairs]
        gap = [outer_us - inner_us for inner_us, outer_us in zip(inner, outer)]
        ratio = ratio_array(inner, outer)
        out.writeln(format_stats("function_total_over_outer_ratio", ratio))
        out.writeln(format_stats("outer_minus_function_total_us", safe_float_array(gap)))
        out.writeln(format_stats(spec.outer_metric, safe_float_array(outer)))

        out.writeln("")
        out.writeln(f"=== {spec.tag}: correlation with outer {spec.outer_metric} ===")
        for metric in time_metrics + spec.nested_time_metrics + spec.count_metrics:
            vals = safe_float_array(float(row[metric]) for row, _ in pairs if metric in row)
            outer_arr = safe_float_array(
                float(outer_row[spec.outer_metric])
                for row, outer_row in pairs
                if metric in row
            )
            corr = pearson_corr(vals, outer_arr)
            out.writeln(f"{metric}: corr_with_{spec.outer_metric}={corr:.6f}")

    print_top_rows(out, spec, rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Parse CSGC pre-phase breakdown logs for get_gc_node_list, "
            "get_lock_gc_node_pages, and check_gc_data_validness."
        )
    )
    parser.add_argument("logfile", help="absolute path to the log file (*.log)")
    args = parser.parse_args()

    logfile = args.logfile
    if not os.path.isabs(logfile):
        raise RuntimeError(f"Input path must be absolute: {logfile}")
    if not logfile.endswith(".log"):
        raise RuntimeError(f"Input file must end with .log: {logfile}")
    if not os.path.isfile(logfile):
        raise RuntimeError(f"Input file does not exist: {logfile}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_name = os.path.basename(logfile)[:-4]
    run_dir = os.path.join(script_dir, "breakdown-csgc-pre-functions", folder_name)
    ensure_dir(run_dir)
    result_path = os.path.join(run_dir, "result.txt")

    rows_by_tag: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    outer_rows: List[Dict[str, object]] = []
    unknown_breakdown_tags: Dict[str, int] = defaultdict(int)
    outer_prefixes = set()

    with open(logfile, "r", errors="replace") as fp:
        for line_no, raw_line in enumerate(fp, start=1):
            line = raw_line.rstrip("\n")

            m = RE_BREAKDOWN.match(line)
            if m:
                tag = m.group("tag")
                spec = FUNCTION_SPECS.get(tag)
                if spec is None:
                    unknown_breakdown_tags[tag] += 1
                    continue

                body = m.group("body")
                if not body.startswith(spec.function_name):
                    raise RuntimeError(
                        f"Unexpected function body for tag={tag} at line {line_no}: {body}"
                    )

                kv = parse_kv_pairs(body)
                row = parse_function_row(spec, kv, line_no)
                row["_line_no"] = line_no
                row["ts"] = float(m.group("ts"))
                row["pid"] = int(m.group("pid"))
                row["comm"] = m.group("comm")
                row["tag"] = tag
                rows_by_tag[tag].append(row)
                continue

            m = RE_OUTER_STAT.match(line)
            if m:
                kv = parse_kv_pairs(m.group("kv"))
                row: Dict[str, object] = {k: maybe_int(v) for k, v in kv.items()}
                row["segno"] = int(m.group("segno"))
                row["_line_no"] = line_no
                row["ts"] = float(m.group("ts"))
                row["outer_prefix"] = m.group("prefix")
                outer_prefixes.add(m.group("prefix"))
                outer_rows.append(row)
                continue

    with open(result_path, "w", encoding="utf-8") as result_fp:
        out = Tee(sys.stdout, result_fp)
        out.writeln(f"logfile={logfile}")
        out.writeln(f"run_dir={run_dir}")
        out.writeln(f"result_file={result_path}")

        out.writeln("")
        out.writeln("=== extraction summary ===")
        out.writeln(f"outer_stat_count={len(outer_rows)}")
        out.writeln(f"outer_stat_prefixes={','.join(sorted(outer_prefixes)) if outer_prefixes else 'none'}")
        for tag in FUNCTION_SPECS:
            out.writeln(f"{tag}_count={len(rows_by_tag.get(tag, []))}")
        if unknown_breakdown_tags:
            for tag, count in sorted(unknown_breakdown_tags.items()):
                out.writeln(f"ignored_unknown_breakdown_tag[{tag}]={count}")

        if len(outer_prefixes) > 1:
            out.writeln("warning=multiple outer stat prefixes found in one log file")
        if not outer_rows:
            out.writeln("warning=no outer stat rows found; outer coverage diagnostics will be skipped")

        for tag, spec in FUNCTION_SPECS.items():
            print_function_report(out, spec, rows_by_tag.get(tag, []), outer_rows)

        out.writeln("")
        out.writeln("=== notes ===")
        out.writeln("1. The script accepts logs where any subset of the three breakdown macros is enabled.")
        out.writeln("2. loop_other_us = loop_total_us - sum(selected non-overlap loop components).")
        out.writeln("3. function_uncovered_us = function_total_us - covered_total_us.")
        out.writeln("4. LOCK_NPAGE nested hotspot metrics overlap with nid_equal_path_us/nid_not_equal_path_us.")
        out.writeln("5. Outer coverage pairs a breakdown row with the next later outer stat row sharing segno and req_idx when available.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
