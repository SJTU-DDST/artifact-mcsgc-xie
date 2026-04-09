#!/usr/bin/env python3
import argparse
import os
import sys
from typing import List, Set


TARGET_STRINGS = [
    "in bash",
    "do_garbage_collect_cs",
]


def scan_file_for_keywords(file_path: str, keywords: List[str]) -> Set[str]:
    found = set()
    keywords_lower = [kw.lower() for kw in keywords]

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line_lower = line.lower()
            for original_kw, kw_lower in zip(keywords, keywords_lower):
                if kw_lower in line_lower:
                    found.add(original_kw)

            if len(found) == len(keywords):
                break

    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete the file if none of the target strings are found in it."
    )
    parser.add_argument("file_path", help="Path to the target file")
    args = parser.parse_args()

    file_path = args.file_path

    if not os.path.exists(file_path):
        print(f"ERROR: file does not exist: {file_path}")
        return 2

    if not os.path.isfile(file_path):
        print(f"ERROR: not a regular file: {file_path}")
        return 2

    try:
        found = scan_file_for_keywords(file_path, TARGET_STRINGS)
    except Exception as e:
        print(f"ERROR: failed to read file: {e}")
        return 2

    if not found:
        print("No target strings found. Deleting file.")
        try:
            os.remove(file_path)
            print(f"Deleted: {file_path}")
            return 0
        except Exception as e:
            print(f"ERROR: failed to delete file: {e}")
            return 2

    found_list = ", ".join(sorted(found))
    print(f"Found target string(s): {found_list}")
    print("File kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())