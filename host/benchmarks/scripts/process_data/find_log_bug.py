#!/usr/bin/env python3
import sys
from collections import defaultdict

def main():
    if len(sys.argv) != 2:
        print("Usage: python3 script.py <logfile>")
        sys.exit(1)

    logfile = sys.argv[1]
    bug_counts = defaultdict(int)

    try:
        with open(logfile, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if "BUG:" in line:
                    
                    bug_info = line.split("BUG:", 1)[1].strip()
                    bug_counts[bug_info] += 1
    except FileNotFoundError:
        print(f"Error: File '{logfile}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    
    if not bug_counts:
        print("No BUG entries found.")
    else:
        print("BUG summary:")
        for bug_info, count in bug_counts.items():
            print(f"{bug_info}: {count}")

if __name__ == "__main__":
    main()
