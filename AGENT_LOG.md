# Agent Operation Log

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
