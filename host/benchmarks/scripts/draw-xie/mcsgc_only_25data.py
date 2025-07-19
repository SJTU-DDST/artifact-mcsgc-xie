#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import os
import re
import sys  # ← 新增，用于退出

# 只需在当前目录下运行，并保证脚本路径正确
fig_path = "./figs/mcsgc-compare-25.png"   # ← 改为 PNG
fig_size = (10, 2.3)

# CSGC 的标签和配色
csgc_label  = 'CSGC'
csgc_color  = '#E67365'
marker_cs   = 'o'

# mCSGC-t2（原 mCSGC）
mcs_t2_label    = 'mCSGC-t2'         
mcs_t2_color    = '#65C295'
marker_mcs_t2   = 's'

# mCSGC-t1（新增）
mcs_t1_label    = 'mCSGC-t1'        
mcs_t1_color    = '#6595C2'          
marker_mcs_t1   = '^'                

markersize = 2.5

# 字体大小设置（保持与原程序一致）
fontsize1 = 13
fontsize2 = 11
plt.rcParams['axes.titlesize'] = fontsize1
plt.rcParams['axes.labelsize'] = fontsize1
plt.rcParams['xtick.labelsize'] = fontsize2
plt.rcParams['ytick.labelsize'] = fontsize2
plt.rcParams['legend.fontsize'] = fontsize2

def get_unique_path(path):
    """
    如果 path 已存在，则：
      1. 如果文件名本身带 "_<数字>" 后缀，就把该数字加1；
      2. 否则在文件名后加 "_1"；
    重复检测，直到找到一个不存在的文件名，返回这个新路径。
    """
    directory, filename = os.path.split(path)
    name, ext = os.path.splitext(filename)
    m = re.match(r'^(.*)_(\d+)$', name)
    if m:
        base_prefix = m.group(1)
        counter = int(m.group(2))
    else:
        base_prefix = name
        counter = 0

    while True:
        if counter == 0:
            candidate_name = base_prefix
        else:
            candidate_name = f"{base_prefix}_{counter}"
        candidate = os.path.join(directory, candidate_name + ext)
        if not os.path.exists(candidate):
            return candidate
        counter += 1

def extract_timeline_data_filebench(raw_data_paths):
    """提取 time(sec)、累计 ops、当前 ops/s"""
    times, ops, opps = [], [], []
    pattern = r'(\d*.?\d*):\s+IO Summary:\s+(\d*\.?\d*)\s+ops\s+(\d*\.?\d*)\s+ops/s'
    for path in raw_data_paths:
        with open(path, 'r') as f:
            for line in f:
                m = re.search(pattern, line)
                if m:
                    times.append(float(m.group(1)))
                    ops.append(float(m.group(2)))
                    opps.append(float(m.group(3)))
    return times, ops, opps

def limit_data(name, times, ops, opps, required=25):
    """
    如果 times 长度 < required，打印错误并退出；
    否则只返回前 required 个数据点。
    """
    if len(times) < required:
        print(f"Error: {name} only has {len(times)} data points, need at least {required}.")
        sys.exit(1)
    return times[:required], ops[:required], opps[:required]

# 日志路径配置
csgc_log = [
    # "/home/xin/work-xie/csgc/xin_scripts/outputs-cs/20250427_235503/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
    "/home/xin/work-xie/csgc/xin_scripts/test_data_imporatant/csgc/20250707_114643/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
]
mcs_t2_log = [
    "/home/xin/work-xie/csgc/xin_scripts/outputs-cs-t2/20250630_062752/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
]
mcs_t1_log = [
    "/home/xin/work-xie/csgc/xin_scripts/outputs-cs-t2/20250604_145649/"
    "filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
]

# 提取原始数据
csgc_time, csgc_op,  csgc_opps  = extract_timeline_data_filebench(csgc_log)
mcs_t2_time, mcs_t2_op, mcs_t2_opps = extract_timeline_data_filebench(mcs_t2_log)
mcs_t1_time, mcs_t1_op, mcs_t1_opps = extract_timeline_data_filebench(mcs_t1_log)

# 只保留前 25 个数据点，并在不足时报错
csgc_time, csgc_op, csgc_opps = limit_data('CSGC',   csgc_time,   csgc_op,   csgc_opps)
mcs_t2_time, mcs_t2_op, mcs_t2_opps = limit_data('mCSGC-t2', mcs_t2_time, mcs_t2_op, mcs_t2_opps)
mcs_t1_time, mcs_t1_op, mcs_t1_opps = limit_data('mCSGC-t1', mcs_t1_time, mcs_t1_op, mcs_t1_opps)

# 归一化时间起点并转换单位
start_cs    = csgc_time[0]
csgc_time   = [int(t - start_cs)  for t in csgc_time]
csgc_opps_k = [o/1000              for o in csgc_opps]
csgc_total_kop = sum(csgc_op)//1000

start_t2    = mcs_t2_time[0]
mcs_t2_time = [int(t - start_t2)    for t in mcs_t2_time]
mcs_t2_opps_k = [o/1000             for o in mcs_t2_opps]
mcs_t2_total_kop = sum(mcs_t2_op)//1000

start_t1    = mcs_t1_time[0]
mcs_t1_time = [int(t - start_t1)    for t in mcs_t1_time]
mcs_t1_opps_k = [o/1000             for o in mcs_t1_opps]
mcs_t1_total_kop = sum(mcs_t1_op)//1000

# 绘图
fig = plt.figure(figsize=fig_size)
gs = GridSpec(1, 2, figure=fig, width_ratios=[1,1])
ax = fig.add_subplot(gs[0, 0])

# 绘制三条曲线
ax.plot(csgc_time, csgc_opps_k, label=csgc_label, color=csgc_color,
        marker=marker_cs, markersize=markersize)
ax.plot(mcs_t2_time, mcs_t2_opps_k, label=mcs_t2_label, color=mcs_t2_color,
        marker=marker_mcs_t2, markersize=markersize)
ax.plot(mcs_t1_time, mcs_t1_opps_k, label=mcs_t1_label, color=mcs_t1_color,
        marker=marker_mcs_t1, markersize=markersize)

# 坐标范围和刻度
ax.set_xlim(0, 400)
y_max = max(max(csgc_opps_k), max(mcs_t2_opps_k), max(mcs_t1_opps_k))
ax.set_ylim(0, y_max * 1.2)
ax.set_yticks(range(0, int(y_max) + 1, 1))

# 在折线末尾标注总操作量
ax.text(csgc_time[-1] + 10, csgc_opps_k[-1],
        f'total={csgc_total_kop:.0f}K', color=csgc_color,
        ha='left', va='bottom', fontsize=fontsize2)
ax.text(mcs_t2_time[-1] + 10, mcs_t2_opps_k[-1],
        f'total={mcs_t2_total_kop:.0f}K', color=mcs_t2_color,
        ha='left', va='bottom', fontsize=fontsize2)
ax.text(mcs_t1_time[-1] + 10, mcs_t1_opps_k[-1],
        f'total={mcs_t1_total_kop:.0f}K', color=mcs_t1_color,
        ha='left', va='bottom', fontsize=fontsize2)

ax.legend()
ax.set_xlabel('Time (s)\n(a)')
ax.set_ylabel('Throughput\n(kop/s)')
ax.grid(True)

plt.tight_layout()
save_path = get_unique_path(fig_path)
plt.savefig(save_path, bbox_inches="tight", format="png")
print(f"Saved figure to {save_path}")
