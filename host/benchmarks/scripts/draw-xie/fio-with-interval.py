# -*- coding: utf-8 -*-

"""
FIO output processor:
- Strict case-sensitive matching
- Robust numeric parsing
- Instantaneous metrics via cumulative differencing
- Three line plots saved under ./figs with unique filenames
- Output directory naming depends only on SERIES_NAMES, not on path patterns
"""

import os
import re
import sys
from datetime import datetime


# ==============================
# Configuration
# ==============================
"""
FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/TOSTUDY/importantdata/outputs-mcsgc/20250812_211141/fio_rw8t8file-1to1_s8_0.86_random/fio.log",   # TODO: set this path ori
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20250811_111004/fio_rw8t8file-1to1_s8_0.86_random/fio.log",  # TODO: set this path csgc
]

FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgcdebug/20250818_193208/fio_rw8t8file-1to1_s8_0.86_random/fio.log",   # TODO: set this path ori
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgcdebug/20250818_143321/fio_rw8t8file-1to1_s8_0.86_random/fio.log",  # TODO: set this path csgc
]

FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20251213_205447/fio_randwrite_s8_0.86_random/fio.log",   # TODO: set this path ori
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20251213_203222/fio_randwrite_s8_0.86_random/fio.log",  # TODO: set this path csgc
]


FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20260114_042343/fio_randwrite_s8_0.86_random/fio.log",   # TODO: set this path ori
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20251213_203222/fio_randwrite_s8_0.86_random/fio.log",  # TODO: set this path csgc
]
"""

FILE_PATHS = [
    #"/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20251213_205447/fio_randwrite_s8_0.86_random/fio.log",   # va-csgc
   "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20260119_071459/fio_randwrite_s8_0.86_random/fio.log",  # mcsgcv2
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20260123_111807/fio_randwrite_s8_0.86_random/fio.log" # mcsgcv3
]
FILE_PATHS = [
   "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20251213_205447/fio_randwrite_s8_0.86_random/fio.log",   # va-csgc
  #"/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20260119_071459/fio_randwrite_s8_0.86_random/fio.log",  # mcsgcv2
   # "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc8thread/20260126_105641/fio_randwrite_s8_0.86_random/fio.log", # mcsgcv3
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc8thread/20260130_123028/fio_randwrite_s8_0.86_random/fio.log" # mcsgcv5
]

FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/va-csgc/fio-con06/20260131_205143/fio_randwrite_s8_0.86_random/fio.log",
   "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_172218/fio_randwrite_s8_0.86_random/fio.log",
]

FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8thread/fio-con06/20260205_114302/fio_randwrite_s8_0.86_random/fio.log",
   "/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/data-to-draw/mcsgc-8t-v0411/fio-con06/20260412_172218/fio_randwrite_s8_0.86_random/fio.log",
]

SERIES_NAMES = ["mcsgc8t-old", "mcsgc8t-v0411"]
EPS = 100000
OUT_DIR = "./figs"
MBYTES_PER_SEC_DIVISOR = 1e6


# ==============================
# Helpers
# ==============================
def error_exit(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}")
    sys.exit(code)


def derive_common_name(paths):
    """
    Derive a benchmark name from file basenames.
    Prefer the longest common prefix; if too short, join with '_vs_'.
    """
    stems = [os.path.splitext(os.path.basename(p))[0] for p in paths]
    if not stems:
        return "fio"
    lcp = os.path.commonprefix(stems).rstrip("_-. ")
    if len(lcp) >= 3:
        return lcp
    if len(stems) == 1:
        return stems[0]
    return f"{stems[0]}_vs_{stems[1]}"


def read_lines(path: str):
    if not os.path.exists(path):
        error_exit(f"File does not exist: {path}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except Exception as e:
        error_exit(f"Failed to read file {path}: {e}")


def unique_png_path(base_path_wo_ext: str) -> str:
    """
    If base_path_wo_ext.png exists, append -<n> before .png.
    """
    candidate = f"{base_path_wo_ext}.png"
    if not os.path.exists(candidate):
        return candidate

    n = 1
    while True:
        cand = f"{base_path_wo_ext}-{n}.png"
        if not os.path.exists(cand):
            return cand
        n += 1


def unique_dir_path(parent_dir: str, dir_name: str) -> str:
    """
    If parent_dir/dir_name exists, append -<n> until unique.
    """
    candidate = os.path.join(parent_dir, dir_name)
    if not os.path.exists(candidate):
        return candidate

    n = 1
    while True:
        cand = os.path.join(parent_dir, f"{dir_name}-{n}")
        if not os.path.exists(cand):
            return cand
        n += 1


def parse_number(text: str) -> float:
    """
    Parse a signed float.
    """
    try:
        return float(text)
    except Exception:
        error_exit(f"Invalid numeric token: '{text}'")


def parse_iops_token(token: str) -> float:
    """
    Parse an IOPS token like '275k' or '2545'.
    Only lowercase 'k' means *1000. Decimals supported.
    """
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(k)?", token)
    if not m:
        error_exit(f"Invalid IOPS token after 'IOPS=': '{token}'")
    val = parse_number(m.group(1))
    if m.group(2) == "k":
        val *= 1000.0
    return val


def parse_iops_lines(lines):
    """
    Find all lines containing exact 'write: IOPS='.
    Return list[float] in the order of appearance and the count.
    """
    result = []
    for ln in lines:
        if "write: IOPS=" in ln:
            m = re.search(r"write: IOPS=([^,]+),", ln)
            if not m:
                error_exit(f"Cannot parse IOPS number from line: {ln.strip()}")
            token = m.group(1).strip()
            result.append(parse_iops_token(token))

    count = len(result)
    if count == 0:
        error_exit("No line with exact 'write: IOPS=' found.")
    return result, count


def parse_write_lines(lines):
    """
    Find all lines containing exact 'WRITE: '.
    Extract:
      1) MB/s inside first parentheses after bw=
      2) io=<num><MiB|GiB>, converted to GiB
      3) run=a-bmsec, requiring a == b, converted to seconds
    Returns (write_mbps, io_gib, time_sec, count)
    """
    write_mbps = []
    io_gib = []
    time_sec = []

    for ln in lines:
        if "WRITE: " in ln:
            m_bw = re.search(
                r"WRITE:\s+.*?\bbw=[^()]*\(\s*([+-]?\d+(?:\.\d+)?)\s*(MB/s)\s*\)",
                ln,
            )
            if not m_bw:
                error_exit(
                    f"Failed to parse '(...)' after 'bw=' with 'MB/s' from line: {ln.strip()}"
                )
            num_bw = parse_number(m_bw.group(1))
            unit_bw = m_bw.group(2)
            if unit_bw != "MB/s":
                error_exit(
                    f"Unit inside first parentheses after 'bw=' is not 'MB/s' (got '{unit_bw}')."
                )
            write_mbps.append(num_bw)

            m_io = re.search(r"\bio=\s*([+-]?\d+(?:\.\d+)?)\s*(MiB|GiB)\b", ln)
            if not m_io:
                error_exit(
                    f"Failed to parse 'io=<num><unit>' with MiB/GiB from line: {ln.strip()}"
                )
            io_val = parse_number(m_io.group(1))
            io_unit = m_io.group(2)
            if io_unit == "MiB":
                io_gib.append(io_val / 1024.0)
            elif io_unit == "GiB":
                io_gib.append(io_val)
            else:
                error_exit(f"Unsupported io unit '{io_unit}' (only MiB or GiB).")

            m_run = re.search(r"\brun=\s*(\d+)-(\d+)msec\b", ln)
            if not m_run:
                error_exit(f"Failed to parse 'run=a-bmsec' from line: {ln.strip()}")
            a = int(m_run.group(1))
            b = int(m_run.group(2))
            if a != b:
                error_exit(f"'run=' two numbers differ: {a} vs {b} in line: {ln.strip()}")
            time_sec.append(a / 1000.0)

    count = len(write_mbps)
    return write_mbps, io_gib, time_sec, count


def validate_strict_increasing(times, eps=EPS):
    """
    Times must be strictly increasing.
    If delta < 0 and abs(delta) < eps, nudge forward by eps.
    """
    if not times:
        error_exit("Empty time list.")

    adj = [times[0]]
    for i in range(1, len(times)):
        delta = times[i] - adj[-1]
        if delta < 0.0:
            if abs(delta) < eps:
                adj.append(adj[-1] + eps)
            else:
                error_exit(f"Time not strictly increasing at index {i}: delta={delta}")
        else:
            adj.append(times[i])
    return adj


def compute_cumulative_and_inst(io_gib, iops_avg, times_sec, eps=EPS):
    """
    Build cumulative series and instantaneous values via differencing.
    """
    if not (len(io_gib) == len(iops_avg) == len(times_sec)):
        error_exit("Length mismatch among io_gib, iops_avg, times_sec.")

    n = len(times_sec)
    if n == 0:
        error_exit("Empty series to compute.")

    times = validate_strict_increasing(times_sec, eps=eps)

    bytes_cum = []
    ops_cum = []
    for i in range(n):
        b = io_gib[i] * (1024.0 ** 3)
        o = iops_avg[i] * times[i]
        bytes_cum.append(b)
        ops_cum.append(o)

    bw_inst = [0.0] * n
    iops_inst = [0.0] * n

    if times[0] <= 0.0:
        error_exit(f"First time snapshot must be > 0 (got {times[0]}).")

    bw_inst[0] = bytes_cum[0] / times[0] / MBYTES_PER_SEC_DIVISOR
    iops_inst[0] = ops_cum[0] / times[0]

    for i in range(1, n):
        dt = times[i] - times[i - 1]
        if dt <= 0.0:
            if abs(dt) < eps:
                dt = eps
            else:
                error_exit(f"Non-positive delta t at index {i}: dt={dt}")

        db = bytes_cum[i] - bytes_cum[i - 1]
        do = ops_cum[i] - ops_cum[i - 1]

        bw_inst[i] = db / dt / MBYTES_PER_SEC_DIVISOR
        iops_inst[i] = do / dt

    return {
        "times": times,
        "bytes_cum": bytes_cum,
        "ops_cum": ops_cum,
        "bw_inst": bw_inst,
        "iops_inst": iops_inst,
    }


def parse_one_file(path: str):
    """
    Parse a single fio output file.
    """
    lines = read_lines(path)

    iops_list, iops_line = parse_iops_lines(lines)
    write_mbps, io_gib, time_sec, bw_line = parse_write_lines(lines)

    if iops_line != bw_line:
        error_exit(f"In {path}, count mismatch: iops_line={iops_line} vs bw_line={bw_line}")

    bmname = os.path.splitext(os.path.basename(path))[0]
    return {
        "bmname": bmname,
        "iops_list": iops_list,
        "write_mbps": write_mbps,
        "io_gib": io_gib,
        "time_sec": time_sec,
        "iops_line": iops_line,
        "bw_line": bw_line,
    }


def prepare_output_dir(dirpath: str):
    try:
        os.makedirs(dirpath, exist_ok=True)
    except Exception as e:
        error_exit(f"Failed to create output directory '{dirpath}': {e}")


def safe_dir_component(s: str, max_len: int = 140) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._-")
    if not s:
        s = "run"
    if len(s) > max_len:
        s = s[:max_len]
    return s


def make_run_output_dir(base_dir: str, series_names) -> str:
    """
    Build output directory name as:
      <date>-<time>-<series0>-<series1>
    If duplicated, append -1, -2, ...
    """
    if len(series_names) != 2:
        error_exit("Exactly two series names are required.")

    date_part = datetime.now().strftime("%Y%m%d")
    time_part = datetime.now().strftime("%H%M%S")
    s0 = safe_dir_component(series_names[0])
    s1 = safe_dir_component(series_names[1])

    dir_name = f"{date_part}-{time_part}-{s0}-{s1}"
    return unique_dir_path(base_dir, dir_name)


def plot_lines_dual(
    time_lists,
    y_lists,
    names,
    y_label,
    title,
    annotate_values=None,
    base_filename="plot",
    out_dir=None,
):
    """
    Plot two line series with their own time axes.
    Save to unique path.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()

    line_objs = []
    for t, y, nm in zip(time_lists, y_lists, names):
        line, = ax.plot(t, y, label=nm)
        line_objs.append(line)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=0)
    ax.legend()

    if annotate_values:
        for t, y, anno, line in zip(time_lists, y_lists, annotate_values, line_objs):
            if len(t) == 0:
                continue
            x_last = t[-1]
            y_last = y[-1]
            color = line.get_color()
            dx = (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.005
            dy = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.02
            ax.text(x_last + dx, max(0.0, y_last + dy), str(anno), color=color, fontsize=9)

    if out_dir is None:
        out_dir = OUT_DIR
    prepare_output_dir(out_dir)
    out_path = unique_png_path(os.path.join(out_dir, base_filename))
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved figure: {out_path}")


def main():
    if len(FILE_PATHS) != 2:
        error_exit("Exactly two file paths are required in FILE_PATHS.")
    if len(SERIES_NAMES) != 2:
        error_exit("Exactly two names are required in SERIES_NAMES.")

    parsed = [parse_one_file(p) for p in FILE_PATHS]

    bmname = derive_common_name(FILE_PATHS)

    iops_lists = [parsed[0]["iops_list"], parsed[1]["iops_list"]]
    write_mbps_lists = [parsed[0]["write_mbps"], parsed[1]["write_mbps"]]
    io_gib_lists = [parsed[0]["io_gib"], parsed[1]["io_gib"]]
    time_lists_sec = [parsed[0]["time_sec"], parsed[1]["time_sec"]]
    iops_counts = [parsed[0]["iops_line"], parsed[1]["iops_line"]]

    if iops_counts[0] == 0 or iops_counts[1] == 0:
        error_exit("No valid 'write: IOPS=' or 'WRITE: ' lines found in at least one file.")

    series = []
    for idx in range(2):
        print("Processing file:", FILE_PATHS[idx])
        s = compute_cumulative_and_inst(
            io_gib_lists[idx],
            iops_lists[idx],
            time_lists_sec[idx],
            eps=EPS,
        )
        series.append(s)

    run_out_dir = make_run_output_dir(OUT_DIR, SERIES_NAMES)
    prepare_output_dir(run_out_dir)
    print(f"Figures output dir: {run_out_dir}")

    safe_bmname = safe_dir_component(bmname)
    safe_s0 = safe_dir_component(SERIES_NAMES[0])
    safe_s1 = safe_dir_component(SERIES_NAMES[1])
    base_tag = f"{safe_bmname}_{safe_s0}_{safe_s1}"

    ann_io_gib_last = [
        f"{io_gib_lists[0][-1]:.3f} GiB",
        f"{io_gib_lists[1][-1]:.3f} GiB",
    ]
    plot_lines_dual(
        time_lists=[series[0]["times"], series[1]["times"]],
        y_lists=[series[0]["bw_inst"], series[1]["bw_inst"]],
        names=SERIES_NAMES,
        y_label="Instant BW (MB/s)",
        title="Instantaneous Bandwidth vs Time",
        annotate_values=ann_io_gib_last,
        base_filename=f"{base_tag}_bw_inst",
        out_dir=run_out_dir,
    )

    ann_iops_last = [
        f"{iops_lists[0][-1]:.3f}",
        f"{iops_lists[1][-1]:.3f}",
    ]
    plot_lines_dual(
        time_lists=[series[0]["times"], series[1]["times"]],
        y_lists=[series[0]["iops_inst"], series[1]["iops_inst"]],
        names=SERIES_NAMES,
        y_label="Instant IOPS (ops/s)",
        title="Instantaneous IOPS vs Time",
        annotate_values=ann_iops_last,
        base_filename=f"{base_tag}_iops_inst",
        out_dir=run_out_dir,
    )

    plot_lines_dual(
        time_lists=[time_lists_sec[0], time_lists_sec[1]],
        y_lists=[write_mbps_lists[0], write_mbps_lists[1]],
        names=SERIES_NAMES,
        y_label="Reported BW (MB/s)",
        title="Reported WRITE BW vs Time",
        annotate_values=None,
        base_filename=f"{base_tag}_write_mbps",
        out_dir=run_out_dir,
    )

    print("All done.")


if __name__ == "__main__":
    main()