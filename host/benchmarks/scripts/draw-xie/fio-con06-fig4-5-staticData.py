#!/usr/bin/env python3

from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

# ==============================
# Configuration (edit here)
# ==============================

# Labels shown in legend
BAR_LABELS = [
    "va-csgc",
    "mcsgc8t-old",
    "mcsgc8t-v0411",
]

# Bar values in kIOPS
BAR_VALUES_KIOPS = [
    7.07,
    6.63,
    7.1,
]

# Output settings
OUT_SUBDIR = "figs_fio_uniform_last_iops"
PLOT_TITLE = "fio-uniform (manual throughput values)"
X_TICK_LABEL = "fio-uniform"

FIG_SIZE = (5.2, 3.0)
FONT_SIZE_AXES = 12
FONT_SIZE_LEGEND = 11

BAR_WIDTH = 0.22
Y_LABEL = "Throughput (kIOPS)"

BAR_HATCHES = ["xx", "//", "\\\\"]


# ==============================
# Helpers
# ==============================

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def unique_path(path: Path) -> Path:
    """
    If path exists, append -1, -2, ... before suffix until it is unique.
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


def make_output_path(script_dir: Path) -> Path:
    """
    Build output directory and a dated file name, then ensure uniqueness.
    """
    out_dir = script_dir / OUT_SUBDIR
    ensure_dir(out_dir)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"fio_uniform_manual_iops_{ts}.png"
    return unique_path(out_dir / base_name)


def validate_config(labels, values) -> None:
    if len(labels) != 3:
        raise ValueError(f"Expected exactly 3 labels, got {len(labels)}")
    if len(values) != 3:
        raise ValueError(f"Expected exactly 3 bar values, got {len(values)}")

    for i, value in enumerate(values):
        if not isinstance(value, (int, float)):
            raise TypeError(f"Bar value at index {i} is not numeric: {value!r}")
        if value < 0:
            raise ValueError(f"Bar value at index {i} is negative: {value}")


def autolabel_bars(rects, ax, values_kops):
    """
    Label bars using kIOPS with 1 decimal.
    """
    for i, rect in enumerate(rects):
        height = rect.get_height()
        ax.annotate(
            f"{values_kops[i]:.1f}",
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
    validate_config(BAR_LABELS, BAR_VALUES_KIOPS)

    print("=== fio-uniform bar plot (manual values) ===")
    print("Input values:")
    for label, value in zip(BAR_LABELS, BAR_VALUES_KIOPS):
        print(f"  - {label}: {value:.3f} kIOPS")

    plt.rcParams["axes.titlesize"] = FONT_SIZE_AXES
    plt.rcParams["axes.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["xtick.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["ytick.labelsize"] = FONT_SIZE_AXES
    plt.rcParams["legend.fontsize"] = FONT_SIZE_LEGEND

    fig = plt.figure(figsize=FIG_SIZE)
    ax = fig.add_subplot(1, 1, 1)

    x0 = np.array([0.0])
    x_positions = [x0 - BAR_WIDTH, x0, x0 + BAR_WIDTH]

    rects = []
    for i in range(3):
        rect = ax.bar(
            x_positions[i],
            [BAR_VALUES_KIOPS[i]],
            BAR_WIDTH,
            label=BAR_LABELS[i],
            edgecolor="black",
            hatch=BAR_HATCHES[i],
        )
        rects.append(rect[0])

    ax.set_title(PLOT_TITLE)
    ax.set_ylabel(Y_LABEL)
    ax.set_xticks([0.0])
    ax.set_xticklabels([X_TICK_LABEL])

    y_max = max(BAR_VALUES_KIOPS) if BAR_VALUES_KIOPS else 1.0
    if y_max <= 0:
        y_max = 1.0
    ax.set_ylim(0, y_max * 1.25)

    autolabel_bars(rects, ax, BAR_VALUES_KIOPS)

    ax.legend()
    plt.tight_layout()

    script_dir = Path(__file__).resolve().parent
    out_path = make_output_path(script_dir)
    fig.savefig(out_path, bbox_inches="tight", format="png")
    plt.close(fig)

    print("\nOutput:")
    print(f"  saved_figure: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())