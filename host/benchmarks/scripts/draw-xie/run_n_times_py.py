#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: python3 run_n_times.py <target_script.py> <N> [args_for_target...]",
            file=sys.stderr,
        )
        return 1

    target_name = sys.argv[1]

    try:
        n = int(sys.argv[2])
    except ValueError:
        print("Error: N must be a positive integer.", file=sys.stderr)
        return 1

    if n <= 0:
        print("Error: N must be a positive integer.", file=sys.stderr)
        return 1

    all_target_args = sys.argv[3:]

    if len(all_target_args) % n != 0:
        print(
            "Error: the number of arguments for the target script must be a multiple of N.",
            file=sys.stderr,
        )
        return 1

    group_size = len(all_target_args) // n

    script_dir = Path(__file__).resolve().parent
    target_path = Path(target_name)
    if not target_path.is_absolute():
        target_path = script_dir / target_path

    if not target_path.is_file():
        print(f"Error: target script not found: {target_path}", file=sys.stderr)
        return 1

    for i in range(n):
        start = i * group_size
        end = start + group_size
        current_args = all_target_args[start:end]

        cmd = [sys.executable, str(target_path), *current_args]
        print(f"Run {i + 1}/{n}: {' '.join(cmd)}")
        result = subprocess.run(cmd)

        if result.returncode != 0:
            print(
                f"Error: target script failed at run {i + 1} with exit code {result.returncode}.",
                file=sys.stderr,
            )
            return result.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())