import os
import re
from datetime import datetime

base_path_cs = "outputs-cs"
base_path_ori = "outputs-ori"
base_path_iplfs = "outputs-iplfs"
# base_path_cs = "host/benchmarks/scripts/outputs-cs"
# base_path_ori = "host/benchmarks/scripts/outputs-ori"
# base_path_iplfs = "host/benchmarks/scripts/outputs-iplfs"

date_pattern = r"(\d{8}_\d{6})"
bm_type = [
    "filebench",
    "ycsb",
    "fio",
]

def get_latest_data_dir(experiment_name, base_path, getStat):
    dirs = []
    bm_name = experiment_name

    if base_path.endswith("iplfs"):
        bm_name = bm_name.replace("s8","s1")
    
    for root, dirs_in_dir, files in os.walk(base_path):
        if bm_name in dirs_in_dir:
            dirs.append(root)
    
    if len(dirs) == 0:
        return None
    
    latest_dir = max(dirs, key=lambda d: datetime.strptime(re.search(date_pattern, os.path.basename(d)).group(1), "%Y%m%d_%H%M%S")) + '/' + bm_name
    if getStat:
        latest_dir += "/stat.log"
    else :
        for bm in bm_type:
            if bm_name.startswith(bm):
                latest_dir += f"/{bm}.log"
                break
    return latest_dir

if __name__ == "__main__":
    experiment_names = [
        "filebench_fileserver_4t_60G_1M_54k_s8", 
        "filebench_varmail_4t_60G_1M_54k_s8", 
        "ycsb_workloada_s8_0.86",
        "ycsb_workloadf_s8_0.8", 
        "fio_randwrite_s8_0.86_random", 
        "fio_randwrite_s8_0.86_zipf:1.1",
    ]
    origc_raw_data_dirs = [
        get_latest_data_dir(experiment_name, base_path_ori, False) for experiment_name in experiment_names
    ]

    iplfs_raw_data_dirs = [
        get_latest_data_dir(experiment_name, base_path_iplfs, False) for experiment_name in experiment_names
    ]

    csgc_raw_data_dirs = [
        get_latest_data_dir(experiment_name, base_path_cs, False) for experiment_name in experiment_names
    ]

    print("Latest origc raw data dirs:")
    for dir in origc_raw_data_dirs:
        print(dir)
    print("Latest iplfs raw data dirs:")
    for dir in iplfs_raw_data_dirs:
        print(dir)
    print("Latest csgc raw data dirs:")
    for dir in csgc_raw_data_dirs:
        print(dir)
