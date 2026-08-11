# GC 单次耗时与 Breakdown 诊断测试手册

## 1. 测量目的

正式 quiet 实验只用于端到端 fio 带宽比较。该构建关闭了 Host 热路径计时、逐 GC trace 和设备统计，因此不能从已有 quiet 结果中事后还原单次 GC 或 PRE/SSD/POST 时间。

本诊断测试独立于正式性能分支，测量：

- 完整 `f2fs_gc()` 调用时间；
- `f2fs_gc()` 内 victim 选择、顶层 checkpoint、collector 和其余控制路径时间；
- 真正进入 collector 与没有调用任何 collector 的 no-work 调用；
- CSGC section wall-clock；
- 每个 segment 的 PRE、Host 等待 SSD 返回和 POST；
- 原始 CSGC 与 mCSGC8t PRE 内部的 summary、inode、data page、node page、数据重验证、打包、preallocation 和请求提交阶段；
- data/node ORIGC collector 分类。

诊断构建会增加时间戳读取和 printk，不能用其 fio 带宽替代正式 quiet 结果。它只负责解释正式结果的时间组成。

### 1.1 `f2fs_gc()` 统一口径

每次 `f2fs_gc()` 输出一条 `F2FS_GC_DIAG`，主要字段为：

```text
duration_us
victim_select_us
checkpoint_us
collector_us
other_us
collector_invoked
no_collector
gc_path=none|csgc|origc|mixed
```

其中 `victim_select_us` 累加该次调用内所有 `__get_victim()` 尝试；`checkpoint_us` 只统计 `f2fs_gc()` 顶层直接发起的 checkpoint；`collector_us` 统计 `do_garbage_collect()` 与 `do_garbage_collect_cs()`；其余入口检查、循环控制、清理和释放锁归入 `other_us`。原始 CSGC 在 collector 内执行的 `sync_fs_before_csgc()` 不重复计入顶层 `checkpoint_us`，而由 `CSGC_ORIGINAL_SECTION section_sync_us` 单独记录。

`collector_invoked=0`、`no_collector=1`、`gc_path=none` 表示该次 `f2fs_gc()` 没有进入任何 collector。这里的“无实际 GC 工作”严格指没有调用 `do_garbage_collect()` 或 `do_garbage_collect_cs()`；入口检查、失败的 victim 搜索或顶层 checkpoint 仍会分别计入相应阶段。这样可以将真正执行 collector 的调用与空间压力已解除、没有 victim 等调用分别统计。ORI 只测量到完整 `do_garbage_collect()` 边界，不继续拆分其内部 block 迁移步骤。

### 1.2 原始 CSGC 的逐 segment 口径

每个成功完成的原始 CSGC segment 输出兼容记录 `CSGC_ORIGINAL_SEGMENT` 和细分记录 `CSGC_ORIGINAL_SEGMENT_DETAIL`。两条记录通过 `segno + start_ns` 对应。PRE 被拆分为 summary、node list、inode lock、data-page lock、`cp_rwsem`、node-page lock、数据重验证、node/SIT 打包和目标块预分配；预分配还额外给出锁等待、分配、同步和等待同步子项。

对于发生 PRE retry 的 segment，`pre_work_total_us` 保留从第一次尝试开始到最终成功预分配的全部 wall-clock；`pre_attempts`、`pre_failed_attempts_us` 和 `pre_retry_gap_us` 单独说明重试成本；各 PRE 细分步骤只描述最终成功的那次尝试，并以 `pre_success_attempt_us` 为总量校验。这样失败尝试不会被错误归入最终一次 summary 读取。

Host 看到的 SSD 阶段拆为：

```text
ssd_trigger_roundtrip_us
ssd_inter_submit_gap_us
ssd_completion_wait_us
```

三者之和对应兼容字段 `approx_gc_cs_ssd_us`。该时间表示 Host 从提交 trigger 到收到 completion 的请求生命周期，包含 Host BIO 提交、NVMe/设备排队、设备执行和结果返回，不能直接等同于纯设备搬运时间。

POST 除了兼容记录中的 queue delay、metadata update 和最终 cleanup，还通过 `CSGC_ORIGINAL_SEGMENT_POST_DETAIL` 将 metadata update 拆成结果状态检查、SIT/summary 更新、dnode 更新、释放操作锁和释放 data pages。这里同样只拆主要阶段，不在每个 block 的更新循环内继续计时。

## 2. 诊断分支

```text
原始 ORI/CSGC:
  exp/diagnostic-original-gc-breakdown-20260811

mCSGC8t no-pipeline:
  exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811
```

两个分支分别从对应正式 quiet 分支建立，不修改正式性能分支。

## 3. 本轮四组测试

本轮固定比较原始 CSGC 和当前最佳 mCSGC8t no-pipeline + SSD1t，分别使用正式性能实验中的大文件和小文件负载。每组运行一次即可；单次运行已经包含数千个 GC 样本，重复三次主要影响 fio 性能置信区间，对阶段耗时分布的新增价值较低。

| 组别 | Host | OpenSSD | 负载 |
|---|---|---|---|
| 1 | 原始 CSGC 诊断分支 | 原始 CSGC 固件 | bigfile |
| 2 | 原始 CSGC 诊断分支 | 原始 CSGC 固件 | smallfile |
| 3 | mCSGC8t no-pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | bigfile |
| 4 | mCSGC8t no-pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | smallfile |

### 3.1 原始 CSGC：大文件与小文件

先确认 OpenSSD 使用正式原始 CSGC 实验的固件。31 服务器对应分支为：

```text
formal-original-csgc-main-20260809
```

然后在 Host 上切换诊断分支、编译模块并依次运行两个负载：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/diagnostic-original-gc-breakdown-20260811

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_gc_breakdown_host_module.sh original
sudo ./run_gc_breakdown_diagnostic.sh original-csgc bigfile
sudo ./run_gc_breakdown_diagnostic.sh original-csgc smallfile
```

两个负载共用同一 Host 模块和设备固件，中间不需要重新编译。

### 3.2 当前最佳 mCSGC8t no-pipeline：大文件与小文件

将 OpenSSD 切换到正式 mCSGC 固件分支并重新编译、启动：

```text
exp/formal-mcsgc-quiet-20260809
```

实际配置必须满足：

```text
Host protocol: Move Plan v2 unsafe fast path
Device workers: SSD1t
Device production performance mode: enabled
```

设备正常启动后，在 Host 上执行：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_gc_breakdown_host_module.sh mcsgc8t-nopipeline
sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-nopipeline bigfile
sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-nopipeline smallfile
```

这四组首先测 Host breakdown，不要求设备固件启用高频 breakdown。`approx_gc_cs_ssd_us` 和 `SSD_START -> SSD_END` 给出 Host 从提交请求到收到结果的时间；它包含设备排队、设备执行和返回延迟，不能单独解释为盘内搬运时间。

## 4. 输出文件

每次运行的结果目录会额外生成：

```text
external-dmesg.log
measured-fio-dmesg.log
gc-breakdown-diagnostic-result.txt
```

mCSGC8t 还会由现有分析链生成：

```text
result.txt
f2fs_gc_heavy_trace_result.txt
csgc_heavy_trace_result.txt
```

优先阅读 `gc-breakdown-diagnostic-result.txt`。其中每个指标都报告：

```text
count
mean
median
P95
P99
min/max
sum
```

`measured-fio-dmesg.log` 只保留最后一个完整的 `MEASURED_FIO_START` 到 `MEASURED_FIO_END` 窗口，预填充、预热和卸载日志不会进入分析。

## 5. 可选的设备内部 Breakdown

如果第一轮确认 `approx_gc_cs_ssd_us` 仍是主要时间项，再构建设备诊断固件，区分：

- 请求 queue/rx/exec/tx；
- Move Plan parse/init/submit/flush/result；
- CSIO pending 与 batch；
- CDMA active/wait；
- worker active/idle。

设备诊断固件应基于正式 mCSGC 固件，仅关闭 production mode 并启用低扰动 Move Plan breakdown。完整 timeline 和高频 busy-poll trace 不应在第一版同时打开。

原始固件目前没有与 Move Plan 完全同构的设备内部 breakdown。原始 CSGC 与 mCSGC 的第一层公平比较应先使用 Host 看到的请求往返时间；如果该时间差异显著，再为原始设备路径补充对应计时。

设备内部 breakdown 应作为独立诊断轮次，不能与四组 Host breakdown 的 fio 吞吐直接混用。诊断目标是获得阶段分布和定位瓶颈；正式 quiet 三次结果仍作为端到端性能结论。
