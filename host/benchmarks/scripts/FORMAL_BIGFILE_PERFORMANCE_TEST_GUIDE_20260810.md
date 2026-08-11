# 单个大文件正式性能测试操作手册

## 1. 测试目标

本轮使用历史上曾测得约 `147 MiB/s -> 301 MiB/s` 的单个大文件负载，在统一的正式性能配置下重新比较：

1. 原始 ORI；
2. 原始 CSGC；
3. 当前优化后的 mCSGC8t no-pipeline + SSD1t。

主要结论以三次有效实验的 fio 写带宽中位数为准。最重要的比较是“当前 mCSGC8t / 原始 CSGC”；ORI 用于给出相对原生 F2FS 的完整端到端基线。

## 2. 固定负载

- F2FS 格式化后，将单个文件 `testbigfile1` 顺序预填充到 namespace 容量的 86%。
- 正式测试前，4 个 fio job 各执行 4 GiB 随机覆盖写，共固定预热 16 GiB。
- 正式 fio 使用 4 个 job 共享同一个预填充文件。
- 块大小为 64 KiB，均匀随机覆盖写，`iodepth=1`，`direct=0`。
- 每个 job 写 20 GiB，总写入量为 80 GiB；不是固定时长测试。
- F2FS 挂载参数仍为 `mode=lfs,background_gc=off,fsync_mode=strict,discard`。
- 正式模式不启用自定义 Host GC epoch、SSD 统计或 pipeline 统计。
- fio 每 5 秒输出一条进度信息，结束时输出完整 normal + JSON 结果。

对应配置文件为：

```text
configs/config25_fio_formal_performance_bigfile_randwrite.sh
```

## 3. 测试前通用要求

1. 不要同时运行其他 fio、filebench、文件系统测试或 OpenSSD 管理操作。
2. 每次批量脚本内部都会重置设备、重新格式化、预填充和预热，不需要手工清理文件系统。
3. 任意一轮失败后，批量脚本立即停止；该轮及其后的结果不能计入三次重复。
4. 切换 Host 配置后必须重新执行 `prepare_formal_host_module.sh`。
5. 切换原始固件与 Move Plan 固件后，必须重新构建全部 Vitis application 并重启 OpenSSD。
6. 设备端固定使用 SSD1t。当前优化版不使用 SSD2t。

## 4. 当前优化版 mCSGC8t

当前 31 服务器已经处于优化版固件分支，因此先完成两种优化版 Host 测试，避免提前切换和重编设备固件。

### 4.1 核对 31 服务器上的 OpenSSD

31 服务器应使用：

```bash
cd /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
git switch exp/formal-mcsgc-quiet-20260809
git pull --ff-only
./scripts/sync_code.sh
```

确认正式性能模式、Move Plan v2、unsafe fast path 均为 1，active worker 为 1。当前 Git 和 Vitis 工作区如果已经同步并且正在运行的就是这套固件，不需要重新编译；否则重新构建全部 Vitis application 并重启 OpenSSD。

### 4.2 先运行 no-pipeline 三次

no-pipeline 是当前主要优化版本：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-mcsgc8t-nopipe-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-nopipeline
sudo ./run_formal_mcsgc8t_nopipeline_bigfile_3x.sh
```

### 4.3 再运行 pipeline 三次

这一步只切换并重编 Host F2FS 模块；OpenSSD 不变：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-mcsgc8t-pipeline-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh mcsgc8t-pipeline
sudo ./run_formal_mcsgc8t_pipeline_bigfile_3x.sh
```

pipeline 作为补充对照，用于确认单个大文件负载下 pipeline 是否仍没有收益。两组优化版必须使用同一套设备固件。

## 5. 原始 ORI 与原始 CSGC

### 5.1 切换 31 服务器上的 OpenSSD

完成两组优化版测试后，在 31 服务器切换到原始固件：

```bash
cd /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
git switch formal-original-csgc-main-20260809
git pull --ff-only
./scripts/sync_code.sh
```

随后重新构建全部 Vitis application，并使用完整的新镜像重启 OpenSSD。跨服务器修改和构建需要在 31 服务器上手工完成。

### 5.2 准备原始 Host 模块

在 52 服务器执行：

```bash
cd /home/xin/work-xie/mcsgc-real/linux-cs
gsw exp/formal-csgc-original-quiet-20260809

cd /home/xin/artifact-csgc/host/benchmarks/scripts
./prepare_formal_host_module.sh original-csgc
```

ORI 与原始 CSGC 使用同一个 Host 分支、同一个 `f2fs.ko` 和同一套原始 OpenSSD 固件，因此这里只需要编译一次 Host 模块。

### 5.3 连续运行三次 ORI

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
sudo ./run_formal_original_ori_bigfile_3x.sh
```

### 5.4 连续运行三次原始 CSGC

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
sudo ./run_formal_original_csgc_bigfile_3x.sh
```

ORI 和 CSGC 两个批次之间不需要重编 Host 或 OpenSSD。每轮测试仍会独立重置 namespace 和重新格式化。

## 6. 结果位置与有效性检查

各模式的结果根目录为：

```text
host/benchmarks/scripts/outputs-ori-ssd1t/
host/benchmarks/scripts/outputs-csgc-original-formal-ssd1t/
host/benchmarks/scripts/outputs-mcsgc8t-nopipeline-formal-csgc-ssd1t/
host/benchmarks/scripts/outputs-mcsgc8t-pipeline-formal-csgc-ssd1t/
```

每轮单个大文件结果位于时间戳目录下：

```text
fio_randwrite_s8_0.86_random/
```

至少检查：

- `fio.log` 存在且 fio error 为 0；
- 总写入量约为 80 GiB；
- 4 个 job 全部完成；
- `terminal.log` 没有 I/O error、NVMe timeout、kernel BUG/Oops 或测试脚本错误；
- 文件系统正常卸载，批量脚本打印该轮完成信息。

正式结论使用每种模式三次有效 fio 带宽的中位数，同时报告平均值、标准差、最小值和最大值。若三次结果离散明显，应先排查异常，不要直接混入中位数。

## 7. 为什么本轮选择 no-pipeline

当前正式小文件三次重复中，pipeline 与 no-pipeline 的端到端吞吐基本相同，no-pipeline 略高且执行路径更简单。历史上接近 2 倍的单大文件结果也来自引入跨 section pipeline 之前。因此，本轮先使用 no-pipeline 作为当前优化版本；如果该结果无法解释，再补做 pipeline，而不在第一轮同时扩大测试矩阵。
