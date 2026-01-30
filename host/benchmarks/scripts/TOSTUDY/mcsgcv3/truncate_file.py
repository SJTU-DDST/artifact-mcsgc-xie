#!/usr/bin/env python3
import argparse
import os
import re
import sys
from typing import Tuple


def parse_size_to_bytes(size_str: str) -> int:
    s = size_str.strip()
    if not s:
        raise ValueError("Empty size string")

    m = re.fullmatch(r"(?i)\s*([0-9]+(?:\.[0-9]+)?)\s*([a-z]*)\s*", s)
    if not m:
        raise ValueError(f"Invalid size format: {size_str!r}")

    num = float(m.group(1))
    unit = (m.group(2) or "").strip().lower()

    unit_map = {
        "": 1,
        "b": 1,
        "byte": 1,
        "bytes": 1,

        "k": 1024,
        "kb": 1024,
        "kib": 1024,

        "m": 1024 ** 2,
        "mb": 1024 ** 2,
        "mib": 1024 ** 2,

        "g": 1024 ** 3,
        "gb": 1024 ** 3,
        "gib": 1024 ** 3,

        "t": 1024 ** 4,
        "tb": 1024 ** 4,
        "tib": 1024 ** 4,
    }

    if unit not in unit_map:
        raise ValueError(
            f"Unsupported unit {unit!r}. Supported: B, KB, MB, GB, TB, K/M/G/T, KiB/MiB/GiB/TiB"
        )

    if num < 0:
        raise ValueError("Size must be non-negative")

    size_bytes = int(num * unit_map[unit])
    return size_bytes


def human_bytes(n: int) -> str:
    if n < 0:
        return f"{n} B"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    x = float(n)
    i = 0
    while x >= 1024.0 and i < len(units) - 1:
        x /= 1024.0
        i += 1
    if i == 0:
        return f"{n} {units[i]}"
    return f"{x:.3f} {units[i]} ({n} B)"


def safe_suffix_for_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    if not s:
        s = "size"
    return s


def copy_prefix(input_path: str, output_path: str, limit_bytes: int, chunk_size: int = 8 * 1024 * 1024) -> None:
    tmp_path = output_path + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    written = 0
    with open(input_path, "rb") as fin, open(tmp_path, "wb") as fout:
        while written < limit_bytes:
            to_read = min(chunk_size, limit_bytes - written)
            buf = fin.read(to_read)
            if not buf:
                break
            fout.write(buf)
            written += len(buf)

        fout.flush()
        os.fsync(fout.fileno())

    os.replace(tmp_path, output_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Truncate a file by keeping only the first N bytes and write to a new file."
    )
    parser.add_argument("input_file", help="Path to input file")
    parser.add_argument("target_size", help="Target size, e.g., 200MB, 1.5GB, 500MiB, 1048576")
    parser.add_argument(
        "delete_flag",
        nargs="?",
        default="",
        help="Optional: use 'd' to delete the original file after successful truncation",
    )
    args = parser.parse_args(argv)

    input_path = args.input_file
    size_token = args.target_size
    delete_flag = (args.delete_flag or "").strip().lower()

    print("=== truncate_file.py ===")
    print(f"Input file      : {input_path!r}")
    print(f"Target size     : {size_token!r}")
    print(f"Delete flag     : {delete_flag!r}")

    if delete_flag not in ("", "d"):
        print("ERROR: Third argument must be 'd' or omitted.", file=sys.stderr)
        return 2

    if not os.path.isfile(input_path):
        print("ERROR: Input path is not a regular file.", file=sys.stderr)
        return 2

    try:
        target_bytes = parse_size_to_bytes(size_token)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    st = os.stat(input_path)
    input_size = st.st_size

    print(f"Input size      : {human_bytes(input_size)}")
    print(f"Target bytes    : {human_bytes(target_bytes)}")

    if input_size <= target_bytes:
        print("No action needed: input file size is already within the target size.")
        print("No output file will be created. No deletion will happen.")
        return 0

    suffix = safe_suffix_for_filename(size_token)
    output_path = input_path + suffix + ".log"

    print("Action          : truncation needed")
    print(f"Output file     : {output_path!r}")
    print(f"Keep bytes      : {human_bytes(target_bytes)}")
    if os.path.exists(output_path):
        print("Note            : output file already exists and will be overwritten.")

    try:
        copy_prefix(input_path, output_path, target_bytes)
    except Exception as e:
        print(f"ERROR: Failed to write output file: {e}", file=sys.stderr)
        return 1

    out_size = os.path.getsize(output_path)
    print(f"Output size     : {human_bytes(out_size)}")
    print("Result          : output file created successfully")

    if delete_flag == "d":
        try:
            os.remove(input_path)
            print("Deletion        : original input file deleted")
        except Exception as e:
            print(f"WARNING: Failed to delete original file: {e}", file=sys.stderr)
            return 1
    else:
        print("Deletion        : skipped (delete flag not set)")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
