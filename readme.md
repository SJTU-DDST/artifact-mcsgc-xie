# 现在常跑的实验
con06: fio，单个大文件，4个线程各自随机写20GB
con01: filebench,54K个1M小文件 300秒  _4t_60G_1M_54k_ **但是三百秒可能跑不通，现在用下面这个更短的版本**
con21:filebench,和上面比就是时间缩短到150秒 fileserver_4t_60G_1M_54k_period_150s h

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
