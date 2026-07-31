# 现在常跑的实验
con06: fio，单个51GB大文件，4个线程各自随机写20GB
con01: filebench,54K个1M小文件 300秒  _4t_60G_1M_54k_ **但是三百秒可能跑不通，现在用下面这个更短的版本**
con21:filebench,和上面比就是时间缩短到150秒 fileserver_4t_60G_1M_54k_period_150s h

# 常用命令
## tmux
tmux attach -t log
即可回到之前的终端和正在运行的任务。duplicate session: log 只是说明同名会话已经存在，不代表损坏。


# 各个脚本的用法
## mydmesg.sh


### 作用

该脚本用于 **实时采集 Linux 内核日志（dmesg）并保存到文件**，适用于内核调试、文件系统实验（如 F2FS / CSGC / mCSGC）等场景。

主要功能：

* 自动生成 **不重名的日志文件**（格式：`name-N.log`）
* 备份当前 dmesg 到 `<output>.old.log`
* 清空内核 ring buffer，确保只记录**新产生的日志**
* 实时跟踪 dmesg 并写入文件
* 过滤少量 `systemd-journald` 噪声日志
* 按下 `Ctrl+C` 时：

  * 停止日志采集
  * 自动调用同目录下的 `finderror.py` 对日志进行分

### 用法

```bash
./mydmesg.sh [output_file]
```

* `output_file`：可选参数，指定日志文件名（可带路径）
* 若不提供，默认使用 `kern.log`

---

### 文件命名规则

输出文件会自动规范为：

```text
<name>-<number>.log
```

并保证不会覆盖已有文件，例如：

| 输入           | 实际输出                 |
| ------------ | -------------------- |
| `test`       | `test-1.log`         |
| `test.log`   | `test-1.log`         |
| `test-3.log` | `test-3.log`（若存在则递增） |
| `logs/a.log` | `logs/a-1.log`       |

---

### 运行流程

执行脚本后：

1. 备份当前 dmesg → `<output>.old.log`
2. 清空内核日志缓冲区
3. 开始实时记录新的 dmesg → `<output>`
4. 等待用户中断（Ctrl+C）

---

### 终止行为（Ctrl+C）

按下 `Ctrl+C` 后：

1. 停止 dmesg 采集
2. 自动执行：

```bash
python3 finderror.py <output_file>
```

3. 脚本退出

---

### 生成文件

运行后会生成：

```text
<output>.log            # 本次采集的内核日志
<output>.log.old.log    # 采集前的旧 dmesg 备份
```

---

### 典型使用方式

```bash
./trace_dmesg.sh gc.log
# 运行你的 workload / benchmark
# 按 Ctrl+C 结束并自动分析日志
```

---

### 依赖

* `sudo` 权限（用于访问 dmesg）
* `python3`（用于运行 `finderror.py`）
* 同目录下需存在：

  ```text
  finderror.py
  ```


