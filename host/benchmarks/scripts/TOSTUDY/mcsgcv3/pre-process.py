#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple


BEGIN_MARKER = "do_garbage_collect begin"
FINISH_MARKER = "do_garbage_collect finish"
SIT_MARKER = "<SIT DUMP>"

TS_PREFIX_RE = re.compile(r"^\[\s*\d+(?:\.\d+)?\]\s*")

# Example:
# [  207.676539] DEBUG_CSGC<pid=7297 comm=file_writer>:Allocated segment 15392 of type 1 in LFS manner
ALLOC_SEG_RE = re.compile(
    r"^\[\s*\d+(?:\.\d+)?\]\s+"
    r"DEBUG_CSGC<pid=\d+\s+comm=[^>]+>:"
    r"Allocated segment \d+ of type (\d+) in LFS manner$"
)

ORIGC_RE = re.compile(r"origc=\s*\d+")


def strip_timestamp_prefix(line: str) -> str:
    m = TS_PREFIX_RE.match(line)
    if not m:
        return line
    return line[m.end():]


def normalize_begin_key(begin_line: str) -> str:
    s = strip_timestamp_prefix(begin_line).strip()
    s = ORIGC_RE.sub("origc=<NUM>", s)
    return s


def is_sit_dump_line(line: str) -> bool:
    s = strip_timestamp_prefix(line)
    return (SIT_MARKER in s) and ("alloc type=" in s)


def is_tail_line_0(line: str) -> bool:
    return "blk_finish_plug" in strip_timestamp_prefix(line)


def is_tail_line_1(line: str) -> bool:
    return FINISH_MARKER in strip_timestamp_prefix(line)


def is_tail_line_2(line: str) -> bool:
    return "total_freed" in strip_timestamp_prefix(line)


def is_tail_line_3(line: str) -> bool:
    return "seg_freed" in strip_timestamp_prefix(line)


def analyze_group(lines: List[str]) -> Tuple[bool, Optional[str], Optional[Tuple[str, ...]]]:
    """
    Returns:
      is_simple_group,
      begin_key (if simple),
      sit_signature (if simple)
    """
    if len(lines) != 13:
        return False, None, None

    begin = lines[0]
    sit_lines = lines[1:9]
    tail = lines[9:13]

    if BEGIN_MARKER not in begin:
        return False, None, None

    if not all(is_sit_dump_line(x) for x in sit_lines):
        return False, None, None

    if not (is_tail_line_0(tail[0]) and is_tail_line_1(tail[1]) and is_tail_line_2(tail[2]) and is_tail_line_3(tail[3])):
        return False, None, None

    begin_key = normalize_begin_key(begin)
    sit_sig = tuple(strip_timestamp_prefix(x).rstrip() for x in sit_lines)
    return True, begin_key, sit_sig


class Emitter:
    def __init__(self, out_f):
        self.out_f = out_f

        self.prev_alloc_type: Optional[int] = None
        self.prev_line_was_alloc: bool = False
        self.pending_dup_alloc: int = 0

    def _flush_alloc_summary_if_needed(self):
        if self.pending_dup_alloc > 0:
            self.out_f.write(f"=== omitted {self.pending_dup_alloc} duplicate allocated-segment lines (same type) ===\n")
            self.pending_dup_alloc = 0

    def emit_line(self, line: str):
        """
        Applies allocated-segment adjacent de-duplication while writing.
        """
        m = ALLOC_SEG_RE.match(line)
        if m:
            cur_type = int(m.group(1))
            if self.prev_line_was_alloc and self.prev_alloc_type == cur_type:
                self.pending_dup_alloc += 1
                return

            # type differs or previous not alloc -> flush summary then emit
            self._flush_alloc_summary_if_needed()
            self.out_f.write(line + "\n")
            self.prev_line_was_alloc = True
            self.prev_alloc_type = cur_type
            return

        # non-alloc line
        self._flush_alloc_summary_if_needed()
        self.out_f.write(line + "\n")
        self.prev_line_was_alloc = False
        self.prev_alloc_type = None

    def finalize(self):
        self._flush_alloc_summary_if_needed()


def build_output_path(input_path: Path, script_dir: Path) -> Path:
    # Use stem to avoid producing "...logafter-pre-process.log" when input ends with ".log"
    out_name = f"{input_path.stem}after-pre-process.log"
    return script_dir / out_name


def main() -> int:
    parser = argparse.ArgumentParser(description="Log pre-processor with GC-group and allocated-segment de-duplication.")
    parser.add_argument("input_file", help="Path to the input log file.")
    args = parser.parse_args()

    in_path = Path(args.input_file)
    if not in_path.exists():
        print(f"ERROR: input file not found: {in_path}", file=sys.stderr)
        return 2
    if not in_path.is_file():
        print(f"ERROR: input path is not a file: {in_path}", file=sys.stderr)
        return 2

    script_dir = Path(__file__).resolve().parent
    out_path = build_output_path(in_path, script_dir)

    prev_group_was_simple = False
    prev_begin_key: Optional[str] = None
    prev_sit_sig: Optional[Tuple[str, ...]] = None
    pending_dup_groups = 0

    in_group = False
    group_buf: List[str] = []

    def flush_dup_group_summary(emitter: Emitter):
        nonlocal pending_dup_groups
        if pending_dup_groups > 0:
            emitter.emit_line(f"=== omitted {pending_dup_groups} duplicate GC groups (identical 8 SIT DUMP lines) ===")
            pending_dup_groups = 0

    def finalize_group(emitter: Emitter):
        nonlocal in_group, group_buf, prev_group_was_simple, prev_begin_key, prev_sit_sig, pending_dup_groups

        if not in_group:
            return

        lines = group_buf
        in_group = False
        group_buf = []

        is_simple, begin_key, sit_sig = analyze_group(lines)

        if is_simple and begin_key is not None and sit_sig is not None:
            is_dup = prev_group_was_simple and (begin_key == prev_begin_key) and (sit_sig == prev_sit_sig)
            if is_dup:
                pending_dup_groups += 1
                # Keep prev_* as-is; current equals prev in terms of signature.
                prev_group_was_simple = True
                return

            # not duplicate
            flush_dup_group_summary(emitter)
            for l in lines:
                emitter.emit_line(l)
            prev_group_was_simple = True
            prev_begin_key = begin_key
            prev_sit_sig = sit_sig
            return

        # non-simple group: always output and break duplicate chain
        flush_dup_group_summary(emitter)
        for l in lines:
            emitter.emit_line(l)
        prev_group_was_simple = False
        prev_begin_key = None
        prev_sit_sig = None

    with in_path.open("r", encoding="utf-8", errors="replace") as f_in, out_path.open("w", encoding="utf-8", newline="\n") as f_out:
        emitter = Emitter(f_out)

        for raw in f_in:
            line = raw.rstrip("\n").rstrip("\r")
            if line.strip() == "":
                continue
            line = line.rstrip()

            if BEGIN_MARKER in line:
                finalize_group(emitter)
                in_group = True
                group_buf = [line]
                continue

            if in_group:
                group_buf.append(line)
            else:
                emitter.emit_line(line)

        finalize_group(emitter)
        flush_dup_group_summary(emitter)
        emitter.finalize()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
