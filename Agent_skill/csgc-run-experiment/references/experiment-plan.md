# Experiment Plan

Create one JSON plan for each requested batch. Keep all keys and string values in English
ASCII. The plan is copied into the experiment record directory when execution starts.

## Schema

```json
{
  "experiment_id": "0804-csgcv0731-ssd1t-fio-batch01",
  "description": "CSGC fio comparison requested on 2026-08-04",
  "expected_host": {
    "branch": "optional exact branch",
    "commit": "optional full or leading commit hash",
    "allow_dirty": true
  },
  "expected_ssd": {
    "branch": "optional exact server-31 branch",
    "commit": "optional full or leading commit hash",
    "allow_dirty": true
  },
  "monitor_interval_seconds": 30,
  "collector_start_timeout_seconds": 30,
  "collector_stop_timeout_seconds": 180,
  "test_stop_timeout_seconds": 60,
  "analyze": true,
  "tests": [
    {
      "mode": "csgcv0731",
      "ssd_thread_mode": "ssd1t",
      "config": "configs/config06_fio_rand.sh",
      "repetitions": 2,
      "test_info": "fio",
      "other_info": "ssdbreak"
    }
  ]
}
```

## Required Fields

- `experiment_id`: unique record-directory name using letters, digits, dots, underscores,
  or hyphens.
- `tests`: nonempty ordered list of test cases.
- `mode`: exact first argument passed to `test.sh`. It must be `ori`, `iplfs`, or contain
  the case-sensitive substring `csgc`.
- `ssd_thread_mode`: expected mode, either `ssd1t` or `ssd2t`. The orchestrator compares
  it with the value detected from the Vitis workspace; it is not passed to `test.sh`.
- `config`: path relative to the benchmark directory and contained under `configs/`.
- `repetitions`: positive integer. Each repetition gets a new Host build, collector, kernel
  log, test invocation, output root, and record entry.
- `test_info`: short filename token such as `fio`.

## Optional Fields

- `description`: optional single-line ASCII batch purpose, at most 512 characters.
- `other_info`: optional filename token such as `ssdbreak`.
- `expected_host` and `expected_ssd`: optional machine-checkable version constraints.
- `allow_dirty`: defaults to `true` because experimental Host and SSD worktrees may contain
  intentional uncommitted code. Dirty paths are always recorded.
- Timeout and interval fields use the defaults shown above.
- `analyze`: defaults to `true`. Disable only when the user explicitly asks to defer
  analysis.

Do not guess a missing test case, mode, SSD-thread mode, config, or repetition count. Ask the
user. Expected Git fields may be omitted when the user did not constrain them; preflight
still records the observed values.
