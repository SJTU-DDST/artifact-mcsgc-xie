import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import numpy as np
import os
import re
from utils import *

# should run in "host/benchmarks/scripts"

fig_path = "figs/2timeline_lat.pdf"
fig_size = (10, 2.3)

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

origc_timeline_raw_data_path = [
    get_latest_data_dir("filebench_fileserver_4t_60G_1M_54k_period_s8", base_path_ori, False)
]
iplfs_timeline_raw_data_path = [
    get_latest_data_dir("filebench_fileserver_4t_60G_1M_54k_period_s8", base_path_iplfs, False)
]
csgc_timeline_raw_data_path = [
    get_latest_data_dir("filebench_fileserver_4t_60G_1M_54k_period_s8", base_path_cs, False)
]

def extract_timeline_data_filebench(raw_data_dirs):
    time = []
    op = []
    opps = []
    pattern = r'(\d*.?\d*):\s+IO Summary:\s+(\d*\.?\d*)\s+ops\s+(\d*\.?\d*)\s+ops/s'
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            lines = f.readlines()
            for line in lines:
                match = re.search(pattern, line)
                if match:
                    time.append(float(match.group(1)))
                    op.append(float(match.group(2)))
                    opps.append(float(match.group(3)))
    return time, op, opps

origc_fb_time, origc_fb_op, origc_fb_opps = extract_timeline_data_filebench(origc_timeline_raw_data_path)
iplfs_fb_time, iplfs_fb_op, iplfs_fb_opps = extract_timeline_data_filebench(iplfs_timeline_raw_data_path)
csgc_fb_time, csgc_fb_op, csgc_fb_opps = extract_timeline_data_filebench(csgc_timeline_raw_data_path)

origc_total_kop = sum(origc_fb_op)//1000
iplfs_total_kop = sum(iplfs_fb_op)//1000
csgc_total_kop = sum(csgc_fb_op)//1000

origc_fb_time = [int(t - origc_fb_time[0]) for t in origc_fb_time]
iplfs_fb_time = [int(t - iplfs_fb_time[0]) for t in iplfs_fb_time]
csgc_fb_time = [int(t - csgc_fb_time[0]) for t in csgc_fb_time]

kop=1000
print("filebench timeline data:")
print(origc_fb_opps)
print(iplfs_fb_opps)
print(csgc_fb_opps)

print(origc_fb_time)
print([a/kop for a in origc_fb_opps])
print(iplfs_fb_time)
print([a/kop for a in iplfs_fb_opps])
print(csgc_fb_time)
print([a/kop for a in csgc_fb_opps])

marker_ori = '^'
marker_iplfs = 's'
marker_cs = 'o'
markersize = 2.5
fig = plt.figure(figsize=fig_size)
gs = GridSpec(1, 2, figure=fig, height_ratios=[1], width_ratios=[1,1])
ax2 = fig.add_subplot(gs[0, 0])
ax2.plot(
    origc_fb_time, [a/kop for a in origc_fb_opps], 
    label=origc_label, 
    color=origc_color,
    marker=marker_ori,
    markersize=markersize,
    # markeredgecolor="black",
)
ax2.plot(
    iplfs_fb_time, [a/kop for a in iplfs_fb_opps],
    label=iplfs_label,
    color=iplfs_color,
    marker=marker_iplfs,
    markersize=markersize,
    # markeredgecolor="black",
)
ax2.plot(
    csgc_fb_time, [a/kop for a in csgc_fb_opps],
    label=csgc_label,
    color=csgc_color,
    marker=marker_cs,
    markersize=markersize,
    # markeredgecolor="black",
)
y_max = max(max(origc_fb_opps), max(iplfs_fb_opps), max(csgc_fb_opps))/kop
ax2.set_xlim(0, 400)
ax2.set_ylim(0, y_max * 1.2)
ax2.set_yticks(range(0,int(y_max)+1,1))
ax2.text(origc_fb_time[-1]+10, origc_fb_opps[-1]/kop-0.15,
        f'total={origc_total_kop:.0f}K', color=origc_color,
        ha='left', va='bottom', fontsize=fontsize2)
ax2.text(iplfs_fb_time[-1]+10, iplfs_fb_opps[-1]/kop+0.20,
        f'total={iplfs_total_kop:.0f}K', color=iplfs_color,
        ha='left', va='bottom', fontsize=fontsize2)
ax2.text(csgc_fb_time[-1]+10, csgc_fb_opps[-1]/kop,
        f'total={csgc_total_kop:.0f}K', color=csgc_color,
        ha='left', va='bottom', fontsize=fontsize2)
ax2.legend()

ax2.set_xlabel('Time (s)\n(a)')
ax2.set_ylabel('Throughput\n(kop/s)')
ax2.grid(True)




origc_lat_raw_data_path = [
    get_latest_data_dir("ycsb_workloada_s8_0.86", base_path_ori, False)
]
iplfs_lat_raw_data_path = [
    get_latest_data_dir("ycsb_workloada_s8_0.86", base_path_iplfs, False)
]
csgc_lat_raw_data_path = [
    get_latest_data_dir("ycsb_workloada_s8_0.86", base_path_cs, False)
]

def extract_lat_data_ycsb(raw_data_dirs):
    latencies = []
    patterns = [
        r'\[READ\], AverageLatency\(us\), ([\d\.]+)',
        r'\[READ\], 99thPercentileLatency\(us\), ([\d\.]+)',
        r'\[UPDATE\], AverageLatency\(us\), ([\d\.]+)',
        r'\[UPDATE\], 99thPercentileLatency\(us\), ([\d\.]+)'
    ]
    for raw_data_dir in raw_data_dirs:
        with open(raw_data_dir, 'r') as f:
            contents = f.read()
            for pattern in patterns:
                matches = re.findall(pattern, contents)
                latencies.extend(matches)
            
    return [float(latency) for latency in latencies]

origc_ycsb_lat = extract_lat_data_ycsb(origc_lat_raw_data_path)
iplfs_ycsb_lat = extract_lat_data_ycsb(iplfs_lat_raw_data_path)
csgc_ycsb_lat = extract_lat_data_ycsb(csgc_lat_raw_data_path)
print("ycsb latency data:")
print(origc_ycsb_lat)
print(iplfs_ycsb_lat)
print(csgc_ycsb_lat)
origc_ycsb_lat = [lat/1000 for lat in origc_ycsb_lat]
iplfs_ycsb_lat = [lat/1000 for lat in iplfs_ycsb_lat]
csgc_ycsb_lat = [lat/1000 for lat in csgc_ycsb_lat]

origc_color = '#A8C3E6'
# iplfs_color = '#DBE6AB'
iplfs_color = '#9AFF9A'
csgc_color = '#E6B4AE'

xlabels = ["read/Avg.", "read/99%", "update/Avg.", "update/99%"]
x = np.arange(len(xlabels))
width = 0.22
ax3 = fig.add_subplot(gs[0, 1])
ax3_rects1 = ax3.bar(
    x - width, origc_ycsb_lat, width, 
    label=origc_label,
    color=origc_color,
    edgecolor='black',
    hatch='xx'
)
ax3_rects2 = ax3.bar(
    x, iplfs_ycsb_lat, width, 
    label=iplfs_label,
    color=iplfs_color,
    edgecolor='black',
    hatch='//'
)
ax3_rects3 = ax3.bar(
    x + width, csgc_ycsb_lat, width, 
    label=csgc_label,
    color=csgc_color,
    edgecolor='black',
    hatch='\\\\'
)

ax3.set_ylabel('Latency(ms)')
ax3.set_xticks(x)
ax3.set_xticklabels(xlabels)
ax3.set_ylim(0, max(max(origc_ycsb_lat), max(iplfs_ycsb_lat), max(csgc_ycsb_lat)) * 1.4)
ax3.legend()
ax3.set_xlabel('\n(b)')

autolabel(ax3_rects1, ax3,0)
autolabel(ax3_rects2, ax3,0)
autolabel(ax3_rects3, ax3,0)

plt.tight_layout()
plt.savefig(fig_path, bbox_inches="tight" , format="pdf")