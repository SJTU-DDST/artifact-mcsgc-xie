#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import math
import os
import re
import sys
from statistics import mean, median
from typing import Dict, List, Tuple, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


BREAKDOWN_MARKER = "sync_fs_time ="
TAKES_SUBSTR_1 = "csgc"
TAKES_SUBSTR_2 = "takes"

RE_KV_US = re.compile(r"([A-Za-z0-9_]+)\s*=\s*(-?\d+)\s*us")
RE_TAKES = re.compile(r"(?i)\btakes\b\s+(-?\d+)(?:\s*us)?")

DEFAULT_SCAN_AHEAD_LINES = 20


def unique_path(dir_path: str, base_name: str, ext: str) -> str:
    os.makedirs(dir_path, exist_ok=True)
    candidate = os.path.join(dir_path, f"{base_name}{ext}")
    if not os.path.exists(candidate):
        return candidate
    idx = 1
    while True:
        candidate = os.path.join(dir_path, f"{base_name}_{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def nearest_rank_quantile(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    n = len(sorted_vals)
    q = max(0.0, min(1.0, q))
    rank = int(math.ceil(q * n))
    rank = max(1, min(n, rank))
    return float(sorted_vals[rank - 1])


def top_fraction_mean(sorted_vals_asc: List[float], frac: float) -> float:
    if not sorted_vals_asc:
        return float("nan")
    n = len(sorted_vals_asc)
    k = int(math.ceil(frac * n))
    k = max(1, min(n, k))
    top_vals = sorted_vals_asc[-k:]
    return float(mean(top_vals))


def pearson_corr(xs: List[float], ys: List[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        return float("nan")
    mx = mean(xs)
    my = mean(ys)
    num = 0.0
    dx2 = 0.0
    dy2 = 0.0
    for x, y in zip(xs, ys):
        dx = x - mx
        dy = y - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    denom = math.sqrt(dx2 * dy2)
    if denom == 0.0:
        return float("nan")
    return num / denom


def parse_breakdown_line(line: str) -> Dict[str, int]:
    kvs: Dict[str, int] = {}
    for m in RE_KV_US.finditer(line):
        key = m.group(1)
        val = int(m.group(2))
        kvs[key] = val
    return kvs


def is_takes_line(line: str) -> bool:
    low = line.lower()
    return (TAKES_SUBSTR_1 in low) and (TAKES_SUBSTR_2 in low)


def parse_takes_us(line: str) -> Optional[int]:
    m = RE_TAKES.search(line)
    if not m:
        return None
    return int(m.group(1))


def plot_scatter_index(values: List[float], title: str, ylabel: str, out_path: str) -> None:
    xs = list(range(len(values)))
    plt.figure()
    plt.scatter(xs, values, s=6)
    plt.title(title)
    plt.xlabel("sample_index")
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


def plot_scatter_xy(xs: List[float], ys: List[float], title: str, xlabel: str, ylabel: str, out_path: str) -> None:
    plt.figure()
    plt.scatter(xs, ys, s=6)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(out_path, dpi=200)
    plt.close()


def summarize_metric(name: str, vals: List[int]) -> str:
    if not vals:
        return f"{name}: count=0"
    s = sorted(float(v) for v in vals)
    n = len(s)
    avg = mean(s)
    mn = s[0]
    mx = s[-1]
    med = median(s)
    p80 = nearest_rank_quantile(s, 0.80)
    top20 = top_fraction_mean(s, 0.20)
    return (
        f"{name}: count={n} mean={avg:.3f} min={mn:.3f} max={mx:.3f} "
        f"median={med:.3f} p80={p80:.3f} top20_mean={top20:.3f}"
    )


def summarize_ratio_distribution(name: str, ratios: List[float]) -> str:
    if not ratios:
        return f"{name}: count=0"
    s = sorted(ratios)
    p50 = nearest_rank_quantile(s, 0.50)
    p90 = nearest_rank_quantile(s, 0.90)
    p99 = nearest_rank_quantile(s, 0.99)
    return f"{name}: count={len(s)} p50={p50:.6f} p90={p90:.6f} p99={p99:.6f}"


def read_all_lines(path: str) -> List[str]:
    if path == "-":
        return sys.stdin.read().splitlines()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse F2FS CSGC timing logs from dmesg output.")
    ap.add_argument("input", help="Input dmesg text file path, or '-' for stdin.")
    ap.add_argument("--scan-ahead", type=int, default=DEFAULT_SCAN_AHEAD_LINES,
                    help="Max lines to scan after breakdown line to find a takes line.")
    ap.add_argument("--figdir", default="./figs", help="Directory to save figures.")
    args = ap.parse_args()

    lines = read_all_lines(args.input)
    scan_ahead = max(1, args.scan_ahead)

    metrics: Dict[str, List[int]] = {}
    takes_us: List[int] = []

    records: List[Dict[str, int]] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        if BREAKDOWN_MARKER in line:
            kvs = parse_breakdown_line(line)
            if not kvs:
                print(f"Error: breakdown marker found but no key/value parsed at line {i+1}.", file=sys.stderr)
                print(f"Line: {line}", file=sys.stderr)
                return 1

            found_takes = None
            found_line_no = None
            for j in range(i + 1, min(len(lines), i + 1 + scan_ahead)):
                if is_takes_line(lines[j]):
                    t = parse_takes_us(lines[j])
                    if t is None:
                        print(f"Error: takes line matched but number not parsed at line {j+1}.", file=sys.stderr)
                        print(f"Line: {lines[j]}", file=sys.stderr)
                        return 1
                    found_takes = t
                    found_line_no = j + 1
                    break

            if found_takes is None:
                print(
                    f"Error: takes line not found within {scan_ahead} lines after breakdown at line {i+1}.",
                    file=sys.stderr,
                )
                print(f"Breakdown line: {line}", file=sys.stderr)
                return 1

            kvs["takes_us"] = int(found_takes)

            records.append(kvs)
            takes_us.append(int(found_takes))

            for k, v in kvs.items():
                metrics.setdefault(k, []).append(int(v))

            i = (found_line_no - 1) if found_line_no is not None else i
        i += 1

    if not records:
        print("Error: no matched records found.", file=sys.stderr)
        return 1

    if "total_time" not in metrics:
        print("Error: 'total_time' not found in parsed breakdown metrics.", file=sys.stderr)
        return 1

    nrec = len(records)
    print(f"Parsed records: {nrec}")

    print("\n=== Per-metric statistics (us) ===")
    for name in sorted(metrics.keys()):
        print(summarize_metric(name, metrics[name]))

    figdir = args.figdir
    print(f"\nSaving scatter plots to: {os.path.abspath(figdir)}")

    for name in sorted(metrics.keys()):
        out = unique_path(figdir, f"scatter_index_{name}", ".png")
        plot_scatter_index([float(v) for v in metrics[name]], f"{name} vs sample_index", f"{name}_us", out)

    print("\n=== Ratio distributions ===")
    total_core = metrics["total_time"]
    total_full = metrics["takes_us"]

    phase_keys = [k for k in metrics.keys() if k not in ("takes_us",)]
    ratio_vs_full: Dict[str, List[float]] = {}
    ratio_vs_core: Dict[str, List[float]] = {}

    for k in phase_keys:
        vals = metrics[k]
        r_full: List[float] = []
        r_core: List[float] = []
        for idx in range(nrec):
            denom_full = float(total_full[idx])
            denom_core = float(total_core[idx])
            v = float(vals[idx])

            if denom_full > 0.0:
                r_full.append(v / denom_full)
            if denom_core > 0.0:
                r_core.append(v / denom_core)
        ratio_vs_full[k] = r_full
        ratio_vs_core[k] = r_core

    for k in sorted(phase_keys):
        print(summarize_ratio_distribution(f"{k}/takes_us", ratio_vs_full.get(k, [])))
    for k in sorted(phase_keys):
        print(summarize_ratio_distribution(f"{k}/total_time", ratio_vs_core.get(k, [])))

    print("\n=== Correlation with takes_us (Pearson) ===")
    corrs: List[Tuple[str, float]] = []
    takes_f = [float(v) for v in total_full]
    for k in sorted(phase_keys):
        xs = [float(v) for v in metrics[k]]
        r = pearson_corr(xs, takes_f)
        corrs.append((k, r))
    corrs_sorted = sorted(corrs, key=lambda x: (float("-inf") if math.isnan(x[1]) else abs(x[1])), reverse=True)
    for k, r in corrs_sorted:
        if math.isnan(r):
            print(f"{k} vs takes_us: r=nan")
        else:
            print(f"{k} vs takes_us: r={r:.6f}")

    for k in sorted(phase_keys):
        xs = [float(v) for v in metrics[k]]
        out = unique_path(figdir, f"scatter_{k}_vs_takes_us", ".png")
        plot_scatter_xy(xs, takes_f, f"{k} vs takes_us", f"{k}_us", "takes_us", out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
