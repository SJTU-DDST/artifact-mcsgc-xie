#!/usr/bin/env python3

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple


SECTION_BASIC = "=== basic statistics (microseconds) ==="
SECTION_NORMALIZED = "=== normalized by valid_blocks (us per valid block) ==="

OUTPUT_FILENAME = "compare_2types.txt"

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


ParsedSection = Dict[str, Dict[str, float]]
ParsedResult = Dict[str, ParsedSection]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two breakdown result.txt files. "
            "The first file is used as baseline, and the second file is compared against it."
        )
    )
    parser.add_argument(
        "baseline_result",
        help="Absolute path to the baseline result.txt file",
    )
    parser.add_argument(
        "target_result",
        help="Absolute path to the target result.txt file",
    )
    return parser.parse_args()


def parse_float(text: str) -> float:
    return float(text)


def format_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    return f"{value:.3f}"


def diff_value(target: float, baseline: float) -> float:
    if math.isnan(target) or math.isnan(baseline):
        return float("nan")
    return target - baseline


def parse_result_file(path: Path) -> ParsedResult:
    result: ParsedResult = {
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
    baseline_data: ParsedSection,
    target_data: ParsedSection,
) -> List[str]:
    baseline_keys = set(baseline_data.keys())
    target_keys = set(target_data.keys())

    if baseline_keys != target_keys:
        messages: List[str] = []

        only_in_baseline = sorted(baseline_keys - target_keys)
        only_in_target = sorted(target_keys - baseline_keys)

        if only_in_baseline:
            messages.append(
                f"{section_name}: items only in baseline file: "
                f"{', '.join(only_in_baseline)}"
            )

        if only_in_target:
            messages.append(
                f"{section_name}: items only in target file: "
                f"{', '.join(only_in_target)}"
            )

        raise ValueError("\n".join(messages))

    return sorted(baseline_keys)


def build_section_output(
    title: str,
    item_names: List[str],
    baseline_data: ParsedSection,
    target_data: ParsedSection,
) -> List[str]:
    ranked_items: List[Tuple[float, str]] = []

    for name in item_names:
        diff_mean = diff_value(
            target_data[name]["mean"],
            baseline_data[name]["mean"],
        )

        sort_key = diff_mean
        if math.isnan(sort_key):
            sort_key = float("-inf")

        ranked_items.append((sort_key, name))

    ranked_items.sort(key=lambda x: (-x[0], x[1]))

    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(title)
    lines.append("Sorted by: diff_mean(target - baseline), descending")
    lines.append("=" * 80)
    lines.append("")

    metrics = ["mean", "median", "p80", "top20_mean"]

    for rank, (_, name) in enumerate(ranked_items, start=1):
        baseline_item = baseline_data[name]
        target_item = target_data[name]

        lines.append(f"rank={rank} item={name}")
        lines.append(
            f"  n          : "
            f"baseline={baseline_item['n']}  "
            f"target={target_item['n']}"
        )

        for metric in metrics:
            baseline_value = baseline_item[metric]
            target_value = target_item[metric]
            diff = diff_value(target_value, baseline_value)

            lines.append(
                f"  {metric:<10}: "
                f"baseline={format_float(baseline_value)}  "
                f"target={format_float(target_value)}  "
                f"diff(target-baseline)={format_float(diff)}"
            )

        lines.append("")

    return lines


def main() -> int:
    args = parse_args()

    try:
        baseline_path = validate_input_file(args.baseline_result)
        target_path = validate_input_file(args.target_result)

        baseline_parsed = parse_result_file(baseline_path)
        target_parsed = parse_result_file(target_path)

        basic_names = validate_section_keys(
            "basic",
            baseline_parsed["basic"],
            target_parsed["basic"],
        )

        normalized_names = validate_section_keys(
            "normalized",
            baseline_parsed["normalized"],
            target_parsed["normalized"],
        )

        if not basic_names and not normalized_names:
            raise ValueError("No comparable timing statistics were found.")

        output_lines: List[str] = []
        output_lines.append(f"baseline_file={baseline_path}")
        output_lines.append(f"target_file={target_path}")
        output_lines.append("comparison=target - baseline")
        output_lines.append("")

        output_lines.extend(
            build_section_output(
                "basic statistics (microseconds)",
                basic_names,
                baseline_parsed["basic"],
                target_parsed["basic"],
            )
        )

        output_lines.append("")

        output_lines.extend(
            build_section_output(
                "normalized by valid_blocks (us per valid block)",
                normalized_names,
                baseline_parsed["normalized"],
                target_parsed["normalized"],
            )
        )

        output_path = baseline_path.parent / OUTPUT_FILENAME

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