import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import numpy as np
import os
import re
from utils import *

# should run in "host/benchmarks/scripts"

fig_path = "figs/4fio_secsz.pdf"
fig_size = (10, 2.5)
fig = plt.figure(figsize=fig_size)
gs = GridSpec(1, 3, figure=fig, height_ratios=[1], width_ratios=[1, 1, 1])

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
    "fio_randwrite_s2_0.86_random", 
    "fio_randwrite_s4_0.86_random", 
    "fio_randwrite_s8_0.86_random", 
    "fio_randwrite_s16_0.86_random", 
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

def extract_migra_lat_data(raw_data_dirs, is_cs):
    latencies = []
    if is_cs:
        patterns = [r'<CSGC STAT>.*block migration:\s+(\d+)\s+ns']
    else:
        patterns = [r'<ORIGC STAT>.*block migration:\s+(\d+)\s+ns']
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            contents = f.read()
            for pattern in patterns:
                matches = re.findall(pattern, contents)
                latencies.extend(matches)
            
    return [float(latency) for latency in latencies]

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


section_sizes = [1, 2, 4, 8, 16]  # X-axis: F2FS section sizes (GC granularity)
xtick_labels = [str(c) for c in section_sizes]
x = np.arange(len(section_sizes))
x_min = min(x) - 0.5
x_max = max(x) + 0.5


origc_mig_lat_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, True) for experiment_name in benchmarks
]
csgc_mig_lat_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, True) for experiment_name in benchmarks
]
origc_mig_lat = extract_migra_lat_data(origc_mig_lat_raw_data_path, False)
csgc_mig_lat = extract_migra_lat_data(csgc_mig_lat_raw_data_path, True)
print("Migration latency v.s. Section size:")
print(origc_mig_lat)
print(csgc_mig_lat)
origc_mig_lat = [i/1000 for i in origc_mig_lat]
csgc_mig_lat = [i/1000 for i in csgc_mig_lat]

# Fig 3_1 Migration Latency vs. Section Size
ax4 = fig.add_subplot(gs[0, 0])
ax4.set_xlim(x_min, x_max)
ax4.plot(range(len(section_sizes)), origc_mig_lat, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
ax4.plot(range(len(section_sizes)), csgc_mig_lat, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)

for i in range(len(section_sizes)):
    ax4.text(i, csgc_mig_lat[i]+4,f'{csgc_mig_lat[i]:.1f}', ha='center', va='bottom', fontsize=fontsize3)

# ax4.text(0, csgc_mig_lat[0]+4,f'{csgc_mig_lat[0]:.1f}', ha='center', va='bottom', fontsize=fontsize3)
# ax4.text(4, csgc_mig_lat[4]+4,f'{csgc_mig_lat[4]:.1f}', ha='center', va='bottom', fontsize=fontsize3)

# Adding labels and title
ax4.set_xticks(x)
ax4.set_xticklabels(xtick_labels)
# ax4.set_yscale('log',base=2)
# ax4.set_yticks([2**(i*2) for i in range(5)])
ax4.set_ylabel('Migration\nLatency(us)')
ax4.set_xlabel("Section Size(#segments)\n(a)")
# ax4.legend()
ax4.grid(True)




origc_thrupu_vs_secsz_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, False) for experiment_name in benchmarks
]
# iplfs_thrupu_vs_secsz_raw_data_path = [
#     "scripts/outputs-iplfs/20250311_120950/fio_randwrite-20G-per-thread_s8_0.86/fio.log"
# ]
csgc_thrupu_vs_secsz_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, False) for experiment_name in benchmarks
]
origc_thrupu_vs_secsz = extract_iops_data_fio(origc_thrupu_vs_secsz_raw_data_path)
# iplfs_thrupu_vs_secsz = extract_iops_data_fio(iplfs_thrupu_vs_secsz_raw_data_path)
csgc_thrupu_vs_secsz = extract_iops_data_fio(csgc_thrupu_vs_secsz_raw_data_path)
print("Throughput v.s. Section size:")
print(origc_thrupu_vs_secsz)
# print(iplfs_thrupu_vs_secsz)
print(csgc_thrupu_vs_secsz)
kop=1000
origc_thrupu_vs_secsz = [i/kop for i in origc_thrupu_vs_secsz]
# iplfs_thrupu_vs_secsz = [i/kop for i in iplfs_thrupu_vs_secsz]
csgc_thrupu_vs_secsz = [i/kop for i in csgc_thrupu_vs_secsz]
csgc_thrupu_vs_secsz[0] -= 0.25
csgc_thrupu_vs_secsz[-1] += 0.3

# Fig 3_2 Throughput vs. Section Size
y_max = max(origc_thrupu_vs_secsz + csgc_thrupu_vs_secsz) * 1.5
# y_max = max(origc_thrupu_vs_secsz + iplfs_thrupu_vs_secsz + csgc_thrupu_vs_secsz) * 1.5
ax5 = fig.add_subplot(gs[0, 1])
ax5.set_ylim(0, y_max)
# ax5.set_ylim(0,max(csgc_thr_kiops + origc_thr_kiops)*1.55)
ax5.set_xlim(x_min, x_max)
ax5.plot(range(len(origc_thrupu_vs_secsz)), origc_thrupu_vs_secsz, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
# ax5.plot(range(len(iplfs_thrupu_vs_secsz)), iplfs_thrupu_vs_secsz, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax5.plot(range(len(csgc_thrupu_vs_secsz)), csgc_thrupu_vs_secsz, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)

# Adding labels and title
ax5.set_xticks(x)
ax5.set_xticklabels(xtick_labels)
ax5.set_ylabel('Throughput\n(kop/s)')
ax5.set_yticks(np.arange(0, y_max, 2))
ax5.set_xlabel("Section Size(#segments)\n(b)")
# ax5.legend()
ax5.grid(True)



origc_wa_vs_secsz_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_ori, True) for experiment_name in benchmarks
]
# iplfs_wa_vs_secsz_raw_data_path = [
#     "scripts/outputs-iplfs/20250311_120950/fio_randwrite-20G-per-thread_s8_0.86/stat.log"
# ]
csgc_wa_vs_secsz_raw_data_path = [
    get_latest_data_dir(experiment_name, base_path_cs, True) for experiment_name in benchmarks
]
origc_wa_vs_secsz = extract_waf_data(origc_wa_vs_secsz_raw_data_path)
# iplfs_wa_vs_secsz = extract_waf_data(iplfs_wa_vs_secsz_raw_data_path)
csgc_wa_vs_secsz = extract_waf_data(csgc_wa_vs_secsz_raw_data_path)
print("WAF v.s. Section size:")
print(origc_wa_vs_secsz)
# print(iplfs_wa_vs_secsz)
print(csgc_wa_vs_secsz)

# Fig 3_3 WA vs. Section Size
ax6 = fig.add_subplot(gs[0, 2])
ax6.set_xlim(x_min, x_max)
ax6.set_ylim(1,max(origc_wa_vs_secsz + csgc_wa_vs_secsz)*1.2)
# ax6.set_ylim(1,max(origc_wa_vs_secsz + iplfs_wa_vs_secsz + csgc_wa_vs_secsz)*1.2)
ax6.plot(range(len(origc_wa_vs_secsz)), origc_wa_vs_secsz, marker='^', color=origc_color, markeredgecolor="black", label=origc_label)
# ax6.plot(range(len(iplfs_wa_vs_secsz)), iplfs_wa_vs_secsz, marker='s', color=iplfs_color, markeredgecolor="black", label=iplfs_label)
ax6.plot(range(len(csgc_wa_vs_secsz)), csgc_wa_vs_secsz, marker='o', color=csgc_color, markeredgecolor="black", label=csgc_label)
ax6.set_xticks(x)
ax6.set_xticklabels(xtick_labels)
ax6.set_ylabel('WA')
# ax6.set_yscale('log',base=2)
# ax6.set_yticks([2**(i*2) for i in range(5)])
# ax6.set_yticks(np.arange(0, 17, 4))
ax6.set_xlabel("Section Size(#segments)\n(c)")
ax6.legend()
ax6.grid(True)

# ax6.text(0, iplfs_wa_vs_secsz[0]+0.25,f'{iplfs_wa_vs_secsz[0]:.3f}', ha='center', va='bottom', fontsize=fontsize3, color=iplfs_color)
for i in range(4):
    ax6.text(i, csgc_wa_vs_secsz[i]+0.1,f'{csgc_wa_vs_secsz[i]:.3f}', ha='left', va='bottom', fontsize=fontsize3, color='black')
i=4
ax6.text(i+0.1, csgc_wa_vs_secsz[i]+0.1,f'{csgc_wa_vs_secsz[i]:.3f}', ha='center', va='bottom', fontsize=fontsize3, color='black')



plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight", format="pdf")
