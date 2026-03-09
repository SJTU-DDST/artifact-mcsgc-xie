#!/usr/bin/env python3
import os
import re
import sys
import bisect
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class StartRecord:
    kind: str  # "pre", "post", or "enqueue"
    segno: int
    pid: Optional[int]
    comm: Optional[str]
    line_idx: int
    line: str


# Allow spaces around '=' (0/1/2 or more)
PID_COMM_RE = re.compile(r"<\s*pid\s*=\s*(\d+)\s+comm\s*=\s*([^>]+)\s*>")

PRE_START_RE = re.compile(r"f2fs_pre_csgc_work starts.*?\bsegno\s*=\s*(\d+)\b")
PRE_END_RE = re.compile(r"f2fs_pre_csgc_work ends.*?\bsegno\s*=\s*(\d+)\b")

POST_START_RE = re.compile(r"f2fs_post_csgc_work starts.*?\bsegno\s*=\s*(\d+)\b")
POST_FINISH_RE = re.compile(r"f2fs_post_csgc_work finish.*?\bsegno\s*=\s*(\d+)\b")

ENQUEUE_RE = re.compile(r"enqueue:\s*seg_a\s*=\s*(\d+)\b")

# enqueue special good-match patterns (pid/comm stream)
NO_VALID_RE = re.compile(r"no valid blocks in segno\s*=\s*(\d+)\b")
QUEUE_SKIP_RE = re.compile(r"queue SKIP already freed segno\s*=\s*(\d+)\b")

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
    return re.compile(rf"\bseg(?:no|_a)?\s*=\s*{segno}\b")


def line_matches_any(line: str, segno_pat: re.Pattern, pid: Optional[int], comm: Optional[str]) -> bool:
    if segno_pat.search(line):
        return True
    if pid is not None and re.search(rf"\bpid\s*=\s*{pid}\b", line):
        return True
    if comm is not None and comm in line:
        return True
    return False


def enqueue_special_skip_good(enq: StartRecord, lines: List[str]) -> bool:
    """
    Special good match:
      In the subsequence of lines with the same (pid, comm) as the enqueue line (ignoring blank lines),
      the next two lines after the enqueue line must be:
        1) "no valid blocks in segno=<S>"
        2) "queue SKIP already freed segno=<S>"
    """
    if enq.pid is None or enq.comm is None:
        return False

    found = 0
    for j in range(enq.line_idx + 1, len(lines)):
        if lines[j].strip() == "":
            continue

        pid2, comm2 = extract_pid_comm(lines[j])
        if pid2 != enq.pid or comm2 != enq.comm:
            continue

        found += 1
        if found == 1:
            m = NO_VALID_RE.search(lines[j])
            if not m:
                return False
            if int(m.group(1)) != enq.segno:
                return False
        elif found == 2:
            m = QUEUE_SKIP_RE.search(lines[j])
            if not m:
                return False
            if int(m.group(1)) != enq.segno:
                return False
            return True
        else:
            break

    return False


def enqueue_limited_prestart_good(enq: StartRecord, pre_start_idxs: List[int], pre_start_segnos: List[int]) -> bool:
    """
    Limited pre-start scan:
      From enqueue line forward, only check the next up to 8 PRE_START lines.
      If any of those has segno == enqueue.segno => good; else bad.
    """
    pos = bisect.bisect_right(pre_start_idxs, enq.line_idx)
    end = min(pos + 8, len(pre_start_idxs))
    for k in range(pos, end):
        if pre_start_segnos[k] == enq.segno:
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

    # PRE matching (start segno -> later end segno)
    pre_pending: Dict[int, List[StartRecord]] = {}
    pre_good = 0

    # POST matching (start segno,pid,comm -> later finish segno,pid,comm)
    post_pending: Dict[Tuple[int, int, str], List[StartRecord]] = {}
    post_good = 0

    # Collect enqueue candidates (offline evaluation)
    enqueue_records: List[StartRecord] = []

    # For enqueue limited scan: record all PRE_START occurrences (idx, segno)
    pre_start_idxs: List[int] = []
    pre_start_segnos: List[int] = []

    for idx, line in enumerate(lines):
        # ENQUEUE line: collect only, do not match online
        m = ENQUEUE_RE.search(line)
        if m:
            segno = int(m.group(1))
            pid, comm = extract_pid_comm(line)
            enqueue_records.append(StartRecord(kind="enqueue", segno=segno, pid=pid, comm=comm, line_idx=idx, line=line))
            continue

        # PRE start
        m = PRE_START_RE.search(line)
        if m:
            segno = int(m.group(1))
            pid, comm = extract_pid_comm(line)

            pre_start_idxs.append(idx)
            pre_start_segnos.append(segno)

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
            if pid is not None and comm is not None:
                key = (segno, pid, comm)
            else:
                key = (segno, pid if pid is not None else -1, comm if comm is not None else "__NA__")
            rec = StartRecord(kind="post", segno=segno, pid=pid, comm=comm, line_idx=idx, line=line)
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

    # PRE/POST bad records (remaining pending)
    pre_bad_records: List[StartRecord] = []
    for _, q in pre_pending.items():
        pre_bad_records.extend(q)

    post_bad_records: List[StartRecord] = []
    for _, q in post_pending.items():
        post_bad_records.extend(q)

    # Evaluate enqueue matches with NEW rules (offline)
    enqueue_good = 0
    enqueue_bad_records: List[StartRecord] = []
    for enq in sorted(enqueue_records, key=lambda r: r.line_idx):
        if enqueue_special_skip_good(enq, lines):
            enqueue_good += 1
            continue
        if enqueue_limited_prestart_good(enq, pre_start_idxs, pre_start_segnos):
            enqueue_good += 1
            continue
        enqueue_bad_records.append(enq)

    pre_bad = len(pre_bad_records)
    post_bad = len(post_bad_records)
    enqueue_bad = len(enqueue_bad_records)

    print("==== csgc match summary ====")
    print(f"Input file     : {input_path}")
    print(f"PRE     good   : {pre_good}")
    print(f"PRE     bad    : {pre_bad}")
    print(f"POST    good   : {post_good}")
    print(f"POST    bad    : {post_bad}")
    print(f"ENQUEUE good   : {enqueue_good}")
    print(f"ENQUEUE bad    : {enqueue_bad}")

    if pre_bad == 0 and post_bad == 0 and enqueue_bad == 0:
        print("No bad matches found. No output file generated.")
        return 0

    out_path = build_output_path(input_path)

    # Tail collection:
    # - main_idx: lines extracted due to pre/post bad matches (no context prefix)
    # - context_only_idx: lines added only by enqueue context windows (with context prefix in tail)
    extracted_lines: Dict[int, str] = {}
    main_idx = set()
    context_only_idx = set()

    def record_main(j: int, ln: str) -> None:
        extracted_lines[j] = ln
        main_idx.add(j)
        if j in context_only_idx:
            context_only_idx.discard(j)

    def record_context(j: int, ln: str) -> None:
        if j not in extracted_lines:
            extracted_lines[j] = ln
        if j not in main_idx:
            context_only_idx.add(j)

    pre_bad_records.sort(key=lambda r: r.line_idx)
    post_bad_records.sort(key=lambda r: r.line_idx)
    enqueue_bad_records.sort(key=lambda r: r.line_idx)

    with open(out_path, "w", encoding="utf-8", errors="replace") as out:
        out.write("==== bad match report ====\n")
        out.write(f"input_file={input_path}\n")
        out.write(f"pre_good={pre_good}\n")
        out.write(f"pre_bad={pre_bad}\n")
        out.write(f"post_good={post_good}\n")
        out.write(f"post_bad={post_bad}\n")
        out.write(f"enqueue_good={enqueue_good}\n")
        out.write(f"enqueue_bad={enqueue_bad}\n\n")

        # PRE bad section
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
                        record_main(j, ln)
                out.write("\n")

        # POST bad section
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
                        record_main(j, ln)
                out.write("\n")

        # ENQUEUE bad section: enqueue line +/- 10 lines
        out.write("############################################################\n")
        out.write("==== ENQUEUE bad matches (new rule): enqueue seg_a mismatched within next 8 pre-starts, and no skip-pattern ====\n")
        out.write("==== Output rule: show the enqueue line plus 10 lines before and after ====\n\n")

        if enqueue_bad == 0:
            out.write("(none)\n\n")
        else:
            n = len(lines)
            for i, rec in enumerate(enqueue_bad_records, 1):
                out.write("------------------------------------------------------------\n")
                out.write(f"[ENQUEUE BAD {i}/{enqueue_bad}] enqueue_line_idx={rec.line_idx}\n")
                out.write(
                    f"segno={rec.segno} pid={rec.pid if rec.pid is not None else 'NA'} "
                    f"comm={rec.comm if rec.comm is not None else 'NA'}\n"
                )

                start = max(0, rec.line_idx - 10)
                end = min(n - 1, rec.line_idx + 10)

                out.write(f"context_range=[{start}, {end}] (inclusive)\n")
                out.write("context_lines:\n")

                for j in range(start, end + 1):
                    prefix = ">> " if j == rec.line_idx else "   "
                    line_text = lines[j].rstrip("\n")  # avoid backslash in f-string expression
                    out.write(prefix + line_text + "\n")
                    record_context(j, lines[j])

                out.write("\n")

        # Tail section: all extracted lines sorted by timestamp, mark last line per (pid,comm),
        # and mark context-only lines with "====context=====".
        out.write("============================================================\n")
        out.write("==== all extracted lines sorted by timestamp (ascending) ====\n")

        sortable: List[Tuple[float, int, str, Optional[int], Optional[str], bool]] = []
        unsortable: List[Tuple[int, str, Optional[int], Optional[str], bool]] = []

        for j, ln in extracted_lines.items():
            pid, comm = extract_pid_comm(ln)
            ts = extract_timestamp(ln)
            is_context_only = j in context_only_idx
            if ts is None:
                unsortable.append((j, ln, pid, comm, is_context_only))
            else:
                sortable.append((ts, j, ln, pid, comm, is_context_only))

        sortable.sort(key=lambda x: (x[0], x[1]))
        unsortable.sort(key=lambda x: x[0])

        combined: List[Tuple[str, Optional[int], Optional[str], bool]] = []
        for _, _, ln, pid, comm, is_ctx in sortable:
            combined.append((ln, pid, comm, is_ctx))
        for _, ln, pid, comm, is_ctx in unsortable:
            combined.append((ln, pid, comm, is_ctx))

        last_pos: Dict[Tuple[int, str], int] = {}
        for idx2, (_, pid, comm, _) in enumerate(combined):
            if pid is None or comm is None:
                continue
            last_pos[(pid, comm)] = idx2

        for idx2, (ln, pid, comm, is_ctx) in enumerate(combined):
            prefix_parts: List[str] = []

            if pid is not None and comm is not None and last_pos.get((pid, comm)) == idx2:
                prefix_parts.append("<LAST_LINE>")

            if is_ctx:
                prefix_parts.append("====context=====")

            prefix = ""
            if prefix_parts:
                prefix = " ".join(prefix_parts) + " "

            out.write(prefix + ln.rstrip("\n") + "\n")

        if unsortable:
            out.write("---- lines without parseable timestamp were appended after sortable lines ----\n")

        out.write("\n")

    print(f"Output written: {out_path}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
