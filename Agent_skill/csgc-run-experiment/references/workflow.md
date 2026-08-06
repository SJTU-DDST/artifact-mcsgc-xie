# CSGC Experiment Workflow

## Fixed Environment

- Host and experiment server: server 52, the local machine.
- SSD source server: `192.168.98.31`.
- Server-31 repository: `/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc`.
- Host F2FS repository: `/home/xin/work-xie/mcsgc-real/linux-cs`.
- Host build script: `/home/xin/work-xie/mcsgc-real/linux-cs/build_f2fs.sh`.
- Benchmark directory: `/home/xin/artifact-csgc/host/benchmarks/scripts`.
- Kernel-log directory:
  `/home/xin/artifact-csgc/host/benchmarks/scripts/TOSTUDY/mcsgc-0309`.
- Kernel-log collector: `old-mydmesg.sh` in the kernel-log directory.
- Analysis entry point: `host/benchmarks/scripts/draw-xie/breakdown.py`.
- Expected experimental block device identity:
  `nvme0n1 259:0 0 59.8G 0 disk`.

All server-31 access is read-only. The workflow may read the branch, commit, and Git status
with Git optional locks disabled so status queries do not refresh the remote index. It must
never modify the remote checkout or any other server-31 state.

The orchestrator itself must not invoke file-deletion operations. The existing approved
Host build, log-collector, and benchmark scripts may perform their normal internal clean
steps, temporary-file cleanup, and managed output replacement.

## State Machine

```text
PLAN_VALIDATED
  -> PREFLIGHT_PASSED
  -> HOST_MODULE_READY
  -> COLLECTOR_READY
  -> TEST_RUNNING
  -> TEST_EXITED
  -> COLLECTOR_FINALIZED
  -> RUN_VALIDATED
  -> NEXT_REPETITION
  -> BATCH_ANALYZED
  -> COMPLETED
```

Any error transitions to `FAILED`. Stop the active test with Ctrl+C only if it belongs to
the current orchestrator. Finalize the current collector after the test exits. Do not start
the next repetition and do not retry automatically.

## Per-Repetition Procedure

1. Acquire the global automation lock.
2. Verify that no benchmark, workload runner, Host F2FS build, device formatter/checker, or
   `old-mydmesg.sh` process is active and that no F2FS filesystem is mounted.
3. Verify the exact `lsblk` device identity.
4. Record the local Host Git branch, commit, and dirty paths.
5. Query and record the fixed server-31 repository branch, commit, and dirty paths through
   fixed read-only SSH commands.
6. Compare optional expected branches and commits from the plan. Fail on mismatch.
7. Confirm non-interactive sudo is available. Detached tmux sessions cannot answer a sudo
   password prompt.
8. If F2FS is loaded, run `sudo -n rmmod f2fs`.
9. Run `sudo -n build_f2fs.sh` from the Host repository and wait for success. Verify that
   F2FS is loaded afterward and record the built `f2fs.ko` SHA-256.
10. Allocate a unique kernel-log name and unique tmux session names.
11. Start `sudo -n old-mydmesg.sh <absolute-log-path>` in the new collector session. Wait
    until its console reports `Tracing dmesg to:`.
12. Snapshot the matching `outputs-<mode>-<ssd-thread>/` timestamp directories.
13. Start `sudo -n ./test.sh <mode> <ssd-thread> <config>` in the new lab session.
14. Monitor only appended console and kernel-log bytes at the configured interval. Stop on
    a fatal signature, unexpected session loss, or nonzero status.
15. Require the test process to exit successfully and require `Test script completed.` in
    the console before stopping the collector.
16. Send Ctrl+C to the current collector tmux session. Wait for `finderror.py` cleanup and
    collector exit.
17. Require a nonempty kernel log and exactly one new timestamped output root.
18. Record the complete run artifacts before beginning the next repetition.

## Tmux Ownership

Use unique sessions beginning with `csgc-auto-`. Never use, send keys to, or close the
operator's `lab`, `log`, or `log2` sessions. A session is owned by the orchestrator only when
its exact generated name is recorded in the current experiment state.

## Log Naming

Construct this base name:

```text
MMDD-<mode>-<ssd-thread>-<test-info>[-<other-info>].log
```

Pass the base name to the same normalization rule as `old-mydmesg.sh`. The resulting file
has a unique numeric suffix, for example:

```text
0804-csgcv0731-ssd1t-fio-ssdbreak-1.log
```

The user-provided `mode` and `ssd_thread_mode` remain the exact first two `test.sh`
arguments. They are labels that must match the actual Host and SSD code configuration; the
script does not infer or change firmware worker count from the label.

## Failure Handling

- Preserve every generated file and log outside the existing scripts' approved internal
  lifecycle cleanup.
- Record the failed command, return code, timestamp, and relevant log tail.
- If the current test is still active, send Ctrl+C only to its recorded tmux session and
  wait for exit.
- Stop the collector only after the test exits. If the test will not exit, leave the
  collector running, record the remaining sessions, and request manual intervention.
- Do not automatically unmount, reset the SSD, remove the module, kill a tmux server, or
  start a replacement run after failure.
