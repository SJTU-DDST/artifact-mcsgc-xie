---
name: csgc-run-experiment
description: Orchestrate, monitor, stop on failure, and analyze destructive CSGC benchmark batches on host server 52 while collecting read-only OpenSSD provenance from server 31. Use only when the user explicitly invokes this skill to validate, execute, inspect, or analyze a CSGC experiment plan involving test.sh, old-mydmesg.sh, host F2FS module builds, repeated mode and SSD-thread combinations, or their generated results.
---

# Run CSGC Experiments

Treat every experiment as a low-freedom, fail-fast hardware workflow. Use the bundled
orchestrator for state-changing steps instead of reproducing the shell sequence manually.

## Load The Workflow

1. Read `references/workflow.md` before preflight or execution.
2. Read `references/experiment-plan.md` before creating or editing a plan.
3. Read `references/analysis.md` after a run completes or fails.

## Require Explicit Intent

- Execute tests only when the user explicitly asks to start or run them.
- Treat a request to review, design, validate, inspect, or explain as non-executing.
- Never infer missing test tuples or repetition counts.
- Ask for missing values before creating an executable plan.
- Treat the user's explicit run instruction as authorization for the plan's documented
  destructive reset, format, module rebuild, and benchmark steps. Tool sandbox and sudo
  approval still apply and cannot be bypassed.

## Create And Validate A Plan

1. Convert the user's requested tuples into one JSON plan.
2. Store the plan under `/home/xin/artifact-csgc/experiment_plans/` with a unique
   `experiment_id`.
3. Preserve the exact mode/config arguments, expected SSD-thread mode, and repetition
   count for every case.
4. Run:

```bash
python3 <skill-dir>/scripts/csgc_experiment.py validate --plan <plan.json>
```

5. Run the read-only host and remote preflight outside the restricted Codex sandbox:

```bash
python3 <skill-dir>/scripts/csgc_experiment.py preflight --plan <plan.json>
```

6. Compare the reported Host branch/commit, server-31 branch/commit/dirty state, Vitis
   SSD-thread detection, device identity, mode label, and config with the user's request.
   Do not claim that source provenance proves which firmware binary is flashed onto the SSD.
7. Stop for clarification if the requested labels cannot be reconciled with the observed
   versions.

## Execute The Batch

Run the orchestrator only after validation and preflight succeed:

```bash
python3 <skill-dir>/scripts/csgc_experiment.py run --plan <plan.json> --execute
```

- Request the narrow host-level approval needed for this exact orchestrator command.
- Do not run a second monitor beside the orchestrator; it already performs incremental,
  low-overhead monitoring.
- Keep waiting while the command is active. Do not abandon the command or start another
  test.
- Report only stage changes, anomalies, or final completion.
- Never close or reuse tmux sessions that were not created by the current run.

## Complete The Agent Task

The orchestrator writes `state.json`, command logs, and `record.md` under
`/home/xin/artifact-csgc/experiment_records/<experiment_id>/`.

1. If all tests and parsers succeed, read every generated analysis result, compare repeated
   runs, and replace the pending Agent Analysis section in `record.md` with the final
   evidence-backed analysis.
2. If any step fails, inspect only the recorded command output, exact run logs, state, and
   narrowly scoped live state needed to explain the failure. Append the diagnosis and stop;
   do not retry unless the user explicitly requests it.
3. State whether any process, tmux session, or F2FS mount remains active.
4. State every file or directory created or modified.

## Enforce Safety Boundaries

- Keep all server-31 operations read-only. Use only the fixed SSH Git and Vitis-source
  queries implemented by the orchestrator and `test.sh`. Never edit, copy to, build on,
  or otherwise mutate server 31.
- The orchestrator must never invoke `rm`, `unlink`, or another deletion operation on
  existing files. Existing approved build and benchmark scripts may perform their own
  normal clean steps, temporary-file cleanup, and managed output replacement.
- Move a file to `/home/xin/artifact-csgc/TODELETE/` only when the user explicitly asks the
  Agent to discard it.
- Never start when another benchmark, collector, F2FS mount, or automation lock is active.
- Never bypass device identity checks, version expectations, sudo readiness, or result
  validation.
- Never automatically retry a failed test.
- Avoid repository scans, full-log rescans, compilation, or analysis during the measured
  workload. Perform analysis only after the final collector has exited.
