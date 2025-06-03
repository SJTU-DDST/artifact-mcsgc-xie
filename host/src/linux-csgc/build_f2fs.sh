#!/bin/bash

make M=fs/f2fs clean
make M=fs/f2fs -j$(nproc) LOCALVERSION=-csgcxie V=1 2>&1 | tee f2fs_build_log
make M=fs/f2fs modules_install

lsmod | grep f2fs

if [ $? -eq 0 ]; then 
    echo "found existing f2fs module, removing it"
    rmmod f2fs
    if [ $? -ne 0 ]; then 
        echo "prepare exit"
        exit
    fi 
fi 

modprobe f2fs
rmmod f2fs
insmod fs/f2fs/f2fs.ko