#!/usr/bin/env python3

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


# =========================
# Configuration
# =========================

SORT_FIELD = "mean"

SECTION_BASIC = "=== basic statistics (microseconds) ==="
SECTION_NORMALIZED = "=== normalized by valid_blocks (us per valid block) ==="

OUTPUT_FILENAME = "des_result.txt"

VALUE_PATTERN = r"(?:nan|[-+]?\d+(?:\.\d+)?)"
STAT_LINE_RE = re.compile(
    rf"^(?P<name>[A-Za-z0-9_]+):\s+"
    rf"n=(?P<n>\d+)\s+"
    rf"mean=(?P<mean>{VALUE_PATTERN})\s+"
    rf"min=(?P<min>{VALUE_PATTERN})\s+"
    rf"max=(?P<max>{VALUE_PATTERN})\s+"
    rf"median=(?P<median>{VALUE_PATTERN})\s+"
    rf"p80=(?P<p80>{VALUE_PATTERN})\s+"
    rf"top20_mean=(?P<top20_mean>{VALUE_PATTERN})$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sort timing statistics in a breakdown result.txt file."
    )
    parser.add_argument(
        "result_file",
        help="Absolute path to the input result.txt file",
    )
    return parser.parse_args()


def to_sortable_float(text: str) -> float:
    value = float(text)
    if math.isnan(value):
        return float("-inf")
    return value


def parse_result_file(path: Path) -> Dict[str, List[Tuple[float, str]]]:
    sections: Dict[str, List[Tuple[float, str]]] = {
        "basic": [],
        "normalized": [],
    }

    current_section = None

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line:
                continue

            if line == SECTION_BASIC:
                current_section = "basic"
                continue

            if line == SECTION_NORMALIZED:
                current_section = "normalized"
                continue

            if line.startswith("==="):
                current_section = None
                continue

            if current_section is None:
                continue

            match = STAT_LINE_RE.match(line)
            if not match:
                continue

            name = match.group("name")

            # Only keep timing-related lines.
            if "_us" not in name:
                continue

            sort_value = to_sortable_float(match.group(SORT_FIELD))
            sections[current_section].append((sort_value, line))

    return sections


def build_output_lines(
    sections: Dict[str, List[Tuple[float, str]]]
) -> List[str]:
    output_lines: List[str] = []

    for section_name in ("basic", "normalized"):
        items = sections[section_name]
        items.sort(key=lambda x: x[0], reverse=True)

        if not items:
            continue

        if output_lines:
            output_lines.append("")

        output_lines.extend(line for _, line in items)

    return output_lines


def main() -> int:
    args = parse_args()
    input_path = Path(args.result_file)

    if not input_path.is_absolute():
        print("Error: input path must be an absolute path.", file=sys.stderr)
        return 1

    if not input_path.is_file():
        print(f"Error: file does not exist: {input_path}", file=sys.stderr)
        return 1

    sections = parse_result_file(input_path)
    output_lines = build_output_lines(sections)

    if not output_lines:
        print(
            "Error: no sortable timing statistics were found in the input file.",
            file=sys.stderr,
        )
        return 1

    output_path = input_path.parent / OUTPUT_FILENAME

    with output_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))
        f.write("\n")

    print(f"Output written to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())