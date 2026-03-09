
# 各个脚本的用法
## mydmesg.sh

### 用法

```bash
./trace_dmesg.sh [output_file]
```
* `output_file`：指定日志输出文件
### 作用
该脚本用于 **实时记录新的 Linux 内核日志（dmesg）到文件**，常用于内核调试或实验日志采集。
还会备份当前内核 ring buffer 到 `<output_file>.old.log`
生成的日志文件 **只包含脚本启动之后产生的内核日志**。
### 生成文件
```
<output_file>           # 实时记录的新 dmesg
<output_file>.old.log   # 脚本启动前的 dmesg 备份
```
### 典型使用场景

在运行 benchmark 或测试前启动该脚本，用于记录内核调试信息。
