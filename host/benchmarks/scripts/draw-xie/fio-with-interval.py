# -*- coding: utf-8 -*-

"""
FIO output processor:
- Strict case-sensitive matching
- Robust numeric parsing (supports negative, zero, positive, decimals; 'k' -> *1000 for IOPS)
- Validations as specified
- Instantaneous metrics via cumulative differencing
- Three line plots saved under ./figs with unique filenames
"""

import os
import re
import sys
from datetime import datetime

# ==============================
# Configuration (all constants here)
# ==============================
FILE_PATHS = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/TOSTUDY/importantdata/outputs-mcsgc/20250812_211141/fio_rw8t8file-1to1_s8_0.86_random/fio.log",   # TODO: set this path ori
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20250811_111004/fio_rw8t8file-1to1_s8_0.86_random/fio.log",  # TODO: set this path csgc
]
SERIES_NAMES = ["ori", "csgc"]        # Names for the two series (in plot legends)
EPS = 100000                             # Tolerance for tiny negative diffs (float errors)
OUT_DIR = "./figs"                     # Output directory for figures
MBYTES_PER_SEC_DIVISOR = 1e6          # For MB/s from bytes/second (decimal MB)

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

def parse_number(text: str) -> float:
    """
    Parse a signed float: supports negative, zero, positive, decimals.
    Raises on failure.
    """
    try:
        return float(text)
    except Exception:
        error_exit(f"Invalid numeric token: '{text}'")

def parse_iops_token(token: str) -> float:
    """
    Parse an IOPS token like '275k' or '2545' (strict case-sensitive).
    Only lowercase 'k' means *1000. Decimals supported (e.g. '2.5k').
    """
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(k)?", token)
    if not m:
        error_exit(f"Invalid IOPS token after 'IOPS=': '{token}'")
    val = parse_number(m.group(1))
    if m.group(2) == "k":
        val *= 1000.0
    return val

'''
def parse_layout_line(line: str):
    """
    Example:
    'random_write_8t_8file: Laying out IO files (8 files / total 49152MiB)'
    Returns (bmname, total_mib: float).
    Enforces unit == 'MiB'.
    """
    # Enforce presence of the exact substring
    if "Laying out IO file" not in line:
        error_exit("Internal parse_layout_line called on a non-layout line.")
    # Extract bmname before first ':'
    if ":" not in line:
        error_exit("Layout line missing ':' separator for bmname.")
    bmname = line.split(":", 1)[0].strip()
    # Extract 'total <number><unit>' inside parentheses; unit must be MiB
    m = re.search(r"total\s+([+-]?\d+(?:\.\d+)?)([A-Za-z]+)", line)
    if not m:
        error_exit("Failed to extract 'total <number><unit>' from layout line.")
    total_val = parse_number(m.group(1))
    unit = m.group(2)
    if unit != "MiB":
        error_exit(f"Unit after 'total' is not 'MiB' (got '{unit}').")
    return bmname, total_val
'''
def parse_iops_lines(lines):
    """
    Find all lines containing exact 'write: IOPS=' (lowercase 'write', case-sensitive).
    Example:
      'write: IOPS=275k, BW=1072MiB/s ...'
      'write: IOPS=2545, BW=159MiB/s ...'
    Return list[float] in the order of appearance. Also return count.
    """
    result = []
    for ln in lines:
        if "write: IOPS=" in ln:
            # Extract the token right after IOPS= up to the first comma
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
    Find all lines containing exact 'WRITE: ' (uppercase, trailing space).
    Example:
      '  WRITE: bw=1072MiB/s (1124MB/s), 1072MiB/s-1072MiB/s (1124MB/s-1124MB/s), io=815MiB (854MB), run=760-760msec'
    Extract:
      4.1) First parentheses after 'bw=' -> number must be '...MB/s' (unit check), push to write_mbps (float)
      4.2) 'io=' number with unit MiB or GiB -> convert to GiB, push to io_gib
      4.3) 'run=a-bmsec' with a == b; store a/1000.0 to time_sec
    Returns (write_mbps, io_gib, time_sec, count)
    """
    write_mbps = []
    io_gib = []
    time_sec = []

    for ln in lines:
        if "WRITE: " in ln:
            # 4.1 bw=... (XMB/s)
            m_bw = re.search(r"WRITE:\s+.*?\bbw=[^()]*\(\s*([+-]?\d+(?:\.\d+)?)\s*(MB/s)\s*\)", ln)
            if not m_bw:
                error_exit(f"Failed to parse '(...)' after 'bw=' with 'MB/s' from line: {ln.strip()}")
            num_bw = parse_number(m_bw.group(1))
            unit_bw = m_bw.group(2)
            if unit_bw != "MB/s":
                error_exit(f"Unit inside first parentheses after 'bw=' is not 'MB/s' (got '{unit_bw}').")
            write_mbps.append(num_bw)

            # 4.2 io=...
            m_io = re.search(r"\bio=\s*([+-]?\d+(?:\.\d+)?)\s*(MiB|GiB)\b", ln)
            if not m_io:
                error_exit(f"Failed to parse 'io=<num><unit>' with MiB/GiB from line: {ln.strip()}")
            io_val = parse_number(m_io.group(1))
            io_unit = m_io.group(2)
            if io_unit == "MiB":
                io_gib.append(io_val / 1024.0)
            elif io_unit == "GiB":
                io_gib.append(io_val)
            else:
                error_exit(f"Unsupported io unit '{io_unit}' (only MiB or GiB).")

            # 4.3 run=a-bmsec
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
    Times must be strictly increasing. Allow tiny negative as float noise:
    if delta < 0 and abs(delta) < eps -> treat as zero-length step and nudge.
    If delta <= 0 and abs(delta) >= eps -> error.
    We nudge zero/near-zero deltas to eps to avoid division by zero downstream.
    Returns adjusted copy of times.
    """
    if not times:
        error_exit("Empty time list.")
    adj = [times[0]]
    for i in range(1, len(times)):
        delta = times[i] - adj[-1]
        if delta < 0.0:
            if abs(delta) < eps:
                # Treat as zero and nudge
                adj.append(adj[-1] + eps)
            else:
                error_exit(f"Time not strictly increasing at index {i}: delta={delta}")
        # elif delta == 0.0:
            # Not allowed unless extremely small and we nudge
            # adj.append(adj[-1] + eps)
        else:
            adj.append(times[i])
    return adj

def compute_cumulative_and_inst(io_gib, iops_avg, times_sec, eps=EPS):
    """
    Build cumulative series and instantaneous values via differencing:
      bytes_cum[i] = io_gib[i] * 1024**3
      ops_cum[i]   = iops_avg[i] * times_sec[i]
    Instantaneous for i>0:
      dt = times_sec[i] - times_sec[i-1]
      bw_inst[i]   = (bytes_cum[i]-bytes_cum[i-1]) / dt / 1e6
      iops_inst[i] = (ops_cum[i]-ops_cum[i-1]) / dt
    For i=0:
      bw_inst[0]   = bytes_cum[0] / times_sec[0] / 1e6
      iops_inst[0] = ops_cum[0]   / times_sec[0]
    Monotonicity: bytes_cum, ops_cum must be non-decreasing (allow tiny negative within eps -> clamp to previous).
    """
    if not (len(io_gib) == len(iops_avg) == len(times_sec)):
        error_exit("Length mismatch among io_gib, iops_avg, times_sec.")
    n = len(times_sec)
    if n == 0:
        error_exit("Empty series to compute.")

    # Times must be strictly increasing (with nudging)
    times = validate_strict_increasing(times_sec, eps=eps)

    # Cumulative
    bytes_cum = []
    ops_cum = []
    for i in range(n):
        b = io_gib[i] * (1024.0 ** 3)
        o = iops_avg[i] * times[i]
        # Enforce non-decreasing with tolerance
        """
        if i > 0:
            db = b - bytes_cum[-1]
            if db < 0.0 and abs(db) >= eps:
                start = max(0, i - 10)
                end = min(len(bytes_cum), i + 10)
                print(f"io_gib snapshot around index {i}:")
                print(io_gib[start:end])
                error_exit(f"bytes_cum decreases at index {i}: delta={db}")
            #if db < 0.0 and abs(db) < eps:
               # b = bytes_cum[-1]
            do = o - ops_cum[-1]
            if do < 0.0 and abs(do) >= eps:
                start = max(0, i - 10)
                end = min(len(ops_cum), i + 10)
                print(f"iops_avg snapshot around index {i}:")
                print(iops_avg[start:end])
                error_exit(f"ops_cum decreases at index {i}: delta={do}")
            #if do < 0.0 and abs(do) < eps:
                #o = ops_cum[-1]
        """
        bytes_cum.append(b)
        ops_cum.append(o)

    # Instantaneous
    bw_inst = [0.0] * n
    iops_inst = [0.0] * n

    if times[0] <= 0.0:
        error_exit(f"First time snapshot must be > 0 (got {times[0]}).")
    bw_inst[0] = bytes_cum[0] / times[0] / MBYTES_PER_SEC_DIVISOR
    iops_inst[0] = ops_cum[0] / times[0]

    for i in range(1, n):
        dt = times[i] - times[i-1]
        if dt <= 0.0:
            if abs(dt) < eps:
                dt = eps  # nudge
            else:
                error_exit(f"Non-positive Δt at index {i}: dt={dt}")
        db = bytes_cum[i] - bytes_cum[i-1]
        do = ops_cum[i] - ops_cum[i-1]
        """
        if db < 0.0 and abs(db) >= eps:
            error_exit(f"bytes_cum difference negative at index {i}: {db}")
        if do < 0.0 and abs(do) >= eps:
            error_exit(f"ops_cum difference negative at index {i}: {do}")
        if db < 0.0 and abs(db) < eps:
            db = 0.0
        if do < 0.0 and abs(do) < eps:
            do = 0.0
        """
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
    Parse a single fio output file per the spec.
    Returns dict with:
      bmname (str), IOtotal_mib (float),
      iops_list (list[float]),
      write_mbps (list[float]),
      io_gib (list[float]),
      time_sec (list[float]),
      iops_line (int), bw_line (int)
    """
    lines = read_lines(path)

    # 2) Unique 'Laying out IO files' line
   # layout_lines = [ln for ln in lines if "Laying out IO file" in ln]
    #if len(layout_lines) != 1:
      #  pass 
        # error_exit(f"'Laying out IO files' lines count is not 1 in {path} (got {len(layout_lines)}).")
    #bmname, total_mib = parse_layout_line(layout_lines[0])

    # 3) 'write: IOPS=' lines
    iops_list, iops_line = parse_iops_lines(lines)

    # 4) 'WRITE: ' lines
    write_mbps, io_gib, time_sec, bw_line = parse_write_lines(lines)

    # 4.4) counts must match per file
    if iops_line != bw_line:
        error_exit(f"In {path}, count mismatch: iops_line={iops_line} vs bw_line={bw_line}")
    bmname = os.path.splitext(os.path.basename(path))[0]  # informative only
    return {
        "bmname": bmname,
        # "IOtotal_mib": float(total_mib),
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

def plot_lines_dual(time_lists, y_lists, names, y_label, title, annotate_values=None, base_filename="plot"):
    """
    Plot two line series with their own time axes. Axes start at 0.
    Annotate line-end with provided annotate_values (strings) in the same color.
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

    # Annotate at line ends if requested
    if annotate_values:
        for (t, y, anno, line) in zip(time_lists, y_lists, annotate_values, line_objs):
            if len(t) == 0:
                continue
            x_last = t[-1]
            y_last = y[-1]
            color = line.get_color()
            # Slight offset for readability
            dx = (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.005
            dy = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.02
            ax.text(x_last + dx, max(0.0, y_last + dy), str(anno), color=color, fontsize=9)

    # Save
    prepare_output_dir(OUT_DIR)
    out_path = unique_png_path(os.path.join(OUT_DIR, base_filename))
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved figure: {out_path}")

def main():
    if len(FILE_PATHS) != 2:
        error_exit("Exactly two file paths are required in FILE_PATHS.")

    # Parse both files
    parsed = [parse_one_file(p) for p in FILE_PATHS]

    # 2) bmname must be identical across files
    # bm0, bm1 = parsed[0]["bmname"], parsed[1]["bmname"]
   # if bm0 != bm1:
    #    error_exit(f"bmname mismatch between files: '{bm0}' vs '{bm1}'")
    #bmname = bm0

    # 2) IOtotal must be in MiB and equal across files
   # io_total_mib = [parsed[0]["IOtotal_mib"], parsed[1]["IOtotal_mib"]]
    #if io_total_mib[0] != io_total_mib[1]:
      #  error_exit(f"IOtotal (MiB) mismatch between files: {io_total_mib[0]} vs {io_total_mib[1]}")
    bmname = derive_common_name(FILE_PATHS)
    # Gather arrays into 2D structures per spec
    iops_lists = [parsed[0]["iops_list"], parsed[1]["iops_list"]]
    write_mbps_lists = [parsed[0]["write_mbps"], parsed[1]["write_mbps"]]
    io_gib_lists = [parsed[0]["io_gib"], parsed[1]["io_gib"]]
    time_lists_sec = [parsed[0]["time_sec"], parsed[1]["time_sec"]]
    iops_counts = [parsed[0]["iops_line"], parsed[1]["iops_line"]]
    bw_counts = [parsed[0]["bw_line"], parsed[1]["bw_line"]]

    # Sanity: counts already checked per-file to be equal; ensure not zero
    if iops_counts[0] == 0 or iops_counts[1] == 0:
        error_exit("No valid 'write: IOPS=' or 'WRITE: ' lines found in at least one file.")

    # 5) Process: cumulative and instantaneous
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

    # 6) Plotting
    # extract tags right after 'outputs-' from each file path
    def _extract_outputs_tag(p: str) -> str:
        m = re.search(r"/outputs-([^/]+)/", p)
        if not m:
            error_exit(f"Path does not contain 'outputs-<tag>': {p}")
        return m.group(1)

    tags = [_extract_outputs_tag(p) for p in FILE_PATHS]
    date_str = datetime.now().strftime("%Y%m%d")
    base_tag = f"{bmname}_{tags[0]}_{tags[1]}_{SERIES_NAMES[0]}_{SERIES_NAMES[1]}_{date_str}"


    # 6.1 Instant BW plot (MB/s), annotate last io_with_time (GiB) per file
    ann_io_gib_last = [f"{io_gib_lists[0][-1]:.3f} GiB", f"{io_gib_lists[1][-1]:.3f} GiB"]
    plot_lines_dual(
        time_lists=[series[0]["times"], series[1]["times"]],
        y_lists=[series[0]["bw_inst"], series[1]["bw_inst"]],
        names=SERIES_NAMES,
        y_label="Instant BW (MB/s)",
        title="Instantaneous Bandwidth vs Time",
        annotate_values=ann_io_gib_last,
        base_filename=f"{base_tag}_bw_inst",
    )

    # 6.2 Instant IOPS plot (ops/s), annotate last overall iops_list value
    ann_iops_last = [f"{iops_lists[0][-1]:.3f}", f"{iops_lists[1][-1]:.3f}"]
    plot_lines_dual(
        time_lists=[series[0]["times"], series[1]["times"]],
        y_lists=[series[0]["iops_inst"], series[1]["iops_inst"]],
        names=SERIES_NAMES,
        y_label="Instant IOPS (ops/s)",
        title="Instantaneous IOPS vs Time",
        annotate_values=ann_iops_last,
        base_filename=f"{base_tag}_iops_inst",
    )

    # 6.3 Reported WRITE bw (MB/s) vs time (raw)
    plot_lines_dual(
        time_lists=[time_lists_sec[0], time_lists_sec[1]],
        y_lists=[write_mbps_lists[0], write_mbps_lists[1]],
        names=SERIES_NAMES,
        y_label="Reported BW (MB/s)",
        title="Reported WRITE BW vs Time",
        annotate_values=None,
        base_filename=f"{base_tag}_write_mbps",
    )

    print("All done.")

if __name__ == "__main__":
    main()
