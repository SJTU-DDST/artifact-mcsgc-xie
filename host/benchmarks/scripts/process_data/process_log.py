#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kernlog_summarizer.py
Extract, classify, deduplicate, and count kernel log incidents from /var/log/kern.log
or from systemd-journald (journalctl -k). Produces per-incident files and summary reports.

Usage examples:
  # From a log file:
  python3 kernlog_summarizer.py --log /var/log/kern.log --out ./exp_klog

  # From journalctl for a time window:
  sudo python3 kernlog_summarizer.py --journal --since "2025-08-18 15:00:00" --until "2025-08-18 16:00:00" --out ./exp_klog

  # Read from stdin:
  sudo journalctl -k --since "2025-08-18 15:00:00" --until "2025-08-18 16:00:00" | python3 kernlog_summarizer.py --out ./exp_klog
"""
import argparse
import os
import re
import sys
import json
import csv
import hashlib
import subprocess
from datetime import datetime

CATEGORY_PATTERNS = {
    "KASAN": [
        r"\bKASAN:\b", r"use-after-free", r"slab-out-of-bounds", r"stack-out-of-bounds",
        r"global-out-of-bounds", r"vmalloc-out-of-bounds", r"wild-memory-access",
        r"\bRead of size\b", r"\bWrite of size\b",
    ],
    "KCSAN": [
        r"\bKCSAN:\b", r"\bdata-race\b", r"\bracing\b", r"\bprevious access\b",
    ],
    "LOCKDEP": [
        r"\blockdep\b", r"possible circular locking dependency detected",
        r"DEBUG_LOCKS_WARN_ON", r"possible recursive locking detected",
        r"bad unlock balance", r"unlocking unowned", r"\bmutex:\b", r"\bspinlock\b",
        r"irq-safe vs\. non-irq-safe", r"hardirqs|softirqs",
    ],
    "ATOMIC_SLEEP": [
        r"BUG: sleeping function called from invalid context",
        r"\bmight_sleep\(\)", r"scheduling while atomic", r"\bin_atomic\(\)",
        r"\bpreempt_count\b",
    ],
    "RCU": [
        r"RCU.*(Stall|detected stall|suspicious|INFO)", r"\brcu_sched\b",
    ],
    "WATCHDOG": [
        r"watchdog:.*(soft lockup|hard LOCKUP)", r"NMI watchdog:.*hard LOCKUP",
    ],
    "HUNG_TASK": [
        r"INFO: task .* blocked for more than",
    ],
    "OOPS_PANIC": [
        r"kernel BUG at", r"\bOops:\b", r"general protection fault",
        r"unable to handle .* dereference", r"BUG: unable to handle page fault",
        r"Kernel panic - not syncing", r"stack-protector",
    ],
    "SLUB_MEM": [
        r"\bSLUB:\b", r"Bad page state", r"page allocation failure", r"list_del corruption",
        r"double free", r"refcount .* (underflow|overflow)",
    ],
    "WORKQUEUE_SCHED": [
        r"\bworkqueue:\b.*(lockup|stuck)", r"\bsched:\b.*(latency|RT throttling)",
    ],
    "F2FS": [
        r"\bf2fs:\b.*(error|warning|BUG|panic|IO error|invalid|inconsistent|corrupted|fsync|checkpoint|SIT|NAT|orphan|discard|segment|quota|gc|roll-forward|cp_error)",
    ],
    "CALL_TRACE": [
        r"\bCall Trace:\b",
    ],
}

# Anchors that typically start a new incident section
ANCHOR_PATTERNS = [
    r"------------\[\s*cut here\s*\]------------",
    r"\bKASAN:\b", r"\bKCSAN:\b",
    r"^BUG:", r"^WARNING:",
    r"\bOops:\b", r"Kernel panic\b",
    r"\bCall Trace:\b",
    r"RCU.*(Stall|detected stall|suspicious|INFO)",
    r"watchdog:.*(soft lockup|hard LOCKUP)",
    r"INFO: task .* blocked for more than",
    r"\bf2fs:\b.*(error|warning|BUG|panic|IO error|invalid|inconsistent|corrupted|checkpoint|SIT|NAT|orphan|discard|segment|quota|gc|fsync|roll-forward|cp_error)",
]
ANCHORS_RE = re.compile("|".join(ANCHOR_PATTERNS), re.IGNORECASE)

# Title heuristics for an incident (first match of these lines, otherwise first non-empty)
TITLE_PATTERNS = [
    r"^BUG:.*", r"^WARNING:.*", r"\bKASAN:.*", r"\bKCSAN:.*", r"\bOops:.*",
    r"\bCall Trace:.*", r"Kernel panic.*", r"RCU.*(Stall|detected stall|suspicious|INFO).*",
    r"watchdog:.*(soft lockup|hard LOCKUP).*", r"INFO: task .* blocked for more than.*",
    r"\bf2fs:.*", r"\blockdep.*", r"sleeping function called from invalid context.*",
]

SYSLOG_PREFIX_RE = re.compile(r"^[A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\S+\s+")
BRACKET_TS_RE = re.compile(r"\[\s*\d+\.\d+\s*\]")
HEX_PTR_RE = re.compile(r"\b0x[0-9a-fA-F]+\b|\bffff[0-9a-fA-F]{8,}\b|\b[0-9a-fA-F]{12,}\b")
DECIMAL_RE = re.compile(r"\b\d+\b")

def normalize_signature(text: str) -> str:
    """Normalize a line to create a deduplication signature."""
    s = SYSLOG_PREFIX_RE.sub("", text)             # drop syslog timestamp/host
    s = BRACKET_TS_RE.sub("", s)                   # drop [ 123.456 ]
    s = HEX_PTR_RE.sub("HEX", s)                   # mask addresses
    s = DECIMAL_RE.sub("N", s)                     # mask bare numbers
    s = re.sub(r"\s+", " ", s).strip()
    return s

def sanitize_for_filename(s: str, limit: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")
    if len(s) > limit:
        s = s[:limit]
    return s or "no_title"

def pick_title(lines):
    for pat in TITLE_PATTERNS:
        rx = re.compile(pat, re.IGNORECASE)
        for ln in lines:
            if rx.search(ln):
                return ln.strip()
    for ln in lines:
        if ln.strip():
            return ln.strip()
    return "(no title)"

def classify(lines):
    for cat, patterns in CATEGORY_PATTERNS.items():
        for pat in patterns:
            rx = re.compile(pat, re.IGNORECASE)
            if any(rx.search(ln) for ln in lines):
                return cat
    return "OTHER"

def split_incidents(lines):
    incidents = []
    cur = []
    for ln in lines:
        if ANCHORS_RE.search(ln):
            if cur:
                incidents.append(cur)
                cur = []
        cur.append(ln.rstrip("\n"))
    if cur:
        incidents.append(cur)
    return incidents

def read_lines_from_log(path):
    with open(path, "r", errors="ignore") as f:
        for ln in f:
            yield ln.rstrip("\n")

def read_lines_from_journalctl(since=None, until=None):
    cmd = ["journalctl", "-k", "-o", "short-precise"]
    if since:
        cmd += ["--since", since]
    if until:
        cmd += ["--until", until]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"journalctl failed: {proc.stderr.strip()}")
    for ln in proc.stdout.splitlines():
        yield ln

def ensure_out_dir(path):
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)

def write_incident_files(incidents, outdir):
    manifest = []
    for idx, inc in enumerate(incidents, start=1):
        title = pick_title(inc)
        cat = classify(inc)
        sig = normalize_signature(title)
        short = sanitize_for_filename(sig)
        fname = f"incident_{idx:04d}_{cat}_{short}.log"
        fpath = os.path.join(outdir, fname)
        with open(fpath, "w") as fw:
            fw.write("\n".join(inc) + "\n")
        manifest.append({
            "index": idx,
            "category": cat,
            "title": title,
            "signature": sig,
            "file": fname,
        })
    return manifest

def dedup_and_count(manifest):
    counts_total = {}
    counts_by_cat = {}
    signatures = {}  # key -> example manifest entry

    for m in manifest:
        key = (m["category"], m["signature"])
        counts_total[key] = counts_total.get(key, 0) + 1
        counts_by_cat[m["category"]] = counts_by_cat.get(m["category"], 0) + 1
        signatures.setdefault(key, m)

    dedup_rows = []
    for (cat, sig), cnt in sorted(counts_total.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
        ex = signatures[(cat, sig)]
        dedup_rows.append({
            "category": cat,
            "count": cnt,
            "signature": sig,
            "example_title": ex["title"],
            "example_file": ex["file"],
        })
    return counts_by_cat, dedup_rows

def write_reports(outdir, counts_by_cat, dedup_rows, manifest):
    # summary.txt
    with open(os.path.join(outdir, "summary.txt"), "w") as fw:
        fw.write("# Kernel Log Summary\n\n")
        fw.write("## Counts by category\n")
        for cat, cnt in sorted(counts_by_cat.items(), key=lambda x: (-x[1], x[0])):
            fw.write(f"- {cat}: {cnt}\n")
        fw.write("\n## Top signatures (deduplicated)\n")
        for row in dedup_rows[:200]:
            fw.write(f"[{row['count']:4d}] {row['category']:<12} {row['signature']}  -> {row['example_file']}\n")

    # summary.json
    with open(os.path.join(outdir, "summary.json"), "w") as jf:
        json.dump({
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "counts_by_category": counts_by_cat,
            "deduplicated": dedup_rows,
            "incidents": manifest,
        }, jf, indent=2)

    # dedup.csv
    with open(os.path.join(outdir, "dedup.csv"), "w", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=["category", "count", "signature", "example_title", "example_file"])
        writer.writeheader()
        for row in dedup_rows:
            writer.writerow(row)

def main():
    ap = argparse.ArgumentParser(description="Extract, classify, and deduplicate kernel log incidents.")
    ap.add_argument("--log", help="Path to /var/log/kern.log (or any kernel log file).")
    ap.add_argument("--journal", action="store_true", help="Read from 'journalctl -k' instead of a file.")
    ap.add_argument("--since", help="Time window start, e.g. '2025-08-18 15:00:00'.")
    ap.add_argument("--until", help="Time window end, e.g. '2025-08-18 16:00:00'.")
    ap.add_argument("--out", required=True, help="Output directory.")
    args = ap.parse_args()

    outdir = ensure_out_dir(args.out)

    # Source lines
    if args.log:
        lines = list(read_lines_from_log(args.log))
    elif args.journal:
        lines = list(read_lines_from_journalctl(args.since, args.until))
    else:
        # stdin
        lines = [ln.rstrip("\n") for ln in sys.stdin]

    if not lines:
        print("No input lines found.", file=sys.stderr)
        sys.exit(1)

    incidents = split_incidents(lines)
    manifest = write_incident_files(incidents, outdir)
    counts_by_cat, dedup_rows = dedup_and_count(manifest)
    write_reports(outdir, counts_by_cat, dedup_rows, manifest)

    # Console summary
    print(f"[+] Incidents: {len(incidents)}")
    print("[+] Counts by category:")
    for cat, cnt in sorted(counts_by_cat.items(), key=lambda x: (-x[1], x[0])):
        print(f"    {cat:12s} {cnt}")
    print(f"[+] Outputs written to: {outdir}")
    print("    - summary.txt / summary.json / dedup.csv")
    print("    - incident_XXXX_<CATEGORY>_<SIGNATURE>.log")

if __name__ == "__main__":
    main()
