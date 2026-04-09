#!/usr/bin/env python3
import argparse
import os
import re
import sys
from typing import List


str1 = "[ 5380.743305] DEBUG_M_LEAST<pid=9851 comm=kworker/u145:3>:sbi->csgc_called=3915, do_garbage_collect_cs finish"

str2 = "[ 5391.125650] do_garbage_collect_cs = 10365304 us, csgc_called = 3916from pid=9851 tgid=9851 comm=kworker/u145:3"


def sanitize_fragment(text: str, length: int = 5) -> str:
    fragment = text[:length]
    fragment = re.sub(r"[^A-Za-z0-9._-]", "_", fragment)
    return fragment or "empty"


def build_output_path(input_path: str, s1: str, s2: str) -> str:
    directory = os.path.dirname(input_path)
    filename = os.path.basename(input_path)

    part1 = sanitize_fragment(s1, 5)
    part2 = sanitize_fragment(s2, 5)

    base_name = f"{filename}-cut-from-{part1}{part2}"
    candidate = os.path.join(directory, f"{base_name}.log")

    if not os.path.exists(candidate):
        return candidate

    index = 1
    while True:
        candidate = os.path.join(directory, f"{base_name}{index}.log")
        if not os.path.exists(candidate):
            return candidate
        index += 1


def find_matching_lines(lines: List[str], needle: str) -> List[int]:
    return [idx for idx, line in enumerate(lines) if needle in line]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cut lines between the unique occurrences of str1 and str2."
    )
    parser.add_argument(
        "file_path",
        help="Absolute path of the target file."
    )
    args = parser.parse_args()

    file_path = args.file_path

    if not os.path.isabs(file_path):
        print("Error: the input file path must be an absolute path.", file=sys.stderr)
        return 1

    if not os.path.exists(file_path):
        print("Error: the input file does not exist.", file=sys.stderr)
        return 1

    if not os.path.isfile(file_path):
        print("Error: the input path is not a regular file.", file=sys.stderr)
        return 1

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        print(f"Error: failed to read file: {exc}", file=sys.stderr)
        return 1

    str1_matches = find_matching_lines(lines, str1)
    str2_matches = find_matching_lines(lines, str2)

    if len(str1_matches) == 0:
        print("Error: str1 was not found in the file.", file=sys.stderr)
        return 1
    if len(str1_matches) > 1:
        print("Error: str1 matched more than one line.", file=sys.stderr)
        return 1

    if len(str2_matches) == 0:
        print("Error: str2 was not found in the file.", file=sys.stderr)
        return 1
    if len(str2_matches) > 1:
        print("Error: str2 matched more than one line.", file=sys.stderr)
        return 1

    start_idx = str1_matches[0]
    end_idx = str2_matches[0]

    if start_idx >= end_idx:
        print("Error: str1 does not appear before str2.", file=sys.stderr)
        return 1

    selected_lines = lines[start_idx:end_idx + 1]
    output_path = build_output_path(file_path, str1, str2)

    try:
        with open(output_path, "w", encoding="utf-8", errors="replace") as f:
            f.writelines(selected_lines)
    except OSError as exc:
        print(f"Error: failed to write output file: {exc}", file=sys.stderr)
        return 1

    print(f"Success: wrote output to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())