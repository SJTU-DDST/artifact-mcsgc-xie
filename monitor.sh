#!/bin/bash

# 输出标题，说明脚本功能
echo "实时监控 F2FS 内核线程状态"
echo "按 Ctrl+C 停止监控"

# 循环实时监控 F2FS 线程状态
while true; do
    # 获取当前的 F2FS 线程状态
    THREADS=$(ps -eLf | grep f2fs)
    
    # 将线程信息追加到终端中
    echo "$THREADS"
    
    # 每隔 1 秒钟刷新一次
    sleep 1
done
