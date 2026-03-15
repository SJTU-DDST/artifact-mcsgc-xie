#!/usr/bin/env python3

import sys
from collections import OrderedDict

# ==============================
# Configuration
# ==============================
TARGET_STRINGS = [
    "error",
    "warning",
    "failed",
    "timeout",
    "bug",
    "f2fs_pre_csgc_work time =",
    "CSGC-va_STAT segno=",
    "do_garbage_collect_cs = ",
    "va-csgc called ",
]

# ==============================
# Main Logic
# ==============================
def count_lines_containing_targets(file_path: str, targets: list[str]) -> OrderedDict[str, int]:
    counts: OrderedDict[str, int] = OrderedDict((target, 0) for target in targets)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            for target in targets:
                if target in line:
                    counts[target] += 1

    return counts


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_file>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        counts = count_lines_containing_targets(file_path, TARGET_STRINGS)
    except FileNotFoundError:
        print(f"Error: file not found: {file_path}")
        sys.exit(1)
    except OSError as e:
        print(f"Error: failed to read file: {e}")
        sys.exit(1)

    print(f"Input file: {file_path}")
    print("Count of lines containing each target string:")
    for target, count in counts.items():
        print(f'"{target}": {count}')


if __name__ == "__main__":
    main()