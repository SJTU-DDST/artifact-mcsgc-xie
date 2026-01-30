#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from typing import List, Optional, Set, TextIO, Tuple


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
    "wait pool timeout",
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


def insert_numeric_suffix_before_last_log(path: str, n: int) -> str:
    lower = path.lower()
    pos = lower.rfind(".log")
    if pos == -1:
        return f"{path}.{n}"
    return f"{path[:pos]}.{n}{path[pos:]}"


def open_unique_output(base_output_path: str) -> Tuple[TextIO, str]:
    last_error: Optional[BaseException] = None

    for attempt in range(0, 10000):
        candidate = base_output_path if attempt == 0 else insert_numeric_suffix_before_last_log(base_output_path, attempt)

        try:
            fh = open(candidate, "x", encoding="utf-8", errors="replace")
            return fh, candidate
        except FileExistsError as e:
            last_error = e
            continue
        except PermissionError as e:
            last_error = e
            continue
        except OSError as e:
            last_error = e
            continue

    if last_error is None:
        raise RuntimeError("Failed to create a unique output file for unknown reasons.")
    raise RuntimeError(f"Failed to create a unique output file. Last error: {last_error}") from last_error


def scan_file(path: str, keywords: List[str]) -> Tuple[int, Optional[str], List[str]]:
    keywords_lower = [k.lower() for k in keywords]
    base_output_path = f"{path}finderror.log"

    total_hits = 0
    out: Optional[TextIO] = None
    out_path: Optional[str] = None
    seen_keywords: Set[str] = set()

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ok, matched = line_matches(line, keywords_lower)
                if not ok:
                    continue

                if out is None:
                    out, out_path = open_unique_output(base_output_path)

                total_hits += 1
                for m in matched:
                    seen_keywords.add(m)
                out.write(line)
    finally:
        if out is not None:
            out.close()

    if total_hits == 0:
        return 0, None, []
    return total_hits, out_path, sorted(seen_keywords)


def run_badmatch(input_path: str) -> int:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    badmatch_path = os.path.join(script_dir, "badmatch.py")

    print("Scan complete. Now running badmatch.py with the same input argument.")
    print("-" * 80)

    if not os.path.isfile(badmatch_path):
        print(f"ERROR: badmatch.py not found: {badmatch_path}")
        return 2

    try:
        proc = subprocess.run([sys.executable, badmatch_path, input_path], check=False)
        if proc.returncode is None:
            print("ERROR: badmatch.py did not return an exit code.")
            return 2
        if proc.returncode < 0:
            print(f"ERROR: badmatch.py terminated by signal {-proc.returncode}.")
            return 2
        return 0
    except Exception as e:
        print(f"ERROR: failed to run badmatch.py: {e}")
        return 2


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
    else:
        kw_str = ", ".join(matched_keywords) if matched_keywords else "(unknown)"
        print(f"Issues found: {hits} matching line(s).")
        print(f"Matched keyword(s): {kw_str}")
        print(f"Output written to: {out_path}")

    badmatch_rc = run_badmatch(input_path)
    if badmatch_rc != 0:
        return badmatch_rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
