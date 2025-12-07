#!/usr/bin/env python3
import os
import sys
import re
from collections import Counter

ONE_MB = 1024 * 1024


def make_compact_log(src_path: str) -> str:
    """Create a compact log file that contains the last 1MB of src_path."""
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"Log file not found: {src_path}")

    size = os.path.getsize(src_path)
    offset = size - ONE_MB if size > ONE_MB else 0

    base, ext = os.path.splitext(src_path)
    if ext == "":
        compact_path = base + "-compact.log"
    else:
        compact_path = f"{base}-compact{ext}"

    with open(src_path, "rb") as src, open(compact_path, "wb") as dst:
        src.seek(offset)
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)

    return compact_path


def count_cp_lock(path: str) -> Counter:
    """
    Count all CP_LOCK categories in the given log file.

    A category is extracted from 'CP_LOCK <category>:'.
    For example: 'CP_LOCK op_read_trylock fail: where=...' ->
    category 'CP_LOCK op_read_trylock fail'.
    """
    counts = Counter()
    pattern = re.compile(r"CP_LOCK\s+([^:]+)")

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "CP_LOCK" not in line:
                continue
            m = pattern.search(line)
            if m:
                key = "CP_LOCK " + m.group(1).strip()
            else:
                # Fallback: line contains CP_LOCK but does not match pattern
                key = "CP_LOCK (unparsed)"
            counts[key] += 1

    return counts


def print_counts(title: str, counts: Counter) -> None:
    print(f"\n===== {title} =====")
    if not counts:
        print("No CP_LOCK entries found.")
        return

    total = sum(counts.values())
    print(f"Total CP_LOCK entries: {total}")
    for key, val in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{key}: {val}")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/a.log")
        sys.exit(1)

    src_path = sys.argv[1]

    try:
        # 1) Create compact log and count CP_LOCK in it
        compact_path = make_compact_log(src_path)
        compact_counts = count_cp_lock(compact_path)
        print_counts(f"CP_LOCK stats in compact log ({compact_path})", compact_counts)

        # 2) Count CP_LOCK in full log
        full_counts = count_cp_lock(src_path)
        print_counts(f"CP_LOCK stats in full log ({src_path})", full_counts)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
