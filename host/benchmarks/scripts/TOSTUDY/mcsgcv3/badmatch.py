#!/usr/bin/env python3
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class StartRecord:
    kind: str            # "pre" or "post"
    segno: int
    pid: Optional[int]
    comm: Optional[str]
    line_idx: int
    line: str


# Allow spaces around '=' (0/1/2 or more)
PID_COMM_RE = re.compile(r"<\s*pid\s*=\s*(\d+)\s+comm\s*=\s*([^>]+)\s*>")

PRE_START_RE = re.compile(r"f2fs_pre_csgc_work starts.*?\bsegno\s*=\s*(\d+)\b")
PRE_END_RE   = re.compile(r"f2fs_pre_csgc_work ends.*?\bsegno\s*=\s*(\d+)\b")

POST_START_RE  = re.compile(r"f2fs_post_csgc_work starts.*?\bsegno\s*=\s*(\d+)\b")
POST_FINISH_RE = re.compile(r"f2fs_post_csgc_work finish.*?\bsegno\s*=\s*(\d+)\b")

TS_RE = re.compile(r"\[\s*([0-9]+(?:\.[0-9]+)?)\s*\]")


def extract_pid_comm(line: str) -> Tuple[Optional[int], Optional[str]]:
    m = PID_COMM_RE.search(line)
    if not m:
        return None, None
    return int(m.group(1)), m.group(2)


def extract_timestamp(line: str) -> Optional[float]:
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def build_output_path(input_path: str) -> str:
    base = os.path.basename(input_path)
    return f"{base}badmatch.log"


def segno_matcher(segno: int) -> re.Pattern:
    # match segno= or seg_a= (allow spaces)
    return re.compile(rf"\bseg(?:no|_a)?\s*=\s*{segno}\b")


def line_matches_any(line: str, segno_pat: re.Pattern, pid: Optional[int], comm: Optional[str]) -> bool:
    if segno_pat.search(line):
        return True
    if pid is not None and re.search(rf"\bpid\s*=\s*{pid}\b", line):
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

    # -----------------------
    # 1) PRE matching
    #    match rule: start segno -> later end segno (segno-only)
    # -----------------------
    pre_pending: Dict[int, List[StartRecord]] = {}
    pre_good = 0

    # -----------------------
    # 2) POST matching
    #    match rule: start(segno,pid,comm) -> later finish(segno,pid,comm)
    # -----------------------
    post_pending: Dict[Tuple[int, int, str], List[StartRecord]] = {}
    post_good = 0

    for idx, line in enumerate(lines):
        # PRE start
        m = PRE_START_RE.search(line)
        if m:
            segno = int(m.group(1))
            pid, comm = extract_pid_comm(line)
            rec = StartRecord(kind="pre", segno=segno, pid=pid, comm=comm, line_idx=idx, line=line)
            pre_pending.setdefault(segno, []).append(rec)
            continue

        # PRE end
        m = PRE_END_RE.search(line)
        if m:
            segno = int(m.group(1))
            q = pre_pending.get(segno)
            if q:
                q.pop(0)
                pre_good += 1
                if not q:
                    pre_pending.pop(segno, None)
            continue

        # POST start
        m = POST_START_RE.search(line)
        if m:
            segno = int(m.group(1))
            pid, comm = extract_pid_comm(line)
            # For post matching, pid/comm are part of the key. If missing, we treat it as unmatched later.
            if pid is not None and comm is not None:
                key = (segno, pid, comm)
                rec = StartRecord(kind="post", segno=segno, pid=pid, comm=comm, line_idx=idx, line=line)
                post_pending.setdefault(key, []).append(rec)
            else:
                # Still record as pending under a special key that will never match
                # so it becomes a bad match and can be reported.
                rec = StartRecord(kind="post", segno=segno, pid=pid, comm=comm, line_idx=idx, line=line)
                key = (segno, pid if pid is not None else -1, comm if comm is not None else "__NA__")
                post_pending.setdefault(key, []).append(rec)
            continue

        # POST finish
        m = POST_FINISH_RE.search(line)
        if m:
            segno = int(m.group(1))
            pid, comm = extract_pid_comm(line)
            if pid is not None and comm is not None:
                key = (segno, pid, comm)
                q = post_pending.get(key)
                if q:
                    q.pop(0)
                    post_good += 1
                    if not q:
                        post_pending.pop(key, None)
            continue

    # Remaining pending are bad matches
    pre_bad_records: List[StartRecord] = []
    for _, q in pre_pending.items():
        pre_bad_records.extend(q)

    post_bad_records: List[StartRecord] = []
    for _, q in post_pending.items():
        post_bad_records.extend(q)

    pre_bad = len(pre_bad_records)
    post_bad = len(post_bad_records)

    print("==== csgc work match summary ====")
    print(f"Input file  : {input_path}")
    print(f"PRE  good   : {pre_good}")
    print(f"PRE  bad    : {pre_bad}")
    print(f"POST good   : {post_good}")
    print(f"POST bad    : {post_bad}")

    if pre_bad == 0 and post_bad == 0:
        print("No bad matches found. No output file generated.")
        return 0

    out_path = build_output_path(input_path)

    # Collect all extracted lines from ALL bad matches (pre+post), de-dup by original line index.
    extracted_seen_idx = set()
    extracted_lines_with_idx: List[Tuple[int, str]] = []

    def record_extracted(j: int, ln: str) -> None:
        if j in extracted_seen_idx:
            return
        extracted_seen_idx.add(j)
        extracted_lines_with_idx.append((j, ln))

    # Sort bad records by their position (stable output)
    pre_bad_records.sort(key=lambda r: r.line_idx)
    post_bad_records.sort(key=lambda r: r.line_idx)

    with open(out_path, "w", encoding="utf-8", errors="replace") as out:
        out.write("==== bad match report ====\n")
        out.write(f"input_file={input_path}\n")
        out.write(f"pre_good={pre_good}\n")
        out.write(f"pre_bad={pre_bad}\n")
        out.write(f"post_good={post_good}\n")
        out.write(f"post_bad={post_bad}\n\n")

        # -----------------------
        # Section: PRE bad matches
        # -----------------------
        out.write("############################################################\n")
        out.write("==== PRE bad matches: f2fs_pre_csgc_work starts without ends ====\n\n")

        if pre_bad == 0:
            out.write("(none)\n\n")
        else:
            for i, rec in enumerate(pre_bad_records, 1):
                out.write("------------------------------------------------------------\n")
                out.write(f"[PRE BAD {i}/{pre_bad}] start_line_idx={rec.line_idx}\n")
                out.write(
                    f"segno={rec.segno} pid={rec.pid if rec.pid is not None else 'NA'} "
                    f"comm={rec.comm if rec.comm is not None else 'NA'}\n"
                )
                out.write("start_line:\n")
                out.write(rec.line.rstrip("\n") + "\n")
                out.write("matched_lines_from_start:\n")

                seg_pat = segno_matcher(rec.segno)
                for j in range(rec.line_idx, len(lines)):
                    ln = lines[j]
                    if line_matches_any(ln, seg_pat, rec.pid, rec.comm):
                        out.write(ln.rstrip("\n") + "\n")
                        record_extracted(j, ln)

                out.write("\n")

        # -----------------------
        # Section: POST bad matches
        # -----------------------
        out.write("############################################################\n")
        out.write("==== POST bad matches: f2fs_post_csgc_work starts without finish ====\n\n")

        if post_bad == 0:
            out.write("(none)\n\n")
        else:
            for i, rec in enumerate(post_bad_records, 1):
                out.write("------------------------------------------------------------\n")
                out.write(f"[POST BAD {i}/{post_bad}] start_line_idx={rec.line_idx}\n")
                out.write(
                    f"segno={rec.segno} pid={rec.pid if rec.pid is not None else 'NA'} "
                    f"comm={rec.comm if rec.comm is not None else 'NA'}\n"
                )
                out.write("start_line:\n")
                out.write(rec.line.rstrip("\n") + "\n")
                out.write("matched_lines_from_start:\n")

                seg_pat = segno_matcher(rec.segno)
                for j in range(rec.line_idx, len(lines)):
                    ln = lines[j]
                    if line_matches_any(ln, seg_pat, rec.pid, rec.comm):
                        out.write(ln.rstrip("\n") + "\n")
                        record_extracted(j, ln)

                out.write("\n")

        # -----------------------
        # Tail section: all extracted lines sorted by timestamp
        # + mark <LAST_LINE> for each (pid,comm) group
        # -----------------------
        out.write("============================================================\n")
        out.write("==== all extracted lines sorted by timestamp (ascending) ====\n")

        sortable: List[Tuple[float, int, str, Optional[int], Optional[str]]] = []
        unsortable: List[Tuple[int, str, Optional[int], Optional[str]]] = []

        for j, ln in extracted_lines_with_idx:
            pid, comm = extract_pid_comm(ln)
            ts = extract_timestamp(ln)
            if ts is None:
                unsortable.append((j, ln, pid, comm))
            else:
                sortable.append((ts, j, ln, pid, comm))

        # sort by timestamp, tie-break by original line index for stability
        sortable.sort(key=lambda x: (x[0], x[1]))
        unsortable.sort(key=lambda x: x[0])

        combined: List[Tuple[str, Optional[int], Optional[str]]] = []
        for _, _, ln, pid, comm in sortable:
            combined.append((ln, pid, comm))
        for _, ln, pid, comm in unsortable:
            combined.append((ln, pid, comm))

        # find last occurrence index of each (pid, comm) pair
        last_pos: Dict[Tuple[int, str], int] = {}
        for idx, (_, pid, comm) in enumerate(combined):
            if pid is None or comm is None:
                continue
            last_pos[(pid, comm)] = idx

        for idx, (ln, pid, comm) in enumerate(combined):
            prefix = ""
            if pid is not None and comm is not None and last_pos.get((pid, comm)) == idx:
                prefix = "<LAST_LINE> "
            out.write(prefix + ln.rstrip("\n") + "\n")

        if unsortable:
            out.write("---- lines without parseable timestamp were appended after sortable lines ----\n")

        out.write("\n")

    print(f"Output written: {out_path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
