import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import numpy as np
import os
import re
from utils import *

# should run in "host/benchmarks/scripts"

fig_path = "figs/1perf_overall.png"
fig_size = (10, 2.3)

origc_label = 'F2FS'
origc_color = '#A8C3E6'
iplfs_label = 'IPLFS'
# iplfs_color = '#DBE6AB'
iplfs_color = '#9AFF9A'
csgc_label = 'CSGC'
csgc_color = '#E6B4AE'

fontsize1 = 13
fontsize2 = 11
plt.rcParams['axes.titlesize'] = fontsize1
plt.rcParams['axes.labelsize'] = fontsize1
plt.rcParams['xtick.labelsize'] = fontsize1
plt.rcParams['ytick.labelsize'] = fontsize1
plt.rcParams['legend.fontsize'] = fontsize2
# plt.rcParams['font.weight'] = 'bold'

benchmarks = [
    "filebench_fileserver_4t_60G_1M_54k_s8", 
    "filebench_varmail_4t_60G_1M_54k_s8", 
    "ycsb_workloada_s8_0.86", 
    "ycsb_workloadf_s8_0.8", 
    "fio_randwrite_s8_0.86_random", 
    "fio_randwrite_s8_0.86_zipf:1.1"
]

def autolabel_val(rects, ax, vals):
    for i, rect in enumerate(rects):
        val = vals[i] / 1000
        height = rect.get_height()
        ax.annotate(f'{val:.1f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 1),  # 1 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom', 
                    fontsize=fontsize2)

def autolabel(rects, ax):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height:.1f}',
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 1),  # 1 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom', 
                    fontsize=fontsize2)

def get_workload_type(file_path):
    file_name_with_ext = os.path.basename(file_path)
    file_name, file_ext = os.path.splitext(file_name_with_ext)
    return file_name



origc_raw_data_dirs = [
    get_latest_data_dir(experiment_name, base_path_ori, False) for experiment_name in benchmarks
]

iplfs_raw_data_dirs = [
    get_latest_data_dir(experiment_name, base_path_iplfs, False) for experiment_name in benchmarks
]

csgc_raw_data_dirs = [
    get_latest_data_dir(experiment_name, base_path_cs, False) for experiment_name in benchmarks
]

def extract_perf_data(raw_data_dirs):
    data = []
    patterns = {
        "filebench": r'IO Summary:\s+\d*\.?\d*\s+ops\s+(\d*\.?\d*)\s+ops/s',
        "ycsb": r'Throughput\(ops/sec\),\s+(\d*\.?\d*)',
        "fio": r'\s+write: IOPS=(\d*\.?\d*k?)'
    }
    for raw_data_dir in raw_data_dirs:
        pattern = patterns[get_workload_type(raw_data_dir)]
        with open(raw_data_dir, 'r') as f:
            lines = f.readlines()
            for line in lines:
                match = re.search(pattern, line)
                if match:
                    perf = match.group(1)
                    perf = float(perf[:-1]) * 1000 if perf[-1] == 'k' else float(perf)
                    data.append(perf)
    return data


origc_data = extract_perf_data(origc_raw_data_dirs)
iplfs_data = extract_perf_data(iplfs_raw_data_dirs)
csgc_data = extract_perf_data(csgc_raw_data_dirs)

print("Raw perf data:")
print(origc_data)
print(iplfs_data)
print(csgc_data)

# normalize
origc_data_norm = origc_data.copy()
iplfs_data_norm = iplfs_data.copy()
csgc_data_norm = csgc_data.copy()
for i in range(len(origc_data)):
    base = csgc_data[i]
    origc_data_norm[i] = origc_data[i] / base
    iplfs_data_norm[i] = iplfs_data[i] / base
    csgc_data_norm[i] = csgc_data[i] / base

def geometry_average(val_list):
    mul = 1
    for i in range(len(val_list)):
        mul *= val_list[i]
    return mul ** (1/len(val_list))

print("Normalized:")
print(origc_data_norm)
print(iplfs_data_norm)
print(csgc_data_norm)
csgc_by_origc = [csgc_data_norm[i]/origc_data_norm[i] for i in range(len(csgc_data_norm))]
csgc_by_iplfs = [csgc_data_norm[i]/iplfs_data_norm[i] for i in range(len(csgc_data_norm))]
print("Outperforms IPLFS by:")
print(csgc_by_iplfs, "Average:", geometry_average(csgc_by_iplfs))
print("Outperforms F2FS by:")
print(csgc_by_origc, "Average:", geometry_average(csgc_by_origc))


fig = plt.figure(figsize=fig_size)
gs = GridSpec(1, 1, figure=fig, height_ratios=[1], width_ratios=[1])
# xlabels = ["fileserver\nkop/s", "varmail\nkop/s", "ycsb-A\nkop/s", "ycsb-F\nkop/s", "fio\nkop/s"]
xlabels = ["fileserver", "varmail", "ycsb-A", "ycsb-F", "fio-uniform", "fio-skewed"]
x = np.arange(len(xlabels))
ax1 = fig.add_subplot(gs[0, 0])
width = 0.22

ax1_rects1 = ax1.bar(
    x - width, origc_data_norm, width, 
    label=origc_label,
    color=origc_color,
    edgecolor='black',
    hatch='xx'
)
ax1_rects2 = ax1.bar(
    x, iplfs_data_norm, width, 
    label=iplfs_label,
    color=iplfs_color,
    edgecolor='black',
    hatch='//'
)
ax1_rects3 = ax1.bar(
    x + width, csgc_data_norm, width, 
    label=csgc_label,
    color=csgc_color,
    edgecolor='black',
    hatch='\\\\'
)

autolabel_val(ax1_rects1, ax1, origc_data)
autolabel_val(ax1_rects2, ax1, iplfs_data)
autolabel_val(ax1_rects3, ax1, csgc_data)

y_max = max(max(origc_data_norm), max(iplfs_data_norm), max(csgc_data_norm))
ax1.set_ylabel('Normalized\nThroughput(kop/s)')
ax1.set_xticks(x)
ax1.set_xticklabels(xlabels)
ax1.set_ylim(0, y_max * 1.7)
ax1.set_yticks(range(0, int(y_max + 1), 1))
ax1.legend()

plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight" , format="png")