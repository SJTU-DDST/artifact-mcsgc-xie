# Euro-Par 负载下的 mCSGC 候选版本复现实验

## 实验矩阵

本实验复用作者 artifact `main@0271b907ec00ed643fd139403b726817c9fe8c32`
中的原始 workload 和配置，不增加后来 GC-heavy 实验使用的额外预热。

测试两个候选 Host 版本：

- Conflict-aware：`exp/formal-mcsgc8t-conflict-aware-lifecycle-quiet-20260825@9f432d2fa2a4a665f99e55562b903a74008da873`
- Rolling-final：`exp/formal-mcsgc8t-rolling-lifecycle-quiet-20260825@e94392029fbdabca386b0b2be3300be84ea90324`

每个候选运行 22 个 case，包括：

- Filebench：period、fileserver、varmail，共 3 个；
- YCSB：workload A 和 F，共 2 个；
- fio overall：uniform 和 Zipf 1.1，共 2 个；
- fio 存储利用率：60%、70%、80%、90%、95%，共 5 个；
- fio section size：1、2、4、8、16 segments，共 5 个；
- fio 写倾斜度：uniform、Zipf 0.3、0.7、0.9、1.1，共 5 个。

两个候选合计 44 轮。相邻 case 交替候选执行，减少长时间实验中的时间漂移。
预计总耗时约 6 至 7 小时，外加设备端切换和编译时间。

## 1. 准备 OpenSSD

在 31 服务器执行：

```bash
cd /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc
git fetch --prune origin
git switch exp/formal-mcsgc-quiet-20260809
git pull --ff-only
git rev-parse HEAD
./scripts/sync_code.sh
```

`git rev-parse HEAD` 必须输出：

```text
52831c159c9f7a73f9670c163a6b513750f64b47
```

随后按照现有 Vitis 流程整体重新编译并启动四个 application。设备配置固定为
SSD1t；实验脚本会在每轮中设置 `L2P=2`、`NAND latency=0` 和 `DSM=1`。

Host 端只能校验 31 服务器源码提交和 Vitis 输入文件哈希，不能证明当前运行
ELF 与源码逐字节对应。因此必须确认 OpenSSD 确实由本次新编译的 ELF 启动。

## 2. 非破坏性预检

在 52 服务器执行：

```bash
cd /home/xin/artifact-csgc-europar25-mcsgc-candidates/host/benchmarks/scripts
./run_europar25_mcsgc_candidate_matrix.sh --preflight
```

该命令不格式化 SSD。它检查设备未挂载、没有残留 benchmark/MySQL 进程、
OpenSSD 分支和 commit、Host 候选提交、原始 workload 完整性以及 YCSB/MySQL
环境。只有输出 `Preflight checks passed` 才开始正式实验。

## 3. 一键运行 44 轮实验

脚本会自动创建或复用 Host worktree，构建两个候选模块，在每轮前加载正确
模块，并记录 Host/OpenSSD/artifact 版本、模块哈希、Vitis 输入哈希和日志。
不需要手工切换 Host 分支，也不需要手工执行 `build_f2fs.sh`。

建议在 tmux 中执行：

```bash
tmux new-session -s europar25-mcsgc-candidates \
  'cd /home/xin/artifact-csgc-europar25-mcsgc-candidates/host/benchmarks/scripts && ./run_europar25_mcsgc_candidate_matrix.sh'
```

该命令会在无交互确认的情况下格式化并覆盖 `/dev/nvme0n1` 44 次。

从 tmux 分离使用 `Ctrl-b d`；重新查看使用：

```bash
tmux attach -t europar25-mcsgc-candidates
```

## 4. 查看状态和断点恢复

最新批次路径记录在：

```text
/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-mcsgc-candidate-reproduction/latest-batch.txt
```

只读查看进度：

```bash
SCRIPT=/home/xin/artifact-csgc-europar25-mcsgc-candidates/host/benchmarks/scripts/run_europar25_mcsgc_candidate_matrix.sh
BATCH=$(cat /home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-mcsgc-candidate-reproduction/latest-batch.txt)
"${SCRIPT}" --status "${BATCH}"
```

普通用户态错误修复后可以跳过已经成功的 case 并继续：

```bash
"${SCRIPT}" --resume "${BATCH}"
```

若日志中出现内核 Oops、NVMe timeout、SIT 不一致或引用异常，不要在故障内核上
继续；先保存现场并重启，再判断是否适合恢复原批次。

## 5. 自动分析输出

44 轮全部成功后，外层脚本自动调用：

```text
analyze_europar25_mcsgc_candidate_matrix.py
```

默认使用以下生命周期修复版原始 CSGC/ORI 批次作为基线：

```text
/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-europar25-original-reproduction/20260828_185514
```

最终在候选批次的 `analysis/` 中生成：

- `comparison.csv`：22 个同名 case 的四系统吞吐、WAF 和倍率；
- `combined-results.json`：完整结构化数据；
- `mcsgc-candidate-comparison.md`：中文结果报告；
- `figures/figure4_*.{pdf,png}` 至 `figure8_*.{pdf,png}`：四系统扩展图。

如需更换原始系统基线，可在启动前设置：

```bash
EUROPAR_BASELINE_BATCH=/absolute/baseline/batch \
  ./run_europar25_mcsgc_candidate_matrix.sh
```
