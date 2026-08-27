# Euro-Par 2025 Original CSGC Reproduction

This branch keeps the workload definitions from artifact commit
`0271b907ec00ed643fd139403b726817c9fe8c32` and adds a fail-fast,
restartable experiment driver.

## Matrix

The driver runs the repaired original CSGC and original ORI once for every
published artifact point:

- Filebench: period fileserver, fileserver, and varmail.
- YCSB: workloads A and F.
- fio overall: uniform and Zipf 1.1.
- Storage utilization: 60%, 70%, 80%, 90%, and 95%.
- F2FS section size: 1, 2, 4, 8, and 16 segments.
- Write distribution: uniform and Zipf 0.3, 0.7, 0.9, and 1.1.

There are 22 cases per system and 44 cases in total. IPLFS is intentionally
excluded because it requires a separate kernel and device configuration.

## Commands

Run the preflight without changing the SSD:

```bash
./run_europar25_original_matrix.sh --preflight
```

Start a new destructive matrix as the regular user:

```bash
tmux new-session -d -s europar25-repro \
  "cd /home/xin/artifact-csgc-europar25-repro/host/benchmarks/scripts && \
   ./run_europar25_original_matrix.sh"
```

Inspect one batch without changing it:

```bash
./run_europar25_original_matrix.sh --status BATCH_DIR
```

After all 44 cases succeed, generate the report and the Figure 4 through
Figure 8 counterparts:

```bash
./analyze_europar25_original_matrix.py BATCH_DIR
```

## Fidelity Notes

- fio starts immediately after the artifact's prefill. The later 16 GiB GC
  precondition is not used.
- The artifact runs YCSB with 36 client threads although the paper text says
  32 threads.
- The artifact Filebench files are uniformly sized from 512 KiB to 1.5 MiB;
  this differs from part of the paper's varmail prose.
- Host-side provenance can verify source commits, module hashes, and OpenSSD
  build inputs, but it cannot prove the byte identity of a running firmware
  ELF that does not export its own hash.
