import os
import re
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# ==============================
# Configuration (edit here)
# ==============================

# Labels shown in legend
CSGC1_LABEL = "va-csgc"
CSGC2_LABEL = "mcsgcv2"
CSGC3_LABEL = "mcsgc8thread"

CSGC1_LABEL = "mcsgc8t-1"
CSGC2_LABEL = "mcsgc8t-2"
CSGC3_LABEL = "mcsgc8t-3"

CSGC1_LABEL = "va-csgc"
CSGC2_LABEL = "mcsgcv2"
CSGC3_LABEL = "mcsgc8t"

CSGC1_LABEL = "va-csgc"
CSGC2_LABEL = "va-csgc2"
CSGC3_LABEL = "va-csgc3"

CSGC1_LABEL = "mcsgc8t-v0411-1"
CSGC2_LABEL = "mcsgc8t-v0411-2"
CSGC3_LABEL = "mcsgc8t-v0411-3"

CSGC1_LABEL = "va-csgc"
CSGC2_LABEL = "mcsgc8t-old"
CSGC3_LABEL = "mcsgc8t-v0411-3"


# Absolute paths to fio.log files (edit these)
FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205143/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205854/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_210739/fio_randwrite_s8_0.86_random/fio.log",
]

FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114302/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114922/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_115526/fio_randwrite_s8_0.86_random/fio.log"
]

FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114302/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114922/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_115526/fio_randwrite_s8_0.86_random/fio.log"
]

FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205143/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205854/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_210739/fio_randwrite_s8_0.86_random/fio.log",
]

FIO_LOG_PATHS = [
"/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_172218/fio_randwrite_s8_0.86_random/fio.log",
"/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_172834/fio_randwrite_s8_0.86_random/fio.log",
"/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_174659/fio_randwrite_s8_0.86_random/fio.log"
]

FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_173437/fio_randwrite_s8_0.86_random/fio.log",
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_174052/fio_randwrite_s8_0.86_random/fio.log",
"/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_174659/fio_randwrite_s8_0.86_random/fio.log"

]

FIO_LOG_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205143/fio_randwrite_s8_0.86_random/fio.log",
        "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114302/fio_randwrite_s8_0.86_random/fio.log",
"/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_174052/fio_randwrite_s8_0.86_random/fio.log",

]





# Output settings
OUT_SUBDIR = "figs_fio_uniform_last_iops"
PLOT_TITLE = "fio-uniform (last write IOPS per file)"
X_TICK_LABEL = "fio-uniform"

FIG_SIZE = (5.2, 3.0)
FONT_SIZE_AXES = 12
FONT_SIZE_LEGEND = 11

BAR_WIDTH = 0.22
Y_LABEL = "Throughput (kIOPS)"

# Regex for matching IOPS lines (case-sensitive)
IOPS_REGEX = re.compile(r"\s+write:\s+IOPS=([0-9]+(?:\.[0-9]+)?)(k?)")

# ==============================
# Helpers
# ==============================

def parse_last_write_iops(file_path: str) -> dict:
    """
    Parse the last occurrence of 'write: IOPS=' in the given file (case-sensitive).
    Returns a dict with parsed info.
    """
    if not os.path.isabs(file_path):
        raise ValueError(f"Expected an absolute path, got: {file_path}")

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"fio log not found: {file_path}")

    last_match = None
    last_line_no = None
    match_count = 0

    with open(file_path, "r", errors="replace") as f:
        for idx, line in enumerate(f, start=1):
            m = IOPS_REGEX.search(line)
            if m:
                match_count += 1
                last_match = m
                last_line_no = idx
                last_line = line.rstrip("\n")

    if last_match is None:
        raise RuntimeError(
            f"No match for 'write: IOPS=' found (case-sensitive) in file: {file_path}"
        )

    value_str = last_match.group(1)
    suffix_k = last_match.group(2)

    iops = float(value_str)
    if suffix_k == "k":
        iops *= 1000.0

    return {
        "file_path": file_path,
        "match_count": match_count,
        "last_line_no": last_line_no,
        "last_line": last_line,
        "iops": iops,
    }


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def unique_path(path: Path) -> Path:
    """
    If path exists, append -1, -2, ... before suffix until it's unique.
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    i = 1
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def make_output_paths(script_dir: Path) -> Path:
    """
    Build output directory and a dated file name, then ensure uniqueness.
    """
    out_dir = script_dir / OUT_SUBDIR
    ensure_dir(out_dir)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"fio_uniform_last_write_iops_{ts}.png"
    out_path = unique_path(out_dir / base_name)
    return out_path


def autolabel_kops(rects, ax, raw_iops_list):
    """
    Label bars using kIOPS (raw_iops/1000) with 1 decimal.
    """
    for i, rect in enumerate(rects):
        kops = raw_iops_list[i] / 1000.0
        height = rect.get_height()
        ax.annotate(
            f"{kops:.1f}",
            xy=(rect.get_x() + rect.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=FONT_SIZE_LEGEND,
        )


# ==============================
# Main
# ==============================

def main() -> int:
    labels = [CSGC1_LABEL, CSGC2_LABEL, CSGC3_LABEL]

    print("=== fio-uniform bar plot (last write IOPS per file) ===")
    print("Input files:")
    for p, lab in zip(FIO_LOG_PATHS, labels):
        print(f"  - {lab}: {p}")

    results = []
    for p in FIO_LOG_PATHS:
        info = parse_last_write_iops(p)
        results.append(info)

    print("\nParsed results:")
    for lab, info in zip(labels, results):
        print(f"[{lab}] matches={info['match_count']}, last_line={info['last_line_no']}, iops={info['iops']:.3f}")
        print(f"  last_match_line: {info['last_line']}")

    raw_iops = [r["iops"] for r in results]
    raw_kops = [v / 1000.0 for v in raw_iops]

    # Plot: only one group at x=0, with 3 bars around it
    plt.rcParams["axes.titlesize"] = FONT_SIZE_AXES
    plt.rcParams["axes.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["xtick.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["ytick.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["legend.fontsize"] = FONT_SIZE_LEGEND

    fig = plt.figure(figsize=FIG_SIZE)
    ax = fig.add_subplot(1, 1, 1)

    x0 = np.array([0.0])
    x_positions = [x0 - BAR_WIDTH, x0, x0 + BAR_WIDTH]

    rects1 = ax.bar(x_positions[0], [raw_kops[0]], BAR_WIDTH, label=labels[0], edgecolor="black", hatch="xx")
    rects2 = ax.bar(x_positions[1], [raw_kops[1]], BAR_WIDTH, label=labels[1], edgecolor="black", hatch="//")
    rects3 = ax.bar(x_positions[2], [raw_kops[2]], BAR_WIDTH, label=labels[2], edgecolor="black", hatch="\\\\")

    ax.set_title(PLOT_TITLE)
    ax.set_ylabel(Y_LABEL)
    ax.set_xticks([0.0])
    ax.set_xticklabels([X_TICK_LABEL])

    y_max = max(raw_kops) if raw_kops else 1.0
    ax.set_ylim(0, y_max * 1.25)

    # Labels on bars
    autolabel_kops([rects1[0], rects2[0], rects3[0]], ax, raw_iops)

    ax.legend()
    plt.tight_layout()

    script_dir = Path(__file__).resolve().parent
    out_path = make_output_paths(script_dir)
    fig.savefig(out_path, bbox_inches="tight", format="png")
    plt.close(fig)

    print("\nOutput:")
    print(f"  saved_figure: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
