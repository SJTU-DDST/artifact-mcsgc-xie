# GC 单次耗时与 Breakdown 诊断测试手册

## 1. 测量目的

正式 quiet 实验只用于端到端 fio 带宽比较。该构建关闭了 Host 热路径计时、逐 GC trace 和设备统计，因此不能从已有 quiet 结果中事后还原单次 GC 或 PRE/SSD/POST 时间。

本诊断测试独立于正式性能分支，测量：

- 完整 `f2fs_gc()` 调用时间；
- 真正进入 collector 与没有 collector 工作的调用；
- CSGC section wall-clock；
- 每个 segment 的 PRE、Host 等待 SSD 返回和 POST；
- mCSGC8t PRE 内部的 summary、inode、data page、node page、preallocation 和请求提交阶段；
- data/node ORIGC collector 分类。

诊断构建会增加时间戳读取和 printk，不能用其 fio 带宽替代正式 quiet 结果。它只负责解释正式结果的时间组成。

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
