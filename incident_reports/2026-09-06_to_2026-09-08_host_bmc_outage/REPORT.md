# 2026-09-06 至 2026-09-08 Host 与 BMC 失联事故调查报告

## 1. 摘要

本报告调查两次最终均出现 `192.168.98.52` Host 与 `192.168.98.62` BMC 无法从外部连接、并由用户现场按服务器物理按键恢复的事故。新增时间证据表明，第一次事故中两者并非已证实在同一时刻开始失效。所有时间均为 Asia/Shanghai (CST)。

结论如下：

1. **52 Host 存在两次有直接证据的软件故障，而且两次故障机制不同。**
   - 第一次保留了 `fs/f2fs/debug.c` 数组越界及随后 `strlen(NULL)` 的 Oops。触发者是实验脚本每 5 秒读取一次 `/sys/kernel/debug/f2fs/status`。该缺陷已由 Host 提交 `a830b143c` 修复。
   - 第二次保留了卸载期间 `f2fs_write_end_io()` 解引用已经置空的 `sbi->node_inode` 的 Oops。实验日志证明卸载前仍有 CSGC/writeback 工作；9 月 5 日和 9 月 14 日存在完全相同的历史崩溃。9 月 14 日的源码修复已经提交并推送，但尚未进行破坏性运行验证。
2. **本轮两次 Host 事故没有软件重启或 SysRq 的证据。** 审计日志在两个事故窗口内均没有 `reboot`、`shutdown`、`poweroff`、`halt`、`kexec`、`systemctl` 或 keyed SysRq 事件；`last -x` 也没有正常关机记录。
3. **本轮两次事故没有 PCIe/AER、MCE、watchdog 或网卡 link-down 的内核证据。** 机器历史上确实发生过一次独立的 PCIe fatal error，但它具有清晰的 GHES/AER 签名，本轮两份 pstore 均没有该签名，不能把历史硬件故障套用到这两次事故。
4. **第一次 BMC 失联与外部 IPMI 探测存在很强的时间相关性。** 用户补充的 Mac agent 现场陈述显示：`.62` 的 HTTPS 先返回 HTTP 200；随后 Mac 和 31 对 `.62` 连续发送 `chassis power status`、`mc info`、`sol info` 等只读 IPMI 请求；这些请求未正常返回，之后 `.62:443` 从连接重置退化为不可达。该顺序使“IPMI 协商触发旧 BMC 固件或管理网异常”成为第一次 BMC 事故的首要假设，但没有 BMC 日志、原始命令时间戳和对照实验，尚不能认定因果关系。
5. **第一次 BMC 与 Host 并非已证实的同时失联。** 同一份 Mac agent 陈述明确说 `.52` 在 BMC 失联时仍在线；本机 audit 也证明 00:42:18 前后 Host 仍持续执行用户态任务。因此，更符合证据的时间线是：Host 先发生一个未立即停机的 F2FS Oops，BMC 后在 IPMI 探测期间失联，Host 又在某个未知的更晚时刻变得不可达。现场恢复时两者都不可达，不等于两者从同一时刻、同一原因开始失效。
6. **第二次 Host 失联可高置信度定位到 16:08 的 F2FS Oops，但第二次 BMC 的失联时刻仍不能与之对齐。** 约 13:00 现场按物理电源键后，`.52` 和 `.62` 均恢复；Codex 使用记录证明 `.52` 至少到 16:00 仍在线；本机日志随后在 16:08:31 记录致命 Oops；17:20 用户首次发现两者均不可达。由于 16:00 的 Codex 记录不证明 `.62` 在线，BMC 的严格故障窗口仍是约 13:00 至 17:20。
7. **两次 BMC 事故的最终根因仍未知。** 本轮按要求没有访问 BMC IP、没有使用本机 IPMI，也没有交换机、PDU/UPS 或 BMC SEL。Host 的两个 F2FS Oops 足以解释 `.52` 的异常，却不能解释自治 BMC 的全部行为。

## 2. 调查边界

本轮只读取已有日志、实验数据、源码和 Git 历史，并在项目中保存报告与证据副本。没有进行以下操作：

- 未访问 `192.168.98.62`，未使用 `/dev/ipmi*` 或 `ipmitool`；
- 未发送重启、关机、SysRq 或电源控制命令；
- 未运行或恢复实验，未执行 fio、Filebench 或压力测试；
- 未 mount、umount、sync、fsck、mkfs、重置设备或操作内核模块；
- 未修改源码、脚本、系统配置、服务、网络、固件或原始日志；
- 未开启 perf、ftrace、eBPF、gdb、strace 或新的持续监测。

本报告使用的主要证据是 pstore、audit、wtmp、两个失败批次的既有日志，以及故障版本的 Host 源码。前一轮启动的 persistent journal 不可用，因此不能依靠 journal 补全崩溃前后的全部事件。

## 3. 时间线

| 时间 | 事件 | 证据与状态 |
| --- | --- | --- |
| 2026-09-06 22:21:36 | Host 启动，boot ID `98aac792-ea21-480d-85b3-7db2062ef3ca` | `uptime -s` 历史记录及 `REBOOT_LOG.md` |
| 2026-09-06 22:36:15 | 12 轮 node-readahead/NOWAIT A/B 批次开始 | 批次 `20260906_223615` provenance |
| 约 2026-09-06 22:57:39 | `cat /sys/kernel/debug/f2fs/status` 触发数组越界和 `strlen(NULL)` Oops | pstore uptime `2162.902 s` |
| 2026-09-06 22:58:10 | teardown 中 `sync -f`、writeback 和 CSGC section worker 均出现 D 状态 | teardown 诊断日志 |
| 2026-09-06 22:58:11.972 | 审计记录启动 `umount /dev/nvme0n1`，没有后续成功结果 | audit event `26570` |
| 约 2026-09-07 00:42 | `.62` 在多次外部只读 IPMI 请求后由 HTTPS 正常逐步退化为连接重置和不可达；Mac agent 当时报告 `.52` 仍在线 | 用户提供的 Mac agent 陈述；没有原始探测日志 |
| 2026-09-07 00:42:18 | `.52` 成功执行 `sudo -n -n -v`；前后相同活动约每 45 秒持续一次 | 本机 audit events `29260/29261` |
| 2026-09-07 12:54:45 | 用户请现场同学按下服务器物理电源键；Host 启动，约 13:00 确认 `.62` 管理网页也恢复 | 新 boot ID `c58a64bd-d5a7-48b7-845d-3ea500230b87`；用户现场陈述 |
| 2026-09-07 15:57:10 | 修复 status Oops 后重新启动 A/B 批次 | 批次 `20260907_155710` provenance |
| 约 2026-09-07 16:00 | Codex 账户仍有来自 52 中转环境的请求记录，证明 Host 当时仍可提供服务 | 用户查询的 Codex 使用记录 |
| 2026-09-07 16:04:23 左右 | 第一轮 control/fileserver 的 300 秒 Filebench 完成 | `filebench.log` |
| 2026-09-07 16:04:28 | teardown 开始 `sync -f`；之后约 4 分钟持续等待写回 | runner 与 teardown 诊断日志 |
| 2026-09-07 16:08:27.931 | 最后一份诊断样本仍见 `sync`、writeback 和 CSGC worker | teardown 诊断日志 |
| 2026-09-07 16:08:30.423 | 审计记录启动 `umount /dev/nvme0n1` | audit event `38021` |
| 约 2026-09-07 16:08:31 | NVMe completion 路径在 `f2fs_write_end_io+0x199` 发生地址 `0x30` 空指针 Oops | pstore uptime `11625.846 s` |
| 2026-09-07 17:20 | 用户首次发现 `.52` 与 `.62` 管理网页均无法连接 | 用户外部观察；不能证明两者同时开始失效 |
| 2026-09-08 10:02:23 | 用户再次现场通过物理按键恢复 Host；`last -x` 无正常 shutdown | 当前 boot ID `9dae0280-d895-44d8-9802-7a6c2108e37b` |

第一份 Oops 实际发生在 9 月 6 日夜间，9 月 7 日中午才现场恢复。因此，“9 月 7 日发生两次”的说法若按恢复操作计数成立；若按内核故障发生时间计数，则两次 Host Oops 分别发生在 9 月 6 日 22:57 和 9 月 7 日 16:08。第一次 BMC 开始异常的时间现可根据外部陈述收窄到约 00:42，但尚无原始探测日志。该陈述与“00:42 时 52 和 62 同时失联”的说法冲突；在取得 Mac/31/Jump181 的原始记录前，本报告采用更保守的表述：00:42 时 BMC 失联，而 Host 至少在本地仍运行，且据 Mac agent 报告仍可从外部访问。

第二次恢复后，`.52` 和 `.62` 在约 13:00 均已恢复。Host 的最后外部活动可确定到约 16:00，本地实验和审计则持续到 16:08，且 Host Oops 精确落在 16:08:31；因此 Host 故障窗口已经高度收敛。BMC 只有 13:00 的正常记录和 17:20 的不可达记录。如果 16:00 左右没有单独检查过 BMC，就不能用 Codex 请求记录将 BMC 故障窗口同步收窄。

## 4. 事故一：F2FS 状态读取越界

### 4.1 已证实事实

- 失败批次：`host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260906_223615`。
- Artifact：`exp/filebench-mcsgc-node-readahead-ab-20260906@b346727d627b8448147f38f920e0bd081459881d`。
- Host：`exp/diagnostic-mcsgc8t-filebench-node-readahead-ab-20260906@304ff514692dc27c2501aa90a8a3cdf3f82ebccc`。
- 批次完成了 control/fileserver 第一轮；故障发生在 NOWAIT/fileserver 第一轮。该结果不能作为完整实验数据。
- pstore 明确报告：
  - `UBSAN: array-index-out-of-bounds in fs/f2fs/debug.c:368:23`；
  - `index 15 is out of range for type 'char *[15]'`；
  - 进程为 `cat`，调用点为 `stat_show()`；
  - 随后在 `strlen()` 以地址 `0x0` 发生 NULL dereference。
- 故障提交 `b6fb9bccb` 增加了 `SBI_CP_NODE_WRITEBACK`，其枚举值为 15，但没有给 `debug.c` 的 `s_flag[]` 增加第 16 个字符串。`stat_show()` 又按 32 位遍历 `s_flag`，因此该 flag 置位时必然访问越界并将 NULL 传给 `seq_puts()`。
- 实验脚本每 5 秒读取一次 `/sys/kernel/debug/f2fs/status`，所以它能稳定撞到短暂置位的 checkpoint node-writeback flag。
- Oops 后 teardown 仍进入 `sync` 与 `umount`；审计只看到卸载启动，没有看到正常结束，`last -x` 也把 tmux 会话记为 crash。

### 4.2 修复状态

该确定缺陷已由 Host 提交 `a830b143cae95e491dffc32fded1ddaf6ee8d089` 修复：

- 增加 `[SBI_CP_NODE_WRITEBACK] = " cp_node_writeback"`；
- 将遍历上限从固定 `32` 改为 `ARRAY_SIZE(s_flag)`。

Artifact 提交 `47e51968c` 还将状态采样器在 sync/umount 之前停止，减少卸载期读取 debugfs 的干扰。第二次事故使用 Host `a830b143c`，没有再出现 status Oops，说明这个具体缺陷已被消除。

### 4.3 仍未知

进程上下文 Oops 本身不一定立刻 panic。现有证据证明它污染了内核，并证明随后卸载没有完成；但 pstore 只保留第一处 Oops，无法证明 52 最终完全失联仅由这一处 `strlen(NULL)` 导致，还是后续又进入了未保存的卸载生命周期故障。

## 5. 事故二：CSGC 卸载生命周期错误

### 5.1 已证实事实

- 失败批次：`host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260907_155710`。
- Artifact：`exp/filebench-mcsgc-node-readahead-ab-20260906@30be3016135f19a81b8fcb933991729aac8f4d56`。
- Host：`exp/diagnostic-mcsgc8t-filebench-node-readahead-ab-20260906@a830b143cae95e491dffc32fded1ddaf6ee8d089`。
- OpenSSD 源码期望版本：`exp/formal-mcsgc-quiet-20260809@52831c159c9f7a73f9670c163a6b513750f64b47`。该 provenance 只能证明源码和 Vitis 输入哈希，不能证明运行 ELF 的逐字节身份。
- 本轮第一项就是 control/fileserver，运行时 NOWAIT 参数为 `N`。因此第二次崩溃不是 NOWAIT 路径造成的。
- Filebench 已完成 300 秒正式窗口，但 `sync -f /mnt/openssd_f2fs` 从 16:04:28 左右持续阻塞。
- 多个 5 秒诊断样本显示：
  - `sync` 在 `wb_wait_for_completion()` / `sync_filesystem()`；
  - writeback worker 在 `f2fs_write_data_pages()` 或 `f2fs_write_inode()` 中进入 `f2fs_balance_fs()`、`f2fs_gc()`、`do_garbage_collect_cs_pipeline()`；
  - CSGC PRE/section worker 也曾处于 D 状态。
- 审计在 16:08:30.423 记录 `umount /dev/nvme0n1` 启动。
- 紧接着 pstore 报告：
  - 大量 `Fail to preallocate blocks, ret = -11`；
  - `BUG: kernel NULL pointer dereference, address: 0000000000000030`；
  - CPU 36、PID 0、IRQ 上下文；
  - RIP 为 `f2fs_write_end_io+0x199/0x440 [f2fs]`；
  - 下层调用是 `bio_endio -> blk_update_request -> nvme_complete_rq`。

### 5.2 源码与机器码对应

故障模块 SHA-256 为：

```text
4dd0b8da5ed00fc0dbf208fe6ebf9b1b52f05cd1bf14ac1ba3bd5e0e368cb9b6
```

对该模块做静态反汇编，`f2fs_write_end_io+0x199` 正好对应 `fs/f2fs/data.c:374`：

```c
f2fs_bug_on(sbi, page->mapping == NODE_MAPPING(sbi) &&
            page->index != nid_of_node(page));
```

`NODE_MAPPING(sbi)` 展开为：

```c
return sbi->node_inode->i_mapping;
```

故障机器码是 `cmp 0x30(%rax), %rdx`，pstore 中 `RAX=0`、`CR2=0x30`，因此可以确定当时 `sbi->node_inode == NULL`。

卸载路径的顺序是：

```text
kill_f2fs_super()
  set SBI_IS_CLOSE
  stop ordinary gc/discard threads
  kill_block_super()
    f2fs_put_super()
      flush merged writes / wait CP data pages
      iput(sbi->node_inode)
      sbi->node_inode = NULL
      destroy node manager
      destroy segment manager
      destroy CSGC section/PRE/POST workqueues
```

也就是说，关闭标志和普通 GC thread 并没有先同步销毁独立的 CSGC section、PRE、POST workqueue。它们直到 `node_inode`、node manager 和 segment manager 已失效之后才被 drain。只要这些异步工作在卸载边界前后留下一个延迟完成的普通 data/node bio，NVMe completion 就仍会进入 `f2fs_write_end_io()`；而该函数对任何普通页面都会无条件求值 `NODE_MAPPING(sbi)`，于是解引用已经置空的 `node_inode`。

### 5.3 历史复现证据

`/var/lib/systemd/pstore/7681983436248/dmesg.txt` 保留了 9 月 5 日同型事故：

- 同样先密集出现 preallocate `-11`；
- 同样访问地址 `0x30`；
- 同样 RIP 为 `f2fs_write_end_io+0x199/0x440`；
- 同样来自 NVMe completion IRQ；
- 审计记录 `umount /dev/nvme0n1` 在 17:39:06 启动，Oops 随后约一秒发生。

因此，“异步 I/O completion 在 CSGC 卸载对象失效后到达”是高置信度、可重复的软件生命周期错误，而不是一次随机内存错误。

### 5.4 修复方向

修复不能只在 `f2fs_write_end_io()` 增加 NULL 判断，因为那会掩盖已经发生的 use-after-teardown。正确方向是：

1. 设置 closing 状态后禁止任何新 CSGC section/PRE/POST 提交；
2. 在 `node_inode`、meta inode、node manager 和 segment manager 仍有效时，停止并 drain 全部 CSGC workqueue；
3. 等待所有 CSGC 请求、普通 data/node write bio 和相关 completion 完成；
4. 再执行最终 checkpoint/flush，并销毁 inode 与核心管理结构；
5. 对挂载失败的回滚路径同步校正顺序。

该顺序必须先做静态锁依赖检查，避免 drain workqueue 时 worker 反向等待 `umount_mutex`、checkpoint 或 writeback，形成新的卸载死锁。

### 5.5 9 月 14 日复现与修复状态

9 月 14 日批次
`/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260914_144442`
在 Host `9f75425881eacba4fad7f8fc6080c0b73c601825` 上再次复现同型 Oops。该版本已经在
`kill_f2fs_super()` 中设置 closing 状态并提前排空 CSGC workqueue，但第一轮
control/fileserver 仍在 Filebench 完成后出现约 5.5 分钟低空间写回压力，并在
`umount` 返回后约 1.6 秒由延迟 NVMe completion 进入 `f2fs_write_end_io()`，再次以
`sbi->node_inode == NULL` 崩溃。这证明仅排空 CSGC worker 不足以覆盖 worker 已经提交、
但尚未完成 end-io callback 的普通写 BIO。

9 月 14 日完成并推送以下修复：

- Host `bff673239b55735fd0f01ab80e61452d9d3aafd8`：为每个普通写 BIO 持有一份挂载级
  生命周期引用；卸载在第二次冲刷合并 BIO 后等待所有 end-io callback 完成，再销毁
  `node_inode`、`meta_inode` 和 node/segment manager。热路径成本为每个写 BIO 一次
  原子增加和一次原子减少，不是每个 4 KiB 页面计数。
- Host `08ce6e2e02cfc45abbfa7c5f1bd985a4d461e2a1`：恢复低空间普通写回保护，并保留
  CSGC 目标段 rollover 的写回空间，减少 `-EAGAIN` 风暴把卸载推入极端尾延迟的概率。
  该层与生命周期修复分成独立提交，可以单独回退和比较。
- Artifact `72388abdaccb7a915720e263f092ccf256acfa92`：将同二进制 control/NOWAIT A/B
  固定到修复后的 Host 提交，保持 fileserver 优先，并默认在离线 fsck 失败时停止。

静态检查已经完成：`git diff --check`、相关对象编译、完整 `f2fs.ko` 构建、Artifact
`bash -n`、`shellcheck`、12 轮 schedule dry-run 和只读 preflight 均通过。完整模块构建
仍出现该内核树在缺少 `vmlinux.o` 时已有的 modpost 未解析符号警告，但成功生成模块，
未出现本次改动引入的编译错误。由于验证实验会重新格式化 `/dev/nvme0n1`，本轮没有
自行运行；因此当前结论是“源码修复已交付，运行时验证待授权”，不能写成故障已经通过
压力测试证明消失。

## 6. 52 Host 失联结论

### 6.1 已证实

- 两次事故窗口都存在明确的定制 F2FS/CSGC Oops。
- 两次均发生在 GC-heavy Filebench 批次末尾的 sync/umount 生命周期附近。
- 第二次及 9 月 5 日历史同型事故都直接落在 `f2fs_write_end_io()` 的卸载后对象访问。
- 两个事故窗口均无软件重启、关机或 SysRq 审计事件；`last -x` 无正常 shutdown。

### 6.2 推测

- 第一次 Host 完全失联很可能是 status Oops 污染内核后，叠加未完成的 CSGC/writeback/umount 导致；精确终止点没有被持久化。
- 第二次 Host 完全失联可以由 IRQ 上下文中的 `f2fs_write_end_io()` Oops 和已损坏的卸载状态充分解释。

### 6.3 反证

- 本轮两份 pstore 均无 AER、PCIe Bus Error、GHES Hardware Error、MCE、kernel panic、NETDEV watchdog 或 link-down 记录。
- 历史 `pstore-20260903-pcie-fatal-counterexample.txt` 明确包含 root port `0000:ae:02.0`、AER uncorrectable status `0x20` 及 `Kernel panic - not syncing: Fatal hardware error!`。本轮没有这组特征，因此不能把那次独立 PCIe 故障视为本轮原因。

### 6.4 置信度

| 判断 | 置信度 |
| --- | --- |
| 第一次 status Oops 的直接代码原因 | 确定 |
| 第二次 `node_inode == NULL` 的直接崩溃原因 | 确定 |
| 第二次由 CSGC workqueue/late bio 与卸载顺序竞态触发 | 很高 |
| 第一次完全失联只由 status Oops 单独造成 | 中等，证据不足以排除后续故障 |
| 本轮两次由 PCIe fatal hardware error 造成 | 低，现有证据反对 |

## 7. 62 BMC 失联分析

### 7.1 已证实

- 根据用户从外部网络的直接观察，两次事故最终都出现 `192.168.98.62` 和 `.52` 无法连接，并在现场物理操作后恢复。
- 用户提供的 Mac agent 陈述记录了第一次 BMC 事故的先后关系：探测前 `.62` HTTPS 返回 HTTP 200；随后从 Mac 和 31 发出多次只读 IPMI 请求；请求未正常响应；此后 `.62:443` 重置连接，Jump181 最终报告 `No route to host`。
- 同一陈述明确称 `.52` 当时仍在线。本机 audit 在 00:42:18 记录到成功的 `sudo -n -n -v`，并在 00:30 至 01:10 查询窗口内约每 45 秒持续出现。这独立证明 Host 内核和用户态当时仍能调度执行，但仅凭本机记录不能证明外部网络连通性。
- 所述 IPMI 命令的预期语义是查询状态，没有发送 power cycle、BMC reset 或配置修改命令。
- 本轮没有接触 BMC，因而没有读取 SEL、BMC uptime、reset reason、网络模式、传感器或固件日志。
- 当前 Host 物理平台是 Intel S2600WFQ；Host 数据网络设备和 BMC 管理控制器是不同功能域。Linux Host 的 F2FS Oops 通常不会直接停止自治 BMC。
- 第一次现场恢复由用户委托的同学在约 13:00 按下服务器物理电源键完成；此后 Host 启动，BMC 网页也恢复。该事实证明物理操作恢复了两个观察对象，但由于操作细节和 BMC uptime 缺失，不能判断电源键是否直接复位了 BMC。

### 7.2 第一次 BMC 事故的因果评估

“只读 IPMI”只表示命令不应修改电源或持久配置，并不表示处理这些网络包对 BMC 没有运行时影响。请求仍会进入 BMC 的网络栈、RMCP/RMCP+ 会话协商、认证和命令解析路径。如果旧固件存在协议兼容、资源泄漏或并发状态机缺陷，查询命令也可能成为触发器。

| 因果证据 | 评价 |
| --- | --- |
| 探测前 HTTPS 正常 | 支持 BMC 在探测前至少部分可用 |
| 多次 IPMI 请求后立即退化 | 强时间相关性，支持探测成为触发器 |
| `.52` 当时仍在线 | 反对“Host 与 BMC 同一时刻因共同 Host 故障倒下” |
| IPMI 请求本身均未成功 | 也可能说明 IPMI 子系统在探测前已经异常，探测只是发现故障 |
| 无 BMC SEL、console 或进程日志 | 无法证明固件在哪个路径卡死，也无法排除网口/交换机问题 |
| 未做复现 | 无重复性证据；本轮明确禁止且不应为定责主动复现 |

因此，第一次事故中“外部 IPMI 探测触发 BMC 管理面卡死”的可能性应从一般假设上调为**首要假设，置信度中等**。但准确表述仍是“可能触发”，不能写成“已经证明由 IPMI 导致”。`No route to host` 也不能单独证明路由设备故障，它还可能来自 ARP/邻居解析失败或中间设备返回 unreachable。

### 7.3 第二次 BMC 事故

第二次事件的 Host 时间线比 BMC 清楚得多：Host 在约 16:00 仍有 Codex 请求，16:04 完成 Filebench，16:08 进入卸载并发生确定的 `f2fs_write_end_io()` Oops。因此，`.52` 的第二次失联几乎可以定位到 16:08 的内核故障。

BMC 在约 13:00 已确认恢复，17:20 被发现不可达，但没有 13:00 至 17:20 之间的独立状态样本。因此：

- “17:20 时两者都不可达”是已知事实；
- “两者在 16:08 同时失效”仍是未经证明的推测；
- Codex 在 16:00 仍有请求只能收窄 Host 故障窗口，不能收窄 BMC 窗口；
- 如果第二次事故前没有再次发送 IPMI 请求，那么第一次的 IPMI 探测更可能只是脆弱 BMC 的一个触发条件，而不是解释所有 BMC 失联的唯一根因。

第二次 BMC 失联在物理恢复后不到五小时内再次出现，使“BMC/管理网存在持续性不稳定”比单次偶然网络抖动更值得怀疑；但没有 BMC 与交换机日志，仍无法区分固件、独立管理网口、交换机端口和平台电源状态。

### 7.4 其他可能解释

以下解释仍未排除：

1. **BMC 已经部分异常，IPMI 探测只是发现而非触发。** HTTPS 200 不能证明 IPMI 服务、会话表和管理网固件整体健康。
2. **BMC 独立网口或交换机端口异常。** 多个观察点都无法访问 `.62`，同时 `.52` 仍在线，更符合 BMC 专属路径而非公共上联故障；但缺少交换机端口日志。
3. **BMC 固件非探测相关的独立卡死。** 与探测时间重合，但并非由探测导致。
4. **主板、电源或平台级共同故障。** 该解释难以说明为什么 00:42 时 `.52` 仍运行，因而对第一次事故的解释力下降；对第二次事故仍无法排除。

### 7.5 关键反证

- “Host 内核崩溃直接导致 BMC 掉线”缺乏架构依据；BMC 不依赖 Linux 内核运行。
- “第一次两者同时因同一个硬件故障倒下”与 `.52` 在 BMC 失联时仍在线的陈述及本机活动记录不符。
- “只读命令绝不可能触发 BMC bug”不成立；只读只限定预期业务效果，不保证固件处理路径无缺陷。
- “已经证明 IPMI 探测导致 BMC 卡死”同样不成立；缺少原始探测记录、BMC 内部日志及重复性证据。
- “一定只是网络问题”也不成立；Host pstore 独立证明机器内部发生了真实 F2FS Oops。

### 7.6 仍未知

- Mac、31 和 Jump181 每条探测命令的精确时间、并发度、超时、重试次数、IPMI 版本/cipher suite 和原始返回值；
- 本轮对 31 的现有 shell history 和 00:30 至 01:00 audit 进行了只读筛选，没有恢复出所述 IPMI 命令；这可能是非交互 SSH 命令未写入 history 或审计未覆盖，不能作为“没有执行”的反证；
- 第一次 BMC 不可达的精确开始时间，以及 `.52` 外部可达的最后时间；
- 第二次 BMC 事故前是否也发生过 IPMI 或管理接口探测；
- 约 13:00 至 17:20 之间是否存在任何 BMC 可达性记录；若存在 16:00 左右的独立记录，才能把 BMC 故障窗口进一步收窄；
- BMC 当时是 ICMP、HTTPS、SSH、RMCP 全部失败，还是仅某一服务失败；
- BMC 使用独立端口还是 shared LAN；
- 交换机端口是否 link-down、MAC 是否漂移、VLAN/ARP 是否异常；
- 现场按键是短按 reset、长按 power，还是同时断开了 AC/待机电源；
- BMC 是否重启、其 reboot reason 与 SEL 中是否存在 watchdog、电源或平台事件。

## 8. 综合判断

不能把两次事件概括为一个已经查明的“服务器硬件故障”。更准确的事故模型是：

```text
52 Host:
  事故一 = 22:57 已证实的 F2FS debug 状态读取 bug
           + 00:42 Host 仍在运行
           + 更晚时刻失联，最终机制未完全保存

  事故二 = 已证实的 CSGC 异步卸载生命周期 bug
           -> late NVMe completion 访问已置空 node_inode

62 BMC:
  第一次 = 探测前 HTTPS 正常
           -> 多次外部只读 IPMI 请求失败
           -> HTTPS reset / 最终不可达
           探测触发旧固件异常是首要但未证实的假设

  第二次 = 13:00 网页已恢复
           -> 16:08 Host 发生独立、确定的 F2FS Oops
           -> 17:20 首次发现 BMC 与 Host 均不可达
           BMC 实际失效时刻仍位于 13:00-17:20
```

因此，本轮最重要的工程结论是：**Host 和 BMC 必须分开处理。继续 Filebench 前应先修复 CSGC 卸载生命周期；第一次 BMC 事故则应按“疑似由 IPMI 探测触发”保存现场和追查原始命令/BMC 日志。现有证据反而表明两者并非同时开始失效。**

## 9. 建议的下一步取证与修复

以下均未在本轮执行：

1. 保持故障 Host 分支停用；9 月 14 日的修复分支已经补齐普通写 BIO 的卸载生命周期等待。
2. 对修复版仅做专门的 mount/workload/sync/umount 生命周期验证；必须得到新的明确授权后才能运行。
3. 从 Mac、31 和 Jump181 导出 00:42 前后的原始命令/会话日志，确定 IPMI 版本、参数、次数、并发度、超时和每次状态转移；不要重新发送这些请求来补证据。
4. 核对第二次 BMC 失联前是否也有管理接口探测，并查找 13:00 至 17:20 之间的 BMC 可达性记录。若没有第二次探测，说明“IPMI 是唯一根因”的解释需要进一步下调。
5. 从第三台机器分别记录 `.52` 和 `.62` 的低频可达性、TCP 服务状态及时间戳，避免只能知道“后来都连不上”。
6. 取得交换机相应端口的 link、VLAN、MAC、错误计数与 flap 日志，并核对两个地址是否经过共同端口或共同上联。
7. 在单独授权的维护窗口读取 BMC SEL、BMC uptime/reset reason、固件版本和 dedicated/shared LAN 配置。本轮禁止访问 BMC，因此未做。
8. 明确两次现场操作是 reset 还是完整 AC power cycle。这个信息直接决定“BMC 是否应当被该操作重启”的判断。
9. 保留当前 pstore、audit 与失败批次目录；不要清理或覆盖。若再次发生，先从外部记录两个 IP 的独立状态，再进行现场操作。

## 10. 恢复状态

在 2026-09-08 16:33 CST 的只读检查中：

- 当前 boot ID：`9dae0280-d895-44d8-9802-7a6c2108e37b`；
- 当前启动时间：`2026-09-08 10:02:23`；
- D 状态进程数：0；
- F2FS 模块：未加载；
- 当前内核定向异常检查未发现新的 Oops、panic、AER 或 NVMe timeout。

这只能说明现场恢复后的当前内核状态稳定，不能证明 BMC 原因已消失，也不能证明下一次加载故障 F2FS 分支后不会复发。

在 2026-09-14 16:57 CST 的修复前只读检查中，当前 boot ID 为
`0d662153-83aa-4531-acee-6f24e372f6a9`，启动时间为 15:28:53，D 状态进程数为 0，
F2FS 模块未加载且 `/dev/nvme0n1` 存在。该状态适合离线修改和编译，但仍不等价于
修复后的压力测试结果。

## 11. 证据索引

本报告目录中的 `evidence/` 保存了只读副本：

- `pstore-20260907-debug-status-oops.txt`：事故一 pstore；
- `pstore-20260908-write-end-io-oops.txt`：事故二 pstore；
- `pstore-20260905-write-end-io-historical.txt`：事故二的历史同型崩溃；
- `pstore-20260903-pcie-fatal-counterexample.txt`：独立 PCIe fatal error 反例；
- `user-supplied-mac-agent-statement-20260907-0042.txt`：用户提供的 Mac agent 原始陈述；
- `host-audit-activity-20260907-0042.txt`：00:42 前后 Host 本地活动的 audit 摘要；
- `user-supplied-second-outage-timeline-20260907.txt`：第一次恢复和第二次发现失联的用户时间线；
- `batch-20260906-*`：事故一批次 provenance、runner 与 teardown 诊断；
- `batch-20260907-*`：事故二批次 provenance、runner 与 teardown 诊断；
- `SHA256SUMS`：所有副本的校验值。

其他原始证据仍保留在：

- `/var/lib/systemd/pstore/7682436602542/dmesg.txt`；
- `/var/lib/systemd/pstore/7682702259154/dmesg.txt`；
- `/var/lib/systemd/pstore/7681983436248/dmesg.txt`；
- `/var/lib/systemd/pstore/7685281575994/dmesg.txt`；
- `/var/lib/systemd/pstore/7681337653555/dmesg.txt`；
- `/var/log/audit/audit.log*`；
- `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260906_223615`；
- `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260907_155710`；
- `/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-filebench-mcsgc-ab/20260914_144442`；
- Host 故障源码 worktree：`/home/xin/work-xie/mcsgc-real/linux-cs-filebench-node-readahead-ab-20260906`。

本报告没有通过访问 BMC 获得任何证据；关于 BMC 的已证实事实仅来自用户外部观察，其他内容均已明确标为推测或未知。
