import argparse
import os
import re
import sys
import math
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


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


RE_STAT = re.compile(
    r"""
    ^\[\s*\d+\.\d+\]\s+
    (?P<tag>BUG:\s*)?mCSGCv2_STAT\s+
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


def derive_figdir(logfile: str) -> str:
    base = os.path.basename(logfile)
    if base.endswith(".log"):
        stem = base[:-4]
    else:
        stem = os.path.splitext(base)[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = "./figs"
    ensure_dir(root)
    cand = os.path.join(root, f"{stem}_{ts}")
    cand = unique_dir(cand)
    ensure_dir(cand)
    return cand


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile", help="path to kernel log file")
    args = ap.parse_args()

    figdir = derive_figdir(args.logfile)

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

    with open(args.logfile, "r", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            m = RE_STAT.match(line)
            if m:
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

    seg_count = len(segno)
    sec_count = len(section_gc_time_us)

    print(f"figdir={figdir}")
    print(f"segment_samples={seg_count}")
    print(f"section_samples={sec_count}")

    if sec_count * 8 != seg_count:
        print(f"ERROR: segment_samples != section_samples*8 ({seg_count} != {sec_count}*8)")
        return 2

    print("")
    print("=== basic statistics (microseconds) ===")

    all_metrics: List[Tuple[str, List[Optional[int]]]] = []
    for k in STAT_KEYS:
        all_metrics.append((k, metric_arrays[k]))
    for k in POST_EXTRA_KEYS:
        all_metrics.append((k, metric_arrays[k]))

    for name, xs in all_metrics:
        arr = safe_float_array(xs)
        s = summarize_metric(name, arr)
        print(
            f"{name}: n={int(s['count'])} mean={s['mean']:.3f} min={s['min']:.3f} "
            f"max={s['max']:.3f} median={s['median']:.3f} p80={s['p80']:.3f} top20_mean={s['top20_mean']:.3f}"
        )

    sec_arr = safe_float_array([int(v) for v in section_gc_time_us])
    sec_s = summarize_metric("section_gc_time_us", sec_arr)
    print(
        f"section_gc_time_us: n={int(sec_s['count'])} mean={sec_s['mean']:.3f} min={sec_s['min']:.3f} "
        f"max={sec_s['max']:.3f} median={sec_s['median']:.3f} p80={sec_s['p80']:.3f} top20_mean={sec_s['top20_mean']:.3f}"
    )

    do_arr = safe_float_array([int(v) for v in do_garbage_collect_cs_us])
    do_s = summarize_metric("do_garbage_collect_cs_us", do_arr)
    print(
        f"do_garbage_collect_cs_us: n={int(do_s['count'])} mean={do_s['mean']:.3f} min={do_s['min']:.3f} "
        f"max={do_s['max']:.3f} median={do_s['median']:.3f} p80={do_s['p80']:.3f} top20_mean={do_s['top20_mean']:.3f}"
    )

    print("")
    print("=== figures: scatter by sample index ===")

    for name, xs in all_metrics:
        out = unique_path(os.path.join(figdir, f"{name}.png"))
        scatter_index_plot(xs, title=name, ylabel=name, out_path=out)
        print(f"saved {out}")

    out = unique_path(os.path.join(figdir, "section_gc_time_us.png"))
    scatter_index_plot(
        [int(v) for v in section_gc_time_us],
        title="section_gc_time_us",
        ylabel="section_gc_time_us",
        out_path=out,
    )
    print(f"saved {out}")

    out = unique_path(os.path.join(figdir, "do_garbage_collect_cs_us.png"))
    scatter_index_plot(
        [int(v) for v in do_garbage_collect_cs_us],
        title="do_garbage_collect_cs_us",
        ylabel="do_garbage_collect_cs_us",
        out_path=out,
    )
    print(f"saved {out}")

    print("")
    print("=== diagnostic A: phase ratio distribution (phase / approx_segment_total_us) ===")
    takes_full = np.array(
        [np.nan if v is None else float(v) for v in metric_arrays["approx_segment_total_us"]],
        dtype=float,
    )
    takes = takes_full[np.isfinite(takes_full)]
    takes = takes[takes >= 0.0]
    if takes.size == 0:
        print("ERROR: approx_segment_total_us has no valid samples")
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
        print(f"{name}_ratio: n={ratio.size} p50={p50:.6f} p90={p90:.6f} p99={p99:.6f}")

    print("")
    print("=== diagnostic B: correlation with approx_segment_total_us and phase-vs-takes scatter ===")
    for name, xs in all_metrics:
        if name in ("approx_segment_total_us",):
            continue
        phase = np.array([np.nan if v is None else float(v) for v in xs], dtype=float)
        corr = pearson_corr(phase, takes_full)
        print(f"{name}: corr_with_approx_segment_total_us={corr:.6f}")
        out = unique_path(os.path.join(figdir, f"{name}_vs_approx_segment_total_us.png"))
        scatter_xy_plot(
            x=phase,
            y=takes_full,
            title=f"{name} vs approx_segment_total_us",
            xlabel=name,
            ylabel="approx_segment_total_us",
            out_path=out,
        )
        print(f"saved {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
