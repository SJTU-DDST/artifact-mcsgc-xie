#!/usr/bin/env bash

set -u

DRY_RUN=0

# 解析选项
while getopts "n" opt; do
    case "$opt" in
        n)
            DRY_RUN=1
            ;;
        *)
            echo "Usage: $0 [-n] <source_path> <destination_path>"
            exit 1
            ;;
    esac
done

shift $((OPTIND - 1))

# 检查参数数量
if [ $# -ne 2 ]; then
    echo "Usage: $0 [-n] <source_path> <destination_path>"
    exit 1
fi

SRC_PATH="$1"
DST_PATH="$2"

# 检查路径是否存在
if [ ! -d "$SRC_PATH" ]; then
    echo "Error: source path does not exist: $SRC_PATH"
    exit 1
fi

if [ ! -d "$DST_PATH" ]; then
    echo "Error: destination path does not exist: $DST_PATH"
    exit 1
fi

# dry-run：只列出文件，按最后修改时间倒序排序
if [ "$DRY_RUN" -eq 1 ]; then
    find "$SRC_PATH" -type f \( -name "*.sh" -o -name "*.py" \) -print0 |
    while IFS= read -r -d '' file; do
        stat --printf='%Y\t%n\t%y\n' "$file"
    done | sort -t $'\t' -k1,1nr | cut -f2-
    exit 0
fi

# 下面是正常复制逻辑
# 需要 Bash 4+ 的关联数组
declare -A newest_path      # basename -> 最新文件路径
declare -A newest_epoch     # basename -> 最新mtime(epoch)
declare -A newest_human     # basename -> 最新mtime(可读)
declare -A all_info         # basename -> 所有同名文件信息（多行字符串）

found_any=0

# 先遍历所有候选文件，按 basename 分组，只保留最新的那个
while IFS= read -r -d '' file; do
    found_any=1

    base=$(basename "$file")
    epoch=$(stat --printf='%Y' "$file")
    human=$(stat --printf='%y' "$file")

    # 记录所有同名文件信息
    if [ -v "all_info[$base]" ]; then
        all_info["$base"]+=$'\n'"$human"$'\t'"$file"
    else
        all_info["$base"]="$human"$'\t'"$file"
    fi

    # 选择最新的那个
    if [ ! -v "newest_epoch[$base]" ] || [ "$epoch" -gt "${newest_epoch[$base]}" ]; then
        newest_epoch["$base"]="$epoch"
        newest_human["$base"]="$human"
        newest_path["$base"]="$file"
    fi
done < <(find "$SRC_PATH" -type f \( -name "*.sh" -o -name "*.py" \) -print0)

if [ "$found_any" -eq 0 ]; then
    echo "INFO: no .sh or .py files found under: $SRC_PATH"
    exit 0
fi

# 在执行任何复制之前，先检查目标路径里是否已经存在同名文件
conflict_found=0
for base in "${!newest_path[@]}"; do
    if [ -e "$DST_PATH/$base" ]; then
        if [ "$conflict_found" -eq 0 ]; then
            echo "Error: destination already contains one or more conflicting filenames."
            echo "No files have been copied."
            conflict_found=1
        fi
        echo "  conflict: $DST_PATH/$base"
        echo "    selected source: ${newest_path[$base]}"
        echo "    selected source mtime: ${newest_human[$base]}"
    fi
done

if [ "$conflict_found" -ne 0 ]; then
    exit 1
fi

# 执行复制
for base in "${!newest_path[@]}"; do
    src_file="${newest_path[$base]}"
    src_human="${newest_human[$base]}"
    dst_file="$DST_PATH/$base"

    echo "=================================================="
    echo "Copying file:"
    echo "  source:      $src_file"
    echo "  destination: $dst_file"
    echo "  selected file mtime: $src_human"

    # 输出是否有其他同名文件
    dup_count=$(printf '%s\n' "${all_info[$base]}" | wc -l)
    if [ "$dup_count" -gt 1 ]; then
        echo "  duplicate same-name files found: yes"
        echo "  all same-name candidates (newest first):"
        printf '%s\n' "${all_info[$base]}" | sort -r | while IFS=$'\t' read -r human path; do
            if [ "$path" = "$src_file" ]; then
                echo "    [SELECTED] mtime=$human  path=$path"
            else
                echo "    [SKIPPED ] mtime=$human  path=$path"
            fi
        done
    else
        echo "  duplicate same-name files found: no"
        echo "    [SELECTED] mtime=$src_human  path=$src_file"
    fi

    cp -- "$src_file" "$dst_file"
    ret=$?

    if [ "$ret" -ne 0 ]; then
        echo "  result: copy failed with exit code $ret"
        exit "$ret"
    else
        echo "  result: copy succeeded"
    fi
done

echo "=================================================="
echo "All copies completed successfully."