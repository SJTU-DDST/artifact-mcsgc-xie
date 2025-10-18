#!/bin/bash

# 用于查找进程的所有子进程、子线程及其状态的函数
# 参数1: 父进程的 PID
# 参数2: 子进程的深度
# 参数3: 已访问的PID集合
get_process_tree() {
    local pid=$1
    local depth=$2
    local visited_pids=$3

    # 如果进程已经访问过，跳过
    if [[ "$visited_pids" =~ "$pid" ]]; then
        return
    fi

    # 将当前进程PID添加到已访问的PID列表
    visited_pids="$visited_pids $pid"

    # 获取当前进程的状态信息和命令
    # 排除内核线程（如 kworker, rcu_gp 等）
    if [[ "$(ps -p $pid -o cmd=)" != *"[kworker"* ]] && [[ "$(ps -p $pid -o cmd=)" != *"[rcu_gp"* ]]; then
        echo "Depth $depth: Process PID: $pid"
        ps -p $pid -o pid,ppid,stat,etime,cmd --sort=etime
    fi

    # 查找当前进程的所有子进程
    child_pids=$(ps --ppid $pid -eo pid --sort=pid)

    # 如果有子进程，递归查看子进程的状态
    for child_pid in $child_pids; do
        # 跳过父进程的PID
        if [[ $child_pid -gt 1 && $child_pid -ne $pid ]]; then
            # 获取子进程状态信息
            echo "  "
            echo "  Depth $depth: Child Process PID: $child_pid"
            get_process_tree $child_pid $((depth+1)) "$visited_pids"
        fi
    done

    # 查找所有线程（包括内核线程）
    thread_pids=$(ps -eLf | awk -v pid=$pid '$3 == pid {print $2}')
    for thread_pid in $thread_pids; do
        # 过滤掉内核线程
        if [[ "$(ps -p $thread_pid -o cmd=)" != *"[kworker"* ]] && [[ "$(ps -p $thread_pid -o cmd=)" != *"[rcu_gp"* ]]; then
            echo "    Thread PID: $thread_pid"
            ps -p $thread_pid -o pid,ppid,stat,etime,cmd
            # 查看线程的调度信息，使用 chrt 或 taskset 等工具
            echo "    Scheduling Info for Thread PID $thread_pid:"
            chrt -p $thread_pid
        fi
    done
}

# 使用输入的PID启动递归查询
if [ $# -ne 1 ]; then
    echo "Usage: $0 <PID>"
    exit 1
fi

root_pid=$1

echo "Fetching process and thread details for PID: $root_pid"
get_process_tree $root_pid 1 ""
