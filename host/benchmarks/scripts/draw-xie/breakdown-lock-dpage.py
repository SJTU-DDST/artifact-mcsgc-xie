import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TextIO, Tuple

import numpy as np


BREAKDOWN_PREFIX = "BREAKDOWN_M"


RE_TIMESTAMP = re.compile(r"^\[(?P<ts>\d+\.\d+)\]\s+")
RE_PREPARE = re.compile(
    r"""
    ^\[(?P<ts>\d+\.\d+)\]\s+
    BREAKDOWN_M<pid=(?P<pid>\d+)\s+comm=(?P<comm>[^>]+)>:
    in\s+get_lock_gc_data_pages,\s+prepare\s+enter\s+the\s+first\s+for\s+loop,\s+
    now\s+the\s+time\s+cost\s*=\s*(?P<prepare_before_first_for_us>\d+)\s+us,\s+
    segno=(?P<segno>\d+)\s*$
    """,
    re.VERBOSE,
)
RE_FIRST_FOR = re.compile(
    r"""
    ^\[(?P<ts>\d+\.\d+)\]\s+
    BREAKDOWN_M<pid=(?P<pid>\d+)\s+comm=(?P<comm>[^>]+)>:
    get_lock_gc_data_pages_first_for\s+
    (?P<kv>.*)$
    """,
    re.VERBOSE,
)
RE_LOCK_FOLIO = re.compile(
    r"""
    ^\[(?P<ts>\d+\.\d+)\]\s+
    BREAKDOWN_M<pid=(?P<pid>\d+)\s+comm=(?P<comm>[^>]+)>:
    segno=(?P<segno>\d+),\s+lock_folio_us=(?P<lock_folio_us>\d+)\s*$
    """,
    re.VERBOSE,
)
RE_CHECK_FOLIO = re.compile(
    r"""
    ^\[(?P<ts>\d+\.\d+)\]\s+
    BREAKDOWN_M<pid=(?P<pid>\d+)\s+comm=(?P<comm>[^>]+)>:
    segno=(?P<segno>\d+),\s+check_folio_us=(?P<check_folio_us>\d+)\s*$
    """,
    re.VERBOSE,
)

RE_KV = re.compile(r"([A-Za-z0-9_]+)=([^\s]+)")

FIRST_FOR_KEYS = [
    "segno",
    "first_for_total_us",
    "first_for_main_path_us",
    "find_gc_de_with_lock_us",
    "find_gc_ie_with_lock_us",
    "get_node_page_nid_equal_us",
    "rwsem_nid_equal_us",
    "get_node_page_nid_not_equal_us",
    "rwsem_nid_not_equal_us",
    "get_data_page_us",
    "add_gc_folio_us",
    "fe_rwsem_us",
    "valid_blocks",
    "add_folio_us",
]

PRIMARY_TIME_METRICS = [
    "pre_data_lock_us",
    "prepare_before_first_for_us",
    "first_for_total_us",
    "first_for_main_path_us",
    "first_for_other_us",
    "find_gc_de_with_lock_us",
    "find_gc_ie_with_lock_us",
    "get_node_page_nid_equal_us",
    "rwsem_nid_equal_us",
    "get_node_page_nid_not_equal_us",
    "rwsem_nid_not_equal_us",
    "get_data_page_us",
    "add_gc_folio_us",
    "fe_rwsem_us",
    "lock_folio_us",
    "check_folio_us",
    "get_lock_gc_data_pages_total_us",
    "outer_inner_gap_us",
]

NON_OVERLAP_PHASES = [
    "prepare_before_first_for_us",
    "first_for_total_us",
    "lock_folio_us",
    "check_folio_us",
]

FIRST_FOR_DECOMP_NON_OVERLAP = [
    "first_for_main_path_us",
    "first_for_other_us",
]

FIRST_FOR_NESTED_HOTSPOTS = [
    "find_gc_de_with_lock_us",
    "find_gc_ie_with_lock_us",
    "get_node_page_nid_equal_us",
    "rwsem_nid_equal_us",
    "get_node_page_nid_not_equal_us",
    "rwsem_nid_not_equal_us",
    "get_data_page_us",
    "add_gc_folio_us",
    "fe_rwsem_us",
]


@dataclass
class OpenRecord:
    segno: int
    inner_pid: Optional[int] = None
    inner_comm: Optional[str] = None
    prepare_ts: Optional[float] = None
    first_for_ts: Optional[float] = None
    lock_folio_ts: Optional[float] = None
    check_folio_ts: Optional[float] = None
    outer_ts: Optional[float] = None
    outer_prefix: Optional[str] = None
    outer_pid: Optional[int] = None
    outer_tgid: Optional[int] = None
    outer_comm: Optional[str] = None
    req_idx: Optional[int] = None

    prepare_before_first_for_us: Optional[int] = None
    first_for_total_us: Optional[int] = None
    first_for_main_path_us: Optional[int] = None
    find_gc_de_with_lock_us: Optional[int] = None
    find_gc_ie_with_lock_us: Optional[int] = None
    get_node_page_nid_equal_us: Optional[int] = None
    rwsem_nid_equal_us: Optional[int] = None
    get_node_page_nid_not_equal_us: Optional[int] = None
    rwsem_nid_not_equal_us: Optional[int] = None
    get_data_page_us: Optional[int] = None
    add_gc_folio_us: Optional[int] = None
    fe_rwsem_us: Optional[int] = None
    valid_blocks: Optional[int] = None
    add_folio_us: Optional[int] = None
    lock_folio_us: Optional[int] = None
    check_folio_us: Optional[int] = None
    pre_data_lock_us: Optional[int] = None
    pre_work_total_us: Optional[int] = None

    stage_lines: Dict[str, int] = field(default_factory=dict)

    def set_once(self, field_name: str, value, line_no: int) -> None:
        if getattr(self, field_name) is not None:
            raise RuntimeError(
                f"Duplicate field '{field_name}' for segno={self.segno} at line {line_no}; "
                f"previously set at line {self.stage_lines.get(field_name, -1)}"
            )
        setattr(self, field_name, value)
        self.stage_lines[field_name] = line_no

    def require_all(self) -> None:
        required = [
            "prepare_before_first_for_us",
            "first_for_total_us",
            "first_for_main_path_us",
            "find_gc_de_with_lock_us",
            "find_gc_ie_with_lock_us",
            "get_node_page_nid_equal_us",
            "rwsem_nid_equal_us",
            "get_node_page_nid_not_equal_us",
            "rwsem_nid_not_equal_us",
            "get_data_page_us",
            "add_gc_folio_us",
            "fe_rwsem_us",
            "valid_blocks",
            "add_folio_us",
            "lock_folio_us",
            "check_folio_us",
            "pre_data_lock_us",
        ]
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise RuntimeError(
                f"Incomplete record for segno={self.segno}; missing fields: {', '.join(missing)}"
            )


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


def parse_timestamp(line: str) -> float:
    m = RE_TIMESTAMP.match(line)
    if not m:
        raise RuntimeError(f"Failed to parse timestamp from line: {line}")
    return float(m.group("ts"))


def get_prefix_before_segno(line: str) -> str:
    m = RE_TIMESTAMP.match(line)
    if not m:
        raise RuntimeError(f"Failed to parse prefix from line: {line}")
    after_ts = line[m.end():]
    pos = after_ts.find(" segno=")
    if pos < 0:
        raise RuntimeError(f"Failed to find ' segno=' in overall stat line: {line}")
    return after_ts[:pos].strip()


def safe_float_array(xs: List[float]) -> np.ndarray:
    arr = np.array(xs, dtype=float)
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
    sorted_arr = np.sort(arr)
    top = sorted_arr[-k:]
    return float(np.mean(top))


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


def pearson_corr(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    x2 = x[mask]
    y2 = y[mask]
    if x2.size < 3:
        return float("nan")
    if float(np.std(x2)) == 0.0 or float(np.std(y2)) == 0.0:
        return float("nan")
    return float(np.corrcoef(x2, y2)[0, 1])


def ratio_array(numerators: List[float], denominators: List[float]) -> np.ndarray:
    num = np.array(numerators, dtype=float)
    den = np.array(denominators, dtype=float)
    mask = np.isfinite(num) & np.isfinite(den) & (den > 0.0)
    if not np.any(mask):
        return np.array([], dtype=float)
    ratio = num[mask] / den[mask]
    ratio = ratio[np.isfinite(ratio)]
    return ratio


def format_stats(name: str, arr: np.ndarray) -> str:
    s = summarize_numeric(arr)
    return (
        f"{name}: n={int(s['count'])} mean={s['mean']:.3f} min={s['min']:.3f} "
        f"max={s['max']:.3f} median={s['median']:.3f} p80={s['p80']:.3f} "
        f"top20_mean={s['top20_mean']:.3f}"
    )


def finalize_record(rec: OpenRecord) -> Dict[str, object]:
    rec.require_all()

    if rec.valid_blocks is None or rec.valid_blocks <= 0:
        raise RuntimeError(f"Invalid valid_blocks for segno={rec.segno}: {rec.valid_blocks}")

    first_for_other_us = rec.first_for_total_us - rec.first_for_main_path_us
    if first_for_other_us < 0:
        raise RuntimeError(
            f"Negative first_for_other_us for segno={rec.segno}: "
            f"{rec.first_for_total_us} - {rec.first_for_main_path_us}"
        )

    get_lock_gc_data_pages_total_us = (
        rec.prepare_before_first_for_us
        + rec.first_for_total_us
        + rec.lock_folio_us
        + rec.check_folio_us
    )
    outer_inner_gap_us = rec.pre_data_lock_us - get_lock_gc_data_pages_total_us

    row: Dict[str, object] = {
        "segno": rec.segno,
        "req_idx": rec.req_idx,
        "inner_pid": rec.inner_pid,
        "inner_comm": rec.inner_comm,
        "outer_pid": rec.outer_pid,
        "outer_tgid": rec.outer_tgid,
        "outer_comm": rec.outer_comm,
        "outer_prefix": rec.outer_prefix,
        "prepare_ts": rec.prepare_ts,
        "first_for_ts": rec.first_for_ts,
        "lock_folio_ts": rec.lock_folio_ts,
        "check_folio_ts": rec.check_folio_ts,
        "outer_ts": rec.outer_ts,
        "prepare_before_first_for_us": rec.prepare_before_first_for_us,
        "pre_data_lock_us": rec.pre_data_lock_us,
        "pre_work_total_us": rec.pre_work_total_us,
        "first_for_total_us": rec.first_for_total_us,
        "first_for_main_path_us": rec.first_for_main_path_us,
        "first_for_other_us": first_for_other_us,
        "find_gc_de_with_lock_us": rec.find_gc_de_with_lock_us,
        "find_gc_ie_with_lock_us": rec.find_gc_ie_with_lock_us,
        "get_node_page_nid_equal_us": rec.get_node_page_nid_equal_us,
        "rwsem_nid_equal_us": rec.rwsem_nid_equal_us,
        "get_node_page_nid_not_equal_us": rec.get_node_page_nid_not_equal_us,
        "rwsem_nid_not_equal_us": rec.rwsem_nid_not_equal_us,
        "get_data_page_us": rec.get_data_page_us,
        "add_gc_folio_us": rec.add_gc_folio_us,
        "fe_rwsem_us": rec.fe_rwsem_us,
        "valid_blocks": rec.valid_blocks,
        "add_folio_us": rec.add_folio_us,
        "lock_folio_us": rec.lock_folio_us,
        "check_folio_us": rec.check_folio_us,
        "get_lock_gc_data_pages_total_us": get_lock_gc_data_pages_total_us,
        "outer_inner_gap_us": outer_inner_gap_us,
    }
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="absolute path to the log file (*.log)")
    args = ap.parse_args()

    logfile = args.logfile
    if not os.path.isabs(logfile):
        raise RuntimeError(f"Input path must be absolute: {logfile}")
    if not logfile.endswith(".log"):
        raise RuntimeError(f"Input file must end with .log: {logfile}")
    if not os.path.isfile(logfile):
        raise RuntimeError(f"Input file does not exist: {logfile}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    folder_name = os.path.basename(logfile)[:-4]
    run_dir = os.path.join(script_dir, "breakdown-get_lock_gc_data_pages", folder_name)
    ensure_dir(run_dir)
    result_path = os.path.join(run_dir, "result.txt")

    with open(result_path, "w", encoding="utf-8") as result_fp:
        out = Tee(sys.stdout, result_fp)
        out.writeln(f"logfile={logfile}")
        out.writeln(f"run_dir={run_dir}")
        out.writeln(f"result_file={result_path}")

        open_records: Dict[int, OpenRecord] = {}
        completed_rows: List[Dict[str, object]] = []
        outer_prefixes: set = set()

        with open(logfile, "r", errors="replace") as fp:
            for line_no, raw_line in enumerate(fp, start=1):
                line = raw_line.rstrip("\n")

                m = RE_PREPARE.match(line)
                if m:
                    segno = int(m.group("segno"))
                    pid = int(m.group("pid"))
                    comm = m.group("comm")
                    rec = open_records.get(segno)
                    if rec is None:
                        rec = OpenRecord(segno=segno)
                        open_records[segno] = rec
                    rec.set_once(
                        "prepare_before_first_for_us",
                        int(m.group("prepare_before_first_for_us")),
                        line_no,
                    )
                    rec.set_once("prepare_ts", float(m.group("ts")), line_no)
                    if rec.inner_pid is None:
                        rec.inner_pid = pid
                    if rec.inner_comm is None:
                        rec.inner_comm = comm
                    continue

                m = RE_FIRST_FOR.match(line)
                if m:
                    ts = float(m.group("ts"))
                    pid = int(m.group("pid"))
                    comm = m.group("comm")
                    kv = parse_kv_pairs(m.group("kv"))
                    missing = [k for k in FIRST_FOR_KEYS if k not in kv]
                    if missing:
                        raise RuntimeError(
                            f"Missing keys in first_for line at line {line_no}: {', '.join(missing)}"
                        )
                    segno = int(kv["segno"])
                    rec = open_records.get(segno)
                    if rec is None:
                        rec = OpenRecord(segno=segno)
                        open_records[segno] = rec
                    rec.set_once("first_for_ts", ts, line_no)
                    if rec.inner_pid is None:
                        rec.inner_pid = pid
                    if rec.inner_comm is None:
                        rec.inner_comm = comm
                    for key in FIRST_FOR_KEYS:
                        if key == "segno":
                            continue
                        rec.set_once(key, int(kv[key]), line_no)
                    continue

                m = RE_LOCK_FOLIO.match(line)
                if m:
                    segno = int(m.group("segno"))
                    rec = open_records.get(segno)
                    if rec is None:
                        rec = OpenRecord(segno=segno)
                        open_records[segno] = rec
                    rec.set_once("lock_folio_us", int(m.group("lock_folio_us")), line_no)
                    rec.set_once("lock_folio_ts", float(m.group("ts")), line_no)
                    if rec.inner_pid is None:
                        rec.inner_pid = int(m.group("pid"))
                    if rec.inner_comm is None:
                        rec.inner_comm = m.group("comm")
                    continue

                m = RE_CHECK_FOLIO.match(line)
                if m:
                    segno = int(m.group("segno"))
                    rec = open_records.get(segno)
                    if rec is None:
                        rec = OpenRecord(segno=segno)
                        open_records[segno] = rec
                    rec.set_once("check_folio_us", int(m.group("check_folio_us")), line_no)
                    rec.set_once("check_folio_ts", float(m.group("ts")), line_no)
                    if rec.inner_pid is None:
                        rec.inner_pid = int(m.group("pid"))
                    if rec.inner_comm is None:
                        rec.inner_comm = m.group("comm")
                    continue

                if BREAKDOWN_PREFIX in line:
                    continue

                if "segno=" in line and "pre_data_lock_us=" in line:
                    ts = parse_timestamp(line)
                    prefix = get_prefix_before_segno(line)
                    outer_prefixes.add(prefix)

                    kv = parse_kv_pairs(line)
                    if "segno" not in kv or "pre_data_lock_us" not in kv:
                        raise RuntimeError(f"Malformed outer stat line at line {line_no}: {line}")

                    segno = int(kv["segno"])
                    rec = open_records.get(segno)
                    if rec is None:
                        raise RuntimeError(
                            f"Outer stat line found before BREAKDOWN_M stages for segno={segno} at line {line_no}"
                        )

                    rec.set_once("outer_ts", ts, line_no)
                    rec.outer_prefix = prefix

                    if "pid" in kv:
                        rec.outer_pid = int(kv["pid"])
                    if "tgid" in kv:
                        rec.outer_tgid = int(kv["tgid"])
                    if "comm" in kv:
                        rec.outer_comm = kv["comm"]
                    if "req_idx" in kv:
                        rec.req_idx = int(kv["req_idx"])
                    if "pre_work_total_us" in kv:
                        rec.pre_work_total_us = int(kv["pre_work_total_us"])

                    rec.set_once("pre_data_lock_us", int(kv["pre_data_lock_us"]), line_no)

                    row = finalize_record(rec)
                    completed_rows.append(row)
                    del open_records[segno]
                    continue

        if open_records:
            leftovers = sorted(open_records.keys())
            raise RuntimeError(
                f"Found unfinished records at EOF for segno values: {leftovers}"
            )

        if not completed_rows:
            raise RuntimeError("No completed get_lock_gc_data_pages records were extracted")

        if not outer_prefixes:
            raise RuntimeError("No outer prefixes were extracted")

        out.writeln("")
        out.writeln("=== extraction summary ===")
        out.writeln(f"records={len(completed_rows)}")
        out.writeln(f"unique_segnos={len(set(int(r['segno']) for r in completed_rows))}")
        out.writeln(f"outer_prefixes={sorted(outer_prefixes)}")

        metrics_as_arrays: Dict[str, np.ndarray] = {}
        for metric in PRIMARY_TIME_METRICS:
            metrics_as_arrays[metric] = safe_float_array(
                [float(r[metric]) for r in completed_rows]
            )

        valid_blocks_arr = safe_float_array([float(r["valid_blocks"]) for r in completed_rows])

        out.writeln("")
        out.writeln("=== basic statistics (microseconds) ===")
        for metric in PRIMARY_TIME_METRICS:
            out.writeln(format_stats(metric, metrics_as_arrays[metric]))

        out.writeln("")
        out.writeln("=== count statistics ===")
        out.writeln(format_stats("valid_blocks", valid_blocks_arr))

        out.writeln("")
        out.writeln("=== normalized by valid_blocks (us per valid block) ===")
        valid_blocks = np.array([float(r["valid_blocks"]) for r in completed_rows], dtype=float)
        for metric in PRIMARY_TIME_METRICS:
            vals = np.array([float(r[metric]) for r in completed_rows], dtype=float)
            per_vb = ratio_array(vals.tolist(), valid_blocks.tolist())
            out.writeln(format_stats(f"{metric}_per_valid_block_us", per_vb))

        out.writeln("")
        out.writeln("=== coverage vs outer pre_data_lock_us ===")
        coverage_ratio = ratio_array(
            [float(r["get_lock_gc_data_pages_total_us"]) for r in completed_rows],
            [float(r["pre_data_lock_us"]) for r in completed_rows],
        )
        out.writeln(format_stats("inner_total_over_outer_pre_data_lock_ratio", coverage_ratio))
        out.writeln(format_stats("outer_inner_gap_us", metrics_as_arrays["outer_inner_gap_us"]))

        out.writeln("")
        out.writeln("=== diagnostic A: non-overlap phase ratio (phase / get_lock_gc_data_pages_total_us) ===")
        inner_total_list = [float(r["get_lock_gc_data_pages_total_us"]) for r in completed_rows]
        for metric in NON_OVERLAP_PHASES:
            ratio = ratio_array(
                [float(r[metric]) for r in completed_rows],
                inner_total_list,
            )
            if ratio.size == 0:
                continue
            out.writeln(
                f"{metric}_ratio: n={ratio.size} "
                f"p50={percentile(ratio, 50.0):.6f} "
                f"p90={percentile(ratio, 90.0):.6f} "
                f"p99={percentile(ratio, 99.0):.6f}"
            )

        out.writeln("")
        out.writeln("=== diagnostic A2: first_for decomposition ratio (non-overlap / first_for_total_us) ===")
        first_for_total_list = [float(r["first_for_total_us"]) for r in completed_rows]
        for metric in FIRST_FOR_DECOMP_NON_OVERLAP:
            ratio = ratio_array(
                [float(r[metric]) for r in completed_rows],
                first_for_total_list,
            )
            if ratio.size == 0:
                continue
            out.writeln(
                f"{metric}_ratio: n={ratio.size} "
                f"p50={percentile(ratio, 50.0):.6f} "
                f"p90={percentile(ratio, 90.0):.6f} "
                f"p99={percentile(ratio, 99.0):.6f}"
            )

        out.writeln("")
        out.writeln("=== diagnostic A3: first_for nested hotspot ratio (overlapping / first_for_total_us) ===")
        out.writeln("note=these nested hotspot ratios may overlap and do not necessarily sum to 1")
        for metric in FIRST_FOR_NESTED_HOTSPOTS:
            ratio = ratio_array(
                [float(r[metric]) for r in completed_rows],
                first_for_total_list,
            )
            if ratio.size == 0:
                continue
            out.writeln(
                f"{metric}_ratio: n={ratio.size} "
                f"p50={percentile(ratio, 50.0):.6f} "
                f"p90={percentile(ratio, 90.0):.6f} "
                f"p99={percentile(ratio, 99.0):.6f}"
            )

        out.writeln("")
        out.writeln("=== diagnostic B: correlation with get_lock_gc_data_pages_total_us ===")
        inner_total_arr = np.array(inner_total_list, dtype=float)
        corr_candidates = [
            "prepare_before_first_for_us",
            "first_for_total_us",
            "first_for_main_path_us",
            "first_for_other_us",
            "find_gc_de_with_lock_us",
            "find_gc_ie_with_lock_us",
            "get_node_page_nid_equal_us",
            "rwsem_nid_equal_us",
            "get_node_page_nid_not_equal_us",
            "rwsem_nid_not_equal_us",
            "get_data_page_us",
            "add_gc_folio_us",
            "fe_rwsem_us",
            "lock_folio_us",
            "check_folio_us",
            "valid_blocks",
        ]
        for metric in corr_candidates:
            vals = np.array([float(r[metric]) for r in completed_rows], dtype=float)
            corr = pearson_corr(vals, inner_total_arr)
            out.writeln(f"{metric}: corr_with_get_lock_gc_data_pages_total_us={corr:.6f}")

        out.writeln("")
        out.writeln("=== diagnostic C: correlation with outer pre_data_lock_us ===")
        outer_total_arr = np.array(
            [float(r["pre_data_lock_us"]) for r in completed_rows], dtype=float
        )
        for metric in corr_candidates + ["get_lock_gc_data_pages_total_us", "outer_inner_gap_us"]:
            vals = np.array([float(r[metric]) for r in completed_rows], dtype=float)
            corr = pearson_corr(vals, outer_total_arr)
            out.writeln(f"{metric}: corr_with_pre_data_lock_us={corr:.6f}")

        out.writeln("")
        out.writeln("=== top slow samples by get_lock_gc_data_pages_total_us ===")
        sorted_rows = sorted(
            completed_rows,
            key=lambda r: float(r["get_lock_gc_data_pages_total_us"]),
            reverse=True,
        )
        top_k = min(10, len(sorted_rows))
        for idx in range(top_k):
            r = sorted_rows[idx]
            vb = float(r["valid_blocks"])
            total_us = float(r["get_lock_gc_data_pages_total_us"])
            per_vb = total_us / vb if vb > 0.0 else float("nan")
            out.writeln(
                "rank={rank} segno={segno} req_idx={req_idx} valid_blocks={valid_blocks} "
                "inner_total_us={inner_total_us} inner_total_per_valid_block_us={inner_total_per_valid_block_us:.3f} "
                "outer_pre_data_lock_us={outer_pre_data_lock_us} first_for_total_us={first_for_total_us} "
                "lock_folio_us={lock_folio_us} check_folio_us={check_folio_us} "
                "find_gc_de_with_lock_us={find_gc_de_with_lock_us} "
                "find_gc_ie_with_lock_us={find_gc_ie_with_lock_us} "
                "get_data_page_us={get_data_page_us} add_gc_folio_us={add_gc_folio_us} "
                "rwsem_nid_equal_us={rwsem_nid_equal_us} rwsem_nid_not_equal_us={rwsem_nid_not_equal_us} "
                "get_node_page_nid_equal_us={get_node_page_nid_equal_us} "
                "get_node_page_nid_not_equal_us={get_node_page_nid_not_equal_us} "
                "outer_inner_gap_us={outer_inner_gap_us}".format(
                    rank=idx + 1,
                    segno=int(r["segno"]),
                    req_idx=int(r["req_idx"]) if r["req_idx"] is not None else -1,
                    valid_blocks=int(r["valid_blocks"]),
                    inner_total_us=int(r["get_lock_gc_data_pages_total_us"]),
                    inner_total_per_valid_block_us=per_vb,
                    outer_pre_data_lock_us=int(r["pre_data_lock_us"]),
                    first_for_total_us=int(r["first_for_total_us"]),
                    lock_folio_us=int(r["lock_folio_us"]),
                    check_folio_us=int(r["check_folio_us"]),
                    find_gc_de_with_lock_us=int(r["find_gc_de_with_lock_us"]),
                    find_gc_ie_with_lock_us=int(r["find_gc_ie_with_lock_us"]),
                    get_data_page_us=int(r["get_data_page_us"]),
                    add_gc_folio_us=int(r["add_gc_folio_us"]),
                    rwsem_nid_equal_us=int(r["rwsem_nid_equal_us"]),
                    rwsem_nid_not_equal_us=int(r["rwsem_nid_not_equal_us"]),
                    get_node_page_nid_equal_us=int(r["get_node_page_nid_equal_us"]),
                    get_node_page_nid_not_equal_us=int(r["get_node_page_nid_not_equal_us"]),
                    outer_inner_gap_us=int(r["outer_inner_gap_us"]),
                )
            )

        out.writeln("")
        out.writeln("=== notes ===")
        out.writeln("1. first_for_other_us = first_for_total_us - first_for_main_path_us")
        out.writeln(
            "2. get_lock_gc_data_pages_total_us = prepare_before_first_for_us + "
            "first_for_total_us + lock_folio_us + check_folio_us"
        )
        out.writeln(
            "3. outer_inner_gap_us = pre_data_lock_us - get_lock_gc_data_pages_total_us"
        )
        out.writeln(
            "4. nested hotspot ratios relative to first_for_total_us may overlap and do not necessarily sum to 1"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)