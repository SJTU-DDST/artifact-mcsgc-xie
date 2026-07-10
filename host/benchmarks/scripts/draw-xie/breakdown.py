import argparse
import os
import re
import subprocess
import sys
import math
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np


# Set to 1 when scatter figures are needed. Keep it disabled by default for
# performance-oriented runs where generating many PNGs adds unnecessary work.
FIG_OUTPUT = 0

if FIG_OUTPUT:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt


STAT_PREFIXES = [
    "mCSGCv2_STAT",
    "mCSGCv2_STAT without wait",
    "CSGC-va_STAT",
    "mCSGCv2_STAT 2thread without wait",
    "mCSGC8t_STAT without wait",
    "mCSGC2t_STAT without wait"
]


STAT_KEYS = [
    "section_sync_us",
    "pre_queue_delay_us",
    "pre_work_total_us",
    "pre_sum_us",
    "pre_node_list_us",
    "pre_inode_lock_us",
    "pre_data_lock_us",
    "pre_cp_rwsem_lock_us",
    "pre_node_pages_lock_us",
    "pre_get_valid_blocks_us",
    "pre_check_data_validness_us",
    "pre_pack_prealloc_us",
    "pre_request_trigger_us",
    "pre_submit_completion_read_us",
    "pre_tail_us",
    "approx_gc_cs_ssd_us",
    "post_queue_delay_us",
    "post_update_meta_us",
    "post_middle_work_us",
    "approx_segment_total_us",
    "segment_finish_offset_us",
]

POST_EXTRA_KEYS = [
    "post_work_from_free_csi_to_finish_time_us",
    "f2fs_post_csgc_work_time_us",
    "this_segment_gc_time_us",
]

SECTION_KEYS = [
    "section_gc_time_us",
]

DO_CSGC_KEYS = [
    "do_garbage_collect_cs_us",
    "csgc_called",
]


STAT_PREFIX_PATTERN = "|".join(re.escape(p) for p in sorted(STAT_PREFIXES, key=len, reverse=True))

RE_STAT = re.compile(
    rf"""
    ^\[\s*\d+\.\d+\]\s+
    (?P<tag>BUG:\s*)?(?P<prefix>{STAT_PREFIX_PATTERN})\s+
    segno=(?P<segno>\d+)\s+
    req_idx=(?P<req_idx>\d+)\s+
    pid=(?P<pid>\d+)\s+
    tgid=(?P<tgid>\d+)\s+
    comm=(?P<comm>\S+)\s+
    (?P<kv>.*)$
    """,
    re.VERBOSE,
)

RE_POST = re.compile(
    r"""
    ^\[\s*\d+\.\d+\]\s+
    (?:DEBUG_M_LEAST<[^>]+>:\s*)?
    post\s+work\s+from\s+free\s+csi\s+to\s+the\s+finish\s+time\s*=\s*(?P<free_to_finish>\d+)\s+us,
    f2fs_post_csgc_work\s+time\s*=\s*(?P<post_time>\d+)\s+us,\s+
    this\s+segment\s+gc\s+time\s*=\s*(?P<seg_gc_time>\d+)\s+us,\s+
    from\s+segno=(?P<segno>\d+),\s+
    pid=(?P<pid>\d+)\s+tgid=(?P<tgid>\d+)\s+comm=(?P<comm>\S+)
    """,
    re.VERBOSE,
)

RE_SECTION = re.compile(
    r"""
    ^\[\s*\d+\.\d+\]\s+
    section_gc_time\s*=\s*(?P<section_gc_time>\d+)\s+us,
    from\s+pid=(?P<pid>\d+)\s+tgid=(?P<tgid>\d+)\s+comm=(?P<comm>\S+)
    """,
    re.VERBOSE,
)

RE_DO_CSGC = re.compile(
    r"""
    ^\[\s*\d+\.\d+\]\s+
    do_garbage_collect_cs\s*=\s*(?P<do_time>\d+)\s+us,\s+
    csgc_called\s*=\s*(?P<csgc_called>\d+)
    from\s+pid=(?P<pid>\d+)\s+tgid=(?P<tgid>\d+)\s+comm=(?P<comm>\S+)
    """,
    re.VERBOSE,
)


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def unique_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    i = 1
    while True:
        cand = f"{path}_{i}"
        if not os.path.exists(cand):
            return cand
        i += 1


def run_heavy_trace_parser(logfile: str, output_path: str, kind: str = "csgc") -> int:
    """Run the shared CSGC/ORIGC heavy-trace parser."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "breakdown-csgc-heavy-trace.py")

    try:
        completed = subprocess.run(
            [sys.executable, script_path, logfile, output_path, "--kind", kind],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(
            f"WARNING: failed to run {kind.upper()} heavy trace parser: {exc}",
            file=sys.stderr,
        )
        return 1

    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.rstrip(), file=sys.stderr)
        print(
            f"WARNING: {kind.upper()} heavy trace parser exited with {completed.returncode}",
            file=sys.stderr,
        )
        return completed.returncode

    if completed.stdout:
        print(completed.stdout.rstrip())
    return 0


def safe_float_array(xs: List[Optional[int]]) -> np.ndarray:
    arr = np.array([np.nan if (v is None) else float(v) for v in xs], dtype=float)
    arr = arr[np.isfinite(arr)]
    arr = arr[arr >= 0.0]
    return arr


def percentile(arr: np.ndarray, p: float) -> float:
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, p))


def top_k_mean(arr: np.ndarray, frac: float) -> float:
    if arr.size == 0:
        return float("nan")
    n = arr.size
    k = max(1, int(math.ceil(frac * n)))
    s = np.sort(arr)
    top = s[-k:]
    return float(np.mean(top))


def summarize_metric(name: str, arr: np.ndarray) -> Dict[str, float]:
    out = {
        "count": float(arr.size),
        "mean": float(np.mean(arr)) if arr.size else float("nan"),
        "min": float(np.min(arr)) if arr.size else float("nan"),
        "max": float(np.max(arr)) if arr.size else float("nan"),
        "median": float(np.median(arr)) if arr.size else float("nan"),
        "p80": percentile(arr, 80.0),
        "top20_mean": top_k_mean(arr, 0.20),
    }
    return out


def scatter_index_plot(xs: List[Optional[int]], title: str, ylabel: str, out_path: str) -> None:
    y = np.array([np.nan if v is None else float(v) for v in xs], dtype=float)
    idx = np.arange(len(y), dtype=float)
    m = np.isfinite(y)
    idx = idx[m]
    y = y[m]
    plt.figure()
    plt.scatter(idx, y, s=6)
    plt.title(title)
    plt.xlabel("sample_index")
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def scatter_xy_plot(x: np.ndarray, y: np.ndarray, title: str, xlabel: str, ylabel: str, out_path: str) -> None:
    m = np.isfinite(x) & np.isfinite(y)
    x2 = x[m]
    y2 = y[m]
    plt.figure()
    plt.scatter(x2, y2, s=6)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def pearson_corr(x: np.ndarray, y: np.ndarray) -> float:
    m = np.isfinite(x) & np.isfinite(y)
    x2 = x[m]
    y2 = y[m]
    if x2.size < 3:
        return float("nan")
    if np.std(x2) == 0.0 or np.std(y2) == 0.0:
        return float("nan")
    return float(np.corrcoef(x2, y2)[0, 1])


def parse_kv_blob(blob: str) -> Dict[str, int]:
    kv = {}
    parts = blob.strip().split()
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        if not k:
            continue
        try:
            kv[k] = int(v)
        except ValueError:
            continue
    return kv


def derive_run_dir(logfile: str) -> str:
    base = os.path.basename(logfile)
    if base.endswith(".log"):
        stem = base[:-4]
    else:
        stem = os.path.splitext(base)[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(script_dir, "breakdown-result")
    ensure_dir(root)
    cand = os.path.join(root, f"{stem}_{ts}")
    cand = unique_dir(cand)
    ensure_dir(cand)
    return cand


def analyze_lseg_lsection_pattern(events: List[str]) -> Dict[Tuple[int, int], int]:
    pattern_counts: Dict[Tuple[int, int], int] = defaultdict(int)

    seg_run = 0
    section_run = 0

    for ev in events:
        if ev == "seg":
            if section_run > 0:
                pattern_counts[(seg_run, section_run)] += 1
                seg_run = 1
                section_run = 0
            else:
                seg_run += 1
        elif ev == "section":
            if seg_run == 0 and section_run == 0:
                section_run = 1
            else:
                section_run += 1
        else:
            raise ValueError(f"unknown event type: {ev}")

    if seg_run > 0 or section_run > 0:
        pattern_counts[(seg_run, section_run)] += 1

    return dict(pattern_counts)


def invalid_lseg_lsection_patterns(
    pattern_counts: Dict[Tuple[int, int], int]
) -> Dict[Tuple[int, int], int]:
    bad: Dict[Tuple[int, int], int] = {}
    for pair, cnt in pattern_counts.items():
        a, b = pair
        if not (1 <= a <= 8 and b == 1):
            bad[pair] = cnt
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="path to kernel log file")
    args = ap.parse_args()

    run_dir = derive_run_dir(args.logfile)
    png_dir = os.path.join(run_dir, "pngs")
    if FIG_OUTPUT:
        ensure_dir(png_dir)
    result_path = os.path.join(run_dir, "result.txt")
    heavy_trace_result_path = os.path.join(run_dir, "csgc_heavy_trace_result.txt")
    origc_heavy_trace_result_path = os.path.join(
        run_dir, "origc_heavy_trace_result.txt"
    )

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    result_fp = open(result_path, "w", encoding="utf-8")

    def emit(message: str = "", to_file: bool = True, stderr: bool = False) -> None:
        stream = original_stderr if stderr else original_stdout
        print(message, file=stream)
        stream.flush()
        if to_file:
            print(message, file=result_fp)
            result_fp.flush()

    try:
        segno: List[int] = []
        req_idx: List[int] = []
        pid: List[int] = []
        tgid: List[int] = []
        comm: List[str] = []
        is_bug: List[int] = []

        metric_arrays: Dict[str, List[Optional[int]]] = {k: [] for k in STAT_KEYS}
        for k in POST_EXTRA_KEYS:
            metric_arrays[k] = []

        post_pending: Dict[int, deque] = defaultdict(deque)

        section_gc_time_us: List[int] = []
        section_pid: List[int] = []
        section_tgid: List[int] = []
        section_comm: List[str] = []

        do_garbage_collect_cs_us: List[int] = []
        csgc_called: List[int] = []
        do_pid: List[int] = []
        do_tgid: List[int] = []
        do_comm: List[str] = []

        seen_stat_prefixes = set()
        lseg_lsection_events: List[str] = []

        with open(args.logfile, "r", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")

                m = RE_STAT.match(line)
                if m:
                    seen_stat_prefixes.add(m.group("prefix"))
                    lseg_lsection_events.append("seg")

                    idx = len(segno)
                    s = int(m.group("segno"))
                    segno.append(s)
                    req_idx.append(int(m.group("req_idx")))
                    pid.append(int(m.group("pid")))
                    tgid.append(int(m.group("tgid")))
                    comm.append(m.group("comm"))
                    is_bug.append(1 if m.group("tag") else 0)

                    kv = parse_kv_blob(m.group("kv"))
                    for k in STAT_KEYS:
                        metric_arrays[k].append(kv.get(k, None))

                    for k in POST_EXTRA_KEYS:
                        metric_arrays[k].append(None)

                    post_pending[s].append(idx)
                    continue

                m = RE_POST.match(line)
                if m:
                    s = int(m.group("segno"))
                    if not post_pending[s]:
                        continue
                    idx = post_pending[s].popleft()
                    metric_arrays["post_work_from_free_csi_to_finish_time_us"][idx] = int(m.group("free_to_finish"))
                    metric_arrays["f2fs_post_csgc_work_time_us"][idx] = int(m.group("post_time"))
                    metric_arrays["this_segment_gc_time_us"][idx] = int(m.group("seg_gc_time"))
                    continue

                m = RE_SECTION.match(line)
                if m:
                    lseg_lsection_events.append("section")
                    section_gc_time_us.append(int(m.group("section_gc_time")))
                    section_pid.append(int(m.group("pid")))
                    section_tgid.append(int(m.group("tgid")))
                    section_comm.append(m.group("comm"))
                    continue

                m = RE_DO_CSGC.match(line)
                if m:
                    do_garbage_collect_cs_us.append(int(m.group("do_time")))
                    csgc_called.append(int(m.group("csgc_called")))
                    do_pid.append(int(m.group("pid")))
                    do_tgid.append(int(m.group("tgid")))
                    do_comm.append(m.group("comm"))
                    continue

        run_heavy_trace_parser(args.logfile, heavy_trace_result_path, "csgc")
        heavy_trace_generated = os.path.exists(heavy_trace_result_path)
        run_heavy_trace_parser(
            args.logfile, origc_heavy_trace_result_path, "origc"
        )
        origc_heavy_trace_generated = os.path.exists(
            origc_heavy_trace_result_path
        )

        pattern_counts = analyze_lseg_lsection_pattern(lseg_lsection_events)
        bad_patterns = invalid_lseg_lsection_patterns(pattern_counts)

        emit("=== Lseg/Lsection pattern statistics ===")
        emit("Lseg means a line matched by RE_STAT.")
        emit("Lsection means a line matched by RE_SECTION.")
        emit("Only the relative order of these two kinds of lines is considered.")
        emit("Each pair (a, b) means: a consecutive Lseg lines followed by b consecutive Lsection lines.")
        emit(f"total_Lseg={len(segno)}")
        emit(f"total_Lsection={len(section_gc_time_us)}")
        emit("pattern_counts:")
        for (a, b), cnt in sorted(pattern_counts.items()):
            emit(f"  (a,b)=({a},{b}) count={cnt}")
        emit("")

        if bad_patterns:
            emit("ERROR: invalid Lseg/Lsection pattern(s) found.", stderr=True)
            emit("Allowed pattern constraints are: 1 <= a <= 8 and b == 1.", stderr=True)
            for (a, b), cnt in sorted(bad_patterns.items()):
                emit(f"  invalid (a,b)=({a},{b}) count={cnt}", stderr=True)
            return 6

        if len(seen_stat_prefixes) == 0 and origc_heavy_trace_generated:
            emit("=== ORIGC heavy-only analysis ===")
            emit(f"run_dir={run_dir}")
            emit(f"result_file={result_path}")
            emit(
                "origc_heavy_trace_result_file="
                f"{origc_heavy_trace_result_path}"
            )
            emit("segment_samples=0")
            emit("section_samples=0")
            return 0

        if len(seen_stat_prefixes) == 0:
            emit("ERROR: no STAT lines matched any configured prefix", stderr=True)
            return 4

        if len(seen_stat_prefixes) > 1:
            emit(f"ERROR: multiple STAT prefixes found in one file: {sorted(seen_stat_prefixes)}", stderr=True)
            return 5

        used_prefix = next(iter(seen_stat_prefixes))

        seg_count = len(segno)
        sec_count = len(section_gc_time_us)

        emit(f"run_dir={run_dir}")
        emit(f"png_dir={png_dir if FIG_OUTPUT else 'disabled'}")
        emit(f"result_file={result_path}")
        emit(f"heavy_trace_result_file={heavy_trace_result_path if heavy_trace_generated else 'not_generated'}")
        emit(
            "origc_heavy_trace_result_file="
            f"{origc_heavy_trace_result_path if origc_heavy_trace_generated else 'not_generated'}"
        )
        emit(f"stat_prefix={used_prefix}")
        emit(f"segment_samples={seg_count}")
        emit(f"section_samples={sec_count}")

        emit("")
        emit("=== basic statistics (microseconds) ===")

        all_metrics: List[Tuple[str, List[Optional[int]]]] = []
        for k in STAT_KEYS:
            all_metrics.append((k, metric_arrays[k]))
        for k in POST_EXTRA_KEYS:
            all_metrics.append((k, metric_arrays[k]))

        for name, xs in all_metrics:
            arr = safe_float_array(xs)
            s = summarize_metric(name, arr)
            emit(
                f"{name}: n={int(s['count'])} mean={s['mean']:.3f} min={s['min']:.3f} "
                f"max={s['max']:.3f} median={s['median']:.3f} p80={s['p80']:.3f} top20_mean={s['top20_mean']:.3f}"
            )

        sec_arr = safe_float_array([int(v) for v in section_gc_time_us])
        sec_s = summarize_metric("section_gc_time_us", sec_arr)
        emit(
            f"section_gc_time_us: n={int(sec_s['count'])} mean={sec_s['mean']:.3f} min={sec_s['min']:.3f} "
            f"max={sec_s['max']:.3f} median={sec_s['median']:.3f} p80={sec_s['p80']:.3f} top20_mean={sec_s['top20_mean']:.3f}"
        )

        do_arr = safe_float_array([int(v) for v in do_garbage_collect_cs_us])
        do_s = summarize_metric("do_garbage_collect_cs_us", do_arr)
        emit(
            f"do_garbage_collect_cs_us: n={int(do_s['count'])} mean={do_s['mean']:.3f} min={do_s['min']:.3f} "
            f"max={do_s['max']:.3f} median={do_s['median']:.3f} p80={do_s['p80']:.3f} top20_mean={do_s['top20_mean']:.3f}"
        )

        emit("")
        emit("=== figures: scatter by sample index ===")

        if FIG_OUTPUT:
            for name, xs in all_metrics:
                out = unique_path(os.path.join(png_dir, f"{name}.png"))
                scatter_index_plot(xs, title=name, ylabel=name, out_path=out)
                emit(f"saved {out}", to_file=False)

            out = unique_path(os.path.join(png_dir, "section_gc_time_us.png"))
            scatter_index_plot(
                [int(v) for v in section_gc_time_us],
                title="section_gc_time_us",
                ylabel="section_gc_time_us",
                out_path=out,
            )
            emit(f"saved {out}", to_file=False)

            out = unique_path(os.path.join(png_dir, "do_garbage_collect_cs_us.png"))
            scatter_index_plot(
                [int(v) for v in do_garbage_collect_cs_us],
                title="do_garbage_collect_cs_us",
                ylabel="do_garbage_collect_cs_us",
                out_path=out,
            )
            emit(f"saved {out}", to_file=False)
        else:
            emit("FIG_OUTPUT=0, skip PNG generation.")

        emit("")
        emit("=== diagnostic A: phase ratio distribution (phase / approx_segment_total_us) ===")
        takes_full = np.array(
            [np.nan if v is None else float(v) for v in metric_arrays["approx_segment_total_us"]],
            dtype=float,
        )
        takes = takes_full[np.isfinite(takes_full)]
        takes = takes[takes >= 0.0]
        if takes.size == 0:
            emit("ERROR: approx_segment_total_us has no valid samples", stderr=True)
            return 3

        for name, xs in all_metrics:
            if name in ("approx_segment_total_us",):
                continue
            phase = np.array([np.nan if v is None else float(v) for v in xs], dtype=float)
            ratio = phase / takes_full
            ratio = ratio[np.isfinite(ratio)]
            ratio = ratio[ratio >= 0.0]
            if ratio.size == 0:
                continue
            p50 = percentile(ratio, 50.0)
            p90 = percentile(ratio, 90.0)
            p99 = percentile(ratio, 99.0)
            emit(f"{name}_ratio: n={ratio.size} p50={p50:.6f} p90={p90:.6f} p99={p99:.6f}")

        emit("")
        emit("=== diagnostic B: correlation with approx_segment_total_us and phase-vs-takes scatter ===")
        for name, xs in all_metrics:
            if name in ("approx_segment_total_us",):
                continue
            phase = np.array([np.nan if v is None else float(v) for v in xs], dtype=float)
            corr = pearson_corr(phase, takes_full)
            emit(f"{name}: corr_with_approx_segment_total_us={corr:.6f}")
            if FIG_OUTPUT:
                out = unique_path(os.path.join(png_dir, f"{name}_vs_approx_segment_total_us.png"))
                scatter_xy_plot(
                    x=phase,
                    y=takes_full,
                    title=f"{name} vs approx_segment_total_us",
                    xlabel=name,
                    ylabel="approx_segment_total_us",
                    out_path=out,
                )
                emit(f"saved {out}", to_file=False)

        if heavy_trace_generated:
            emit("")
            emit("=== CSGC heavy trace parser ===")
            emit(f"heavy_trace_result_file={heavy_trace_result_path}")
        if origc_heavy_trace_generated:
            emit("")
            emit("=== ORIGC heavy trace parser ===")
            emit(
                f"origc_heavy_trace_result_file={origc_heavy_trace_result_path}"
            )
        return 0

    finally:
        result_fp.close()


if __name__ == "__main__":
    raise SystemExit(main())
