# Experiment Analysis

Analyze only after the final collector exits, or after a failed batch has stopped changing
state. Do not run broad analysis concurrently with a measured workload.

## Validate Each Repetition

Use `state.json` and the recorded paths. Confirm:

1. Host build status is zero and the built module SHA-256 is recorded.
2. Test status is zero for a successful repetition.
3. The test console contains `Test script completed.`.
4. The kernel log is nonempty and belongs to the same recorded tmux/test run.
5. The collector received Ctrl+C, ran its cleanup, and exited.
6. Exactly one new `outputs-<mode>-<ssd-thread>/<timestamp>` root was associated with the
   repetition.
7. The analysis command completed and its result directory exists.

Treat any failed item as an experiment issue. Do not silently exclude the run.

## Run The Existing Parser

The orchestrator runs this command for every successful kernel log when `analyze` is true:

```bash
python3 /home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/breakdown.py \
  /absolute/path/to/kernel.log
```

It records the new directory created under:

```text
/home/xin/artifact-csgc/host/benchmarks/scripts/draw-xie/breakdown-result/
```

## Compare Results

- Report the exact mode, SSD-thread label, config, repetition number, Host commit, remote
  commit, dirty paths, kernel log, output root, and parser result directory.
- Extract workload throughput and error status from the run's fio/filebench/YCSB output.
- Read `result.txt` and any generated GC-heavy result files in each parser result directory.
- Compare repetitions using the same metric and denominator. Report sample count, mean,
  spread, and obvious outliers when available.
- Separate Host evidence, device-log evidence, end-to-end workload results, and hypotheses.
- Do not claim that detected `ssd1t` or `ssd2t` proves the corresponding firmware binary
  is running merely because it appears in the Vitis source or directory name.
- Do not treat the server-31 checkout commit as proof of the flashed firmware binary.

## Finish The Record

Replace the `Pending agent analysis.` text under `## Agent Analysis` in the batch
`record.md`. Include:

- requested and completed test counts;
- success or the first stopping failure;
- important performance results;
- anomalies and invalid runs;
- exact supporting paths;
- remaining processes, tmux sessions, or mounts;
- conclusions separated from unproven explanations.
