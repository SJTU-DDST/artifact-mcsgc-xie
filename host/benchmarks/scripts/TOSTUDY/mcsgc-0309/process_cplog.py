#!/usr/bin/env python3
import os
import sys
import re
from collections import Counter

ONE_MB = 1024 * 1024
TEN_MB = 10 * 1024 * 1024
HUNDRED_MB = 100 * 1024 * 1024


def make_unique_path(base_path: str, suffix: str) -> str:
    """
    Given a base log path like /dir/a.log and a suffix like '-compact',
    generate a unique path like:
      /dir/a-compact.log
      /dir/a-compact2.log
      /dir/a-compact3.log
    """
    directory, filename = os.path.split(base_path)
    root, ext = os.path.splitext(filename)
    if not ext:
        ext = ".log"

    candidate = os.path.join(directory, f"{root}{suffix}{ext}")
    if not os.path.exists(candidate):
        return candidate

    idx = 2
    while True:
        candidate = os.path.join(directory, f"{root}{suffix}{idx}{ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def extract_cp_lock_body(line: str) -> str:
    """
    Extract the body of a CP_LOCK line, ignoring the leading [timestamp].
    Example:
      "[ 3203.690410] CP_LOCK op_read_trylock fail: pid=... "
    -> "CP_LOCK op_read_trylock fail: pid=... "
    """
    idx = line.find("]")
    if idx == -1:
        # No timestamp bracket; use the whole line as body (minus newline)
        return line.rstrip("\n")
    # Skip the ']' and following spaces
    body = line[idx + 1:].lstrip()
    return body.rstrip("\n")


def build_compact_log(src_path: str) -> str:
    """
    Build a compact log based on CP_LOCK runs and return the compact path.
    """
    compact_path = make_unique_path(src_path, "-compact")

    with open(src_path, "r", encoding="utf-8", errors="replace") as src, \
         open(compact_path, "w", encoding="utf-8", errors="replace") as dst:

        run_body = None
        first_line = None
        last_line = None
        run_count = 0

        def flush_run():
            nonlocal run_body, first_line, last_line, run_count
            if run_body is None or first_line is None:
                return
            if run_count == 1:
                dst.write(first_line)
            elif run_count == 2:
                dst.write(first_line)
                dst.write(last_line)
            else:
                # N >= 3: first line, summary, last line
                dst.write(first_line)
                skipped = run_count - 2
                summary = f"{run_body} appeared {skipped} times\n"
                dst.write(summary)
                dst.write(last_line)
            run_body = None
            first_line = None
            last_line = None
            run_count = 0

        for line in src:
            if "CP_LOCK" in line:
                body = extract_cp_lock_body(line)
                if run_body is None:
                    # Start a new run
                    run_body = body
                    first_line = line
                    last_line = line
                    run_count = 1
                else:
                    if body == run_body:
                        # Same run
                        run_count += 1
                        last_line = line
                    else:
                        # Different CP_LOCK body -> flush previous run, start a new one
                        flush_run()
                        run_body = body
                        first_line = line
                        last_line = line
                        run_count = 1
            else:
                # Non CP_LOCK line: flush any pending run, then write this line
                flush_run()
                dst.write(line)

        # End of file: flush any remaining run
        flush_run()

    return compact_path


def maybe_make_final_compact(compact_path: str) -> str | None:
    """
    If compact_path is larger than 100MB, create a final-compact file
    with only the last 10MB and return its path. Otherwise, return None.
    """
    size = os.path.getsize(compact_path)
    if size <= HUNDRED_MB:
        return None

    final_path = make_unique_path(compact_path, "-final-compact")

    with open(compact_path, "rb") as src, open(final_path, "wb") as dst:
        offset = size - TEN_MB if size > TEN_MB else 0
        src.seek(offset)
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)

    return final_path


def count_cp_lock(path: str) -> Counter:
    """
    Count CP_LOCK categories in the given log file.
    Category is extracted as 'CP_LOCK <something>' up to the colon.
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
    if not os.path.isfile(src_path):
        print(f"Error: file not found: {src_path}", file=sys.stderr)
        sys.exit(1)

    try:
        # 1) Build compact log with CP_LOCK run compression
        compact_path = build_compact_log(src_path)

        # 2) If compact log is larger than 100MB, build final-compact (last 10MB)
        final_path = maybe_make_final_compact(compact_path)

        # 3) Count CP_LOCK categories
        src_counts = count_cp_lock(src_path)
        compact_counts = count_cp_lock(compact_path)

        print_counts(f"CP_LOCK stats in full log ({src_path})", src_counts)
        print_counts(f"CP_LOCK stats in compact log ({compact_path})", compact_counts)

        if final_path is not None:
            final_counts = count_cp_lock(final_path)
            print_counts(f"CP_LOCK stats in final-compact log ({final_path})",
                         final_counts)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
