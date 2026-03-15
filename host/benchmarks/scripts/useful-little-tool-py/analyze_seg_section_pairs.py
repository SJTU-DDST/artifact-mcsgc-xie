#!/usr/bin/env python3

import sys
from collections import Counter

# ==============================
# Configuration
# ==============================
LSEG_MARKER = "CSGC-va_STAT segno"
LSECTION_MARKER = "section_gc_time ="


# ==============================
# Core Logic
# ==============================
def analyze_pairs(file_path: str) -> None:
    pair_counter = Counter()

    state = "idle"          # idle -> in_seg -> in_section
    seg_count = 0
    section_count = 0

    total_lseg = 0
    total_lsection = 0

    incomplete_heads = []
    incomplete_tails = []

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            is_lseg = LSEG_MARKER in line
            is_lsection = LSECTION_MARKER in line

            if not is_lseg and not is_lsection:
                continue

            if is_lseg:
                total_lseg += 1
            if is_lsection:
                total_lsection += 1

            if state == "idle":
                if is_lseg:
                    state = "in_seg"
                    seg_count = 1
                    section_count = 0
                elif is_lsection:
                    incomplete_heads.append((line_number, line.rstrip("\n")))
                continue

            if state == "in_seg":
                if is_lseg:
                    seg_count += 1
                elif is_lsection:
                    state = "in_section"
                    section_count = 1
                continue

            if state == "in_section":
                if is_lsection:
                    section_count += 1
                elif is_lseg:
                    pair_counter[(seg_count, section_count)] += 1
                    state = "in_seg"
                    seg_count = 1
                    section_count = 0
                continue

    if state == "in_section":
        pair_counter[(seg_count, section_count)] += 1
    elif state == "in_seg":
        incomplete_tails.append(
            f"File ended with an incomplete group: seg_count={seg_count}, section_count=0"
        )

    # ==============================
    # Print Result
    # ==============================
    print("=== Summary ===")
    print(f"Input file: {file_path}")
    print(f"Total Lseg lines: {total_lseg}")
    print(f"Total Lsection lines: {total_lsection}")
    print(f"Total distinct (a, b) types: {len(pair_counter)}")
    print(f"Total completed groups: {sum(pair_counter.values())}")
    print()

    print("=== Grouped (a, b) Statistics ===")
    if not pair_counter:
        print("No complete (a, b) groups found.")
    else:
        for (a, b), count in sorted(pair_counter.items()):
            print(f"(a={a}, b={b}) -> {count} time(s)")
    print()

    if incomplete_heads or incomplete_tails:
        print("=== Warnings ===")
        if incomplete_heads:
            print(f"Found {len(incomplete_heads)} Lsection line(s) appearing before any Lseg group.")
            for line_number, content in incomplete_heads[:10]:
                print(f"  Line {line_number}: {content}")
            if len(incomplete_heads) > 10:
                print(f"  ... {len(incomplete_heads) - 10} more omitted")
        if incomplete_tails:
            for msg in incomplete_tails:
                print(msg)


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_file>")
        sys.exit(1)

    file_path = sys.argv[1]

    try:
        analyze_pairs(file_path)
    except FileNotFoundError:
        print(f"Error: file not found: {file_path}")
        sys.exit(1)
    except OSError as e:
        print(f"Error: failed to read file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()