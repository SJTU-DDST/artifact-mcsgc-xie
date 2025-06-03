import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import numpy as np
import os
import re
from utils import *

# should run in "host/benchmarks/scripts"

fig_path = "figs/5fio_skewness.pdf"
fig_size = (6, 2.5)
# fig, axes = plt.subplots(1, 2, figsize=fig_size)
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
    "fio_randwrite_s1_0.86_random", 
    "fio_randwrite_s2_0.86_zipf:0.3", 
    "fio_randwrite_s2_0.86_zipf:0.7", 
    "fio_randwrite_s2_0.86_zipf:0.9", 
    "fio_randwrite_s2_0.86_zipf:1.1", 
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
        r'\s+write: IOPS=(\d*\.?\d*k?)',
    ]
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            contents = f.read()
            for pattern in patterns:
                matches = re.findall(pattern, contents)
                
                latencies.extend(matches)
            
    return [(float(perf[:-1]) * 1000 if perf[-1] == 'k' else float(perf)) for perf in latencies]

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


# skewness = ['unif.','zipf/0.3','zipf/0.7','zipf/0.9','zipf/1.1']  # X-axis: skewness
skewness = ['uni.','z/0.3','z/0.7','z/0.9','z/1.1']  # X-axis: skewness
xtick_labels = skewness
x = np.arange(len(skewness))
x_min = min(x) - 0.5
x_max = max(x) + 0.5


origc_thrupu_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, False) for experiment_name in benchmarks
]
iplfs_thrupu_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_iplfs, False) for experiment_name in benchmarks
]
csgc_thrupu_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, False) for experiment_name in benchmarks
]


origc_thrupu_vs_skewness = extract_iops_data_fio(origc_thrupu_vs_skewness_raw_data_path)
iplfs_thrupu_vs_skewness = extract_iops_data_fio(iplfs_thrupu_vs_skewness_raw_data_path)
csgc_thrupu_vs_skewness = extract_iops_data_fio(csgc_thrupu_vs_skewness_raw_data_path)
print("Throughput v.s. Write distribution:")
print(origc_thrupu_vs_skewness)
print(iplfs_thrupu_vs_skewness)
print(csgc_thrupu_vs_skewness)
kop=1000
origc_thrupu_vs_skewness = [i/kop for i in origc_thrupu_vs_skewness]
iplfs_thrupu_vs_skewness = [i/kop for i in iplfs_thrupu_vs_skewness]
csgc_thrupu_vs_skewness = [i/kop for i in csgc_thrupu_vs_skewness]

# Fig 4_1 Throughput vs. Storage Utilization
y_max = max(origc_thrupu_vs_skewness + iplfs_thrupu_vs_skewness + csgc_thrupu_vs_skewness) * 1.5
ax9 = fig.add_subplot(gs[0, 0])
# ax9 = axes[0]
ax9.set_ylim(0, y_max)
# ax9.set_ylim(0,max(csgc_thr_kiops + origc_thr_kiops)*1.55)
ax9.set_xlim(x_min, x_max)
ax9.plot(range(len(origc_thrupu_vs_skewness)), origc_thrupu_vs_skewness, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
ax9.plot(range(len(iplfs_thrupu_vs_skewness)), iplfs_thrupu_vs_skewness, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax9.plot(range(len(csgc_thrupu_vs_skewness)), csgc_thrupu_vs_skewness, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)

# Adding labels and title
ax9.set_xticks(x)
ax9.set_xticklabels(xtick_labels)
# plt.xticks(rotation=15)
ax9.set_ylabel('Throughput(kop/s)')
ax9.set_yticks(np.arange(0, y_max, 5))
ax9.set_xlabel("Write Distribution\n(a)")
ax9.legend()
ax9.grid(True)


origc_wa_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, True) for experiment_name in benchmarks
]
iplfs_wa_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_iplfs, True) for experiment_name in benchmarks
]
csgc_wa_vs_skewness_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, True) for experiment_name in benchmarks
]

origc_wa_vs_skewness = extract_waf_data(origc_wa_vs_skewness_raw_data_path)
iplfs_wa_vs_skewness = extract_waf_data(iplfs_wa_vs_skewness_raw_data_path)
csgc_wa_vs_skewness = extract_waf_data(csgc_wa_vs_skewness_raw_data_path)
print("WAF v.s. Storage Utilization:")
print(origc_wa_vs_skewness)
print(iplfs_wa_vs_skewness)
print(csgc_wa_vs_skewness)

# Fig 4_2 WA vs. Storage Utilization
ax10 = fig.add_subplot(gs[0, 1])
# ax10 = axes[1]
ax10.set_xlim(x_min, x_max)
ax10.set_ylim(1,max(origc_wa_vs_skewness + iplfs_wa_vs_skewness + csgc_wa_vs_skewness)*1.2)
ax10.plot(range(len(origc_wa_vs_skewness)), origc_wa_vs_skewness, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
ax10.plot(range(len(iplfs_wa_vs_skewness)), iplfs_wa_vs_skewness, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax10.plot(range(len(csgc_wa_vs_skewness)), csgc_wa_vs_skewness, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)
ax10.set_xticks(x)
ax10.set_xticklabels(xtick_labels)
ax10.set_ylabel('Write Amplification')
# ax10.set_yscale('log',base=2)
ax10.set_yticks(np.arange(1, max(origc_wa_vs_skewness + iplfs_wa_vs_skewness + csgc_wa_vs_skewness)*1.2, 0.2))
# ax10.set_yticks([2**(i*2) for i in range(5)])
# ax10.set_yticks(np.arange(0, 17, 4))
ax10.set_xlabel("Write distribution\n(b)")
# ax10.legend()
ax10.grid(True)


plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight", format="pdf")
# plt.savefig(fig_path, format="pdf")
