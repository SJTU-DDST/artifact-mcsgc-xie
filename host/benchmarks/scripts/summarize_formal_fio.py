#!/usr/bin/env python3
"""Summarize fio JSON logs from formal CSGC performance runs."""

import argparse
import json
from pathlib import Path


def parse_spec(value: str) -> tuple[str, Path]:
    """Parse a LABEL=FIO_LOG command-line argument."""
    label, separator, path = value.partition("=")
    if not separator or not label or not path:
        raise argparse.ArgumentTypeError("result must use LABEL=FIO_LOG")
    return label, Path(path)


def load_fio_json(path: Path) -> dict:
    """Load fio JSON after the benchmark wrapper's short text preamble."""
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find("{")
    if start < 0:
        raise ValueError(f"fio JSON object is missing: {path}")
    result, _ = json.JSONDecoder().raw_decode(text[start:])
    return result


def summarize(path: Path) -> dict[str, float | int]:
    """Aggregate write throughput, IOPS, bytes, runtime, and errors."""
    document = load_fio_json(path)
    jobs = document.get("jobs", [])
    if not jobs:
        raise ValueError(f"fio jobs are missing: {path}")

    bw_bytes = 0.0
    iops = 0.0
    io_bytes = 0
    runtime_ms = 0
    errors = 0
    for job in jobs:
        write = job.get("write", {})
        bw_bytes += float(write.get("bw_bytes", 0))
        iops += float(write.get("iops", 0))
        io_bytes += int(write.get("io_bytes", 0))
        runtime_ms = max(runtime_ms, int(write.get("runtime", 0)))
        errors += int(job.get("error", 0) != 0)

    return {
        "mib_s": bw_bytes / (1024 * 1024),
        "iops": iops,
        "gib_written": io_bytes / (1024**3),
        "runtime_s": runtime_ms / 1000,
        "errors": errors,
    }


def main() -> int:
    """Print a TSV comparison and speedup relative to the selected baseline."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("result", nargs="+", type=parse_spec)
    args = parser.parse_args()

    rows = [(label, summarize(path)) for label, path in args.result]
    by_label = dict(rows)
    if args.baseline not in by_label:
        parser.error(f"baseline label is missing: {args.baseline}")
    baseline_mib_s = float(by_label[args.baseline]["mib_s"])
    if baseline_mib_s <= 0:
        raise ValueError("baseline throughput must be positive")

    print("label\tmib_s\tiops\tgib_written\truntime_s\terrors\tspeedup")
    for label, row in rows:
        print(
            f"{label}\t{row['mib_s']:.3f}\t{row['iops']:.1f}\t"
            f"{row['gib_written']:.3f}\t{row['runtime_s']:.3f}\t"
            f"{row['errors']}\t{float(row['mib_s']) / baseline_mib_s:.4f}x"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
