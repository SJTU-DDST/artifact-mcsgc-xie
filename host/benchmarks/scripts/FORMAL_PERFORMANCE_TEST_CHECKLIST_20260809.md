# F2FS/CSGC 正式性能对比测试执行清单

## 测试目标

在编译阶段彻底关闭 Host 和 OpenSSD 上可选的调试、跟踪、计时及汇总诊断计算，测量 fio 端到端写吞吐。主要测试以下三种配置：

1. 原始 F2FS（`ori`）配合原始 OpenSSD 固件路径。
2. 原始 CSGC，使用与 ORI 相同的原始 Host 和 OpenSSD 构建版本。
3. 当前优化后的 mCSGC8t pipeline，配合当前 Move Plan 固件，并启用 1 个设备端 worker。

另外运行一次优化版 no-pipeline 构建作为选择性检查。在两个优化版中，保留 fio 吞吐更高的一版，作为最终对比中的第三种配置。

## 固定测试负载

- 86% 分区式预填充：共 26,336 个文件，每个文件 2 MiB，划分为 16 个互不重叠的 job 文件池。
- 固定预热：每个 job 执行一轮 1 GiB 随机写。
- 正式负载：16 个 job，4 KiB 均匀随机覆盖写，`iodepth=16`，buffered I/O，运行 300 秒。
- F2FS 挂载参数：`mode=lfs,background_gc=off,fsync_mode=strict,discard`。
- 主要结果指标：fio JSON 中以 MiB/s 为单位的写带宽。

正式性能模式有意不读取自定义 GC sysfs 计数器，不重置或读取设备统计，不启动 Host 测量 epoch，也不要求 pipeline 统计。fio 每 5 秒输出一条轻量进度信息，包含当前带宽、IOPS、完成比例和预计剩余时间，便于观察吞吐变化并及时发现停滞；测试结束时仍输出完整 JSON 结果。fio 前后只保留两个内核日志标记。

## 重复次数与首轮验证

- 每种配置至少完成 3 次相互独立的有效测试；每次测试都重新重置设备、格式化文件系统、预填充并预热。
- 不额外设置缩短时长的冒烟测试。每种配置的第一次完整 300 秒测试同时承担配置验证作用；如果正常完成且结果有效，可以计入 3 次正式重复。
- 如果第一次测试出现分支或固件不匹配、fio 错误、运行时间明显不足 300 秒、I/O error、NVMe timeout、内核错误或无法正常收尾，则该次结果作废，修复后重新开始正式重复。
- 汇报时使用 3 次有效结果的中位数，并同时保留平均值、标准差、最小值和最大值。若变异系数超过 3%–5%，应增加到 5 次。

## 静默测试分支

| 用途 | 分支 | 本地代码路径 |
|---|---|---|
| ORI 和 CSGC 使用的原始 Host | `exp/formal-csgc-original-quiet-20260809` | 由脚本根据当前 Git worktree 自动发现 |
| 优化版 Host pipeline | `exp/formal-mcsgc8t-pipeline-quiet-20260809` | 由脚本根据当前 Git worktree 自动发现 |
| 优化版 Host no-pipeline | `exp/formal-mcsgc8t-nopipe-quiet-20260809` | 由脚本根据当前 Git worktree 自动发现 |
| 原始 OpenSSD | `exp/formal-csgc-original-quiet-20260809` | 31 服务器上的 `/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc` |
| 当前 OpenSSD，SSD1t | `exp/formal-mcsgc-quiet-20260809` | 31 服务器上的 `/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc` |

Host 的自定义运行时日志宏和运行时 breakdown 宏均已关闭。关闭后者还会一并移除诊断用时钟读取、原子计数、自增累加、锁操作以及额外的 `get_valid_blocks()` 调用。标准 Kconfig 选项 `CONFIG_F2FS_STAT_FS=y` 在全部 Host 配置中统一保留，避免不同模式使用不同的标准 F2FS 编译配置。OpenSSD 的正式性能模式同样会强制关闭设备时间线、Move Plan breakdown、可选运行时统计，以及仅供 `GET_SSD_LOG` 使用的流量/WAF 计数。

## 构建并测试原始 ORI/CSGC

在 Host 上执行：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-csgc-original-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh original-csgc
```

如果没有安装 `gsw`，可使用 `git switch exp/formal-csgc-original-quiet-20260809`；脚本会从当前 Git worktree 自动找到目标分支，不再依赖 `/tmp` 下的固定目录。

在 31 服务器上切换到 `exp/formal-csgc-original-quiet-20260809`，运行已有的 `scripts/sync_code.sh`，重新构建全部 Vitis application，并使用这套完整镜像重启 OpenSSD。同步后的四个 Vitis 项目的 `config.h` 都必须显示：

```text
CONFIG_OPENSSD_PRODUCTION_PERFORMANCE=1
CONFIG_CSGC_ACTIVE_WORKERS=1
```

然后在 Host 上执行以下批量脚本。它按 `ORI -> 原始 CSGC` 的顺序运行三轮，共完成 6 次测试；任一测试失败时会立即停止，不会继续运行后续测试：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
sudo ./run_formal_original_comparison_3x.sh
```

六次测试之间不要重新构建或重启 Host、OpenSSD。批量脚本调用的测试脚本会在每次测试开始时分别重置并重新格式化 namespace，因此每次测试都具有独立的文件系统初始状态。

## 构建并测试优化版 mCSGC8t

在 31 服务器上切换到 `exp/formal-mcsgc-quiet-20260809`，完成代码同步，重新构建全部 Vitis application，并重启 OpenSSD。确认正式性能模式为 1、Move Plan unsafe fast 模式为 1、active workers 为 1。

测试 pipeline Host：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-mcsgc8t-pipeline-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-pipeline
sudo ./run_formal_performance_test.sh mcsgc8t-pipeline
```

测试 no-pipeline Host，作为优化版本选择检查：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-mcsgc8t-nopipe-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-nopipeline
sudo ./run_formal_performance_test.sh mcsgc8t-nopipeline
```

封装脚本会验证 Host 分支，记录 Host commit 和模块 SHA-256，要求使用对应 worktree 构建出的模块，并要求 31 服务器的 Vitis workspace 报告 SSD1t 配置。

## 汇总测试结果

找到各次测试生成的 `fio.log` 后，执行：

```bash
python3 ./summarize_formal_fio.py \
  --baseline original-csgc \
  original-ori=/absolute/path/to/original-ori/fio.log \
  original-csgc=/absolute/path/to/original-csgc/fio.log \
  optimized=/absolute/path/to/selected-optimized/fio.log
```

脚本会输出 MiB/s、IOPS、写入 GiB、运行时间、fio 错误数，以及相对原始 CSGC 的加速比。出现以下任一情况时，正式测试结果无效：`errors` 非零；实际运行时间明显短于 300 秒；所使用的 Host 或设备端代码来源与本清单不一致。
