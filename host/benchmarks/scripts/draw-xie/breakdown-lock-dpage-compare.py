#!/usr/bin/env python3

import argparse
import math
import sys
from pathlib import Path
import re
from typing import Dict, List, Tuple


SECTION_BASIC = "=== basic statistics (microseconds) ==="
SECTION_NORMALIZED = "=== normalized by valid_blocks (us per valid block) ==="
OUTPUT_FILENAME = "compare_3types.txt"

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
        description="Compare breakdown result.txt files from 1-thread, 2-thread, and 8-thread runs."
    )
    parser.add_argument("single_result", help="Absolute path to single-thread result.txt")
    parser.add_argument("two_result", help="Absolute path to 2-thread result.txt")
    parser.add_argument("eight_result", help="Absolute path to 8-thread result.txt")
    return parser.parse_args()


def parse_float(text: str) -> float:
    value = float(text)
    return value


def format_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    return f"{value:.3f}"


def diff_value(a: float, b: float) -> float:
    if math.isnan(a) or math.isnan(b):
        return float("nan")
    return a - b


def parse_result_file(path: Path) -> Dict[str, Dict[str, Dict[str, float]]]:
    result: Dict[str, Dict[str, Dict[str, float]]] = {
        "basic": {},
        "normalized": {},
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

            # Keep only timing-related measurement items.
            if "_us" not in name:
                continue

            result[current_section][name] = {
                "n": int(match.group("n")),
                "mean": parse_float(match.group("mean")),
                "median": parse_float(match.group("median")),
                "p80": parse_float(match.group("p80")),
                "top20_mean": parse_float(match.group("top20_mean")),
            }

    return result


def validate_input_file(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        raise ValueError(f"Input path must be absolute: {path}")
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    return path


def validate_section_keys(
    section_name: str,
    single_data: Dict[str, Dict[str, float]],
    two_data: Dict[str, Dict[str, float]],
    eight_data: Dict[str, Dict[str, float]],
) -> List[str]:
    single_keys = set(single_data.keys())
    two_keys = set(two_data.keys())
    eight_keys = set(eight_data.keys())

    if single_keys != two_keys or single_keys != eight_keys:
        missing_messages: List[str] = []

        only_in_single = sorted(single_keys - two_keys | single_keys - eight_keys)
        only_in_two = sorted(two_keys - single_keys | two_keys - eight_keys)
        only_in_eight = sorted(eight_keys - single_keys | eight_keys - two_keys)

        if only_in_single:
            missing_messages.append(
                f"{section_name}: items only in single-thread file: {', '.join(only_in_single)}"
            )
        if only_in_two:
            missing_messages.append(
                f"{section_name}: items only in 2-thread file: {', '.join(only_in_two)}"
            )
        if only_in_eight:
            missing_messages.append(
                f"{section_name}: items only in 8-thread file: {', '.join(only_in_eight)}"
            )

        raise ValueError("\n".join(missing_messages))

    return sorted(single_keys)


def build_section_output(
    title: str,
    item_names: List[str],
    single_data: Dict[str, Dict[str, float]],
    two_data: Dict[str, Dict[str, float]],
    eight_data: Dict[str, Dict[str, float]],
) -> List[str]:
    ranked_items: List[Tuple[float, str]] = []

    for name in item_names:
        diff_mean_8_1 = diff_value(eight_data[name]["mean"], single_data[name]["mean"])
        sort_key = diff_mean_8_1
        if math.isnan(sort_key):
            sort_key = float("-inf")
        ranked_items.append((sort_key, name))

    ranked_items.sort(key=lambda x: (-x[0], x[1]))

    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(title)
    lines.append("Sorted by: diff_mean(8t - 1t), descending")
    lines.append("=" * 80)
    lines.append("")

    metrics = ["mean", "median", "p80", "top20_mean"]

    for rank, (_, name) in enumerate(ranked_items, start=1):
        s = single_data[name]
        t = two_data[name]
        e = eight_data[name]

        lines.append(f"rank={rank} item={name}")
        lines.append(
            f"  n          : 1t={s['n']}  2t={t['n']}  8t={e['n']}"
        )

        for metric in metrics:
            s_val = s[metric]
            t_val = t[metric]
            e_val = e[metric]

            diff_8_1 = diff_value(e_val, s_val)
            diff_2_1 = diff_value(t_val, s_val)
            diff_8_2 = diff_value(e_val, t_val)

            lines.append(
                f"  {metric:<10}: "
                f"1t={format_float(s_val)}  "
                f"2t={format_float(t_val)}  "
                f"8t={format_float(e_val)}  "
                f"diff(8t-1t)={format_float(diff_8_1)}  "
                f"diff(2t-1t)={format_float(diff_2_1)}  "
                f"diff(8t-2t)={format_float(diff_8_2)}"
            )

        lines.append("")

    return lines


def main() -> int:
    args = parse_args()

    try:
        single_path = validate_input_file(args.single_result)
        two_path = validate_input_file(args.two_result)
        eight_path = validate_input_file(args.eight_result)

        single_parsed = parse_result_file(single_path)
        two_parsed = parse_result_file(two_path)
        eight_parsed = parse_result_file(eight_path)

        basic_names = validate_section_keys(
            "basic",
            single_parsed["basic"],
            two_parsed["basic"],
            eight_parsed["basic"],
        )
        normalized_names = validate_section_keys(
            "normalized",
            single_parsed["normalized"],
            two_parsed["normalized"],
            eight_parsed["normalized"],
        )

        output_lines: List[str] = []
        output_lines.extend(
            build_section_output(
                "basic statistics (microseconds)",
                basic_names,
                single_parsed["basic"],
                two_parsed["basic"],
                eight_parsed["basic"],
            )
        )
        output_lines.append("")
        output_lines.extend(
            build_section_output(
                "normalized by valid_blocks (us per valid block)",
                normalized_names,
                single_parsed["normalized"],
                two_parsed["normalized"],
                eight_parsed["normalized"],
            )
        )

        output_path = single_path.parent / OUTPUT_FILENAME
        with output_path.open("w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))
            f.write("\n")

        print(f"Output written to: {output_path}")
        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())