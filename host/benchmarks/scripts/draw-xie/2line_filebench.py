#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import re
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

# ================= 用户配置 =================
# 要使用的数据点数量
data_point_number = 60
# 每条折线对应的日志文件路径列表

input1_label = 'mCSGC'
input1_paths = [
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-mcsgc/20250714_125712/filebench_fileserver_4t_60G_1M_54k_period_s8/filebench.log"
    #"/home/xin/work-xie/csgc/xin_scripts/test_data_imporatant/mcsgc/findgctime/filebench.log"
]
input2_label = 'CSGC'
input2_paths = [
    #"/home/xin/work-xie/csgc/xin_scripts/outputs-cs-t2/20250623_115622/"
    #"filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
    #"/home/xin/work-xie/csgc/xin_scripts/test_data_imporatant/csgc/20250707_114643/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"
    "/home/xin/artifact-csgc/host/benchmarks/scripts/outputs-csgc/20250714_115208/filebench_fileserver_4t_60G_1M_54k_period_s8/filebench.log"
]


# ================= 用户配置 =================


# 输出图片（PNG格式）及大小
fig_path = "./figs/compare_csgc-mcsgc40-0712.png"
fig_size = (10, 2.3)

# 对应的颜色和标记
input1_color  = '#E67365'
input1_marker = 'o'
input2_color  = '#65C295'
input2_marker = 's'
# ============================================

markersize = 2.5
fontsize1 = 13
fontsize2 = 11
plt.rcParams['axes.titlesize']   = fontsize1
plt.rcParams['axes.labelsize']   = fontsize1
plt.rcParams['xtick.labelsize']  = fontsize2
plt.rcParams['ytick.labelsize']  = fontsize2
plt.rcParams['legend.fontsize']  = fontsize2

def get_unique_path(path):
    """
    如果 path 已存在，则在文件名上加数字后缀直到不重复。
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

# === 提取原始数据 ===
t1_raw, op1_raw, opps1_raw = extract_timeline_data_filebench(input1_paths)
t2_raw, op2_raw, opps2_raw = extract_timeline_data_filebench(input2_paths)

# === 点数检查 ===
def check_and_truncate(label, t_raw, op_raw, opps_raw):
    if len(t_raw) < data_point_number:
        print(f"Error: {label} only has {len(t_raw)} data points, fewer than required {data_point_number}.")
        sys.exit(1)
    # 截取前 data_point_number 个点
    t = t_raw[:data_point_number]
    op = op_raw[:data_point_number]
    opps = opps_raw[:data_point_number]
    # 零值检查（只看前 data_point_number 个点）
    if any(x == 0 for x in opps):
        print(f"Error: {label} has zero throughput in the first {data_point_number} points.")
        sys.exit(1)
    return t, op, opps

t1, op1, opps1 = check_and_truncate(input1_label, t1_raw, op1_raw, opps1_raw)
t2, op2, opps2 = check_and_truncate(input2_label, t2_raw, op2_raw, opps2_raw)

# === 数据归一化与单位转换 ===
start1      = t1[0]
t1          = [int(x - start1) for x in t1]
opps1_k     = [x/1000 for x in opps1]
total1_kop  = sum(op1)//1000

start2      = t2[0]
t2          = [int(x - start2) for x in t2]
opps2_k     = [x/1000 for x in opps2]
total2_kop  = sum(op2)//1000

# === 绘图 ===
fig = plt.figure(figsize=fig_size)
gs  = GridSpec(1, 2, figure=fig, width_ratios=[1,1])
ax  = fig.add_subplot(gs[0, 0])

ax.plot(
    t1, opps1_k,
    label=input1_label,
    color=input1_color,
    marker=input1_marker,
    markersize=markersize
)
ax.plot(
    t2, opps2_k,
    label=input2_label,
    color=input2_color,
    marker=input2_marker,
    markersize=markersize
)

ax.set_xlim(0, max(t1[-1], t2[-1]) + 20)
ymax = max(max(opps1_k), max(opps2_k))
ax.set_ylim(0, ymax * 1.2)
ax.set_yticks(range(0, int(ymax) + 1, 1))

# 在折线末尾标注 total
text1 = f"total={total1_kop:.0f}K"
ax.text(
    t1[-1] + 10, opps1_k[-1],
    text1,
    color=input1_color, ha='left', va='bottom',
    fontsize=fontsize2
)
text2 = f"total={total2_kop:.0f}K"
ax.text(
    t2[-1] + 10, opps2_k[-1],
    text2,
    color=input2_color, ha='left', va='bottom',
    fontsize=fontsize2
)

# 打印到终端
print(f"{input1_label}: {text1}")
print(f"{input2_label}: {text2}")

ax.legend()
ax.set_xlabel('Time (s)\n(a)')
ax.set_ylabel('Throughput\n(kop/s)')
ax.grid(True)

# 保留右侧空白以保持布局
# ax2 = fig.add_subplot(gs[0, 1])
# ax2.axis('off')

plt.tight_layout()
save_path = get_unique_path(fig_path)
plt.savefig(save_path, bbox_inches="tight", format="png")
print(f"Saved figure to {save_path}")
