import argparse
import os
import re
import sys
from typing import List, Tuple


RE_MEAN_LINE = re.compile(r"mean=([-+]?\d+(?:\.\d+)?)")


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def extract_mean_lines(result_file: str) -> List[Tuple[float, str]]:
    rows: List[Tuple[float, str]] = []

    with open(result_file, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if "mean=" not in line:
                continue

            m = RE_MEAN_LINE.search(line)
            if not m:
                continue

            mean_value = float(m.group(1))
            rows.append((mean_value, line))

    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result_file", help="absolute or relative path to result.txt")
    args = ap.parse_args()

    result_file = os.path.abspath(args.result_file)

    if not os.path.isfile(result_file):
        print(f"ERROR: input file does not exist: {result_file}", file=sys.stderr)
        return 1

    rows = extract_mean_lines(result_file)
    rows.sort(key=lambda x: x[0], reverse=True)

    out_dir = os.path.dirname(result_file)
    out_path = os.path.join(out_dir, "des_order_mean.txt")
    out_path = unique_path(out_path)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"source_file={result_file}\n")
        f.write(f"matched_lines={len(rows)}\n")
        f.write("sort_key=mean descending\n")
        f.write("\n")

        for mean_value, line in rows:
            f.write(line + "\n")

    print(f"source_file={result_file}")
    print(f"matched_lines={len(rows)}")
    print(f"output_file={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())