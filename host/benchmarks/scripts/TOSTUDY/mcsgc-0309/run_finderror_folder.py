#!/usr/bin/env python3
import os
import sys
import subprocess

def run_finderror_on_folder(folder_path, finderror_script='finderror.py'):
    if not os.path.isdir(folder_path):
        print(f"Error: {folder_path} is not a valid directory.")
        return

    # 遍历文件夹下所有文件（不递归子文件夹）
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    if not files:
        print(f"No files found in {folder_path}.")
        return

    for f in files:
        file_path = os.path.join(folder_path, f)
        print(f"Processing file: {file_path}")
        # 调用 finderror.py
        try:
            subprocess.run(['python3', finderror_script, file_path], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error processing {file_path}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} /absolute/path/to/folder")
        sys.exit(1)
    
    folder_path = sys.argv[1]
    run_finderror_on_folder(folder_path)