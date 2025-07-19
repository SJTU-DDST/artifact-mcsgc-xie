#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import os
import re

# 只需在当前目录下运行，并保证脚本路径正确
fig_path = "./figs/timeline_mcsgc_only.png"
fig_size = (10, 2.3)

# CSGC 的标签和配色
csgc_label = 'CSGC'
csgc_color = '#E67365'
marker_cs = 'o'
markersize = 2.5

# 字体大小设置（保持与原程序一致）
fontsize1 = 13
fontsize2 = 11
plt.rcParams['axes.titlesize'] = fontsize1
plt.rcParams['axes.labelsize'] = fontsize1
plt.rcParams['xtick.labelsize'] = fontsize2
plt.rcParams['ytick.labelsize'] = fontsize2
plt.rcParams['legend.fontsize'] = fontsize2

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

# 只加载 CSGC 那一份日志
# "/home/xin/work-xie/csgc/xin_scripts/outputs-cs/20250427_235503/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log",
csgc_log = ["/home/xin/work-xie/csgc/xin_scripts/outputs-cs-t2/20250604_145649/filebench_fileserver_4t_60G_1M_54k_s8/filebench.log"]
csgc_time, csgc_op, csgc_opps = extract_timeline_data_filebench(csgc_log)

# 计算相对时间（从 0 开始），并将 ops/s 转为 kop/s
start = csgc_time[0]
csgc_time = [int(t - start) for t in csgc_time]
csgc_opps_k = [o/1000 for o in csgc_opps]

# 计算总操作量（千次）
csgc_total_kop = sum(csgc_op)//1000

# 绘图
fig = plt.figure(figsize=fig_size)
# 虽只用一个子图，但仍保留 1×2 布局以保持宽度比例；右边留白
gs = GridSpec(1, 2, figure=fig, width_ratios=[1,1])
ax = fig.add_subplot(gs[0, 0])

ax.plot(
    csgc_time, csgc_opps_k,
    label=csgc_label,
    color=csgc_color,
    marker=marker_cs,
    markersize=markersize
)

# 保持原程序的坐标范围和刻度
ax.set_xlim(0, 400)
y_max = max(csgc_opps_k)
ax.set_ylim(0, y_max * 1.2)
ax.set_yticks(range(0, int(y_max)+1, 1))

# 在折线末尾标注总操作量
ax.text(
    csgc_time[-1] + 10,
    csgc_opps_k[-1],
    f'total={csgc_total_kop:.0f}K',
    color=csgc_color,
    ha='left', va='bottom',
    fontsize=fontsize2
)

ax.legend()
ax.set_xlabel('Time (s)\n(a)')
ax.set_ylabel('Throughput\n(kop/s)')
ax.grid(True)

# 留空的右子图（可选，不影响左图显示）
# ax2 = fig.add_subplot(gs[0, 1])
# ax2.axis('off')

plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight", format="png")
