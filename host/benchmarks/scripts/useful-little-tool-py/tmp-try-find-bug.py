#!/usr/bin/env python3

import re
import sys


# ==============================
# Configuration
# ==============================
PATTERN = re.compile(r"va-csgc called (\d+) times")


# ==============================
# Main Logic
# ==============================
def check_va_csgc_sequence(file_path: str) -> None:
    expected = 1
    matched_count = 0

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_number, line in enumerate(f, start=1):
                match = PATTERN.search(line)
                if match is None:
                    continue

                matched_count += 1
                actual = int(match.group(1))

                if actual != expected:
                    print("Error: sequence check failed.")
                    print(f"Line number: {line_number}")
                    print(f"Expected number: {expected}")
                    print(f"Actual number: {actual}")
                    print("Original line:")
                    print(line.rstrip("\n"))
                    sys.exit(1)

                expected += 1

    except FileNotFoundError:
        print(f"Error: file not found: {file_path}")
        sys.exit(1)
    except OSError as e:
        print(f"Error: failed to read file: {e}")
        sys.exit(1)

    if matched_count == 0:
        print("Warning: no line matching 'va-csgc called <number> times' was found.")
        return

    print("Check passed.")
    print(f"Matched lines: {matched_count}")
    print(f"Last valid number: {expected - 1}")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_file>")
        sys.exit(1)

    file_path = sys.argv[1]
    check_va_csgc_sequence(file_path)


if __name__ == "__main__":
    main()