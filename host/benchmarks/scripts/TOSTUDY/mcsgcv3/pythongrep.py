#!/usr/bin/env python3
import sys
import os
import re
import hashlib
from typing import List, Tuple

# ====== CONFIGURATION ======


KEYWORDS = [
    "owner_cp_rwsem",
   "sbi->cp_rwsem",
   "try lock op"
]

KEYWORDS = [
   "pack node info and sit entry "
]

KEYWORDS = [
    "FAIL",
    "ERROR",
    "enqueue:",
    "Fail",
    "fail",
    "enqueue:",
    "free csi: segno",
    "do_garbage_collect_cs",
    "f2fs_post_csgc_work",
    "f2fs_pre_csgc_work",
    "CSGC: wait pool timeout",
    "queue all",
    "queue all"
]




# Switch: whether to include context lines (before/after) around matches.
# - True: output match lines with MATCH prefix + context + omission markers.
# - False: output only match lines with MATCH prefix.
ENABLE_CONTEXT = 0

CONTEXT_BEFORE = 50
CONTEXT_AFTER = 50
BRIDGE_GAP_LINES = 100  # If lines between two matches <= 100, print everything between them.

MATCH_PREFIX = "MATCH "
OMIT_FMT = "--- OMITTED {n} LINES ---\n"
# ===========================


def _dedup_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _sanitize_tag(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._+\-]+", "_", s).strip("_")
    if not s:
        s = "keywords"
    if len(s) <= 120:
        return s
    h = hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{s[:80]}_{h}"


def _find_matches_and_total_lines(input_path: str, keywords: List[str]) -> Tuple[List[int], int]:
    match_lines: List[int] = []
    total = 0
    with open(input_path, "r", errors="replace") as fin:
        for total, line in enumerate(fin, 1):
            if any(k in line for k in keywords):
                match_lines.append(total)
    return match_lines, total


def _build_segments(match_lines: List[int], total_lines: int) -> List[Tuple[int, int]]:
    if not match_lines:
        return []

    clusters: List[Tuple[int, int]] = []
    cluster_start = match_lines[0]
    cluster_end = match_lines[0]

    for m in match_lines[1:]:
        gap = m - cluster_end - 1
        if gap <= BRIDGE_GAP_LINES:
            cluster_end = m
        else:
            clusters.append((cluster_start, cluster_end))
            cluster_start = cluster_end = m
    clusters.append((cluster_start, cluster_end))

    segments: List[Tuple[int, int]] = []
    for a, b in clusters:
        seg_start = max(1, a - CONTEXT_BEFORE)
        seg_end = min(total_lines, b + CONTEXT_AFTER)
        segments.append((seg_start, seg_end))

    return segments


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <input_file>")
        sys.exit(1)

    input_path = sys.argv[1]
    if not os.path.isfile(input_path):
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    keywords = [k for k in KEYWORDS if k]
    if not keywords:
        print("Error: KEYWORDS is empty.")
        sys.exit(1)

    keywords_unique = _dedup_keep_order(keywords)

    base = os.path.basename(input_path)
    out_dir = os.path.dirname(input_path) or "."
    keyword_tag = _sanitize_tag("+".join(keywords_unique))
    output_path = os.path.join(out_dir, f"{base}.grep-{keyword_tag}.log")

    if not ENABLE_CONTEXT:
        # Output only matching lines, keep original order, prefix with MATCH.
        wrote_any = False
        with open(input_path, "r", errors="replace") as fin, open(output_path, "w") as fout:
            for line in fin:
                if any(k in line for k in keywords_unique):
                    fout.write(MATCH_PREFIX + line)
                    wrote_any = True
        if wrote_any:
            print(f"Done. Output written to: {output_path}")
        else:
            print(f"No matches. Created empty output: {output_path}")
        return

    # ENABLE_CONTEXT == True: output matches + context segments + omission markers.
    match_lines, total_lines = _find_matches_and_total_lines(input_path, keywords_unique)
    if not match_lines:
        open(output_path, "w").close()
        print(f"No matches. Created empty output: {output_path}")
        return

    segments = _build_segments(match_lines, total_lines)

    seg_idx = 0
    match_idx = 0
    prev_end = None

    with open(input_path, "r", errors="replace") as fin, open(output_path, "w") as fout:
        for lineno, line in enumerate(fin, 1):
            if seg_idx >= len(segments):
                break

            while seg_idx < len(segments) and lineno > segments[seg_idx][1]:
                prev_end = segments[seg_idx][1]
                seg_idx += 1
                if seg_idx >= len(segments):
                    break

            if seg_idx >= len(segments):
                break

            seg_start, seg_end = segments[seg_idx]
            if lineno < seg_start or lineno > seg_end:
                continue

            if lineno == seg_start and prev_end is not None:
                omitted = seg_start - prev_end - 1
                if omitted > 0:
                    fout.write(OMIT_FMT.format(n=omitted))

            while match_idx < len(match_lines) and match_lines[match_idx] < lineno:
                match_idx += 1

            if match_idx < len(match_lines) and match_lines[match_idx] == lineno:
                fout.write(MATCH_PREFIX + line)
                match_idx += 1
            else:
                fout.write(line)

    print(f"Done. Output written to: {output_path}")


if __name__ == "__main__":
    main()
