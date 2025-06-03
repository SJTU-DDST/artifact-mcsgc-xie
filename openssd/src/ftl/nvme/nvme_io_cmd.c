//////////////////////////////////////////////////////////////////////////////////
// nvme_io_cmd.c for Cosmos+ OpenSSD
// Copyright (c) 2016 Hanyang University ENC Lab.
// Contributed by Yong Ho Song <yhsong@enc.hanyang.ac.kr>
//				  Youngjin Jo <yjjo@enc.hanyang.ac.kr>
//				  Sangjin Lee <sjlee@enc.hanyang.ac.kr>
//				  Jaewook Kwak <jwkwak@enc.hanyang.ac.kr>
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
//
// Project Name: Cosmos+ OpenSSD
// Design Name: Cosmos+ Firmware
// Module Name: NVMe IO Command Handler
// File Name: nvme_io_cmd.c
//
// Version: v1.0.1
//
// Description:
//   - handles NVMe IO command
//////////////////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////////////////
// Revision History:
//
// * v1.0.1
//   - header file for buffer is changed from "ia_lru_buffer.h" to "lru_buffer.h"
//
// * v1.0.0
//   - First draft
//////////////////////////////////////////////////////////////////////////////////


#include "xil_printf.h"
#include "debug.h"
#include "io_access.h"

#include "nvme.h"
#include "host_lld.h"
#include "nvme_io_cmd.h"
#include "dma_data_buffer.h"
#include "../memory_map.h"
#include "../cs_args.h"
// #include "../f2fs_probe.h"
#include "../shared_mem.h"
#include "../utils.h"
#include <stdbool.h>

static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
extern struct linear_allocator allocator;

static struct host_io_req *host_io_reqs;
static QTAILQ_HEAD(free_host_io_reqs, host_io_req) free_host_io_reqs;
static QTAILQ_HEAD(pending_host_io_dmas, host_io_req) pending_host_io_dmas;
static uint64_t nr_free_host_io_reqs;

static struct host_io_req *alloc_host_io_req()
{
	struct host_io_req *req;

	ASSERT(!QTAILQ_EMPTY(&free_host_io_reqs));

	req = QTAILQ_FIRST(&free_host_io_reqs);
	QTAILQ_REMOVE(&free_host_io_reqs, req, qent);

	nr_free_host_io_reqs--;

	return req;
}

static void free_host_io_req(struct host_io_req *req)
{
	QTAILQ_INSERT_HEAD(&free_host_io_reqs, req, qent);
	nr_free_host_io_reqs++;
}

void init_host_io_reqs()
{
	host_io_reqs = linear_malloc(&allocator, CONFIG_NR_HOST_IO_REQS * sizeof(struct host_io_req), 0);
	nr_free_host_io_reqs = CONFIG_NR_HOST_IO_REQS;

	QTAILQ_INIT(&free_host_io_reqs);
	QTAILQ_INIT(&pending_host_io_dmas);
	for (int i = 0; i < CONFIG_NR_HOST_IO_REQS; i++)
		QTAILQ_INSERT_TAIL(&free_host_io_reqs, &host_io_reqs[i], qent);
}

void check_host_io_dma_done()
{
	struct host_io_req *req;

	while (1) {
		if (QTAILQ_EMPTY(&pending_host_io_dmas))
			break;

		req = QTAILQ_FIRST(&pending_host_io_dmas);
		ASSERT(is_read_req(req) || is_write_req(req));
		if (is_read_req(req)) {
			if (!check_auto_tx_dma_partial_done(req->dma_tail, req->dma_overflow_cnt))
				break;
		} else {
			if (!check_auto_rx_dma_partial_done(req->dma_tail, req->dma_overflow_cnt))
				break;
		}

		QTAILQ_REMOVE(&pending_host_io_dmas, req, qent);

		if (is_read_req(req)) {
			set_auto_nvme_cpl(req->cmd_slot_tag, 0, 0);
			dealloc_dma_data_buf(req->dma_buf_ent);
			free_host_io_req(req);
		} else {
			struct emu_req_sqe *sqe = qpair_alloc_sqe(&m->emu_req_qp);

			sqe->host_io_req = req;
			qpair_submit_sqe(&m->emu_req_qp, sqe);
		}
	}
}

void process_emu_req_cq()
{
	struct emu_req_cqe *cqe;

	while ((cqe = qpair_peek_cqe(&m->emu_req_qp)) != NULL) {
		struct host_io_req *req = cqe->host_io_req;

		if (is_read_req(req)) {
			uintptr_t addr;
			uint32_t addr_hi, addr_lo;
			if(CONFIG_ACCESS_EXACT_PPA)
				addr = (uintptr_t)req->dma_buf_ent->buf;
			else
				addr = DDR4_BUFFER_BASE_ADDR + ((size_t)req->slba) * BYTES_PER_NVME_BLOCK;

			for (int i = 0; i < req->nlb; i++, addr += BYTES_PER_NVME_BLOCK) {
				addr_hi = (addr >> 32);
				addr_lo = (addr & 0xffffffff);
				set_auto_tx_dma(req->cmd_slot_tag, i, addr_hi, addr_lo, NVME_COMMAND_AUTO_COMPLETION_OFF);
			}
			req->dma_tail = g_hostDmaStatus.fifoTail.autoDmaTx;
			req->dma_overflow_cnt = g_hostDmaAssistStatus.autoDmaTxOverFlowCnt;
			QTAILQ_INSERT_TAIL(&pending_host_io_dmas, req, qent);
		} else if (is_write_req(req) || is_dsm_req(req)){
			set_auto_nvme_cpl(req->cmd_slot_tag, 0, 0);
			dealloc_dma_data_buf(req->dma_buf_ent);
			free_host_io_req(req);
		} else{
			ASSERT(0);
		}

		qpair_consume_cqe(&m->emu_req_qp, cqe);
	}
}

struct data_buffer_qent *allocate_dma_data_buf_no_fail(unsigned int n_nvme_blocks)
{
	struct data_buffer_qent *dma_buf_ent;
	while(!(dma_buf_ent = allocate_dma_data_buf(n_nvme_blocks))){
		check_host_io_dma_done();
		process_emu_req_cq();
	}
	return dma_buf_ent;
}

#define LOGICAL_WAF_CS(stat) ((stat->nand_cs_write_bytes + stat->host_normal_write_bytes) * 1000 \
							/ (1 + stat->host_normal_write_bytes))
#define LOGICAL_WAF_ORI(stat) ((stat->host_gc_write_bytes + stat->host_normal_write_bytes) * 1000 \
							/ (1 + stat->host_normal_write_bytes))
#define PHYSICAL_WAF(stat) stat->nand_write_bytes * 1000/ (1 + stat->host_normal_write_bytes)

static inline void print_ssd_stat()
{
    struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    struct ssd_stat *stat = &m->ssd_stat;
	SSD_INFO(m, "host_normal_read_bytes:  %lu", stat->host_normal_read_bytes);
	SSD_INFO(m, "host_normal_write_bytes: %lu", stat->host_normal_write_bytes);
	SSD_INFO(m, "host_gc_read_bytes:      %lu", stat->host_gc_read_bytes);
	SSD_INFO(m, "host_gc_write_bytes:     %lu", stat->host_gc_write_bytes);
	SSD_INFO(m, "nand_cs_read_bytes:      %lu", stat->nand_cs_read_bytes);
	SSD_INFO(m, "nand_cs_write_bytes:     %lu", stat->nand_cs_write_bytes);
    SSD_INFO(m, "nand_read_bytes:         %lu", stat->nand_read_bytes);
	SSD_INFO(m, "nand_write_bytes:        %lu", stat->nand_write_bytes);
	SSD_INFO(m, "nand_gc_cnt:             %lu", stat->nand_gc_cnt);
	SSD_INFO(m, "nand_gc_read_bytes:      %lu", stat->nand_gc_read_bytes);
	SSD_INFO(m, "nand_gc_write_bytes:     %lu", stat->nand_gc_write_bytes);
	SSD_INFO(m, "nand_page_dsm_cnt:       %lu", stat->nand_page_dsm_cnt);
	SSD_INFO(m, "Write Amplification Statistics: (x1000)");
	SSD_INFO(m, "logical WAF(CS):		  %lu", LOGICAL_WAF_CS(stat));
	SSD_INFO(m, "logical WAF(ORI):        %lu", LOGICAL_WAF_ORI(stat));
	SSD_INFO(m, "physical WAF:     		  %lu", PHYSICAL_WAF(stat));
}

static void transfer_ssd_log(unsigned int cmdSlotTag, unsigned int requestedNvmeBlock)
{
	unsigned int dmaIndex, numOfNvmeBlock, devAddrH, devAddrL;
	unsigned long long devAddr;

	dmaIndex = 0;
	numOfNvmeBlock = 0;
	devAddr = (uintptr_t)&m->ssd_log_buf;
	devAddrH = (unsigned int)(devAddr >> 32);
	devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);

	FLUSH_CACHE(devAddr, requestedNvmeBlock*BYTES_PER_NVME_BLOCK);
	
	while(numOfNvmeBlock < requestedNvmeBlock)
	{
		set_auto_tx_dma(cmdSlotTag, dmaIndex, devAddrH, devAddrL, NVME_COMMAND_AUTO_COMPLETION_ON);

		numOfNvmeBlock++;
		dmaIndex++;
		devAddr += BYTES_PER_NVME_BLOCK;
		devAddrH = (unsigned int)(devAddr >> 32);
		devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
	}
}

void handle_nvme_io_read_auto(unsigned int cmdSlotTag, NVME_IO_COMMAND *nvmeIOCmd,
                         unsigned int qid, unsigned int cid)
{
	unsigned int requestedNvmeBlock, dmaIndex, numOfNvmeBlock, devAddrH, devAddrL;
	unsigned long long devAddr;

	IO_READ_COMMAND_DW12 readInfo12;
	//IO_READ_COMMAND_DW13 readInfo13;
	//IO_READ_COMMAND_DW15 readInfo15;
	unsigned int startLba[2];
	unsigned int nlb;

	readInfo12.dword = nvmeIOCmd->dword[12];
	//readInfo13.dword = nvmeIOCmd->dword[13];
	//readInfo15.dword = nvmeIOCmd->dword[15];

	startLba[0] = nvmeIOCmd->dword[10];
	startLba[1] = nvmeIOCmd->dword[11];
	nlb = readInfo12.NLB;
	ASSERT(startLba[0] < STORAGE_CAPACITY_L && (startLba[1] < STORAGE_CAPACITY_H || startLba[1] == 0));
	//ASSERT(nlb < MAX_NUM_OF_NLB);
	ASSERT((nvmeIOCmd->PRP1[0] & 0x3) == 0 && (nvmeIOCmd->PRP2[0] & 0x3) == 0); //error
	ASSERT(nvmeIOCmd->PRP1[1] < 0x10000 && nvmeIOCmd->PRP2[1] < 0x10000);

	if (readInfo12.IS_CS) {
		unsigned int cs_seq_id;
		cs_seq_id = readInfo12.CS_SEQ_ID;
		// transfer_cs_args(cmdSlotTag, qid, cid, nlb, CS_ARGS_TX);
		queue_cs_args_req(cmdSlotTag, qid, cid, nlb, cs_seq_id, CS_ARGS_TX);
		return;
	}

    dmaIndex = 0;
    requestedNvmeBlock = nlb + 1;
    devAddr = (unsigned long long)DDR4_BUFFER_BASE_ADDR + (unsigned long long)startLba[0] * (unsigned long long)BYTES_PER_NVME_BLOCK;
    devAddrH = (unsigned int)(devAddr >> 32);
    devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    numOfNvmeBlock = 0;

    while(numOfNvmeBlock < requestedNvmeBlock)
    {
        set_auto_tx_dma(cmdSlotTag, dmaIndex, devAddrH, devAddrL, NVME_COMMAND_AUTO_COMPLETION_ON);

        numOfNvmeBlock++;
        dmaIndex++;
        devAddr += BYTES_PER_NVME_BLOCK;
        devAddrH = (unsigned int)(devAddr >> 32);
        devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    }
}


void handle_nvme_io_write_auto(unsigned int cmdSlotTag, NVME_IO_COMMAND *nvmeIOCmd,
                          unsigned int qid, unsigned int cid)
{
	unsigned int requestedNvmeBlock, dmaIndex, numOfNvmeBlock, devAddrH, devAddrL;
	unsigned long long devAddr;

	IO_READ_COMMAND_DW12 writeInfo12;
	//IO_READ_COMMAND_DW13 writeInfo13;
	//IO_READ_COMMAND_DW15 writeInfo15;
	unsigned int startLba[2];
	unsigned int nlb;

	writeInfo12.dword = nvmeIOCmd->dword[12];
	//writeInfo13.dword = nvmeIOCmd->dword[13];
	//writeInfo15.dword = nvmeIOCmd->dword[15];

	//if(writeInfo12.FUA == 1)
	//	xil_printf("write FUA\r\n");

	startLba[0] = nvmeIOCmd->dword[10];
	startLba[1] = nvmeIOCmd->dword[11];
	nlb = writeInfo12.NLB;

	ASSERT(startLba[0] < STORAGE_CAPACITY_L && (startLba[1] < STORAGE_CAPACITY_H || startLba[1] == 0));
	//ASSERT(nlb < MAX_NUM_OF_NLB);
	ASSERT((nvmeIOCmd->PRP1[0] & 0xF) == 0 && (nvmeIOCmd->PRP2[0] & 0xF) == 0);
	ASSERT(nvmeIOCmd->PRP1[1] < 0x10000 && nvmeIOCmd->PRP2[1] < 0x10000);

	if (writeInfo12.IS_CS) {
		unsigned int cs_seq_id;
		cs_seq_id = writeInfo12.CS_SEQ_ID;
		// if(writeInfo12.IS_CS_HEAD)
		// 	f2fs_read_super();
		transfer_cs_args(cmdSlotTag, qid, cid, nlb, cs_seq_id, CS_ARGS_RX);
		return;
	}

    dmaIndex = 0;
    requestedNvmeBlock = nlb + 1;
    devAddr = (unsigned long long)DDR4_BUFFER_BASE_ADDR + (unsigned long long)startLba[0] * (unsigned long long)BYTES_PER_NVME_BLOCK;
    devAddrH = (unsigned int)(devAddr >> 32);
    devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    numOfNvmeBlock = 0;

    while(numOfNvmeBlock < requestedNvmeBlock)
    {
        set_auto_rx_dma(cmdSlotTag, dmaIndex, devAddrH, devAddrL, NVME_COMMAND_AUTO_COMPLETION_ON);

        numOfNvmeBlock++;
        dmaIndex++;
        devAddr += BYTES_PER_NVME_BLOCK;
        devAddrH = (unsigned int)(devAddr >> 32);
        devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    }
}

void handle_nvme_io_read(unsigned int cmdSlotTag, NVME_IO_COMMAND *nvmeIOCmd,
                         unsigned int qid, unsigned int cid)
{
	// unsigned int requestedNvmeBlock, dmaIndex, numOfNvmeBlock, devAddrH, devAddrL;
	unsigned int requestedNvmeBlock;
	// unsigned long long devAddr;
	struct host_io_req *req;
	struct emu_req_sqe *sqe;

	IO_READ_COMMAND_DW12 readInfo12;
	//IO_READ_COMMAND_DW13 readInfo13;
	//IO_READ_COMMAND_DW15 readInfo15;
	unsigned int startLba[2];
	unsigned int nlb;

	readInfo12.dword = nvmeIOCmd->dword[12];
	//readInfo13.dword = nvmeIOCmd->dword[13];
	//readInfo15.dword = nvmeIOCmd->dword[15];

	startLba[0] = nvmeIOCmd->dword[10]; // low addr
	startLba[1] = nvmeIOCmd->dword[11]; // high addr
	nlb = readInfo12.NLB;
	// ASSERT(startLba[0] < STORAGE_CAPACITY_L && (startLba[1] < STORAGE_CAPACITY_H || startLba[1] == 0));
	//ASSERT(nlb < MAX_NUM_OF_NLB);
	ASSERT((nvmeIOCmd->PRP1[0] & 0x3) == 0 && (nvmeIOCmd->PRP2[0] & 0x3) == 0); //error
	ASSERT(nvmeIOCmd->PRP1[1] < 0x10000 && nvmeIOCmd->PRP2[1] < 0x10000);

	if (readInfo12.IS_CS) {
		unsigned int cs_seq_id;
		cs_seq_id = readInfo12.CS_SEQ_ID;
#ifdef CS_DEBUG_ARGS
		xil_printf_safe("Received CS read request, seq_id: %u, is_head:%u\n", 
				cs_seq_id, readInfo12.IS_CS_HEAD);
#endif 
		// transfer_cs_args(cmdSlotTag, qid, cid, nlb, CS_ARGS_TX);
		queue_cs_args_req(cmdSlotTag, qid, cid, nlb, cs_seq_id, CS_ARGS_TX);
		return;
	}

	if(readInfo12.IS_HOST_GCIO)
		m->ssd_stat.host_gc_read_bytes += (nlb + 1) * BYTES_PER_NVME_BLOCK;
	else
		m->ssd_stat.host_normal_read_bytes += (nlb + 1) * BYTES_PER_NVME_BLOCK;

	if(readInfo12.GET_SSD_LOG) {
		reset_ssd_buffer();
		print_ssd_stat();
		transfer_ssd_log(cmdSlotTag, nlb+1);
		return;
	}

    // dmaIndex = 0;
    requestedNvmeBlock = nlb + 1;
    // devAddr = (unsigned long long)DDR4_BUFFER_BASE_ADDR + (unsigned long long)startLba[0] * (unsigned long long)BYTES_PER_NVME_BLOCK;
    // devAddrH = (unsigned int)(devAddr >> 32);
    // devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    // numOfNvmeBlock = 0;

    // while(numOfNvmeBlock < requestedNvmeBlock)
    // {
    //     set_auto_tx_dma(cmdSlotTag, dmaIndex, devAddrH, devAddrL, NVME_COMMAND_AUTO_COMPLETION_OFF);

    //     numOfNvmeBlock++;
    //     dmaIndex++;
    //     devAddr += BYTES_PER_NVME_BLOCK;
    //     devAddrH = (unsigned int)(devAddr >> 32);
    //     devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    // }

	req = alloc_host_io_req();
	req->type = NVME_OP_READ;
	req->slba = startLba[0];
	req->nlb = requestedNvmeBlock;
	req->cmd_slot_tag = cmdSlotTag;
	req->qid = qid;
	req->cid = cid;
	req->dma_buf_ent = allocate_dma_data_buf_no_fail(requestedNvmeBlock);
	// req->dma_tail = g_hostDmaStatus.fifoTail.autoDmaTx;
	// req->dma_overflow_cnt = g_hostDmaAssistStatus.autoDmaTxOverFlowCnt;
	// QTAILQ_INSERT_TAIL(&pending_host_io_dmas, req, qent);

	sqe = qpair_alloc_sqe(&m->emu_req_qp);
	sqe->host_io_req = req;
	qpair_submit_sqe(&m->emu_req_qp, sqe);
}


void handle_nvme_io_write(unsigned int cmdSlotTag, NVME_IO_COMMAND *nvmeIOCmd,
                          unsigned int qid, unsigned int cid)
{
	unsigned int requestedNvmeBlock, dmaIndex, numOfNvmeBlock, devAddrH, devAddrL;
	unsigned long long devAddr;
	struct host_io_req *req;
	struct data_buffer_qent *dma_buf_ent;
	
	IO_READ_COMMAND_DW12 writeInfo12;
	//IO_READ_COMMAND_DW13 writeInfo13;
	//IO_READ_COMMAND_DW15 writeInfo15;
	unsigned int startLba[2];
	unsigned int nlb;

	writeInfo12.dword = nvmeIOCmd->dword[12];
	//writeInfo13.dword = nvmeIOCmd->dword[13];
	//writeInfo15.dword = nvmeIOCmd->dword[15];

	//if(writeInfo12.FUA == 1)
	//	xil_printf("write FUA\r\n");

	startLba[0] = nvmeIOCmd->dword[10];
	startLba[1] = nvmeIOCmd->dword[11];
	nlb = writeInfo12.NLB;

	// ASSERT(startLba[0] < STORAGE_CAPACITY_L && (startLba[1] < STORAGE_CAPACITY_H || startLba[1] == 0));
	//ASSERT(nlb < MAX_NUM_OF_NLB);
	ASSERT((nvmeIOCmd->PRP1[0] & 0xF) == 0 && (nvmeIOCmd->PRP2[0] & 0xF) == 0);
	ASSERT(nvmeIOCmd->PRP1[1] < 0x10000 && nvmeIOCmd->PRP2[1] < 0x10000);

	if (writeInfo12.IS_CS) {
		unsigned int cs_seq_id;
		cs_seq_id = writeInfo12.CS_SEQ_ID;
		// if(writeInfo12.IS_CS_HEAD)
		// 	f2fs_read_super();
#ifdef CS_DEBUG_ARGS
		xil_printf_safe("Received CS write request, seq_id: %u, is_head:%u\n", 
				cs_seq_id, writeInfo12.IS_CS_HEAD);
#endif 
		transfer_cs_args(cmdSlotTag, qid, cid, nlb, cs_seq_id, CS_ARGS_RX);
		return;
	}
	if(writeInfo12.IS_HOST_GCIO)
		m->ssd_stat.host_gc_write_bytes += (nlb + 1) * BYTES_PER_NVME_BLOCK;
	else
	 	m->ssd_stat.host_normal_write_bytes += (nlb + 1) * BYTES_PER_NVME_BLOCK;

    dmaIndex = 0;
    requestedNvmeBlock = nlb + 1;
	dma_buf_ent = allocate_dma_data_buf_no_fail(requestedNvmeBlock);
	if(CONFIG_ACCESS_EXACT_PPA)
		devAddr = (unsigned long long) dma_buf_ent->buf;
	else
		devAddr = (unsigned long long)DDR4_BUFFER_BASE_ADDR + (unsigned long long)startLba[0] * (unsigned long long)BYTES_PER_NVME_BLOCK;
    devAddrH = (unsigned int)(devAddr >> 32);
    devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    numOfNvmeBlock = 0;

    while(numOfNvmeBlock < requestedNvmeBlock)
    {
		set_auto_rx_dma(cmdSlotTag, dmaIndex, devAddrH, devAddrL, NVME_COMMAND_AUTO_COMPLETION_OFF);

        numOfNvmeBlock++;
        dmaIndex++;
        devAddr += BYTES_PER_NVME_BLOCK;
        devAddrH = (unsigned int)(devAddr >> 32);
        devAddrL = (unsigned int)(devAddr & 0xFFFFFFFF);
    }

	req = alloc_host_io_req();
	req->type = NVME_OP_WRITE;
	req->slba = startLba[0];
	req->nlb = requestedNvmeBlock;
	req->cmd_slot_tag = cmdSlotTag;
	req->qid = qid;
	req->cid = cid;
	req->dma_tail = g_hostDmaStatus.fifoTail.autoDmaRx;
	req->dma_overflow_cnt = g_hostDmaAssistStatus.autoDmaRxOverFlowCnt;
	req->dma_buf_ent = dma_buf_ent;
	QTAILQ_INSERT_TAIL(&pending_host_io_dmas, req, qent);
}


/* Xie */
void handle_nvme_io_dsm(unsigned int cmdSlotTag, NVME_IO_COMMAND *nvmeIOCmd, unsigned int qid, unsigned int cid)
{ 
	/*nvmeIOCmd->PRP1[1]： high addr,nvmeIOCmd->PRP1[0]； low addr, */

	struct host_io_req *req;
	struct emu_req_sqe *sqe;
	struct data_buffer_qent *dma_buf_ent;
	unsigned int devAddrH, devAddrL;

	IO_DATASET_MANAGEMENT_COMMAND_DW10 dsmInfo10;
	IO_DATASET_MANAGEMENT_COMMAND_DW11 dsmInfo11;
	
	NVME_COMPLETION nvmeCPL;
	uint32_t rangeSize;

	dsmInfo10.dword = nvmeIOCmd->dword[10];
	dsmInfo11.dword = nvmeIOCmd->dword[11];

	rangeSize = (dsmInfo10.NR + 1) * sizeof(DATASET_MANAGEMENT_RANGE);
	dma_buf_ent = allocate_dma_data_buf_no_fail(
			DIVIDE_CEILING(rangeSize, BYTES_PER_NVME_BLOCK));
	devAddrH = ((unsigned long)dma_buf_ent->buf) >> 32;
	devAddrL = ((unsigned long)dma_buf_ent->buf) & 0xFFFFFFFF;
	// FLUSH_CACHE(dma_buf_ent->buf, rangeSize);
	set_direct_rx_dma(devAddrH,devAddrL, nvmeIOCmd->PRP1[1], nvmeIOCmd->PRP1[0], ALIGN_CEILING(rangeSize, 4096));
	check_direct_rx_dma_done();
	// FLUSH_CACHE(dma_buf_ent->buf, rangeSize);
	// struct dsm_range_t *range = (struct dsm_range_t *)dma_buf_ent->buf;
	// if(dsmInfo10.NR >= 34){
	// 	xil_printf("<dsm> buf_ent=0x%p, buf=0x%p NR=%d, [0].nlb=%d, [0].slba=%llx"
	// 	"[34].nlb=%d, [34].slba=%llx, [n-1].nlb=%d, [n-1].slba=%llx\n",
	// 	dma_buf_ent, dma_buf_ent->buf, dsmInfo10.NR+1, 
	// 	(range[0].lengthInLogicalBlocks), (range[0].startingLBA[0]),
	// 	(range[34].lengthInLogicalBlocks), (range[34].startingLBA[0]),
	// 	(range[dsmInfo10.NR].lengthInLogicalBlocks), (range[dsmInfo10.NR].startingLBA[0]));
	// }
	// for(int i = 0; i < dsmInfo10.NR + 1; i++){
	// 	if(range[i].ContextAttributes != 0){
	// 		xil_printf("nvme core: DSM range[%d] ContextAttributes=0x%x, lengthInLogicalBlocks=%d, startingLBA=0x%llx\n", 
	// 		i, range[i].ContextAttributes, range[i].lengthInLogicalBlocks, range[i].startingLBA[0]);
	// 	}
	// }

	req = alloc_host_io_req();
	req->type = NVME_OP_DSM;
	req->cmd_slot_tag = cmdSlotTag;
	req->qid = qid;
	req->cid = cid;
	req->dma_buf_ent = dma_buf_ent;
	req->dsm_i.numRanges = dsmInfo10.NR + 1;
	req->dsm_i.is_deallocate = dsmInfo11.AD;
	// if(dsmInfo10.NR >= 34){
	// 	xil_printf("nvme core: req=0x%p, buf_ent=0x%p, buf=0x%p\n", 
	// 	req, req->dma_buf_ent, req->dma_buf_ent->buf);
	// }
	sqe = qpair_alloc_sqe(&m->emu_req_qp);
	sqe->host_io_req = req;
	qpair_submit_sqe(&m->emu_req_qp, sqe);
}

void handle_nvme_io_cmd(NVME_COMMAND *nvmeCmd)
{
	NVME_IO_COMMAND *nvmeIOCmd;
	NVME_COMPLETION nvmeCPL;
	unsigned int opc;

	nvmeIOCmd = (NVME_IO_COMMAND*)nvmeCmd->cmdDword;

	opc = (unsigned int)nvmeIOCmd->OPC;

	switch(opc)
	{
		case IO_NVM_FLUSH:
		{
			PRINT("IO Flush Command\r\n");
			nvmeCPL.dword[0] = 0;
			nvmeCPL.specific = 0x0;
			set_auto_nvme_cpl(nvmeCmd->cmdSlotTag, nvmeCPL.specific, nvmeCPL.statusFieldWord);
			break;
		}
		case IO_NVM_WRITE:
		{
			PRINT("IO Write Command\r\n");
			handle_nvme_io_write(nvmeCmd->cmdSlotTag, nvmeIOCmd, nvmeCmd->qID, nvmeIOCmd->CID);
			// handle_nvme_io_write_auto(nvmeCmd->cmdSlotTag, nvmeIOCmd, nvmeCmd->qID, nvmeIOCmd->CID);
			break;
		}
		case IO_NVM_READ:
		{
			PRINT("IO Read Command\r\n");
			handle_nvme_io_read(nvmeCmd->cmdSlotTag, nvmeIOCmd, nvmeCmd->qID, nvmeIOCmd->CID);
			// handle_nvme_io_read_auto(nvmeCmd->cmdSlotTag, nvmeIOCmd, nvmeCmd->qID, nvmeIOCmd->CID);
			break;
		}
		case IO_NVM_DATASET_MANAGEMENT:// TODO
		{
			PRINT("IO Dsm Command\r\n");
			handle_nvme_io_dsm(nvmeCmd->cmdSlotTag, nvmeIOCmd, nvmeCmd->qID, nvmeIOCmd->CID);
			break;
		}
		default:
		{
			xil_printf("Not Support IO Command OPC: 0x%X\r\n", opc);
			ASSERT(0);
			break;
		}
	}

#if (__IO_CMD_DONE_MESSAGE_PRINT)
    xil_printf("OPC = 0x%X\r\n", nvmeIOCmd->OPC);
    xil_printf("PRP1[63:32] = 0x%X, PRP1[31:0] = 0x%X\r\n", nvmeIOCmd->PRP1[1], nvmeIOCmd->PRP1[0]);
    xil_printf("PRP2[63:32] = 0x%X, PRP2[31:0] = 0x%X\r\n", nvmeIOCmd->PRP2[1], nvmeIOCmd->PRP2[0]);
    xil_printf("dword10 = 0x%X\r\n", nvmeIOCmd->dword10);
    xil_printf("dword11 = 0x%X\r\n", nvmeIOCmd->dword11);
    xil_printf("dword12 = 0x%X\r\n", nvmeIOCmd->dword12);
#endif
}

