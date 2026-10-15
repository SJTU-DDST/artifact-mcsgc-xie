# Agent Operation Log

## 2026-09-16 Quiet NOWAIT Euro-Par reproduction preparation

- Host quiet branch: `exp/formal-mcsgc8t-nowait-quiet-20260916`.
- Host functional base: `a84e5bc532e85ef35f3e8db48585f7d2412e73bf`.
- Disabled checkpoint-source diagnostics and removed the associated normal-path
  accounting and notice output.
- Compiled the NOWAIT node-page readahead path unconditionally and removed its
  runtime A/B parameter and branch.
- Added a repetition-major 66-case Euro-Par launcher: 22 paper cases times three,
  with fio and YCSB before the historically riskier Filebench cases.
- The launcher records exact provenance, disables automatic panic reboot, checks
  raw checkpoints before offline fsck, and stops on lifecycle or kernel anomalies.
- Added repeated-result analysis with per-case dispersion and three-system Figure
  4 through Figure 8 extensions using the paired ORI/original-CSGC baseline.

## 2026-09-04 Standard-prefree Filebench incident

- Failed batch: `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260904_033455`.
- First fault: `new_curseg()` exhausted all allocator-visible segments, warned at `fs/f2fs/segment.c:3054` and `:3108`, then passed `MAIN_SEGS` as a segment number to `update_sit_entry()`, causing a NULL-pointer Oops in `f2fs_csgc_preallocate()`.
- The later `curseg_mutex` wait and stuck `syncfs` were consequences of the crashed PRE worker retaining the mutex, not the initiating fault.
- Host fixes:
  - standard-prefree: `3fb1821875defcc403762349e511745e32ca6d4e`
  - pre-sync: `c27ce0aed405880c33f5938c29eed19ea7ff34a9`
- The fix makes CSGC curseg rollover return `-ENOSPC`, uses the existing partial-preallocation rollback path, and avoids allocating a successor curseg after the final requested block.
- Artifact pin update: `4fee3fef3`.
- The kernel must be rebooted before validation because the Oops left the F2FS mount and writeback workers blocked.

## 2026-09-05 Standard-prefree writeback starvation incident

- Failed batch: `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_011508`.
- Workload: one `standard-prefree` Filebench fileserver validation run.
- The workload reached zero operations and hung during Filebench shutdown. Four Filebench workers waited in `f2fs_balance_fs()` for `gc_lock`.
- The initiating warning was `new_curseg()` returning `-ENOSPC` from ordinary writeback in `allocate_segment_by_default()`. Its void caller continued with a full curseg and raised a second warning in `f2fs_allocate_data_block()`.
- The writeback worker then held `gc_lock` in a no-progress foreground GC loop. GC calls exceeded two million while free/prefree/checkpoint state remained unchanged.
- Root cause: restoring standard prefree checkpoints disabled the emergency writeback reserve that had been guarded by `CONFIG_F2FS_CSGC_UNSAFE_PREFREE_RECLAIM`. Parallel CSGC destination preallocation could therefore consume every allocator-visible segment before ordinary writeback rolled its curseg.
- Fixes:
  - standard-prefree: `6800913218b20ffe9cd5b8c8b2c4ee52837ae8ca`
  - pre-sync: `38ab7d74408ecb8b9d05f7df933a42539aaf269f`
- The fix preserves four sections plus normal curseg rollover headroom, makes ordinary writeback balance before consuming that reserve, and makes CSGC curseg rollover return retryable `-EAGAIN` at the reserve boundary. It adds no locks or logging.
- Both branches passed `git diff --check`, target-object compilation, and full `f2fs.ko` compilation.
- Reboot pre-state recorded at `2026-09-05T01:41:05+08:00`: boot ID `85e0c5f7-e9ea-435c-9626-131d898f68a1`, boot time `2026-09-04 16:19:23`, uptime `33702.44` seconds.
- Runtime validation requires a reboot because the failed mount remains active and `SBI_NEED_FSCK` is set.

### Reboot handoff

- Pre-command state at `2026-09-05T01:42:27+08:00`: boot ID `85e0c5f7-e9ea-435c-9626-131d898f68a1`, boot time `2026-09-04 16:19:23`, uptime `33785.09` seconds.
- `/dev/nvme0n1` remained mounted at `/mnt/openssd_f2fs`; writeback worker `50233` remained in the no-progress GC loop.
- Planned action: one graceful `systemctl reboot` request.
- Graceful reboot requests issued for this incident: `1` after the command below is submitted.
- Forced or Magic SysRq reboot requests issued for this incident: `0`.
- On task recovery, compare the current boot ID and boot time before any further action. Never infer failure from the interrupted command transport, and do not automatically retry or escalate this reboot request.
- Reboot completion verified at `2026-09-05T04:36:30+08:00`: boot ID changed to `e9a837c3-a97c-459c-8082-eb7aec1c9682`, boot time advanced to `2026-09-05 01:52:21`, and uptime was `9849.59` seconds. No additional reboot command was issued.

## 2026-09-05 Standard-prefree reserve-boundary livelock

- Failed batch: `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_043754`.
- The repaired Filebench fileserver measurement completed all 300 seconds with sustained operations; the run then hung in `sync -f /mnt/openssd_f2fs`.
- `sync` remained in `wb_wait_for_completion()` while `flush-259:0` continuously executed `f2fs_gc()` and `do_garbage_collect_cs_pipeline()` under `gc_lock`.
- No allocator warning or Oops occurred. F2FS remained `CP: Good`, but the stable state was `free_segments=39`, `free_sections=1`, `prefree_segments=0`, while the configured writeback reserve was `40` segments.
- Root cause: the reserve correctly rejected additional CSGC curseg rollovers, but victim dispatch still selected CSGC when no prefree segment existed for a checkpoint to release. The foreground GC call repeatedly retried work that could not allocate a CSGC destination, so writeback never completed.
- Fixes:
  - standard-prefree: `971116fea3a93af94592b84c060fc0bd9cc71850`
  - pre-sync: `7dba52d06a4596a13b66a04432055e42d7e792d3`
- At or below the reserve boundary, data victims now use the original in-kernel collector. This consumes the reserved headroom to reclaim a section, after which CSGC can resume. The normal path adds one unlikely counter comparison and no lock or log operation.
- Both branches passed `git diff --check`, `gc.o` compilation, and full `f2fs.ko` compilation.

### Reboot handoff

- Pre-command state at `2026-09-05T04:58:50+08:00`: boot ID `e9a837c3-a97c-459c-8082-eb7aec1c9682`, boot time `2026-09-05 01:52:21`, uptime `11189.37` seconds.
- Three tasks were in uninterruptible sleep; writeback worker `11307` remained blocked in the failed F2FS run. The namespace was no longer listed as mounted, but the old F2FS module could not be reused safely.
- Planned action: one graceful `systemctl reboot` request.
- Graceful reboot requests issued for this incident: `1` after the command below is submitted.
- Forced or Magic SysRq reboot requests issued for this incident: `0`.
- On task recovery, compare boot identity first. Do not retry or escalate this reboot request based on an interrupted tool call.
- Final dispatch check at `2026-09-05T05:00:49+08:00`: boot ID remained `e9a837c3-a97c-459c-8082-eb7aec1c9682`, boot time remained `2026-09-05 01:52:21`, uptime was `11308.33` seconds, and no shutdown or reboot job was active.
- Reboot completion verified at `2026-09-05T11:33:45+08:00`: boot ID changed to `99ba46f5-e30c-4cd7-a6ab-d8127e35c170`, boot time advanced to `2026-09-05 05:07:27`, and uptime was `23178.60` seconds. No additional reboot command was issued.

## 2026-09-05 Reserve-fallback validation

- Standard-prefree batch: /home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_113521.
- Host commit: 971116fea3a93af94592b84c060fc0bd9cc71850.
- Filebench completed with 462.191 ops/s, but throughput collapsed at approximately 65 seconds from an early mean of 1778.082 ops/s to a late mean of 27.675 ops/s.
- The filesystem eventually synced and unmounted, proving that low-reserve ORIGC fallback removed the permanent livelock. Teardown still took 256.342 seconds and generated one hung-sync report, so the case is not lifecycle-clean.
- The collapse began while 63 free sections remained. The five-section emergency reserve therefore cannot explain the application-window regression.

## 2026-09-05 Pre-sync allocator corruption

- Pre-sync batch: /home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_115100.
- Host commit: 7dba52d06a4596a13b66a04432055e42d7e792d3.
- The outer runner returned zero and reported 476.899 ops/s, but the saved dmesg contains f2fs_allocate_data_block() warnings, "Bitmap was wrongly set", and "something went wrong during csgc, need fsck". This result is invalid.
- The low-reserve ORIGC path reached free_sections <= 2 while raw free segments still existed in partially occupied sections. new_curseg() returned -ENOSPC, but the legacy void normal-allocation call chain continued using the full curseg and corrupted SIT accounting.
- The reserve predicate now checks both raw free-segment headroom and complete free sections. Corrected commits:
  - standard-prefree: 9f268788af4a1eb2bb6ca0deb23213f85bd45a11
  - pre-sync: 965a68ebd07bd5c4ac32b8144de8f8e05ee76dcc
- Both branches passed git diff --check, target-object compilation, and full f2fs.ko compilation. The normal build initially encountered root-owned generated .cmd files from the existing sudo build workflow; verification then used the same sudo build ownership model and completed successfully.
- The experiment validator and analyzer now treat generic kernel WARN reports, incorrect SIT bitmaps, and "need fsck" as fatal signatures. A zero shell status can no longer classify this failure as successful.
- A reboot is required before the corrected standard-prefree branch can be tested because the running kernel emitted allocator and SIT warnings.

### Reboot handoff for allocator/SIT warning

- Pre-command state recorded at 2026-09-05T12:20:51+08:00.
- Boot ID: 99ba46f5-e30c-4cd7-a6ab-d8127e35c170.
- Boot time: 2026-09-05 05:07:27.
- Uptime: 26004.80 seconds.
- /dev/nvme0n1 was not mounted, no task was in uninterruptible sleep, no shutdown job was active, and the tainted F2FS module remained loaded with zero users.
- The allocator/SIT warnings occurred at 2026-09-05 11:59:56, after this boot began. This boot has therefore not yet cleared the incident.
- Planned action: issue one graceful systemctl reboot request after a final boot-identity check.
- Graceful reboot requests issued for this incident: 1 after the command below is submitted.
- Forced or Magic SysRq reboot requests issued for this incident: 0.
- On recovery, compare the current boot ID and boot time first. If either proves that reboot completed, do not issue another reboot command. If the result is unknown, do not retry or escalate automatically.
- Reboot completion verified at 2026-09-05T13:50:31+08:00: boot ID changed to 553f25ef-b0dd-4be0-940e-2aab7d3742eb, boot time advanced to 2026-09-05 12:24:16, and uptime was 5175.65 seconds. No additional reboot or force command was issued.

## 2026-09-05 Curseg exhaustion during ORIGC writeback

- Failed batch: `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_135234`.
- The `standard-prefree` Filebench fileserver measurement completed with `479.875 ops/s`; its early and late means were `1738.499` and `104.612 ops/s` respectively. The result is invalid because teardown subsequently corrupted SIT state.
- At `2026-09-05 14:01:01`, ordinary ORIGC nested under writeback reached a full curseg with no complete free section. `get_new_segment()` could not allocate a successor, but the legacy void normal-allocation interface continued and crossed the curseg boundary. The kernel then reported `Fail to preallocate blocks, ret = -11`, `Bitmap was wrongly set`, and `something went wrong during csgc, need fsck`.
- The common repair converts curseg allocation and normal data/node write paths to return errors. A failed rollover now exits before summary or SIT mutation; an allocation that validly consumed the old curseg's final block still succeeds, while the next caller retries rollover. CSGC preallocation uses the existing rollback path on a retryable rollover failure.
- Corrected Host commits:
  - standard-prefree: `bd5cee3e696c1636879e2529e2cc8aa553d681d4`
  - pre-sync: `e339ef84feac6ec37ad283a6e94abb7bf76a8233`
  - node-checkpoint: `d0f3ffafba2ac1e8e54ae3f7d33abd5344d7c4b2`
  - allocator-first: `fd0e8dbb546d69115fc82420286971181983cdb2`
- All four branches were pushed. The node-checkpoint and allocator-first branches passed full `f2fs.ko` builds; standard-prefree and pre-sync had already passed the same build before this record was added.
- The current boot emitted allocator and SIT warnings and must not be reused for another benchmark. A single graceful reboot will be requested only after recording boot identity; task recovery must verify that identity before taking any further reboot action.

### Reboot handoff for the ORIGC curseg incident

- Pre-command state recorded at `2026-09-05T16:05:37+08:00`.
- Boot ID: `553f25ef-b0dd-4be0-940e-2aab7d3742eb`.
- Boot time: `2026-09-05 12:24:16`.
- Uptime: `13281.50` seconds.
- `/dev/nvme0n1` was not mounted, no task was in uninterruptible sleep, and no shutdown or reboot job was active.
- Planned action: issue one graceful `systemctl reboot` request after one final boot-identity check.
- Graceful reboot requests issued for this incident: `0` before dispatch and `1` after the command is submitted.
- Forced or Magic SysRq reboot requests issued for this incident: `0`; none are authorized as an automatic fallback.
- On recovery, compare the current boot ID and boot time before any other action. If either proves that reboot completed, mark this handoff complete and never issue another reboot command. If the outcome is unknown, inspect only and wait for the user rather than retrying or escalating.
- Reboot completion verified at `2026-09-05T16:48:11+08:00`: boot ID changed to `95284f28-5d3f-4fcf-b8ff-48dd5b3ff93a`, boot time advanced to `2026-09-05 16:07:43`, and uptime was `2428.31` seconds.
- The post-reboot audit found `/dev/nvme0n1` present, no F2FS mount, no loaded F2FS module, no uninterruptible task, and no new F2FS, SIT, NVMe timeout, or kernel Oops report. No additional reboot or force command was issued.

## 2026-09-05 Post-reboot standard-prefree validation

- Batch: `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260905_165104`.
- Host commit: `bd5cee3e696c1636879e2529e2cc8aa553d681d4`.
- Filebench completed at `493.080 ops/s`; the early and late interval means were `1835.087` and `28.890 ops/s`. Sustained collapse began near 55 seconds while 65 complete free sections remained.
- The common curseg repair prevented the earlier allocator/SIT corruption. There was no Oops, SIT warning, NVMe timeout, or residual mount, and sync eventually completed.
- Teardown took `471.574` seconds and emitted three hung-sync reports. The runner now records a completed slow sync as a lifecycle warning when teardown reached `teardown_end` and the namespace is unmounted; unresolved waits and all correctness signatures remain fatal.

## 2026-10-15 to 2026-10-16: Prepare the IPLFS paper matrix

- Scope: preparation only. No namespace formatting, mounts, benchmark runs,
  reboots, device admin commands, server31 operations, global installation or
  system-configuration changes were performed.
- Created this independent worktree on
  `exp/taco-iplfs-evaluation-preparation-20261015`, based on artifact commit
  `c9823728e67c8869644b2bba083790783a3469d2`. The original artifact worktree and
  its existing modifications were preserved. Remote tracking was checked
  before choosing the base; no divergent history was merged.
- Audited the original artifact, saved F2FS/WeaveGC inputs, and pinned firmware
  `463e8b0b13ad345ed99c2176b1f81ad34d3c986a`. Implemented the fixed 63-run matrix,
  an eight-case short smoke suite, read-only readiness checks, exact command and
  version provenance, bounded teardown, failure-stop behavior and offline
  analysis. Workload generation preserves the original measurement boundaries.
- Built the full IPLFS kernel and modules in a private source/output tree as
  `5.11.0-iplfs-paper-20261016`; did not install them. The installed
  `5.11.0-iplfs` configuration matches the original source configuration and its
  F2FS srcversion matches this fresh build. Old whole-kernel build provenance
  remains unknown. Installed image/module hashes are pinned locally.
- Added a read-only, noninteractive `-n` mode to the private IPLFS fsck source.
  Built private mkfs/fsck and file_writer; froze the other executable inputs.
  Checked O_RDONLY behavior and unchanged contents on an invalid regular-file
  fixture, never on the target namespace. Valid IPLFS fsck acceptance still
  requires the future smoke test.
- Found that the pinned firmware reads a complete 4-KiB DSM parameter page.
  Added a dedicated, aligned-page DSM helper for the raw smoke test rather
  than the generic single-range CLI's 16-byte mapping. The IPLFS kernel already
  maps the full page; no kernel or firmware change was needed for this issue.
- Implemented archive-first server31 synchronization and operator startup
  receipts. Exercised archive preservation and link rejection only in local
  fixtures; did not run the deployment utility on server31.
- Confirmed existing integer WAF counters in firmware and historical IPLFS
  logs. The matrix collects them during formal runs, without a new firmware
  build or a separate 30-run WAF batch. Runtime validation remains pending.
- Passed 18 offline tests, shell syntax, Python parsing and whitespace checks.
  Build attempts, test fixtures and local configuration are retained under
  ignored `iplfs-paper-preparation/`; failed/intermediate build directories were
  not deleted. The final evidence directory is recorded in the paper guide.
- Read-only preflight correctly blocks execution: current kernel is
  `6.1.54-csgcmt-csgcmt`, ASLR is 2 rather than historical 0, and the firmware
  startup receipt/UART path are not configured. No system settings were changed.
- Added the operator README and updated the paper integration guide and handoff.
  No new performance data, manuscript revision, staging, commit or push occurred
  in this preparation task.

### Authorized publication

- The user subsequently authorized add, commit and push of the current work.
  Fresh fetches found no divergence; the new experiment branch did not yet
  exist on origin. Publication excludes private builds, generated results,
  archives and modifications in other worktrees.
- Recorded the user's explicit authorization to overwrite the dedicated
  `/dev/nvme0n1` during this IPLFS evaluation. Deployment prerequisites remain;
  no experiment or system-setting change is part of this publication task.
- Commit and remote equality are verified after push. The earlier unpublished
  statement describes the preparation stage, not the subsequent publication.
