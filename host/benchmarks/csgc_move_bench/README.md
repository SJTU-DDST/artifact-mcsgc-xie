# CSGC Move Plan Microbenchmark

## 1. 目标

这个工具用于测量当前 OpenSSD Move Plan v2 数据路径的理论供给和服务上限。它不挂载 F2FS，也不等待真实文件系统产生 GC，而是由 Host 持续构造合法的 `src_blkaddr -> dst_blkaddr` 请求并直接发送给 OpenSSD。

测量链路为：

```text
Host userspace
  -> NVMe CSGC write（32 KiB Move Plan）
  -> OpenSSD CS request queue
  -> CS worker
  -> serialized CSIO / CDMA migration
  -> NVMe CSGC read（32 KiB result）
  -> Host validation
```

因此，它排除了以下开销：

- F2FS 空间压力判断和 victim 选择；
- `gc_lock` 等待；
- checkpoint；
- Host PRE 中的有效块识别、脏页回写和目标块预分配；
- Host POST 中的 dnode、SIT 和 summary 更新；
- fio 的普通写入和文件系统元数据操作。

它保留了真实的 Host/Device NVMe 参数传输、设备请求排队、CS worker、CSIO、CDMA 和结果回传。因此，这个结果是 **Move Plan/控制器数据路径上限**，不是 F2FS 端到端性能。

## 2. 前置条件

- 使用支持 Move Plan v2 的 Host NVMe 驱动和 OpenSSD 固件。
- OpenSSD 必须启用 `CONFIG_CSGC_MOVE_PLAN_V2=1`。
- 推荐启用 `CONFIG_CSGC_MOVE_PLAN_FAST_UNSAFE=1`，测量当前性能优先路径。
- 诊断测试推荐启用 `CONFIG_CSGC_MOVE_PLAN_BREAKDOWN=1`。
- 使用 `CONFIG_CSGC_ACTIVE_WORKERS=1` 或 `2` 构建固件，以分别测试 SSD1t 和 SSD2t。
- 修改上述共享配置后，必须一起重编译并部署 `ftl/nvme`、`cs`、`cs_worker1` 和 `io_worker/emu`。

当前测试关闭 NAND latency emulation（`--nand 0`）。结果仍包含真实固件、DDR 和 AXI CDMA 路径，但不表示真实 NAND 介质延迟。

## 3. 运行方法

先做冒烟测试：

```bash
cd /home/xin/artifact-csgc/host/benchmarks/scripts
./run_csgc_moveplan_microbenchmark.sh /dev/nvme0n1 smoke
```

再测主要 QD 矩阵：

```bash
CSGC_MP_BENCH_EXPECTED_WORKERS=1 \
./run_csgc_moveplan_microbenchmark.sh /dev/nvme0n1 core
```

切换并重启 SSD2t 固件后：

```bash
CSGC_MP_BENCH_EXPECTED_WORKERS=2 \
./run_csgc_moveplan_microbenchmark.sh /dev/nvme0n1 core
```

完整矩阵会测试 `moves=32/128/512` 与 `QD=1/2/4/8/16/32`：

```bash
CSGC_MP_BENCH_EXPECTED_WORKERS=1 \
CSGC_MP_BENCH_RUNTIME=30 \
./run_csgc_moveplan_microbenchmark.sh /dev/nvme0n1 full
```

脚本应由普通用户运行，内部只对设备命令使用 `sudo`。它会要求输入完整的破坏性确认字符串。自动运行时可设置 `CSGC_MP_BENCH_ASSUME_YES=1`。

## 4. 设备初始化

脚本会执行：

```text
ssd-admin --l2p 2 --nand 0 --dsm 0
mkfs.f2fs -f -s 8
nvme fs-ready -f 1
mkfs.f2fs -f -s 8
```

第二次 `mkfs` 只用于在设备 reset 后重新写入与设备端几何一致的 F2FS superblock。之后文件系统不会被挂载。benchmark 会重复覆盖 main area 中彼此分离的 source/destination segment 对。

该流程会销毁命名空间中的全部已有数据。测试结束后，设备保持未挂载且内容不可用于正常文件系统实验，必须重新 reset/mkfs。

## 5. 输出

结果保存在：

```text
host/benchmarks/scripts/outputs-csgc-moveplan-bench/<timestamp>_<device>_<profile>/
```

主要文件：

- `results.csv`：所有 case 的 Host 吞吐、延迟及设备统计汇总；
- `cases/<case>/runN/command.log`：客户端原始输出；
- `cases/<case>/runN/ssd-stat.log`：去除 NUL padding 后的 OpenSSD 日志；
- `terminal.log`：完整运行过程；
- `artifact-working-tree.patch`、`openssd-working-tree.patch`：实验时源码状态；
- `dmesg.before.log`、`dmesg.after.log`：运行前后内核日志。

Host 关键指标：

- `requests_s`：每秒完成的 Move Plan 请求数；
- `logical_mib_s`：每秒完成迁移的数据量；
- `estimated_dma_mib_s`：按每个块一次读取、一次写入估算的 DDR/CDMA 总流量；
- `avg/p50/p95/p99/max_us`：一次完整 `CS write + device execution + CS read` 的 Host wall-clock 延迟。

设备关键指标：

- `supply_x10000`：在设备统计 span 内，存在 outstanding CSGC 的时间比例；
- `service_x10000`：有 outstanding 请求时，串行 CSIO 当前请求处于 active 的比例；
- `dma_x10000`：CSIO active 期间 CDMA 活跃比例；
- `device_srv_mib_s`：按 `current_ns` 归一化的服务速率；
- `device_good_mib_s`：按完整设备 span 归一化的有效迁移速率。

客户端 warmup 不计入 Host 吞吐和延迟，但会计入本 case 的设备端累计统计。因此脚本检查设备内部的 `declared == submitted == completed`，不会要求设备累计 move 数与 Host 正式窗口计数完全相等。

## 6. 如何判断理论上限

优先看 `moves=512` 的 QD 曲线：

1. `logical_mib_s` 随 QD 上升，随后进入平台，平台值就是当前 Move Plan 路径可达到的持续服务上限。
2. 若 `supply_x10000` 接近 `10000`，说明 Host microbenchmark 已经持续供给，结果不再受真实 F2FS 请求空档影响。
3. 若 SSD2t 与 SSD1t 的平台值相同，但两个 worker 都处理了请求，则底层串行 CSIO/CDMA 路径限制了双 worker 扩展。
4. 若 `device_srv_mib_s` 接近已有裸 CDMA benchmark，而 `device_good_mib_s` 也接近它，说明软件层已基本打满当前单 CDMA。
5. 小 `moves` 明显降低吞吐时，差值主要反映每请求固定开销和 batch 粒度，而不是数据搬迁带宽。

不要把该平台值直接当作 fio 可实现吞吐。真实系统还必须执行正常写入、Host PRE/POST 和 F2FS 元数据维护；这个结果用于判断“如果 Host 能持续供给请求，OpenSSD 最多能处理多少 CSGC 数据”。

建议把测试分成两轮：先启用 Move Plan breakdown，确认 `supply_x10000` 接近饱和且请求全部成功；再使用关闭可选统计的 performance firmware，并设置 `CSGC_MP_BENCH_REQUIRE_STATS=0` 重跑 `core`，以 Host `logical_mib_s` 测量最低扰动的性能上限。
