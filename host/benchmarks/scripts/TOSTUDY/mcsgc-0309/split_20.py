#!/usr/bin/env python3
import os
import sys

def split_file(input_file, num_splits=20):
    # 检查输入文件是否存在
    if not os.path.isfile(input_file):
        print(f"Error: {input_file} does not exist.")
        return

    # 获取输入文件路径信息
    dir_name = os.path.dirname(input_file)
    base_name = os.path.basename(input_file)
    file_stem = os.path.splitext(base_name)[0]

    # 创建输出文件夹
    out_dir = os.path.join(dir_name, f"{file_stem}split.log")
    os.makedirs(out_dir, exist_ok=True)

    # 读取所有行
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    total_lines = len(lines)
    per_split = total_lines // num_splits
    remainder = total_lines % num_splits

    start = 0
    for i in range(1, num_splits + 1):
        # 每个文件分配行数：均分 + 前 remainder 个文件各多 1 行
        end = start + per_split + (1 if i <= remainder else 0)
        split_lines = lines[start:end]

        out_file = os.path.join(out_dir, f"{file_stem}split{i}.log")
        with open(out_file, 'w', encoding='utf-8') as f:
            f.writelines(split_lines)

        start = end

    print(f"Split completed. {num_splits} files are created in '{out_dir}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} /absolute/path/to/input_file")
        sys.exit(1)
    
    input_file = sys.argv[1]
    split_file(input_file)