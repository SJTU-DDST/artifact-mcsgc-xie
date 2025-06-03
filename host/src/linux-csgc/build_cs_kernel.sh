#!/bin/bash

cd $(dirname $0)

make clean

cp /boot/config-$(uname -r) .config
make olddefconfig
scripts/config --disable SECURITY_LOCKDOWN_LSM
scripts/config --disable MODULE_SIG
scripts/config --disable SYSTEM_REVOCATION_LIST
scripts/config --set-str SYSTEM_TRUSTED_KEYS ""

make -j$(nproc) LOCALVERSION=-cs V=1 2>&1 | tee kernel_build_log
