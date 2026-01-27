#!/usr/bin/env python3
import argparse
import os
import sys
from typing import Iterable, List, Optional, TextIO, Tuple


DEFAULT_KEYWORDS = [
    "fail",
    "failed",
    "failure",
    "bug",
    "warn",
    "warning",
    "error",
    "exception",
    "fatal",
    "panic",
    "oops",
    "segfault",
    "segmentation fault",
    "traceback",
    "assert",
    "assertion",
    "corrupt",
    "corruption",
    "invalid",
    "abort",
    "aborted",
    "deadlock",
    "hung",
    "stall",
    "stack trace",
    "fault"
]


def find_all(haystack: str, needle: str) -> List[int]:
    idxs: List[int] = []
    start = 0
    while True:
        i = haystack.find(needle, start)
        if i == -1:
            return idxs
        idxs.append(i)
        start = i + 1


def bug_matches_outside_debug_underscore(line_lower: str) -> bool:
    needle = "bug"
    idxs = find_all(line_lower, needle)
    if not idxs:
        return False

    for i in idxs:
        left = i - 2
        right = i + 4
        if left >= 0 and right <= len(line_lower) and line_lower[left:right] == "debug_":
            continue
        return True

    return False


def line_matches(line: str, keywords_lower: List[str]) -> Tuple[bool, List[str]]:
    s = line.lower()
    matched: List[str] = []

    for kw in keywords_lower:
        if kw == "bug":
            if bug_matches_outside_debug_underscore(s):
                matched.append("bug")
            continue

        if kw in s:
            matched.append(kw)

    return (len(matched) > 0), matched


def scan_file(path: str, keywords: List[str]) -> Tuple[int, Optional[str], List[str]]:
    keywords_lower = [k.lower() for k in keywords]
    output_path = f"{path}finderror.log"

    total_hits = 0
    out: Optional[TextIO] = None
    seen_keywords: set = set()

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ok, matched = line_matches(line, keywords_lower)
                if not ok:
                    continue

                if out is None:
                    out = open(output_path, "w", encoding="utf-8", errors="replace")

                total_hits += 1
                for m in matched:
                    seen_keywords.add(m)
                out.write(line)
    finally:
        if out is not None:
            out.close()

    if total_hits == 0:
        return 0, None, []
    return total_hits, output_path, sorted(seen_keywords)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan a file for error-related substrings (case-insensitive) with a special rule for 'bug' vs 'DEBUG_'."
    )
    parser.add_argument("input_file", help="Path to the input file to scan.")
    args = parser.parse_args()

    input_path = args.input_file
    if not os.path.isfile(input_path):
        print(f"ERROR: file not found: {input_path}")
        return 2

    hits, out_path, matched_keywords = scan_file(input_path, DEFAULT_KEYWORDS)

    if hits == 0:
        print("No issues found.")
        return 0

    kw_str = ", ".join(matched_keywords) if matched_keywords else "(unknown)"
    print(f"Issues found: {hits} matching line(s).")
    print(f"Matched keyword(s): {kw_str}")
    print(f"Output written to: {out_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
