//////////////////////////////////////////////////////////////////////////////////
// nvme_main.c for Cosmos+ OpenSSD
// Copyright (c) 2016 Hanyang University ENC Lab.
// Contributed by Yong Ho Song <yhsong@enc.hanyang.ac.kr>
//				  Youngjin Jo <yjjo@enc.hanyang.ac.kr>
//				  Sangjin Lee <sjlee@enc.hanyang.ac.kr>
//				  Jaewook Kwak <jwkwak@enc.hanyang.ac.kr>
//				  Kibin Park <kbpark@enc.hanyang.ac.kr>
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
// Engineer: Sangjin Lee <sjlee@enc.hanyang.ac.kr>
//			 Jaewook Kwak <jwkwak@enc.hanyang.ac.kr>
//			 Kibin Park <kbpark@enc.hanyang.ac.kr>
//
// Project Name: Cosmos+ OpenSSD
// Design Name: Cosmos+ Firmware
// Module Name: NVMe Main
// File Name: nvme_main.c
//
// Version: v1.2.0
//
// Description:
//   - initializes FTL and NAND
//   - handles NVMe controller
//////////////////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////////////////
// Revision History:
//
// * v1.2.0
//   - header file for buffer is changed from "ia_lru_buffer.h" to "lru_buffer.h"
//   - Low level scheduler execution is allowed when there is no i/o command
//
// * v1.1.0
//   - DMA status initialization is added
//
// * v1.0.0
//   - First draft
//////////////////////////////////////////////////////////////////////////////////

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <arm_neon.h>

#include "xil_printf.h"
#include "debug.h"
#include "io_access.h"

#include "nvme.h"
#include "host_lld.h"
#include "nvme_main.h"
#include "nvme_admin_cmd.h"
#include "nvme_io_cmd.h"
#include "dma_data_buffer.h"

#include "../memory_map.h"
#include "../cdma.h"
#include "../utils.h"
#include "../cs_args.h"
// #include "../cs_io.h"

volatile NVME_CONTEXT g_nvmeTask;
extern struct linear_allocator allocator;

#define DDR4_INIT_BUF_SIZE (8 * 1024 * 1024)
static void init_ddr4()
{
	uint8_t *buf = linear_malloc(&allocator, DDR4_INIT_BUF_SIZE, 0);
	uint64_t start, end, total, error_count;
	uint64_t success;

	assert(buf != NULL);

	// int test_sizes[] = { 4, 8, 16, 32, 64, 128, 256, 512 };
	// for (int i = 0; i < sizeof(test_sizes) / sizeof(int); i++) {
	// 	printf("testing transfer size %d\n", test_sizes[i]);
	// 	cdma_transfer((void *)DDR4_BUFFER_BASE_ADDR + test_sizes[i], buf + test_sizes[i], test_sizes[i] - 1, 0, 0, 1, 1);
	// }
	// while (1);

	for (int i = 0; i < DDR4_INIT_BUF_SIZE; i++)
		buf[i] = 0xff;
	FLUSH_CACHE(buf, DDR4_INIT_BUF_SIZE);

	total = 0;
	for (size_t offset = 0; offset < FLASH_STORAGE; offset += DDR4_INIT_BUF_SIZE) {
		start = get_time_ns();
		success = cdma_transfer((void *)(DDR4_BUFFER_BASE_ADDR + offset),
		                        buf, DDR4_INIT_BUF_SIZE, 0, 0, 1, 1);
		assert(success);
		end = get_time_ns();
		total += end - start;

		if (offset % ONE_GB == 0) {
			printf("DDR4 init: %d/%d, last transfer: %luns\n", (int)(offset / ONE_GB) + 1,
			       (int)(FLASH_STORAGE / ONE_GB), end - start);
		}
	}

	printf("Filled %dGB in %.2lfs\n", (int)(FLASH_STORAGE / ONE_GB),
		   total / 1000000000.0);
	printf("Measured bandwidth: %.2lfGB/s\n",
	       (FLASH_STORAGE / ONE_GB) / (total / 1000000000.0));

	printf("Checking first 1GB of data\n");

	total = 0;
	error_count = 0;
	for (size_t offset = 0; offset < ONE_GB; offset += DDR4_INIT_BUF_SIZE) {
		memset(buf, 0, DDR4_INIT_BUF_SIZE);
		FLUSH_CACHE(buf, DDR4_INIT_BUF_SIZE);

		start = get_time_ns();
		success = cdma_transfer(buf, (void *)(DDR4_BUFFER_BASE_ADDR + offset),
		                        DDR4_INIT_BUF_SIZE, 0, 0, 1, 1);
		assert(success);
		end = get_time_ns();
		total += end - start;

		for (int i = 0; i < DDR4_INIT_BUF_SIZE / sizeof(uint64_t); i++)
			if (((uint64_t *)buf)[i] != 0xffffffffffffffff)
				error_count++;

		printf("%luMB checked, %lu errors found, transfer time %luns\n",
		       (offset + DDR4_INIT_BUF_SIZE) >> 20, error_count, end - start);
	}

	printf("Read 1GB in %.2fs\n", total / 1000000000.0);
	printf("Measured bandwidth: %.2lfGB/s\n", 1.0 / total * 1000000000.0);

	linear_malloc_reset(&allocator);
}

#define VECTOR_DIMENSION 112
#define TEST_NR_VECTORS ((uint64_t)1024 * 1024)
#if VECTOR_DIMENSION % 16 != 0
#error "vector dimension not aligned"
#endif
uint32_t l2_distance_uint8(uint8_t* vec1, uint8_t* vec2) {
    // Initialize the accumulator
    uint32x4_t acc = vdupq_n_u32(0);

    // Process four 8-bit elements at a time
    for (size_t i = 0; i < VECTOR_DIMENSION; i += 16) {
        // Load 16 elements from each vector
        uint8x16_t v1 = vld1q_u8(&vec1[i]);
        uint8x16_t v2 = vld1q_u8(&vec2[i]);

        // Subtract and get the absolute differences
        uint8x16_t diff = vabdq_u8(v1, v2);

        // Widen the difference to 16-bit to avoid overflow when squaring
        uint16x8_t diff_lo = vmovl_u8(vget_low_u8(diff));
        uint16x8_t diff_hi = vmovl_u8(vget_high_u8(diff));

        // Square the differences
        uint32x4_t sqr_lo_low = vmull_u16(vget_low_u16(diff_lo), vget_low_u16(diff_lo));
        uint32x4_t sqr_lo_high = vmull_u16(vget_high_u16(diff_lo), vget_high_u16(diff_lo));
        uint32x4_t sqr_hi_low = vmull_u16(vget_low_u16(diff_hi), vget_low_u16(diff_hi));
        uint32x4_t sqr_hi_high = vmull_u16(vget_high_u16(diff_hi), vget_high_u16(diff_hi));

        // Accumulate the squared differences
        acc = vaddq_u32(acc, sqr_lo_low);
        acc = vaddq_u32(acc, sqr_lo_high);
        acc = vaddq_u32(acc, sqr_hi_low);
        acc = vaddq_u32(acc, sqr_hi_high);
    }

    return vaddvq_u32(acc);
}

__attribute__((noinline)) void test_neon_performance()
{
#ifdef CONFIG_TEST_NEON
	uint8_t *vec1, *vec2;
	uint32_t answer, result;
	uint64_t start_time, total_naive, total_neon;

	vec1 = linear_malloc(&allocator, VECTOR_DIMENSION, 0);
	vec2 = linear_malloc(&allocator, VECTOR_DIMENSION, 0);
	assert(vec1 != NULL && vec2 != NULL);

	total_naive = 0;
	total_neon = 0;
	for (uint64_t i = 0; i < TEST_NR_VECTORS; i++) {
		for (int j = 0; j < VECTOR_DIMENSION; j++) {
			vec1[j] = rand();
			vec2[j] = 0;
		}

		MEMORY_BARRIER();

		answer = 0;
		start_time = get_time_ns();
		for (int j = 0; j < VECTOR_DIMENSION; j++)
			answer += ((int32_t)((int16_t)vec1[j] - (int16_t)vec2[j])) * ((int32_t)((int16_t)vec1[j] - (int16_t)vec2[j]));
		total_naive += get_time_ns() - start_time;

		MEMORY_BARRIER();

		start_time = get_time_ns();
		result = l2_distance_uint8(vec1, vec2);
		total_neon += get_time_ns() - start_time;
		assert(result == answer);
	}

	printf("%lu distance calculations\n", TEST_NR_VECTORS);
	printf("Naive: %luns (%lu ops/s)\n",
	       total_naive, TEST_NR_VECTORS * 1000000000 / total_naive);
	printf("NEON: %luns (%lu ops/s)\n",
	       total_neon, TEST_NR_VECTORS * 1000000000 / total_neon);

	linear_malloc_reset(&allocator);
#endif
}

void do_low_level_tasks()
{
	/* anything here must be reentrant safe */
	execute_queued_cs_args_reqs();
	check_done_cs_args_reqs();
	// schedule_cs_io_reqs();
}

static void wait_cs_workers_up(){
	wait_cpu_up(1);
	wait_cpu_up(2);
	wait_cpu_up(3);

	clear_boot_magics();
}

void nvme_main()
{
	unsigned int rstCnt = 0;
	struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

	test_cdma_sg_bw_d2d(100000*4096, 120000*4096,4, 2, 8*4*1024);
	
	init_ddr4();
	
	test_neon_performance();

	/* these must be here because functions above reset linear_malloc */
	init_host_io_reqs();
	init_ssd_admin_reqs();
	init_cs_args();
	// init_cs_io_reqs();
	spinlock_init(&m->xil_print_lock);
	init_buf_allocator(CPU0_UNCACHED_MEMORY_BASE_ADDR, 
		CPU0_UNCACHED_MEMORY_END_ADDR);

	xil_printf("cpu0 initialized\n");
	linear_allocator_get_mem_usage(&allocator, true);
	signal_cpu_up(0);
	wait_cs_workers_up();
	
	nsleep(1000000000);
	
	xil_printf("[ storage capacity %d MB ]\r\n", STORAGE_CAPACITY_L / ((1024*1024) / BYTES_PER_NVME_BLOCK));

	xil_printf("Turn on the host PC \r\n");

	while(1)
	{
		do_low_level_tasks();
		check_host_io_dma_done();
		process_emu_req_cq();
		process_ssd_admin_cq();

		if(g_nvmeTask.status == NVME_TASK_WAIT_CC_EN)
		{
			unsigned int ccEn;
			ccEn = check_nvme_cc_en();
			if(ccEn == 1)
			{
				set_nvme_admin_queue(1, 1, 1);
				set_nvme_csts_rdy(1);
				g_nvmeTask.status = NVME_TASK_RUNNING;
				xil_printf("\r\nNVMe ready!!!\r\n");
			}
		}
		else if(g_nvmeTask.status == NVME_TASK_RUNNING)
		{
			NVME_COMMAND nvmeCmd;
			unsigned int cmdValid;

			cmdValid = get_nvme_cmd(&nvmeCmd.qID, &nvmeCmd.cmdSlotTag, &nvmeCmd.cmdSeqNum, nvmeCmd.cmdDword);

			if(cmdValid == 1)
			{
				rstCnt = 0;
				if(nvmeCmd.qID == 0)
				{
					handle_nvme_admin_cmd(&nvmeCmd);
				}
				else
				{
					handle_nvme_io_cmd(&nvmeCmd);
				}
			}
		}
		else if(g_nvmeTask.status == NVME_TASK_SHUTDOWN)
		{
			NVME_STATUS_REG nvmeReg;
			nvmeReg.dword = IO_READ32(NVME_STATUS_REG_ADDR);
			if(nvmeReg.ccShn != 0)
			{
				unsigned int qID;
				set_nvme_csts_shst(1);

				for(qID = 0; qID < 8; qID++)
				{
					set_io_cq(qID, 0, 0, 0, 0, 0, 0);
					set_io_sq(qID, 0, 0, 0, 0, 0);
				}

				set_nvme_admin_queue(0, 0, 0);
				g_nvmeTask.cacheEn = 0;
				set_nvme_csts_shst(2);
				g_nvmeTask.status = NVME_TASK_WAIT_RESET;

				xil_printf("\r\nNVMe shutdown!!!\r\n");
			}
		}
		else if(g_nvmeTask.status == NVME_TASK_WAIT_RESET)
		{
			unsigned int ccEn;
			ccEn = check_nvme_cc_en();
			if(ccEn == 0)
			{
                unsigned int qID;

				g_nvmeTask.cacheEn = 0;
				set_nvme_csts_shst(0);
				set_nvme_csts_rdy(0);

                set_nvme_admin_queue(0, 0, 0);
                for(qID = 0; qID < 8; qID++)
                {
                    set_io_cq(qID, 0, 0, 0, 0, 0, 0);
                    set_io_sq(qID, 0, 0, 0, 0, 0);
                }

				g_nvmeTask.status = NVME_TASK_IDLE;
				xil_printf("\r\nNVMe disable!!!\r\n");
			}
		}
		else if(g_nvmeTask.status == NVME_TASK_RESET)
		{
			unsigned int qID;
			for(qID = 0; qID < 8; qID++)
			{
				set_io_cq(qID, 0, 0, 0, 0, 0, 0);
				set_io_sq(qID, 0, 0, 0, 0, 0);
			}

			if (rstCnt== 5){
				pcie_async_reset(rstCnt);
				rstCnt = 0;
				xil_printf("\r\nPcie iink disable!!!\r\n");
				xil_printf("Wait few minute or reconnect the PCIe cable\r\n");
			}
			else
				rstCnt++;

			g_nvmeTask.cacheEn = 0;
			set_nvme_admin_queue(0, 0, 0);
			set_nvme_csts_shst(0);
			set_nvme_csts_rdy(0);
			g_nvmeTask.status = NVME_TASK_IDLE;

			xil_printf("\r\nNVMe reset!!!\r\n");
		}
	}
}


