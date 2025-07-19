#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Compare fio-uniform throughput of F2FS (origc), mCSGC and CSGC.

Output: a PDF bar chart with normalized throughput and the mCSGC/CSGC ratio.
"""

import os
import re
import datetime
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

from utils import get_latest_data_dir  # must be present in the project

# ----------------------------------------------------------------------
# ----------------------------- SETTINGS --------------------------------
# ----------------------------------------------------------------------

# One workload only
FIO_UNIFORM_NAME = "fio_randwrite_s8_0.86_random"

# Optional hard-coded log paths (set to a string to override auto-search)
HARDCODED_CSGC_LOG   = None  # e.g. "/abs/path/to/outputs-csgc/log.txt"
HARDCODED_MCSGC_LOG  = None
HARDCODED_ORI_LOG    = None

# Default root directories
ROOT_CSGC  = "outputs-cs"
ROOT_MCSGC = "outputs-mcsgc"
ROOT_ORI   = "outputs-ori"

# Figure appearance
FIG_SIZE        = (5, 3)
BAR_WIDTH       = 0.25
LABEL_ORI       = "F2FS"
LABEL_MCSGC     = "mCSGC"
LABEL_CSGC      = "CSGC"
COLOR_ORI       = "#A8C3E6"
COLOR_MCSGC     = "#9AFF9A"
COLOR_CSGC      = "#E6B4AE"
FONT_SIZE_MAIN  = 13
FONT_SIZE_LABEL = 11

# Output directory for figures
FIG_DIR = "figs"

# ----------------------------------------------------------------------
# ---------------------- HELPER FUNCTIONS ------------------------------
# ----------------------------------------------------------------------

def workload_type_from_name(name: str) -> str:
    """Return 'filebench', 'ycsb', or 'fio' based on filename."""
    if "filebench" in name:
        return "filebench"
    if "ycsb" in name:
        return "ycsb"
    if "fio" in name:
        return "fio"
    raise ValueError(f"Cannot determine workload type from {name}")

PATTERNS = {
    "filebench": r'IO Summary:\s+\d*\.?\d*\s+ops\s+(\d*\.?\d*)\s+ops/s',
    "ycsb":      r'Throughput\(ops/sec\),\s+(\d*\.?\d*)',
    "fio":       r'\s+write: IOPS=(\d*\.?\d*k?)'
}

def extract_perf(path: str) -> float:
    """Read a single log file and return the throughput value as float ops/s."""
    wtype   = workload_type_from_name(os.path.basename(path))
    pattern = PATTERNS[wtype]
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = re.search(pattern, line)
            if m:
                perf_str = m.group(1)
                return float(perf_str[:-1]) * 1_000 if perf_str.endswith("k") else float(perf_str)
    raise RuntimeError(f"No matching throughput line found in {path}")

def make_unique_fig_path(base_name_no_ext: str) -> str:
    """Return a unique PDF path under FIG_DIR by adding date and numeric suffix."""
    date_tag = datetime.datetime.now().strftime("%Y%m%d")
    candidate = os.path.join(FIG_DIR, f"{base_name_no_ext}_{date_tag}.pdf")
    idx = 1
    while os.path.exists(candidate):
        candidate = os.path.join(FIG_DIR, f"{base_name_no_ext}_{date_tag}_{idx}.pdf")
        idx += 1
    return candidate

def geometry_avg(values):
    """Geometric average of a list."""
    prod = 1.0
    for v in values:
        prod *= v
    return prod ** (1.0 / len(values))

# ----------------------------------------------------------------------
# --------------------------- DATA PATHS -------------------------------
# ----------------------------------------------------------------------

# Ensure output directory exists
os.makedirs(FIG_DIR, exist_ok=True)

if HARDCODED_CSGC_LOG:
    path_csgc = HARDCODED_CSGC_LOG
else:
    path_csgc = get_latest_data_dir(FIO_UNIFORM_NAME, ROOT_CSGC, False)

if HARDCODED_MCSGC_LOG:
    path_mcsgc = HARDCODED_MCSGC_LOG
else:
    path_mcsgc = get_latest_data_dir(FIO_UNIFORM_NAME, ROOT_MCSGC, False)

if HARDCODED_ORI_LOG:
    path_ori = HARDCODED_ORI_LOG
else:
    path_ori = get_latest_data_dir(FIO_UNIFORM_NAME, ROOT_ORI, False)

# Print every log path that will be processed
print("Log paths:")
print("  CSGC : ", path_csgc)
print("  mCSGC: ", path_mcsgc)
print("  F2FS : ", path_ori)

# ----------------------------------------------------------------------
# ------------------------ EXTRACT THROUGHPUT --------------------------
# ----------------------------------------------------------------------

perf_csgc  = extract_perf(path_csgc)
perf_mcsgc = extract_perf(path_mcsgc)
perf_ori   = extract_perf(path_ori)

# Normalize against CSGC
norm_csgc  = 1.0
norm_mcsgc = perf_mcsgc / perf_csgc
norm_ori   = perf_ori   / perf_csgc

ratio_mc_over_cs = perf_mcsgc / perf_csgc
print(f"mCSGC / CSGC ratio: {ratio_mc_over_cs:.2f}")

# ----------------------------------------------------------------------
# ------------------------------ PLOT ----------------------------------
# ----------------------------------------------------------------------

plt.rcParams["axes.titlesize"]  = FONT_SIZE_MAIN
plt.rcParams["axes.labelsize"]  = FONT_SIZE_MAIN
plt.rcParams["xtick.labelsize"] = FONT_SIZE_MAIN
plt.rcParams["ytick.labelsize"] = FONT_SIZE_MAIN
plt.rcParams["legend.fontsize"] = FONT_SIZE_LABEL

fig = plt.figure(figsize=FIG_SIZE)
gs  = GridSpec(1, 1, figure=fig)
ax  = fig.add_subplot(gs[0, 0])

x_pos = [0]  # single workload
bars_ori   = ax.bar([x_pos[0] - BAR_WIDTH], [norm_ori],   BAR_WIDTH,
                    label=LABEL_ORI,   color=COLOR_ORI,   edgecolor="black", hatch="xx")
bars_mcsgc = ax.bar([x_pos[0]],          [norm_mcsgc], BAR_WIDTH,
                    label=LABEL_MCSGC, color=COLOR_MCSGC, edgecolor="black", hatch="//")
bars_csgc  = ax.bar([x_pos[0] + BAR_WIDTH], [norm_csgc],  BAR_WIDTH,
                    label=LABEL_CSGC,  color=COLOR_CSGC,  edgecolor="black", hatch="\\\\")  # noqa: E501 double backslash

# Annotate raw throughput (kops/s) on top of bars
def annotate_value(bar_container, value):
    bar = bar_container.patches[0]
    ax.annotate(f"{value/1000:.1f}",
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 1),
                textcoords="offset points",
                ha="center", va="bottom", fontsize=FONT_SIZE_LABEL)

annotate_value(bars_ori,   perf_ori)
annotate_value(bars_mcsgc, perf_mcsgc)
annotate_value(bars_csgc,  perf_csgc)

# Add ratio text above bars
ax.text(x_pos[0], max(norm_ori, norm_mcsgc, norm_csgc) * 1.25,
        f"mCSGC / CSGC = {ratio_mc_over_cs:.2f}",
        ha="center", va="bottom", fontsize=FONT_SIZE_MAIN, fontweight="bold")

ax.set_ylabel("Normalized Throughput (kop/s)")
ax.set_xticks(x_pos)
ax.set_xticklabels(["fio-uniform"])
ax.set_ylim(0, max(norm_ori, norm_mcsgc) * 1.5)
ax.set_yticks([0, 0.5, 1.0, 1.5, 2.0])
ax.legend()

plt.tight_layout()

# ----------------------------------------------------------------------
# -------------------------- SAVE & REPORT -----------------------------
# ----------------------------------------------------------------------

fig_out = make_unique_fig_path("fio_uniform_perf")
plt.savefig(fig_out, bbox_inches="tight", format="pdf")
print("Figure saved to:", fig_out)
