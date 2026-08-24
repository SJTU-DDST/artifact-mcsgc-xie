# 原版 CSGC 与当前最佳版本复现实验手册

## 1. 目标与实验口径

本流程只运行四轮：

1. 原版 CSGC，大文件负载，一次；
2. 原版 CSGC，小文件负载，一次；
3. 当前最佳 mCSGC8t，大文件负载，一次；
4. 当前最佳 mCSGC8t，小文件负载，一次。

每轮都会重置、重新格式化并覆盖 `/dev/nvme0n1`，盘上原有文件全部丢失。

这里的“复现当前已报告性能”采用历史数字对应的原始配置：

- 原版 CSGC 使用 quiet 正式构建。历史三次中位数为大文件 `213.712 MiB/s`、小文件 `276.110 MiB/s`。
- 当前最佳使用产生当前推荐结果的 conflict-aware diagnostic 构建。历史单次结果为大文件 `423.723 MiB/s`、小文件 `426.853 MiB/s`。

两者不是同等诊断开销的严格因果 A/B。该流程用于复现当前汇报采用的两条性能线；一次实验只应接近历史结果，不要求逐位相同。若以后需要严格 A/B，应让原版和新版使用相同的 quiet 或 diagnostic 计时代码，并各重复至少三次。

## 2. 固定代码版本

### 2.1 原版 CSGC

Host：

```text
repository: /home/xin/work-xie/mcsgc-real/linux-cs
branch:     exp/formal-csgc-original-quiet-20260809
commit:     813c35f3ec81bc317c2ca82d796e9a767ad6384e
```

OpenSSD（31 服务器）：

```text
repository: /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
branch:     formal-original-csgc-main-20260809
commit:     463e8b0b13ad345ed99c2176b1f81ad34d3c986a
```

该 OpenSSD commit 以原始 `main@8dcab7d2` 为功能基线，只增加了当前 Vitis 同步脚本兼容和启动提示 padding，不包含 Move Plan。

### 2.2 当前最佳统一版本

Host：

```text
repository: /home/xin/work-xie/mcsgc-real/linux-cs
branch:     exp/diagnostic-mcsgc8t-conflict-aware-supply-20260819
commit:     62f0a68a891bf39e14398e5d08a083ee79fe73fe
```

该版本包含 Move Plan、mCSGC8t、批量 dnode、unsafe prefree reclaim 和 conflict-aware supply。它是当前统一推荐版本；大文件的 rolling 单次峰值不作为已验证的统一最佳设计。

OpenSSD（31 服务器）：

```text
repository: /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
branch:     exp/formal-mcsgc-quiet-20260809
commit:     52831c159c9f7a73f9670c163a6b513750f64b47
mode:       SSD1t, Move Plan v2 unsafe fast path
```

## 3. 测试负载

两种负载都使用 86% 预填充、固定 16 GiB 随机覆盖预热、`8 segments/section`、`background_gc=off` 和 buffered I/O。

### 大文件

- 单个约 `51.44 GiB` 文件；
- 4 个 fio job 共享该文件；
- 每个 job 写 `20 GiB`，合计固定写入 `80 GiB`；
- `64 KiB` 均匀随机覆盖写，`iodepth=1`，`direct=0`。

### 小文件

- 26,336 个 `2 MiB` 文件，总计约 `51.44 GiB`；
- 16 个 job，各自使用互不重叠的 1,646 个文件；
- `4 KiB` 均匀随机覆盖写，`iodepth=16`，`direct=0`；
- 正式窗口固定运行 300 秒。

## 4. 第一次手工操作：准备原版 OpenSSD

在 31 服务器执行：

```bash
ssh xin@192.168.98.31
cd /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
git fetch --prune origin
git switch formal-original-csgc-main-20260809
git pull --ff-only
git status --short
git rev-parse HEAD
```

必须确认：

```text
git status --short  没有 tracked 修改
HEAD = 463e8b0b13ad345ed99c2176b1f81ad34d3c986a
```

随后同步代码：

```bash
./scripts/sync_code.sh
```

在 Vitis 中完整重新编译并启动四个 application：

```text
ftl
cs_leader
cs_worker1
cs_worker2（当前 io_worker/emu 工程）
```

确认 OpenSSD 正常启动、52 服务器能够看到 `/dev/nvme0n1`。如果重新加载固件后 NVMe namespace 没有正常恢复，重启 52 服务器。

## 5. 第二次手工操作：运行原版两轮测试

在 52 服务器使用普通用户进入脚本目录，建议在 tmux 中运行：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
tmux new -s csgc-reproduce-original
./run_best_performance_reproduction.sh original
```

不要在最外层加 `sudo`。脚本会自行获取 sudo，并要求输入：

```text
DESTROY /dev/nvme0n1
```

脚本自动执行：

1. 验证 31 服务器 OpenSSD 仓库的 branch、commit 和 tracked-clean 状态；
2. 在 `/tmp/linux-cs-reproduce-original-csgc-20260824` 建立或复用独立 Host worktree；
3. 固定 Host 到 `813c35f3...`，生成内核头文件并调用：

   ```bash
   ./prepare_formal_host_module.sh original-csgc
   ```

4. 依次调用：

   ```bash
   sudo ./run_formal_performance_test.sh original-csgc bigfile
   sudo ./run_formal_performance_test.sh original-csgc smallfile
   ```

5. 每轮由更内层的 `test.sh -> run_fio.sh` 完成 SSD 配置重置、mkfs、挂载、86% 预填充、16 GiB 预热和正式 fio；
6. 自动保存 `terminal.log`、`fio.log`、`dmesg.old`、`dmesg.precondition.log`、`dmesg.log`、SSD/Host 状态文件、代码版本和模块哈希。

原版阶段完成后，脚本会保留 batch 状态，允许重启后继续。

## 6. 第三次手工操作：切换到当前最佳 OpenSSD

在 31 服务器执行：

```bash
cd /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
git fetch --prune origin
git switch exp/formal-mcsgc-quiet-20260809
git pull --ff-only
git status --short
git rev-parse HEAD
./scripts/sync_code.sh
```

必须确认：

```text
git status --short  没有 tracked 修改
HEAD = 52831c159c9f7a73f9670c163a6b513750f64b47
```

再次在 Vitis 中完整编译并启动 `ftl`、`cs_leader`、`cs_worker1` 和 `cs_worker2`。确认配置为 SSD1t，且 OpenSSD 正常启动。必要时重启 52 服务器。

## 7. 第四次手工操作：运行当前最佳两轮测试

在 52 服务器执行：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
tmux new -s csgc-reproduce-best
./run_best_performance_reproduction.sh best
```

脚本会自动找到上一步未完成的 batch，然后：

1. 验证 31 服务器已切换到指定最佳 OpenSSD branch 和 commit；
2. 在 `/tmp/linux-cs-reproduce-best-mcsgc8t-20260824` 建立或复用 Host worktree；
3. 固定 Host 到 `62f0a68a...`，并调用：

   ```bash
   ./prepare_gc_breakdown_host_module.sh mcsgc8t-conflict-aware-supply
   ```

4. 依次调用：

   ```bash
   sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-conflict-aware-supply bigfile
   sudo ./run_gc_breakdown_diagnostic.sh mcsgc8t-conflict-aware-supply smallfile
   ```

5. 除 fio、terminal 和普通 dmesg 外，还自动运行 `dmesg --follow`，生成 `external-dmesg.log`、marker 窗口日志和 GC breakdown 汇总；
6. 最后生成四轮结果的 `comparison.tsv` 和 `summary.md`。

## 8. 日志与结果位置

查看当前进度：

```bash
./run_best_performance_reproduction.sh status
```

总 batch 位于：

```text
/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-best-performance-reproduction/<时间戳>/
```

其中包括：

```text
manifest.txt                    所有 Host/OpenSSD/Artifact 版本及模块哈希
original-phase-console.log      原版阶段完整控制台日志
best-phase-console.log          最佳版本阶段完整控制台日志
original-csgc-bigfile -> ...    指向原始结果目录的稳定链接
original-csgc-smallfile -> ...
best-conflict-aware-bigfile -> ...
best-conflict-aware-smallfile -> ...
*.metrics                       从 fio JSON 提取的带宽和错误状态
comparison.tsv                  四轮机器可读对比
summary.md                      四轮简表与倍率
```

每个链接指向的原始实验目录保留完整 `fio.log`、`terminal.log` 和内核日志。当前最佳两轮还包含完整 GC breakdown。

## 9. 重要限制

1. 脚本能验证 31 服务器源码仓库和 Vitis 工作区配置，但 Git commit 无法证明当前 FPGA 上正在运行的二进制确实由该源码编译；固件完整重编译和启动仍由操作者负责。
2. 当前最佳版本主动牺牲部分一致性与崩溃恢复保证。每轮实验都会重新格式化；异常中止后不要继续使用旧文件系统。
3. 单次测试存在波动。四轮流程适合快速复现当前水平，但不能替代三次以上重复实验及同开销 A/B。
