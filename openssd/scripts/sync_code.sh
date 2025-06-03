#!/bin/bash

VITIS_WORKSPACE_DIR=/home/jin/workspaces/csgc2
VITIS_FTL_PROJECT_NAME=ftl
VITIS_CS_PROJECT_NAME=cs_leader
VITIS_CS_WORKER1_PROJECT_NAME=cs_worker1
VITIS_CS_WORKER2_PROJECT_NAME=cs_worker2

pushd $(dirname $0) > /dev/null

# FTL, handle nvme commands, also handle local csio issued by f2fs_probe or f2fs_read_super
# (share the same cdma instance with CS Worker2, protected by m->cdma_lock).
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src/*.c
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src/*.h
sudo rm -rf ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src/nvme

sudo cp -r ../src/ftl/* ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src
sudo cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src
# sudo cp -r ../src/io_worker/* ${VITIS_WORKSPACE_DIR}/${VITIS_FTL_PROJECT_NAME}/src

# CS Leader(Worker0)
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_PROJECT_NAME}/src/*.c
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_PROJECT_NAME}/src/*.h

sudo cp -r ../src/cs/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_PROJECT_NAME}/src
sudo cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_PROJECT_NAME}/src
sudo cp -r ../src/shared_cs/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_PROJECT_NAME}/src

# CS Worker1
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER1_PROJECT_NAME}/src/*.c
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER1_PROJECT_NAME}/src/*.h

sudo cp -r ../src/cs_worker1/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER1_PROJECT_NAME}/src
sudo cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER1_PROJECT_NAME}/src
sudo cp -r ../src/shared_cs/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER1_PROJECT_NAME}/src

# CS Worker2, handle emu and csio requsts from Worker0-1 or FTL
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src/*.c
sudo rm -f ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src/*.h
sudo rm -rf ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src/inverval-mapping

sudo cp -r ../src/cs_worker2/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src
sudo cp -r ../src/shared/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src
sudo cp -r ../src/io_worker/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src
sudo cp -r ../src/emu/* ${VITIS_WORKSPACE_DIR}/${VITIS_CS_WORKER2_PROJECT_NAME}/src

popd > /dev/null
