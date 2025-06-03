//////////////////////////////////////////////////////////////////////////////////
// memory_map.h for Cosmos+ OpenSSD
// Copyright (c) 2017 Hanyang University ENC Lab.
// Contributed by Yong Ho Song <yhsong@enc.hanyang.ac.kr>
//                  Jaewook Kwak <jwkwak@enc.hanyang.ac.kr>
//                  Sangjin Lee <sjlee@enc.hanyang.ac.kr>
//
// This file is part of Cosmos+ OpenSSD.
//
// Cosmos+ OpenSSD is free software; you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation; either version 3, or (at your option)
// any later version.
//
// Cosmos+ OpenSSD is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
// See the GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Cosmos+ OpenSSD; see the file COPYING.
// If not, see <http://www.gnu.org/licenses/>.
//////////////////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////////////////
// Company: ENC Lab. <http://enc.hanyang.ac.kr>
// Engineer: Jaewook Kwak <jwkwak@enc.hanyang.ac.kr>
//
// Project Name: Cosmos+ OpenSSD
// Design Name: Cosmos+ Firmware
// Module Name: Static Memory Allocator
// File Name: memory_map.h
//
// Version: v1.0.0
//
// Description:
//     - allocate DRAM address space (0x0010_0000 ~ 0x3FFF_FFFF) to each module
//////////////////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////////////////
// Revision History:
//
// * v1.0.0
//   - First draft
//////////////////////////////////////////////////////////////////////////////////

#include <stdint.h>

#ifndef MEMORY_MAP_H_
#define MEMORY_MAP_H_

#define DRAM_START_ADDR                 ((uintptr_t)0x00100000)
#define DRAM_END_ADDR                   ((uintptr_t)0x80000000)

#define CPU0_DRAM_START_ADDR            DRAM_START_ADDR
#define CPU0_DRAM_END_ADDR              ((uintptr_t)0x5a000000)

#define CPU0_MEMORY_SEGMENTS_START_ADDR CPU0_DRAM_START_ADDR
#define CPU0_MEMORY_SEGMENTS_END_ADDR   ((uintptr_t)0x00200000)

#define NVME_MANAGEMENT_START_ADDR      ((uintptr_t)0x00200000)
#define NVME_MANAGEMENT_END_ADDR        ((uintptr_t)0x10000000)

#define DUMMY_RD_WR_ADDR                ((uintptr_t)(0x40000000 - 0x1000)) // Reserved for NVMe IP.

// cpu0 cached: 16MB
#define CPU0_CACHED_MEMORY_BASE_ADDR    ((uintptr_t)0x40000000)
#define CPU0_CACHED_MEMORY_END_ADDR     ((uintptr_t)0x41000000)
// #define CPU0_CACHED_MEMORY_END_ADDR     ((uintptr_t)0x47e00000)

// cpu0 uncached 400MB, for dma data buffer
#define CPU0_UNCACHED_MEMORY_BASE_ADDR  CPU0_CACHED_MEMORY_END_ADDR
#define CPU0_UNCACHED_MEMORY_END_ADDR   CPU0_DRAM_END_ADDR

#define CPU0_LINEAR_MALLOC_BASE_ADDR    CPU0_CACHED_MEMORY_BASE_ADDR
#define CPU0_LINEAR_MALLOC_END_ADDR     CPU0_CACHED_MEMORY_END_ADDR

// cpu1 total: 16MB
#define CPU1_DRAM_START_ADDR            CPU0_DRAM_END_ADDR
#define CPU1_DRAM_END_ADDR              ((uintptr_t)0x5b000000)

// memory segments: 2MB
#define CPU1_MEMORY_SEGMENTS_START_ADDR CPU1_DRAM_START_ADDR
#define CPU1_MEMORY_SEGMENTS_END_ADDR   (CPU1_DRAM_START_ADDR + (uintptr_t)0x00100000)

// cpu1 cached: 16MB - 2MB
#define CPU1_CACHED_MEMORY_BASE_ADDR    CPU1_MEMORY_SEGMENTS_END_ADDR
#define CPU1_CACHED_MEMORY_END_ADDR     CPU1_DRAM_END_ADDR

#define CPU1_LINEAR_MALLOC_BASE_ADDR    CPU1_CACHED_MEMORY_BASE_ADDR
#define CPU1_LINEAR_MALLOC_END_ADDR     CPU1_CACHED_MEMORY_END_ADDR

// cpu2 total: 16MB
#define CPU2_DRAM_START_ADDR            CPU1_DRAM_END_ADDR
#define CPU2_DRAM_END_ADDR              ((uintptr_t)0x5c000000)

#define CPU2_MEMORY_SEGMENTS_START_ADDR CPU2_DRAM_START_ADDR
#define CPU2_MEMORY_SEGMENTS_END_ADDR   (CPU2_DRAM_START_ADDR + (uintptr_t)0x00100000)

#define CPU2_CACHED_MEMORY_BASE_ADDR    CPU2_MEMORY_SEGMENTS_END_ADDR
#define CPU2_CACHED_MEMORY_END_ADDR     CPU2_DRAM_END_ADDR

#define CPU2_LINEAR_MALLOC_BASE_ADDR    CPU2_CACHED_MEMORY_BASE_ADDR
#define CPU2_LINEAR_MALLOC_END_ADDR     CPU2_CACHED_MEMORY_END_ADDR

// cpu3 total: 512MB, for `struct ssd`
#define CPU3_DRAM_START_ADDR            CPU2_DRAM_END_ADDR
#define CPU3_DRAM_END_ADDR              ((uintptr_t)0x7c000000)

#define CPU3_MEMORY_SEGMENTS_START_ADDR CPU3_DRAM_START_ADDR
#define CPU3_MEMORY_SEGMENTS_END_ADDR   (CPU3_DRAM_START_ADDR + (uintptr_t)0x00100000)

#define CPU3_CACHED_MEMORY_BASE_ADDR    CPU3_MEMORY_SEGMENTS_END_ADDR
#define CPU3_CACHED_MEMORY_END_ADDR     CPU3_DRAM_END_ADDR

#define CPU3_LINEAR_MALLOC_BASE_ADDR    CPU3_CACHED_MEMORY_BASE_ADDR
#define CPU3_LINEAR_MALLOC_END_ADDR     CPU3_CACHED_MEMORY_END_ADDR

// 32MB for csio and sgio requests and queues
#define CPU3_REQ_MEMORY_BASE_ADDR       CPU3_LINEAR_MALLOC_BASE_ADDR
#define CPU3_REQ_MEMORY_END_ADDR        (CPU3_LINEAR_MALLOC_BASE_ADDR + (uintptr_t)0x02000000)

// 478 MB for ssd structures
#define CPU3_SSD_MEMORY_BASE_ADDR       CPU3_REQ_MEMORY_END_ADDR
#define CPU3_SSD_MEMORY_END_ADDR        CPU3_LINEAR_MALLOC_END_ADDR

// non-cacheable memory for CDMA: 4MB
#define DMA_NON_CACHEABLE_BASE_ADDR     CPU3_DRAM_END_ADDR
#define DMA_NON_CACHEABLE_END_ADDR      (DMA_NON_CACHEABLE_BASE_ADDR + (uintptr_t)0x00400000)

// non-cacheable memory for BD-ring: 2MB
#define BD_RING_NON_CACHEABLE_BASE_ADDR DMA_NON_CACHEABLE_END_ADDR
#define BD_RING_NON_CACHEABLE_END_ADDR  (BD_RING_NON_CACHEABLE_BASE_ADDR + (uintptr_t)0x00200000)

// shared mem: 58MB, init by cs worker 0
#define SHARED_MEM_BASE_ADDR            BD_RING_NON_CACHEABLE_END_ADDR
#define SHARED_MEM_END_ADDR             DRAM_END_ADDR

#define DDR4_BUFFER_BASE_ADDR ((uintptr_t)XPAR_DDR4_0_C0_DDR4_MEMORY_MAP_BASEADDR)

#define BD_SPACE_BASE                   BD_RING_NON_CACHEABLE_BASE_ADDR
#define BD_SPACE_HIGH                   BD_RING_NON_CACHEABLE_END_ADDR

void setup_page_table();
void check_memory_map(uintptr_t segment_end_addr);


#endif /* MEMORY_MAP_H_ */
