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
