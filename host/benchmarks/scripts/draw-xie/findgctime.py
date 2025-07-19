# -*- coding: utf-8 -*-
"""
Analyze CSGC timing from kern.log AND draw filebench throughput curve
with vertical lines marking every CSGC begin / finish moment.

Author : <your-name>
Date   : 2025-07-12
"""
import numpy as np
import pandas as pd
import os
import re
import sys
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

# ============================================================
# --------------- ❶  >>> USER CONFIG AREA <<<  ---------------
# ============================================================
# (1) log paths
KERN_LOG_PATH      = "/home/xin/work-xie/csgc/xin_scripts/test_data_imporatant/mcsgc/findgctime/mcsgc-findgc-2.log"
FILEBENCH_LOG_PATH = "/home/xin/work-xie/csgc/xin_scripts/test_data_imporatant/mcsgc/findgctime/filebench.log"

# (2) filebench curve setting
CURVE_LABEL             = "mCSGC-filebench"
DATA_POINT_NUMBER       = 40              # how many points to draw (head of list)
FIG_PATH                = "./figs/filebench_find_gc_time.png"
FIG_SIZE                = (10, 3.0)       # inches (w, h)

# (3) visual styles
CURVE_COLOR     = "#E67365"
CURVE_MARKER    = "o"
BEGIN_LINE_KW   = dict(color="#FFA07A", linestyle="--", linewidth=0.1, alpha=0.3)
FINISH_LINE_KW  = dict(color="#ADD8E6", linestyle="--", linewidth=0.1, alpha=0.3)

# ============================================================
# ---------------- ❷  >>> KERN.LOG PARSER <<<  ----------------
# ============================================================


def analyze_csgc_frequency(begin_rel, bin_sec=1, smooth_window=10):
    """
    参数说明
    ----------
    begin_rel      : list[float]  所有 CSGC begin 的相对时间（秒）
    bin_sec        : int         统计频率的时间窗大小（秒）
    smooth_window  : int         滑动平均窗口大小（以 bin 为单位）
    """
    if not begin_rel:
        print("No begin_rel data.")
        return

    # === 1. 直方计：每 bin_sec 秒多少次 ===
    max_t   = int(np.ceil(max(begin_rel)))
    bins    = np.arange(0, max_t + bin_sec, bin_sec)
    counts, _ = np.histogram(begin_rel, bins=bins)

    # === 2. 滑动平均平滑 ===
    freq_series   = pd.Series(counts)
    smooth_counts = freq_series.rolling(window=smooth_window, min_periods=1, center=True).mean()

    # === 3. 打印峰值信息 ===
    peak_idx   = np.argmax(counts)
    print(f"[Freq] peak 1-sec bin: {bins[peak_idx]}-{bins[peak_idx+1]} s, "
          f"{counts[peak_idx]} events")
    print(f"[Freq] mean freq (raw)   : {counts.mean():.2f} /{bin_sec}s")
    print(f"[Freq] mean freq (smooth): {smooth_counts.mean():.2f} /{bin_sec}s")

    # === 4. 画图 ===
    plt.figure(figsize=(10, 3))
    plt.step(bins[:-1], counts, where="post", label=f"raw ({bin_sec}s bin)", alpha=0.4)
    #plt.plot(bins[:-1], smooth_counts, label=f"mov.avg (win={smooth_window})", lw=1.5)
    plt.plot(bins[:-1], smooth_counts.to_numpy(), label=f"mov.avg (win={smooth_window})", lw=1.5)
    plt.xlabel("Time (s)")
    plt.ylabel(f"mCSGC count / {bin_sec}s")
    plt.title("mCSGC frequency over time")
    plt.legend()
    plt.tight_layout()
    
    save_path = get_unique_path("./figs/mcsgc_freq.png")
    plt.savefig(save_path, bbox_inches="tight", format="png")
    print(f"Saved CSGC frequency plot to {save_path}")

def read_text_lines(path: str):
    if not os.path.isfile(path):
        print(f"Error: file not found -> {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.readlines()

def extract_single_t0(lines):
    """Return float t0, ensure exactly ONE match exists."""
    pattern = re.compile(r"\[ *([0-9]+\.[0-9]+)\].*mCSGC prepare to run filebench in bash")
    matches = [float(m.group(1)) for l in lines if (m := pattern.search(l))]
    if len(matches) != 1:
        print(f"Error: expected exactly 1 'prepare' line, found {len(matches)}.")
        sys.exit(1)
    return matches[0]

def extract_time_series(lines, keyword):
    """
    Extract ascending list of floats inside [] preceding `keyword`
    keyword examples: 'do_garbage_collect_cs begin' / 'finish'
    """
    pat = re.compile(r"\[ *([0-9]+\.[0-9]+)\].*" + re.escape(keyword))
    series = [float(m.group(1)) for l in lines if (m := pat.search(l))]
    # order check
    if any(earlier > later for earlier, later in zip(series, series[1:])):
        print(f"Error: timestamps extracted for '{keyword}' are NOT monotonically increasing.")
        sys.exit(1)
    return series

def parse_kern_log(path: str):
    lines = read_text_lines(path)
    t0 = extract_single_t0(lines)
    begin_ts  = extract_time_series(lines, "do_garbage_collect_cs begin")
    finish_ts = extract_time_series(lines, "do_garbage_collect_cs finish")

    # shift by t0
    begin_rel   = [t - t0 for t in begin_ts]
    finish_rel  = [t - t0 for t in finish_ts]

    # print head
    head_n = 10
    print("\n=== KERN.LOG SUMMARY ===")
    print(f"t0 = {t0:.6f}")
    print("csgc_begin_time[0:10]  :", begin_rel[:head_n])
    print("csgc_finish_time[0:10] :", finish_rel[:head_n])

    # duration array if sizes match
    durations = []
    computed  = False
    while (len(begin_rel) > len(finish_rel)):
        begin_rel.pop()
    if len(begin_rel) == len(finish_rel):
        durations = [f - b for b, f in zip(begin_rel, finish_rel)]
        computed = True
        print("durations[0:10]        :", durations[:head_n])
    print(f"Durations computed? {computed}\n")
    print("len(begin_rel) = ",end='')
    print(len(begin_rel))
    print("len(finish_rel) = ",end='')
    print(len(finish_rel))
    return begin_rel, finish_rel

# ============================================================
# --------- ❸  >>> FILEBENCH.LOG DATA & PLOTTING  <<< ---------
# ============================================================

def extract_timeline_data_filebench(path):
    """
    Return three lists:
        t_filebench (float), cumulative_ops (float), current_ops_per_sec (float)
    """
    times, ops, opps = [], [], []
    pat = re.compile(r"(\d+\.?\d*):\s+IO Summary:\s+(\d+\.?\d*)\s+ops\s+(\d+\.?\d*)\s+ops/s")
    for line in read_text_lines(path):
        if (m := pat.search(line)):
            times.append(float(m.group(1)))
            ops.append(float(m.group(2)))
            opps.append(float(m.group(3)))
    return times, ops, opps

def truncate_and_validate(t_raw, op_raw, opps_raw, need_points, label):
    if len(t_raw) < need_points:
        print(f"Error: {label} only has {len(t_raw)} data points (<{need_points}).")
        sys.exit(1)
    t      = t_raw[:need_points]
    op     = op_raw[:need_points]
    opps   = opps_raw[:need_points]
    if any(x == 0 for x in opps):
        print(f"Error: {label} has zero throughput in first {need_points} points.")
        sys.exit(1)
    return t, op, opps

def get_unique_path(path):
    """Avoid overwrite: add _<n> before extension until path free."""
    directory, filename = os.path.split(path)
    name, ext = os.path.splitext(filename)
    counter = 0
    while True:
        candidate = os.path.join(directory, f"{name}_{counter}{ext}") if counter else os.path.join(directory, name + ext)
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def plot_filebench(t, opps, begin_rel, finish_rel):
    opps_k = [x / 1000 for x in opps]
    ymax   = max(opps_k) * 1.2

    plt.figure(figsize=FIG_SIZE)
    gs = GridSpec(1, 1, figure=plt.gcf())
    ax = plt.gcf().add_subplot(gs[0, 0])

    # throughput curve
    ax.plot(t, opps_k, label=CURVE_LABEL, color=CURVE_COLOR,
            marker=CURVE_MARKER, markersize=3)

    # vertical lines: begin (red) & finish (blue)
    for x in begin_rel:
        ax.axvline(x=x, **BEGIN_LINE_KW)
    for x in finish_rel:
        ax.axvline(x=x, **FINISH_LINE_KW)

    # annotate first & last begin
    if begin_rel:
        ax.text(begin_rel[0], ymax*0.95, f"{begin_rel[0]:.1f}s", color="red",
                ha="center", va="top", rotation=90, fontsize=8)
        ax.text(begin_rel[-1], ymax*0.95, f"{begin_rel[-1]:.1f}s", color="red",
                ha="center", va="top", rotation=90, fontsize=8)

    # axes cosmetics
    ax.set_xlim(0, max(t[-1], finish_rel[-1] if finish_rel else t[-1]) + 10)
    ax.set_ylim(0, ymax)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Throughput (kop/s)")
    ax.grid(True)
    ax.legend()

    plt.tight_layout()
    save_path = get_unique_path(FIG_PATH)
    plt.savefig(save_path, bbox_inches="tight", format="png")
    print(f"Saved figure to {save_path}")

# ============================================================
# ---------------- ❹  >>> MAIN WORKFLOW <<<  ------------------
# ============================================================

def main():
    # ---- step-A : parse kern.log ----
    csgc_begin_rel, csgc_finish_rel = parse_kern_log(KERN_LOG_PATH)

    # ---- step-B : parse filebench.log ----
    t_raw, op_raw, opps_raw = extract_timeline_data_filebench(FILEBENCH_LOG_PATH)
    
    
    t, op, opps = truncate_and_validate(
        t_raw, op_raw, opps_raw, DATA_POINT_NUMBER, CURVE_LABEL
    )
    
    # ---- step-C : global time-shift so that all three arrays share same zero ----
    base_time = t[0]                         # MODIFY INFO
    t                 = [x - base_time for x in t]                 # MODIFY INFO
    csgc_begin_rel    = [x - base_time for x in csgc_begin_rel]    # MODIFY INFO
    csgc_finish_rel   = [x - base_time for x in csgc_finish_rel]   # MODIFY INFO
    
    analyze_csgc_frequency(csgc_begin_rel, bin_sec=1, smooth_window=10)
    # ---- step-D : plotting ----
    #plot_filebench(t, opps, csgc_begin_rel, csgc_finish_rel)

if __name__ == "__main__":
    main()
