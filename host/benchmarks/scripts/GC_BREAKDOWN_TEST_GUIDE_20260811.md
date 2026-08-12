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

### 1.3 mCSGC8t 的对应口径

mCSGC8t 诊断分支输出与原版相同的 `F2FS_GC_DIAG` 和 `F2FS_GC_COLLECTOR_DIAG`，因此完整 `f2fs_gc()`、victim 选择、顶层 checkpoint、collector、no-collector 调用和 ORIGC fallback 可以直接按同名字段比较。

每个 mCSGC section 输出 `MCSGC_SECTION`，字段 `section_gc_time_us`、`section_sync_us` 和 `collector_us` 与 `CSGC_ORIGINAL_SECTION` 对应。当前 Move Plan 路径不执行 section 前 checkpoint，因此正常情况下 `section_sync_us=0`。

每个成功 segment 继续输出旧兼容记录 `mCSGC8t_STAT without wait`，并新增：

```text
MCSGC_SEGMENT_PRE_DETAIL
MCSGC_SEGMENT_MOVE_DETAIL
MCSGC_SEGMENT_SSD_DETAIL
MCSGC_SEGMENT_POST_DETAIL
MCSGC_SEGMENT_RELEASE_DETAIL
```

`MCSGC_SEGMENT_PRE_DETAIL` 和 `MCSGC_SEGMENT_MOVE_DETAIL` 通过 `segno + start_ns` 配对，将 PRE 拆为 valid-offset 构建、summary、node list、inode/data page 锁、dirty-source 扫描、`cp_rwsem`、node page、有效块重读、数据有效性检查、Move Plan 构建、目标块预分配和 Move Plan finalize。PRE retry 仍按“全部 PRE wall-clock、最终成功尝试、失败尝试和 retry gap”分别统计。

`MCSGC_SEGMENT_SSD_DETAIL` 使用与原版相同的 trigger、inter-submit 和 completion-wait 三段记录 Host 可见的 SSD 请求生命周期。拆成三条日志是为了避免单条 printk 过长被内核截断；解析器会自动重新组合，并通过 `incomplete_mcsgc_pre_details` 报告缺失配对。

`MCSGC_SEGMENT_POST_DETAIL` 拆分设备结果状态、设备结果校验、本地提交校验、cache invalidation、summary 提交、dnode 提交、成功记账、错误回滚和操作锁释放。`MCSGC_SEGMENT_RELEASE_DETAIL` 单独记录 mCSGC 在 segment 完成记录之后执行的 data-page 释放和本地缓存清理。

分析结果中的 `comparable_*` 是跨版本公共口径：

- 原版 PRE 起始到 summary 完成，对应 mCSGC valid-offset 构建加 summary 读取，统一为 `comparable_pre_sum_us`；
- 原版 node/SIT pack 对应 mCSGC Move Plan prepare/finalize，统一为 `comparable_pre_request_metadata_us`；
- 原版数据重验证对应 mCSGC 的 valid-block 重读加有效性检查，统一为 `comparable_pre_data_revalidate_us`；
- 两边的目标块预分配和 Host 看到的 SSD 三阶段直接同名对应；
- POST 使用结果校验、segment metadata、dnode、unlock、data-page 释放和 cleanup 的公共语义；mCSGC 延后执行的 data-page release 会由解析器按 segment 加回 `comparable_post_update_meta_us`，其余 release cleanup 加回 `comparable_post_cleanup_us`，二者之和为 `comparable_post_total_work_us`；
- mCSGC 独有阶段仍保留在 `modern_detail_*`、`modern_post_detail_*` 和 `modern_release_*` 中，不会因公共字段合并而丢失。

只有 `ret=0` 的 mCSGC POST 会进入成功路径的阶段分布。失败 POST 使用 `modern_post_failures` 和 `modern_post_failure_rollback_us` 单独报告，避免错误恢复时间污染正常 GC 的均值。

由于 mCSGC 的 8 个 segment 会并发执行，逐 segment 时间是各请求自身的 wall-clock，不能相加后当作 section wall-clock。section 级加速必须比较 `comparable_section_section_gc_time_us`；segment 细分用于解释 section 时间为何变化。

## 2. 诊断分支

```text
原始 ORI/CSGC:
  exp/diagnostic-original-gc-breakdown-20260811

mCSGC8t no-pipeline:
  exp/diagnostic-mcsgc8t-nopipe-breakdown-20260811

mCSGC8t pipeline:
  exp/diagnostic-mcsgc8t-pipeline-breakdown-20260812
```

两个分支分别从对应正式 quiet 分支建立，不修改正式性能分支。

## 3. 本轮六组测试

本轮固定比较原始 CSGC、mCSGC8t no-pipeline + SSD1t 和 mCSGC8t
pipeline + SSD1t，分别使用正式性能实验中的大文件和小文件负载。每组运行一次即可；
单次运行已经包含数千个 GC 样本，重复三次主要影响 fio 性能置信区间，对阶段耗时分布的
新增价值较低。

| 组别 | Host | OpenSSD | 负载 |
|---|---|---|---|
| 1 | 原始 CSGC 诊断分支 | 原始 CSGC 固件 | bigfile |
| 2 | 原始 CSGC 诊断分支 | 原始 CSGC 固件 | smallfile |
| 3 | mCSGC8t no-pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | bigfile |
| 4 | mCSGC8t no-pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | smallfile |
| 5 | mCSGC8t pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | bigfile |
| 6 | mCSGC8t pipeline 诊断分支 | Move Plan unsafe fast path，SSD1t | smallfile |

### 3.1 原始 CSGC：大文件与小文件

当前核查状态（2026-08-12）：

- 31 服务器的 OpenSSD 源码位于 `formal-original-csgc-main-20260809`，当前提交为
  `463e8b0b13ad345ed99c2176b1f81ad34d3c986a`；
- 四个 Vitis application 的配置和共享头文件一致；
- 设备使用 legacy 原始 CSGC 协议，实际 worker 模式为 `ssd1t`；
- 因此，只要 OpenSSD 当前运行的仍是最近由这份源码编译、启动的固件，本轮无需修改或重新编译设备端代码；
- Host 诊断分支已经位于
  `/tmp/linux-cs-diagnostic-original-breakdown-v2`，无需在主工作树中手工执行 `git switch` 或 `gsw`。

正式原始 CSGC 实验使用的设备分支为：

```text
formal-original-csgc-main-20260809
```

先在 Host 上编译并安装一次诊断模块。外层不要加 `sudo`，脚本只会在调用
`build_f2fs.sh` 时自行使用 `sudo`：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_gc_breakdown_host_module.sh original
```

编译成功后，依次运行原始 CSGC 的大文件和小文件测试：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
sudo ./run_gc_breakdown_diagnostic.sh original-csgc bigfile
sudo ./run_gc_breakdown_diagnostic.sh original-csgc smallfile
```

两个负载共用同一 Host 模块和设备固件，中间不需要重新编译。

如需顺便测量原版 ORI 的顶层 `f2fs_gc()`、victim、checkpoint 和 ordinary collector breakdown，可在同一模块与固件下执行：

```bash
sudo ./run_gc_breakdown_diagnostic.sh original-ori bigfile
sudo ./run_gc_breakdown_diagnostic.sh original-ori smallfile
```

每条测试命令都会重新格式化并覆盖 `/dev/nvme0n1`，必须串行执行。运行脚本会自行：

1. 校验 Host 分支、提交和已加载模块；
2. 只读校验 31 服务器 Vitis 工作区的协议和 worker 配置；
3. 采集完整外部 dmesg；
4. 执行对应正式负载；
5. 裁剪正式 fio 测量窗口；
6. 调用 Python 分析器生成 `gc-breakdown-diagnostic-result.txt`。

不需要另外运行 `old-mydmesg.sh` 或手工调用 Python 脚本。诊断构建带有结构化计时和
printk，只用于分析阶段构成；端到端带宽仍以 quiet 正式实验结果为准。

也可以使用一条命令严格按照“CSGC 大文件、CSGC 小文件、ORI 大文件、ORI 小文件”的
顺序执行全部四组：

```bash
sudo ./run_gc_breakdown_original_matrix.sh
```

批处理脚本遇到任意一组失败后会立即停止。每一组仍在自己的结果目录中保存完整的
`external-dmesg.log`；此外，批次目录
`outputs-gc-breakdown-original-matrix/<timestamp>/` 会提供四个名称明确的
`*-kernel.log` 硬链接以及汇总路径文件 `results.txt`。因此不需要另行启动内核日志
采集脚本。

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

这些测试首先测 Host breakdown，不要求设备固件启用高频 breakdown。
`approx_gc_cs_ssd_us` 给出 Host 从提交请求到收到结果的时间；它包含设备排队、设备执行
和返回延迟，不能单独解释为盘内搬运时间。

### 3.3 mCSGC8t pipeline：大文件与小文件

pipeline 诊断使用与 no-pipeline 相同的 Move Plan unsafe fast path 和 SSD1t
设备固件，只改变 Host 外层跨 section 调度策略。设备端仍使用：

```text
exp/formal-mcsgc-quiet-20260809
```

Host 诊断分支已经位于独立 worktree，准备脚本会自动定位，无需手工切换主工作树：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_gc_breakdown_host_module.sh mcsgc8t-pipeline
sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-pipeline bigfile
sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-pipeline smallfile
```

除 no-pipeline 已有的顶层 `f2fs_gc()`、section、segment PRE/SSD/POST 口径外，
pipeline 分支额外输出 `MCSGC_PIPELINE`，统计：

- 每个外层批次实际启动 1 个还是 2 个 section；
- pipeline wall-clock 与两个 section 生命周期之和；
- section 生命周期的严格并集、完整跨度、重叠时间、两段之间的空档和启动间隔；
- pipeline 外层调度前后控制开销；
- 第二个 victim 的选择时间和返回结果；
- section 并行度及计时完整性。

分析结果在 `gc-breakdown-diagnostic-result.txt` 的
`cross-section pipeline` 分组中。`pipeline_effective_parallelism_milli` 和
`pipeline_section_parallelism_milli` 以千分之一为单位，例如 `1500` 表示
`1.5x`；`pipeline_overlap_fraction_permille=500` 表示 section 生命周期并集的
50% 同时有两个 section 活跃。`pipeline_dual_batch_fraction_permille` 表示成功启动两个
section 的外层批次比例，`pipeline_net_saved_us` 为两个 section 串行耗时之和减去实际
pipeline wall-clock，正值才表示本批 pipeline 获得了净 wall-clock 收益。

## 4. 输出文件

每次运行的结果目录会额外生成：

```text
external-dmesg.log
measured-fio-dmesg.log
gc-breakdown-diagnostic-result.txt
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

mCSGC 诊断构建只启用结构化 breakdown，不额外启用逐事件 GC-heavy trace。这样原版与 mCSGC 的 printk 数量更接近，避免为了生成高频 timeline 而额外改变并发时序。正式 fio 带宽仍必须使用 quiet 分支的结果，不能使用本诊断轮次替代。

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
