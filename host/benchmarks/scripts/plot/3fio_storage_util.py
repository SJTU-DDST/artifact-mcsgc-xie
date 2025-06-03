import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import numpy as np
import os
import re
from utils import *

# should run in "host/benchmarks/scripts"

fig_path = "figs/3fio_storage_util.pdf"
fig_size = (6, 2.5)
fig = plt.figure(figsize=fig_size)
gs = GridSpec(1, 2, figure=fig, height_ratios=[1], width_ratios=[1, 1])

origc_label = 'F2FS'
iplfs_label = 'IPLFS'
csgc_label = 'CSGC'

origc_color = '#5E96E6'
# iplfs_color = '#C9E561'
iplfs_color = '#7CCD7C'
csgc_color = '#E67365'

fontsize1 = 13
fontsize2 = 11
fontsize2_ = 10
fontsize3 = 8
plt.rcParams['axes.titlesize'] = fontsize1
plt.rcParams['axes.labelsize'] = fontsize1
plt.rcParams['xtick.labelsize'] = fontsize2
plt.rcParams['ytick.labelsize'] = fontsize2
plt.rcParams['legend.fontsize'] = fontsize2_
# plt.rcParams['font.weight'] = 'bold'

benchmarks = [    
    "fio_randwrite_s8_0.6_random", 
    "fio_randwrite_s8_0.7_random", 
    "fio_randwrite_s8_0.8_random", 
    "fio_randwrite_s8_0.9_random", 
    "fio_randwrite_s8_0.95_random",
]

def autolabel(rects, ax, off):
    for rect in rects:
        height = rect.get_height()
        ax.annotate(f'{height:.1f}',
                    xy=(rect.get_x() + rect.get_width()*(0.5+off), height),
                    xytext=(0, 1),  # 1 points vertical offset
                    textcoords="offset points",
                    ha='center', va='bottom', 
                    fontsize=fontsize3)

def get_workload_type(file_path):
    file_name_with_ext = os.path.basename(file_path)
    file_name, file_ext = os.path.splitext(file_name_with_ext)
    return file_name
            
def extract_iops_data_fio(raw_data_dirs):
    latencies = []
    patterns = [
        r'\s+write: IOPS=(\d*\.?\d*)',
    ]
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            contents = f.read()
            for pattern in patterns:
                matches = re.findall(pattern, contents)
                latencies.extend(matches)
            
    return [float(latency) for latency in latencies]

def extract_waf_data(raw_data_dirs):
    wafs = []
    patterns = [
        r'\s+physical WAF:\s+(\d+)',
    ]
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            contents = f.read()
            for pattern in patterns:
                matches = re.findall(pattern, contents)
                wafs.extend(matches)
            
    return [float(waf)/1000 for waf in wafs]


storage_util = [0.6, 0.7, 0.8, 0.9, 0.95]  # X-axis: storage utilization
xtick_labels = [str(c) for c in storage_util]
x = np.arange(len(storage_util))
x_min = min(x) - 0.5
x_max = max(x) + 0.5




origc_thrupu_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, False) for experiment_name in benchmarks
]
iplfs_thrupu_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_iplfs, False) for experiment_name in benchmarks
]
csgc_thrupu_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, False) for experiment_name in benchmarks
]
origc_thrupu_vs_storage_util = extract_iops_data_fio(origc_thrupu_vs_storage_util_raw_data_path)
iplfs_thrupu_vs_storage_util = extract_iops_data_fio(iplfs_thrupu_vs_storage_util_raw_data_path)
csgc_thrupu_vs_storage_util = extract_iops_data_fio(csgc_thrupu_vs_storage_util_raw_data_path)
print("Throughput v.s. Storage Utilization:")
print(origc_thrupu_vs_storage_util)
print(iplfs_thrupu_vs_storage_util)
print(csgc_thrupu_vs_storage_util)
kop=1000
origc_thrupu_vs_storage_util = [i/kop for i in origc_thrupu_vs_storage_util]
iplfs_thrupu_vs_storage_util = [i/kop for i in iplfs_thrupu_vs_storage_util]
csgc_thrupu_vs_storage_util = [i/kop for i in csgc_thrupu_vs_storage_util]

# Fig 4_1 Throughput vs. Storage Utilization
y_max = max(origc_thrupu_vs_storage_util + iplfs_thrupu_vs_storage_util + csgc_thrupu_vs_storage_util) * 1.5
ax7 = fig.add_subplot(gs[0, 0])
ax7.set_ylim(0, y_max)
# ax7.set_ylim(0,max(csgc_thr_kiops + origc_thr_kiops)*1.55)
ax7.set_xlim(x_min, x_max)
ax7.plot(range(len(origc_thrupu_vs_storage_util)), origc_thrupu_vs_storage_util, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
ax7.plot(range(len(iplfs_thrupu_vs_storage_util)), iplfs_thrupu_vs_storage_util, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax7.plot(range(len(csgc_thrupu_vs_storage_util)), csgc_thrupu_vs_storage_util, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)

# Adding labels and title
ax7.set_xticks(x)
ax7.set_xticklabels(xtick_labels)
ax7.set_ylabel('Throughput(kop/s)')
# ax7.set_yticks(np.arange(0, 17, 4))
ax7.set_xlabel("Storage Utilization\n(a)")
# ax7.legend()
ax7.grid(True)



origc_wa_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, True) for experiment_name in benchmarks
]
iplfs_wa_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_iplfs, True) for experiment_name in benchmarks
]
csgc_wa_vs_storage_util_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, True) for experiment_name in benchmarks
]
origc_wa_vs_storage_util = extract_waf_data(origc_wa_vs_storage_util_raw_data_path)
iplfs_wa_vs_storage_util = extract_waf_data(iplfs_wa_vs_storage_util_raw_data_path)
csgc_wa_vs_storage_util = extract_waf_data(csgc_wa_vs_storage_util_raw_data_path)
print("WAF v.s. Storage Utilization:")
print(origc_wa_vs_storage_util)
print(iplfs_wa_vs_storage_util)
print(csgc_wa_vs_storage_util)

# Fig 4_2 WA vs. Storage Utilization
ax8 = fig.add_subplot(gs[0, 1])
ax8.set_xlim(x_min, x_max)
ax8.set_ylim(1,max(origc_wa_vs_storage_util + iplfs_wa_vs_storage_util + csgc_wa_vs_storage_util)*1.2)
ax8.plot(range(len(origc_wa_vs_storage_util)), origc_wa_vs_storage_util, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
ax8.plot(range(len(iplfs_wa_vs_storage_util)), iplfs_wa_vs_storage_util, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax8.plot(range(len(csgc_wa_vs_storage_util)), csgc_wa_vs_storage_util, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)
ax8.set_xticks(x)
ax8.set_xticklabels(xtick_labels)
ax8.set_ylabel('Write Amplification')
# ax8.set_yscale('log',base=2)
# ax8.set_yticks([2**(i*2) for i in range(5)])
# ax8.set_yticks(np.arange(0, 17, 4))
ax8.set_xlabel("Storage Utilization\n(b)")
ax8.legend()
ax8.grid(True)


plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight", format="pdf")
