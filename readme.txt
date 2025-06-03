Brief Overview:

    The artifact contains necessary source codes to reproduce results in the paper 
    CSGC: Collaborative File System Garbage Collection with Computational Storage. 
    The `host` directory contains host-side code, benchmarks, and kernels (under 
    GPL 2.0 only), and `openssd` directory holds device-side firmware code (under 
    GPL 3.0 or later). 
    To reproduce the results, two servers (A and B) and an OpenSSD are required; 
    the OpenSSD is installed on server A and programmed via server B. Server B's setup 
    involves installing Vivado and Vitis. Server A's configuration includes building 
    custom CSGC and IPLFS Linux kernels, preparing benchmark environments and installing 
    benchmark tools. 
    The reproduction process begins on server B by programming the OpenSSD firmware 
    and booting the OpenSSD. Subsequently, server A is booted. On server A, after 
    switching to the appropriate kernel (CSGC, vanilla F2FS, or IPLFS), the test script 
    is run with specified configurations to evaluate the systems. Finally, Python scripts 
    are used to generate plots from the collected benchmark data.
    Check artifact-overview.pdf for more details.

Folder Structure:

    csgc-artifacts
    ├── artifact-overview.pdf
    ├── host
    │   ├── benchmarks
    │   │   ├── filebench
    │   │   ├── file_writer
    │   │   ├── fio
    │   │   ├── myworkloads
    │   │   ├── scripts
    │   │   └── ycsb-0.17.0
    │   └── src
    │       ├── f2fs-tools-csgc
    │       ├── f2fs-tools-iplfs
    │       ├── linux-csgc
    │       ├── linux-iplfs
    │       └── nvme-cli
    ├── openssd
    │   ├── .gitignore
    │   ├── LICENSE.txt
    │   ├── README.txt
    │   ├── scripts
    │   │   ├── mv_workspace.sh
    │   │   └── sync_code.sh
    │   ├── src
    │   │   ├── cs
    │   │   ├── cs_worker1
    │   │   ├── cs_worker2
    │   │   ├── emu
    │   │   ├── ftl
    │   │   ├── io_worker
    │   │   ├── shared
    │   │   └── shared_cs
    │   └── vitis_export_archive_csgc.ide.zip
    └── readme.txt

Main Changes:

1. Implemented the csgc offloader in f2fs
    involved files: host/src/linux-csgc/fs/f2fs/{gc.c,segment.c,data.c,f2fs.h}
2. Modified nvme driver to support customized nvme command
    involved files: host/src/linux-csgc/drivers/nvme/host/core.c (the function `nvme_setup_rw`)
3. Implemented the csgc executor and sFTL in openssd firmware
    involved files: openssd/src/shared_cs/* (csgc executor); openssd/src/emu/{ftl.c,ftl.h} (sFTL)
4. Customized nvme-cli for control of openssd
    involved files: host/src/nvme-cli/nvme.c (the function `fs_ready_cmd` and `ssd_admin_cmd`)
