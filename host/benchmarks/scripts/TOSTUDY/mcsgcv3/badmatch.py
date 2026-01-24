#!/usr/bin/env python3
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


@dataclass
class StartRecord:
    segno: int
    pid: Optional[int]
    comm: Optional[str]
    line_idx: int
    line: str


PID_COMM_RE = re.compile(r"<pid=(\d+)\s+comm=([^>]+)>")
START_RE = re.compile(r"f2fs_pre_csgc_work starts.*?\bsegno\s*=\s*(\d+)\b")
END_RE = re.compile(r"f2fs_pre_csgc_work ends.*?\bsegno\s*=\s*(\d+)\b")


def extract_pid_comm(line: str) -> Tuple[Optional[int], Optional[str]]:
    m = PID_COMM_RE.search(line)
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def extract_start_segno(line: str) -> Optional[int]:
    m = START_RE.search(line)
    if not m:
        return None
    return int(m.group(1))


def extract_end_segno(line: str) -> Optional[int]:
    m = END_RE.search(line)
    if not m:
        return None
    return int(m.group(1))


def build_output_path(input_path: str) -> str:
    base = os.path.basename(input_path)
    date_tag = datetime.now().strftime("%Y%m%d")
    return f"{base}_{date_tag}.log"


def segno_matcher(segno: int) -> re.Pattern:
    return re.compile(rf"\bseg(?:no|_a)?\s*=\s*{segno}\b")


def line_matches_any(line: str, segno_pat: re.Pattern, pid: Optional[int], comm: Optional[str]) -> bool:
    if segno_pat.search(line):
        return True
    if pid is not None and f"pid={pid}" in line:
        return True
    if comm is not None and comm in line:
        return True
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_log_file>")
        return 2

    input_path = sys.argv[1]
    if not os.path.isfile(input_path):
        print(f"Error: file not found: {input_path}")
        return 2

    with open(input_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    pending: Dict[int, List[StartRecord]] = {}
    good_matches = 0

    for idx, line in enumerate(lines):
        sseg = extract_start_segno(line)
        if sseg is not None:
            pid, comm = extract_pid_comm(line)
            rec = StartRecord(segno=sseg, pid=pid, comm=comm, line_idx=idx, line=line)
            pending.setdefault(sseg, []).append(rec)
            continue

        eseg = extract_end_segno(line)
        if eseg is not None:
            q = pending.get(eseg)
            if q:
                q.pop(0)
                good_matches += 1
                if not q:
                    pending.pop(eseg, None)

    bad_records: List[StartRecord] = []
    for segno, q in pending.items():
        bad_records.extend(q)

    bad_matches = len(bad_records)

    print("==== f2fs_pre_csgc_work match summary ====")
    print(f"Input file     : {input_path}")
    print(f"Good matches   : {good_matches}")
    print(f"Bad matches    : {bad_matches}")

    if bad_matches == 0:
        print("No bad matches found. No output file generated.")
        return 0

    bad_records.sort(key=lambda r: r.line_idx)
    out_path = build_output_path(input_path)

    with open(out_path, "w", encoding="utf-8", errors="replace") as out:
        out.write("==== bad match report: f2fs_pre_csgc_work starts without ends ====\n")
        out.write(f"input_file={input_path}\n")
        out.write(f"generated_at={datetime.now().isoformat()}\n")
        out.write(f"good_matches={good_matches}\n")
        out.write(f"bad_matches={bad_matches}\n\n")

        for i, rec in enumerate(bad_records, 1):
            out.write("------------------------------------------------------------\n")
            out.write(f"[BAD {i}/{bad_matches}] start_line_idx={rec.line_idx}\n")
            out.write(f"segno={rec.segno} pid={rec.pid if rec.pid is not None else 'NA'} "
                      f"comm={rec.comm if rec.comm is not None else 'NA'}\n")
            out.write("start_line:\n")
            out.write(rec.line.rstrip("\n") + "\n")
            out.write("matched_lines_from_start:\n")

            seg_pat = segno_matcher(rec.segno)
            for j in range(rec.line_idx, len(lines)):
                ln = lines[j]
                if line_matches_any(ln, seg_pat, rec.pid, rec.comm):
                    out.write(ln.rstrip("\n") + "\n")

            out.write("\n")

    print(f"Output written : {out_path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
