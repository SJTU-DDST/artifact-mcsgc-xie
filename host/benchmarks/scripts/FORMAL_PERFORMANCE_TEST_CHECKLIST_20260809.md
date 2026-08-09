# Formal F2FS/CSGC Performance Comparison Checklist

## Goal

Measure fio end-to-end write throughput with optional Host and OpenSSD debug,
trace, timing, and aggregate diagnostic calculations compiled out. The three
primary configurations are:

1. Original F2FS (`ori`) with the original OpenSSD firmware path.
2. Original CSGC with the same original Host and OpenSSD builds.
3. Current optimized mCSGC8t pipeline with the current Move Plan firmware and
   one active device worker.

Run the optimized no-pipeline build once as a selection check. Keep whichever
optimized build has the higher fio throughput as the final third configuration.

## Fixed Workload

- 86% partitioned prefill: 26,336 files, 2 MiB each, 16 disjoint job pools.
- Fixed precondition: one 1 GiB random-write round per job.
- Measured workload: 16 jobs, 4 KiB uniform random overwrite, `iodepth=16`,
  buffered I/O, 300 seconds.
- F2FS mount: `mode=lfs,background_gc=off,fsync_mode=strict,discard`.
- Primary result: fio JSON write bandwidth in MiB/s.

Formal mode deliberately does not read custom GC sysfs counters, reset or read
device statistics, start a Host measurement epoch, print periodic fio status,
or require pipeline statistics. It retains only two kernel markers around fio.

## Quiet Branches

| Role | Branch | Local worktree |
|---|---|---|
| Original Host for ORI and CSGC | `exp/formal-csgc-original-quiet-20260809` | `/tmp/linux-cs-formal-original-quiet` |
| Optimized Host pipeline | `exp/formal-mcsgc8t-pipeline-quiet-20260809` | `/tmp/linux-cs-formal-mcsgc8t-pipe-quiet` |
| Optimized Host no-pipeline | `exp/formal-mcsgc8t-nopipe-quiet-20260809` | `/tmp/linux-cs-formal-mcsgc8t-nopipe-quiet` |
| Original OpenSSD | `exp/formal-csgc-original-quiet-20260809` | `/tmp/openssd-formal-original-quiet` |
| Current OpenSSD, SSD1t | `exp/formal-mcsgc-quiet-20260809` | `/tmp/openssd-formal-mcsgc-quiet` |

The Host runtime log and runtime breakdown macros are both disabled. The latter
also removes diagnostic clock reads, atomic increments, accumulation, locks,
and extra `get_valid_blocks()` calls. OpenSSD production mode similarly forces
device timeline, Move Plan breakdown, optional runtime statistics, and
`GET_SSD_LOG`-only traffic/WAF counters off. The formal Host build helper also
disables the standard `CONFIG_F2FS_STAT_FS` Kconfig option.

## Build And Test Original ORI/CSGC

On the Host:

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh original-csgc
```

On server 31, check out `exp/formal-csgc-original-quiet-20260809`, run the
existing `scripts/sync_code.sh`, rebuild all Vitis applications, and restart
the OpenSSD with that complete image. The synchronized `config.h` must report
`CONFIG_OPENSSD_PRODUCTION_PERFORMANCE=1` and
`CONFIG_CSGC_ACTIVE_WORKERS=1` in all four Vitis projects.

Then run on the Host:

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
sudo ./run_formal_performance_test.sh original-ori
sudo ./run_formal_performance_test.sh original-csgc
```

Do not rebuild or restart either side between these two commands. The test
script resets and reformats the namespace for each command.

## Build And Test Optimized mCSGC8t

On server 31, check out `exp/formal-mcsgc-quiet-20260809`, synchronize, rebuild
all Vitis applications, and restart the OpenSSD. Confirm production mode is 1,
Move Plan fast unsafe mode is 1, and active workers is 1.

Pipeline Host:

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-pipeline
sudo ./run_formal_performance_test.sh mcsgc8t-pipeline
```

No-pipeline Host selection check:

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-nopipeline
sudo ./run_formal_performance_test.sh mcsgc8t-nopipeline
```

The wrapper validates the Host branch, records the Host commit and module
SHA-256, requires a built module from the matching worktree, and requires the
server-31 Vitis workspace to report SSD1t.

## Summarize Results

Locate each generated `fio.log`, then run:

```bash
python3 ./summarize_formal_fio.py \
  --baseline original-csgc \
  original-ori=/absolute/path/to/original-ori/fio.log \
  original-csgc=/absolute/path/to/original-csgc/fio.log \
  optimized=/absolute/path/to/selected-optimized/fio.log
```

The output reports MiB/s, IOPS, written GiB, runtime, fio errors, and speedup
relative to original CSGC. A formal result is invalid if `errors` is nonzero,
the runtime is materially shorter than 300 seconds, or the selected Host and
device provenance does not match this checklist.
