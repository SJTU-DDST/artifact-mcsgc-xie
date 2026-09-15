# Euro-Par 负载下的 mCSGC NOWAIT quiet 复现实验

## 实验目标

本实验使用已经通过 Filebench 正确性和性能验收的 NOWAIT 功能版本，关闭与功能
无关的 Host 诊断统计、计时和输出，并在原论文 artifact 的 22 个实验点上重复三次。
原始 ORI 和原始 CSGC 复用已有的同负载基线，不在本脚本中重新运行。

固定版本如下：

- Host：`exp/formal-mcsgc8t-nowait-quiet-20260916@dec1964f0bf196b1929799119e93393bfa6b79fb`
- Host 功能基线：`exp/fix-mcsgc8t-terminal-curseg-rollover-20260915@a84e5bc532e85ef35f3e8db48585f7d2412e73bf`
- OpenSSD：`exp/formal-mcsgc-quiet-20260809@52831c159c9f7a73f9670c163a6b513750f64b47`
- 原始 workload：作者 artifact `main@0271b907ec00ed643fd139403b726817c9fe8c32`
- ORI/原始 CSGC 基线：`outputs-europar25-original-reproduction/20260828_185514`

Host quiet 版本编译期固定启用 NOWAIT，不再保留运行时 A/B 参数；现有有效功能
仍包括 unsafe prefree reclaim、conflict-aware continuous supply、批量 dnode 提交和
近期 Filebench 生命周期修复。OpenSSD 继续使用 SSD1t；脚本逐轮设置 L2P=2、
NAND latency=0 和 DSM=1，不修改盘上代码。

## 实验矩阵

每次重复包含 22 个 case：

- fio overall：uniform、Zipf 1.1，共 2 个；
- fio 存储利用率：60%、70%、80%、90%、95%，共 5 个；
- fio section size：1、2、4、8、16 segments，共 5 个；
- fio 写倾斜度：uniform、Zipf 0.3、0.7、0.9、1.1，共 5 个；
- YCSB：workload A、F，共 2 个；
- Filebench：period、fileserver、varmail，共 3 个。

脚本按“完整矩阵优先”执行：先完成第一组 22 点，再运行第二组和第三组。每组内
先运行历史上较稳定的 fio/YCSB，最后运行曾暴露生命周期问题的 Filebench。
总计 66 轮，每轮都会重新格式化并覆盖 `/dev/nvme0n1`。

## 运行前条件

31 服务器必须已经使用上述 OpenSSD commit 同步、整体编译并启动四个 Vitis
application。Host 只能校验 31 服务器源码版本和 Vitis 输入哈希，无法从运行时证明
ELF 与源码逐字节一致。

52 服务器不需要手工切换 Host 分支，也不需要手工运行 `build_f2fs.sh`。外层脚本会：

1. 检查设备未挂载、无残留 benchmark/MySQL 进程、依赖和代码版本；
2. 创建或复用精确 Host worktree，编译并加载固定模块；
3. 逐 case 生成原论文配置，重新 mkfs，并运行 workload；
4. 保存完整 workload、dmesg、SSD、版本和模块哈希记录；
5. 每轮卸载后先检查原始 checkpoint，再运行离线 `fsck.f2fs`；
6. 66 轮完成后自动生成三系统表格、JSON、报告和 Figure 4 至 Figure 8 扩展图。

## 单命令运行

在 52 服务器以普通用户执行，不要在外层加 `sudo`：

```bash
tmux new-session -d -s europar25-nowait-quiet-3x \
  "cd /home/xin/artifact-csgc-europar25-nowait-20260916/host/benchmarks/scripts && exec ./run_europar25_mcsgc_nowait_matrix_3x.sh"
```

脚本内部使用无交互 `sudo`，启动后不会要求确认。预计约 8 小时完成；考虑
Filebench teardown 波动，建议预留 9 至 10 小时。

查看 tmux 输出：

```bash
tmux attach -t europar25-nowait-quiet-3x
```

使用 `Ctrl-b d` 可从 tmux 分离而不中止实验。

## 状态与恢复

最新批次路径保存在：

```text
/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-mcsgc-nowait-reproduction/latest-batch.txt
```

只读查看进度：

```bash
SCRIPT=/home/xin/artifact-csgc-europar25-nowait-20260916/host/benchmarks/scripts/run_europar25_mcsgc_nowait_matrix_3x.sh
BATCH=$(cat /home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-mcsgc-nowait-reproduction/latest-batch.txt)
"${SCRIPT}" --status "${BATCH}"
```

仅在确认没有内核、设备或文件系统异常时，才可断点恢复：

```bash
"${SCRIPT}" --resume "${BATCH}"
```

脚本只跳过同时具备成功结果和完整验证标记的 case。若出现 Oops、SIT 不一致、
NVMe timeout、hung task 或 fsck/checkpoint 失败，脚本会停止并保留现场，不会自动重启。

## 输出

每个批次目录包含：

- `case-results.tsv`、`schedule.tsv`、`runner.log`；
- `provenance.txt`、`runtime-modes.tsv`；
- `checkpoint-results.tsv`、`fsck-results.tsv`、`validated/*.ok`；
- `raw/nowait-quiet/` 下的完整逐轮原始数据；
- `analysis/comparison.csv`：22 点的三次值、均值、中位数、极值、标准差和倍率；
- `analysis/combined-results.json`：结构化基线、聚合值和三次原始值；
- `analysis/mcsgc-nowait-quiet-comparison.md`：自动汇总报告；
- `analysis/figures/figure4_*` 至 `figure8_*`：PDF 和 PNG 图。

候选固件未导出 physical WAF，quiet Host 也不采集平均 block migration latency；
分析器会明确标记这两项未采集，不把占位 0 当作实验数据。
