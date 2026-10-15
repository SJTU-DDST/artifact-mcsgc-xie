# IPLFS 期刊实验：准备与运行指南

更新日期：2026-10-16，Asia/Shanghai。**本阶段只完成代码、构建和离线检查，没有运行 IPLFS 实验。**

## 1. 准备结果与当前阻塞项

- 独立工作树：`/home/xin/artifact-csgc-iplfs-paper-20261015`。
- 分支：`exp/taco-iplfs-evaluation-preparation-20261015`。
- 实验资产基线：`c9823728e67c8869644b2bba083790783a3469d2`；原作者发布源：`0271b907ec00ed643fd139403b726817c9fe8c32`。
- 入口：[run_taco_iplfs_matrix.sh](run_taco_iplfs_matrix.sh)，实际调用 [matrix.py](iplfs_paper/matrix.py)。不调用旧 `test.sh`、`common.sh` 或全局工具安装脚本。
- 已编译独立 IPLFS mkfs、非交互只读 fsck、file_writer 和整页 DSM 辅助工具。fio、Filebench、nvme-cli 等冻结到私有工具目录并记录 SHA-256；Java 依赖安装布局，原位固定其真实路径与哈希。
- 已完整编译 `5.11.0-iplfs-paper-20261016` 内核及模块，**没有安装到 `/boot` 或 `/lib/modules`，没有更新 GRUB**。
- 已安装 `5.11.0-iplfs` 的配置与 artifact 源配置完全一致；F2FS 模块的源码校验标识与本次重建一致：`8C2555111C23C81D00A7C38`。已有镜像、initrd 和 GRUB 菜单可供选择。旧整个内核的原始构建过程不可追溯，不能把这个核对说成整个 ELF 与新构建逐字节相同。
- 构建、工具哈希、配置、测试证据在工作树根目录 `iplfs-paper-preparation/`，该目录已忽略；旧构建和测试文件均保留。

**目前不能开始实验。** 52 仍运行 `6.1.54-csgcmt-csgcmt`；ASLR 当前为 2，历史实验为 0；缺少 31 的固件启动记录与 UART 日志路径。`kernel.panic` 当前为 0。预检会明确拒绝启动，不自动解决这些问题。

需要用户处理或另行授权的事项：

1. 在 31 同步并启动固定原始固件，开启现有串口终端的文件记录，记录四个 ELF。
2. 在 52 手工选择 `Ubuntu, with Linux 5.11.0-iplfs` 启动。新建的备用内核暂时不需要安装；若要使用它，安装内核、模块、initramfs 和 GRUB 属于另一个需要批准的系统级步骤。
3. 决定是否批准本次基准使用 `kernel.randomize_va_space=0`，与历史实验对齐；脚本不会代写 sysctl。后续若批准，可临时设置，不需要改持久配置文件。启动 IPLFS 内核后也要重新确认 `kernel.panic=0`。
4. 用户已明确授权本次 IPLFS 冒烟和正式实验按需读写、清空及格式化 `/dev/nvme0n1`，无需再次询问数据覆盖。部署和系统配置仍须满足前述条件；本阶段未启动实验。

## 2. 现在可以执行的只读命令

在 52：

```bash
cd /home/xin/artifact-csgc-iplfs-paper-20261015/host/benchmarks/scripts
./run_taco_iplfs_matrix.sh --preflight
./run_taco_iplfs_matrix.sh --dry-run
```

`--preflight` 不格式化、不挂载、不下发 NVMe 管理命令、不改 sysctl，也不自动访问 31。它检查内核、已固定工具、设备身份、挂载/占用、cgroup 能力、MySQL 种子/配置和已有内核异常。未就绪返回 2，这是预期行为。

`--dry-run` 只输出固定的正式清单和短冒烟清单，不调用外部进程。需要核对现有 UART 捕获文件时，另加 `--online-uart`，只通过 SSH 读取 31 的现有文件。

默认不带参数仅显示帮助。不会像旧脚本一样全局替换 mkfs/fsck、自动改 ASLR/panic、清空 dmesg 或尝试重启。

## 3. 正式清单：63 次

| 类别 | 条件 | 次数 |
|---|---|---:|
| headline | fileserver、varmail、YCSB-A、YCSB-F、uniform fio、Zipf 1.1 fio | 6 × 3 = 18 |
| 利用率 | 0.6 / 0.7 / 0.8 / 0.9 / 0.95 | 5 × 3 = 15 |
| 倾斜度 | uniform、Zipf 0.3 / **0.7** / 0.9 / 1.1 | 5 × 3 = 15 |
| fileserver 时间序列 | 严格 300 秒、约 5 秒一段 | 3 |
| GC-heavy | 大文件、小文件各六次 | 12 |
| 合计 | 不含冒烟、故障重试 | **63** |

实际顺序：论文 fio/YCSB 42 次 → GC-heavy 12 次 → Filebench 9 次。每组按轮次轮换点位，保留原先相同参数但用途不同的 P06/Z05 两组，不暗中去重。

所有 IPLFS 测试 `s=1`、L2P=3、NAND latency=0、DSM=1，挂载 `mode=lfs,background_gc=off,fsync_mode=strict,discard`。不补 IPLFS 的 s=2/4/8/16 曲线；FS-GC 平均块迁移延迟为 N/A，不是 0。

关键口径：

- 物理容量分母固定为 `64,223,813,632` 字节；不使用 IPLFS 的逻辑可用空间来放大预填充量，不修改其逻辑地址空间。
- 论文 uniform/利用率：4 jobs、64 KiB buffered randwrite、20 GiB/job 定量结束。论文 Zipf headline/倾斜度：300 秒定时结束，不误解为只写 80 GiB。没有额外覆盖预热。
- GC-heavy 大文件：86% 物理容量预填充；16 GiB 覆写预热；4 jobs、64 KiB、20 GiB/job。
- GC-heavy 小文件：16 × 1,646 个 2 MiB 文件，16 个独立池；每池顺序创建、池内八线程预填充，编号转为 0…1,645；16 GiB 覆写预热；16 jobs、4 KiB、iodepth=16、300 秒；固定种子 20260807。
- 普通论文 fio/YCSB 不额外插入历史脚本不存在的预填充后 sync。GC-heavy 保留预填充同步、warmup end_fsync 和 warmup 后同步。
- 原始 Filebench flowop 原样复用；只替换路径/时长。时间序列将 `psrun -5 $runtime` 改为字面量 `psrun -5 300`，避免旧版解析器实际只跑默认 60 秒。
- YCSB：1M records、2M operations、36 线程；A 50% read/50% update，F 20% read/80% RMW；预填充分别 0.86/0.8。
- fio/Filebench 使用独立 8 GiB cgroup，记录实际成员与 memory.max/swap.max。历史 YCSB 客户端和 MySQL 未真正处于该 8 GiB cgroup，本入口保持其实际 unlimited 范围并记录；不能宣称所有负载都有相同 8 GiB 内存限制。
- 无固定随机种子的历史 workload 不追认逐次配对。保留真实 seed 设置，后续对比按独立样本处理。

## 4. 31 上安全同步与启动记录

以下由用户在 31 手工执行；本次 Agent 没有登录、写入或操作 31。

先确保 52 没有实验、被测盘已经正常卸载。不要在 52 正使用 OpenSSD 时启动或复位 FPGA。

在 31 新建唯一目录，从 52 取同步脚本，并建立固定提交的独立源码树。以下命令不覆盖 31 原有源码工作树：

```bash
(
set -euo pipefail
task_dir=$(mktemp -d /home/xin/iplfs-paper-prepare.XXXXXX)
scp xin@192.168.98.52:/home/xin/artifact-csgc-iplfs-paper-20261015/host/benchmarks/scripts/sync_iplfs_openssd_safe.py "$task_dir/"
git -C /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc fetch origin formal-original-csgc-main-20260809
git -C /home/xin/work-xie/openssd-csgc-withjin/openssd-csgc worktree add --detach \
  "$task_dir/openssd-source" 463e8b0b13ad345ed99c2176b1f81ad34d3c986a
python3 "$task_dir/sync_iplfs_openssd_safe.py" --repo "$task_dir/openssd-source"
printf 'Prepared directory: %s\n' "$task_dir"
)
```

记住打印的 `Prepared directory`。前面的 Python 调用只是预览，不移动 Vitis 工作区文件。确认 52 设备已离线后，在 31 用真实目录替换下面的 `/home/xin/iplfs-paper-prepare.XXXXXX`：

```bash
python3 /home/xin/iplfs-paper-prepare.XXXXXX/sync_iplfs_openssd_safe.py \
  --repo /home/xin/iplfs-paper-prepare.XXXXXX/openssd-source \
  --apply --confirm-device-offline
```

同步效果：

- Vitis 工作区固定为 `/home/xin/vitis_workspaces/xie_csgc_withjin`；项目为 `ftl`、`cs_leader`、`cs_worker1`、`cs_worker2`。
- 全部输入和目标先核验，再创建完整 staging 树；各旧 `src/` 整体移到 `/home/xin/openssd-source-archives/<时间_唯一标识>/`，包括旧代码和用户文件。
- 非代码项目资产（例如 linker script）保留；需要替换的旧文件均有归档。不调用旧 `sync_code.sh`，不执行删除、构建、下载或复位命令。
- 新源码更新时间刷新，避免 Vitis 把源文件旧时间误判为无需编译；记录实际内容哈希。
- 输出 `sync-receipt.json` 的绝对路径。若中途中断，停止手工重试，先读归档中的 `plan.json` / `interrupted.json`。

随后在 Vitis 中**整体重新编译四个应用并启动四个 ELF**。默认 ELF 路径如下；若项目配置使用 Release 或不同名称，以 Vitis 实际输出为准：

```text
/home/xin/vitis_workspaces/xie_csgc_withjin/ftl/Debug/ftl.elf
/home/xin/vitis_workspaces/xie_csgc_withjin/cs_leader/Debug/cs_leader.elf
/home/xin/vitis_workspaces/xie_csgc_withjin/cs_worker1/Debug/cs_worker1.elf
/home/xin/vitis_workspaces/xie_csgc_withjin/cs_worker2/Debug/cs_worker2.elf
```

在你现在使用的串口终端中启用写文件的日志功能，记下绝对路径。这里不能凭空指定串口设备名、启动第二个串口 reader 或重设串口参数，否则可能干扰你现有终端。捕获文件需要持续追加，并能让 52 通过只读 SSH 访问。

固件 GET config 只向 UART 打印，不向 Host 返回参数结构。因此串口文件是自动核验 L2P=3、NAND latency=0、DSM=1 的必需输入；成功执行 admin 命令本身不算回读成功。

启动完成后，在 31 记录四个 ELF 和 UART 路径（替换前三个占位路径）：

```bash
python3 /home/xin/iplfs-paper-prepare.XXXXXX/sync_iplfs_openssd_safe.py \
  --record-startup /home/xin/openssd-source-archives/ACTUAL_DIRECTORY/sync-receipt.json \
  --confirm-four-elfs-started --uart-log /absolute/path/to/current-uart.log \
  --elf ftl=/home/xin/vitis_workspaces/xie_csgc_withjin/ftl/Debug/ftl.elf \
  --elf cs_leader=/home/xin/vitis_workspaces/xie_csgc_withjin/cs_leader/Debug/cs_leader.elf \
  --elf cs_worker1=/home/xin/vitis_workspaces/xie_csgc_withjin/cs_worker1/Debug/cs_worker1.elf \
  --elf cs_worker2=/home/xin/vitis_workspaces/xie_csgc_withjin/cs_worker2/Debug/cs_worker2.elf
```

记录包含输入/ELF 哈希与“用户确认已启动”，**不伪称设备返回了固件二进制哈希**。复制输出的 `startup-receipt-*.json` 到 52 一个新文件名，然后在 52 绑定：

```bash
cd /home/xin/artifact-csgc-iplfs-paper-20261015/host/benchmarks/scripts
./run_taco_iplfs_matrix.sh --bind-firmware /absolute/local/path/to/startup-receipt.json
./run_taco_iplfs_matrix.sh --preflight --online-uart
```

本地绑定只更新本工作树的 `iplfs-paper-preparation/config.json`，旧版先归档。不写 31，也不操作 SSD。

## 5. 部署完成后的一条命令

**以下是未来运行命令，本阶段没有执行。** 本次实验盘覆盖已获授权；运行仍需要 IPLFS 已启动、系统设置已批准、固件证据已绑定、预检通过：

```bash
tmux new-session -d -s iplfs-paper \
  'cd /home/xin/artifact-csgc-iplfs-paper-20261015/host/benchmarks/scripts && exec ./run_taco_iplfs_matrix.sh --all --allow-destructive /dev/nvme0n1'
```

`--all` 自动执行八个短冒烟，再执行 63 次正式运行；不反复询问。冒烟包含高 LBA 读回/覆盖写/DSM、文件写回读与打洞、短 fio、YCSB、GC-heavy 大小文件及 fileserver/varmail/periodic。每个冒烟独立重建状态，和正式批次分目录，绝不进入论文图。

高 LBA 直接 DSM 使用私有 `iplfs_dsm_page`：4 KiB 对齐且映射完整 4 KiB，匹配旧固件整页 DMA 接收方式；不调用只映射 16 字节的通用单范围 `nvme dsm`。IPLFS 5.11 自身的 discard 路径已经映射完整 4 KiB，未改内核。

若希望先只做冒烟：

```bash
./run_taco_iplfs_matrix.sh --smoke --allow-destructive /dev/nvme0n1
```

之后仅运行正式 63 次：

```bash
./run_taco_iplfs_matrix.sh --run --allow-destructive /dev/nvme0n1 \
  --smoke-batch /absolute/path/to/completed-smoke-batch
```

正式运行要求冒烟与当前配置、工具、脚本、固件证据和 boot ID 相同。修改这些输入或重启后，需要重新冒烟。正常批次不切换固件、不重启、不全局安装工具。

## 6. 日志、监测与失败处理

所有批次保存在工作树根目录的 `iplfs-paper-preparation/batches/`，命名区分 `smoke-*`、`formal-*`。终端打印准确路径。

```text
batch/
  provenance.json              # 操作者、外层脚本、源码 commit、完整输入 fingerprint、boot ID
  config.json / preflight.json
  schedule.json / schedule.tsv # 冻结清单
  state.json                   # PID + start ticks、当前 case/phase、成功 ID
  source.patch / source-snapshot/
  case-results.tsv
  runs/<case-id>/
    case.json
    phases.jsonl               # 每条真实 argv、起止时间、退出状态
    *-benchmark.log            # fio normal+JSON / YCSB / Filebench 原始输出
    effective-workload.*
    *-cgroup.json
    device-config-uart-*.log / device-config-*.json
    *-device-stat-after.log
    *-readonly-fsck.log
    kernel-new.log / validation.json
  analysis/
    per-run.csv / aggregate.csv / failures.csv
    fileserver-intervals.csv / summary.json
    figure-inputs/*.csv / integration-report-zh.md
  completed.json               # 仅全部成功后发布
```

低频只读查看，建议 5 分钟一次：

```bash
./run_taco_iplfs_matrix.sh --status /absolute/path/to/batch
tmux capture-pane -pt iplfs-paper -S -30
```

状态核对整批清单、逐项验证、完成标记以及父进程的 boot ID/start ticks。某个 fio 退出、暂时没有 benchmark 子进程或最后一个 case 正在 fsck，都不算整批完成。

首次同类点完成后累积实际墙钟时长；`--status` 只有对全部剩余点已有同点样本时才给出剩余时长估算，否则列出缺少估计依据的点。短冒烟不是正式时长的可靠外推。

失败策略：fio/Filebench/YCSB、同步、卸载、fsck、UART、NVMe 或内核异常均使批次失败并停止后续 case；有超时，stdin 关闭，fsck 使用 `-n` 并以 O_RDONLY 打开介质。不会回答修复提示、强制卸载、自动修复文件系统或重启。失败后保存进程/内核/挂载现场，由用户或后续授权诊断决定如何恢复。

恢复功能是显式的 `--run --resume OLD_FAILED_BATCH --smoke-batch CURRENT_PASSED_SMOKE`，仍须配置/源码一致及新的运行预检；它建立新目录，复制已通过样本并记录来源，只重跑未通过项。**不能把这种继承批次说成一轮全新独立重复。** 若要整轮重做，直接重新用 `--all`，保留旧失败目录。

自动分析每次成功后更新，失败后也更新；离线重做分析可执行：

```bash
./run_taco_iplfs_matrix.sh --analyze /absolute/path/to/batch
```

## 7. WAF 与统计边界

固定原始固件 `463e8b0` 的 `src/shared/shared_mem.h`、`src/ftl/nvme/nvme_io_cmd.c` 和 `src/emu/ftl.c` 已包含 64 位写入/GC/DSM 计数；2025 年 IPLFS 原始日志亦包含这些字段。因此本轮不需要新固件统计宏，**计划直接在正式运行中采集 U/Z WAF，不默认增加 30 次**。

每轮正式应用前 reset statistics 并核验计数，应用完成后同步、正常卸载，再读取原始整数。物理 WAF 精确使用 `nand_write_bytes / host_normal_write_bytes`；不使用 fio 字节数或设备输出的千分比截断值作分母。缺字段、零分母、reset 不成立等情况拒绝生成有效样本。IPLFS 出现 Host-GC/CSGC 写入量也会停止，而不继续画错系统的数据。

fio 的 WAF 窗口不含覆盖预热，含正式运行后的排空；Filebench/YCSB 的初始化发生在程序内部/DB 拷贝阶段，其设备统计窗口更宽，自动报告明确区分，不能把它们直接解释为纯 measured workload 的 WAF。

**源码和旧日志证实接口存在，不等于新部署已运行验证通过。** 新启动后要通过真实冒烟验证 counters、映射模式、高 LBA、discard 和只读 fsck；本阶段没有这项实测结果。

## 8. 已完成检查、旧数据与时间预算

离线验证包含语法/空白检查、完整内核构建、私有工具构建，以及 18 项合成测试：清单/停止条件、错误样本、YCSB RMW 计数、Filebench 加权时间序列、WAF、UART 配置解析、PID 复用、轮次间空档、失败不启动下一轮、归档保留、只读 fsck 的 O_RDONLY 系统调用、DSM 整页缓冲区、源码同步归档等。

测试文件全部保存在 `iplfs-paper-preparation/test-runs/`；它们是本地普通文件和模拟日志，没有格式化/挂载任何设备，也不是 IPLFS 性能或正确性实验。

旧数据保留与复用入口：

| 旧数据 | 状态与后续用法 |
|---|---|
| WeaveGC 主矩阵 `20260916_045438` | 保留已核实的三次样本；uniform/Zipf、YCSB、fileset 参数按其实际配置对齐 |
| WeaveGC/F2FS paper-metrics 与 s=16 补测 | 继续用于对应指标；IPLFS 不生成不适用的 s 扫描和 FS-GC 迁移值 |
| F2FS/WeaveGC 严格 300 秒时间序列 | 新 IPLFS T01 使用相同完整窗口；不混入旧 60 秒 periodic 运行 |
| GC-heavy 六次数据 | G01/G02 各补六次；保留固定预热量和文件池布局，不按 IPLFS 是否触发 FS-GC 追加预热 |
| F2FS 主性能样本 | 部分面板当前只使用一次；投稿前要核对是否还有相容重复，不能当作已经达到 n=3 |

完整来源索引在 `/home/xin/artifact-csgc/Paper-TACO/writing-support-20260924/data/source-manifest.json`。本次没有重测这些基线或宣称新环境已校准。正式提交前建议少量代表点校准（uniform fio、varmail、GC-heavy 大文件），不要求现在再跑整套基线；需要的固件/内核切换应集中安排。

初步预算：真正的短冒烟约 15–30 分钟，63 次完整运行先预留约 9–14 小时，另计 Vitis 操作和启动内核。依据是每轮预填充、300 秒定时段或 80 GiB 写入、同步/卸载/fsck 均占时间；旧 IPLFS uniform 样本曾用 252.775 秒写完 80 GiB，但不足以预测新版完整矩阵，尤其是 GC-heavy 和 95% 利用率。此范围是排程预算，不是已测保证。实际启动后使用逐点真实时长更新 ETA；发生错误就停止，不为赶时间跳过检查。

## 9. 后续维护命令

以下只构建本工作树私有文件，不安装系统文件；现有构建已经完成，无需立刻重复：

```bash
python3 -B iplfs_paper/prepare.py tools --jobs 8
python3 -B iplfs_paper/prepare.py kernel --jobs 24
python3 -B iplfs_paper/prepare.py audit-installed
python3 -B iplfs_paper/test_suite.py
```

禁止用 `make clean`、全局 `make install`、旧自动工具安装函数或旧固件 `sync_code.sh` 替换这里的流程。新构建使用新目录，旧文件和失败构建保留。
