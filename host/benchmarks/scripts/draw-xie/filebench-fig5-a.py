import os
import re
import sys
from datetime import datetime

import matplotlib.pyplot as plt


# ==============================
# Configuration / Constants
# ==============================

# Labels (legend)
CSGC1 = "va-csgc1"
CSGC2 = "mcsgcv2"
CSGC3 = "mcsgc8thread"

CSGC1 = "va-csgc1"
CSGC2 = "va-csgc2"
CSGC3 = "va-csgc3"

CSGC1 = "mcsgc8t-1"
CSGC2 = "mcsgc8t-2"
CSGC3 = "mcsgc8t-3"

# Absolute paths to filebench log files (must be absolute paths)
FILE_PATH_1 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/filebench-150s/20260130_131334/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"
FILE_PATH_2 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/filebench-150s/20260130_132051/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"
FILE_PATH_3 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/filebench-150s/20260130_132642/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"

FILE_PATH_1 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/filebench-150s/20260205_111343/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"
FILE_PATH_2 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/filebench-150s/20260205_112114/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"
FILE_PATH_3 = "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/filebench-150s/20260205_112607/filebench_fileserver_4t_60G_1M_54k_period_150s_s8/filebench.log"

# Plot style (keep consistent with original script)
COLOR_1 = "#5E96E6"
COLOR_2 = "#7CCD7C"
COLOR_3 = "#E67365"

MARKER_1 = "^"
MARKER_2 = "s"
MARKER_3 = "o"
MARKER_SIZE = 2.5

FIG_SIZE = (10, 2.3)

FONT_SIZE_TITLE = 13
FONT_SIZE_AXIS = 13
FONT_SIZE_TICK = 11
FONT_SIZE_LEGEND = 10

KOP = 1000.0

X_LIM_LEFT = 0
X_LIM_RIGHT = 400

# Regex pattern (same as original)
PATTERN_IO_SUMMARY = r"(\d*.?\d*):\s+IO Summary:\s+(\d*\.?\d*)\s+ops\s+(\d*\.?\d*)\s+ops/s"

# Naming
OUTPUT_NAME_BASE = "filebench_timeline"
OUTPUT_INFO_TAG = "filebench_iosummary_3lines"


# ==============================
# Helpers
# ==============================

def die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def ensure_abs_existing_file(path: str) -> None:
    if not path:
        die("Empty file path.")
    if not os.path.isabs(path):
        die(f"Path is not absolute: {path}")
    if not os.path.isfile(path):
        die(f"File not found: {path}")


def extract_timeline_data_filebench(file_path: str):
    times = []
    ops = []
    opps = []

    rx = re.compile(PATTERN_IO_SUMMARY)

    with open(file_path, "r", errors="replace") as f:
        for line in f:
            m = rx.search(line)
            if not m:
                continue
            times.append(float(m.group(1)))
            ops.append(float(m.group(2)))
            opps.append(float(m.group(3)))

    return times, ops, opps


def normalize_time(times):
    if not times:
        return []
    t0 = times[0]
    return [int(t - t0) for t in times]


def make_unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    idx = 1
    while True:
        cand = f"{base}_{idx}{ext}"
        if not os.path.exists(cand):
            return cand
        idx += 1


# ==============================
# Main
# ==============================

def main() -> None:
    paths = [FILE_PATH_1, FILE_PATH_2, FILE_PATH_3]
    labels = [CSGC1, CSGC2, CSGC3]
    colors = [COLOR_1, COLOR_2, COLOR_3]
    markers = [MARKER_1, MARKER_2, MARKER_3]

    print("[INFO] Checking input files...")
    for p in paths:
        ensure_abs_existing_file(p)
        print(f"[INFO] OK: {p}")

    print("[INFO] Extracting IO Summary timeline data...")
    series = []
    for lbl, p in zip(labels, paths):
        t, op, opps = extract_timeline_data_filebench(p)
        if len(t) == 0:
            die(f"No IO Summary points extracted for label='{lbl}' from file: {p}")
        series.append((lbl, p, t, op, opps))
        print(f"[INFO] label='{lbl}': points={len(t)}")

    n0 = len(series[0][2])
    for lbl, p, t, op, opps in series[1:]:
        if len(t) != n0:
            print("[ERROR] Point count mismatch among files:")
            for lbl2, p2, t2, op2, opps2 in series:
                print(f"  label='{lbl2}' points={len(t2)} file='{p2}'")
            die("Extracted point counts are not equal. Exiting.")

    print(f"[INFO] All series have equal point count: {n0}")

    print("[INFO] Computing totals (K ops) using sum(ops) // 1000 (same as original script)...")
    totals_k = []
    for lbl, p, t, op, opps in series:
        total_k = int(sum(op) // 1000)
        totals_k.append(total_k)
        print(f"[INFO] label='{lbl}': total={total_k}K")

    print("[INFO] Normalizing time (t - t0) and converting throughput to kop/s...")
    norm_times = []
    kopps = []
    for lbl, p, t, op, opps in series:
        nt = normalize_time(t)
        norm_times.append(nt)
        kopps.append([x / KOP for x in opps])

        print(
            f"[INFO] label='{lbl}': time_range=[{nt[0]}, {nt[-1]}] "
            f"throughput_range=[{min(kopps[-1]):.3f}, {max(kopps[-1]):.3f}] kop/s"
        )

    plt.rcParams["axes.titlesize"] = FONT_SIZE_TITLE
    plt.rcParams["axes.labelsize"] = FONT_SIZE_AXIS
    plt.rcParams["xtick.labelsize"] = FONT_SIZE_TICK
    plt.rcParams["ytick.labelsize"] = FONT_SIZE_TICK
    plt.rcParams["legend.fontsize"] = FONT_SIZE_LEGEND

    fig, ax = plt.subplots(figsize=FIG_SIZE)

    for i, (lbl, p, t, op, opps) in enumerate(series):
        ax.plot(
            norm_times[i],
            kopps[i],
            label=labels[i],
            color=colors[i],
            marker=markers[i],
            markersize=MARKER_SIZE,
        )

    y_max = max(max(k) for k in kopps)
    ax.set_xlim(X_LIM_LEFT, X_LIM_RIGHT)
    ax.set_ylim(0, y_max * 1.2)

    try:
        ax.set_yticks(range(0, int(y_max) + 1, 1))
    except Exception:
        pass

    offsets = [-0.15, 0.20, 0.0]
    for i, (lbl, p, t, op, opps) in enumerate(series):
        x_last = norm_times[i][-1]
        y_last = kopps[i][-1]
        ax.text(
            x_last + 10,
            y_last + offsets[i],
            f"total={totals_k[i]:.0f}K",
            color=colors[i],
            ha="left",
            va="bottom",
            fontsize=FONT_SIZE_TICK,
        )

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Throughput (kop/s)")
    ax.grid(True)
    ax.legend()

    dt = datetime.now().strftime("%Y%m%d_%H%M%S")

    cwd = os.getcwd()
    out_dir = os.path.join(cwd, "figs", f"{OUTPUT_INFO_TAG}_{dt}")
    os.makedirs(out_dir, exist_ok=True)

    out_name = f"{OUTPUT_NAME_BASE}_{dt}.png"
    out_path = make_unique_path(os.path.join(out_dir, out_name))

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", format="png", dpi=300)

    print(f"[INFO] Saved figure: {out_path}")


if __name__ == "__main__":
    main()