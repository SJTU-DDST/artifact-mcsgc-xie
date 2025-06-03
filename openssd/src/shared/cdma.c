#include "cdma.h"
#include "utils.h"
#include "shared_mem.h"
#include "xil_mmu.h"

static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

static uint64_t *transfer_seq;
static XAxiCdma_Config *cdma_cfg;
static XAxiCdma *cdma_inst;
static spinlock_t *cdma_lock;

static int bd_submitted = 0;
static int bd_done = 0;

uint32_t cdma_status = 0;               // check when error occurs
XAxiCdma_Bd *curr_hw_bd_ptr = NULL;     // current BD in hardware, check when error occurs

static uint32_t cdma_get_error(XAxiCdma *cdma_inst)
{
    cdma_status = XAxiCdma_ReadReg(cdma_inst->BaseAddr, XAXICDMA_SR_OFFSET);
    return cdma_status & XAXICDMA_SR_ERR_ALL_MASK;
}

void cdma_print_status()
{
    xil_printf("CDMA status register %08x, error bit %x, error mask %x\n"
                , cdma_status, cdma_status & XAXICDMA_SR_ERR_ALL_MASK, XAXICDMA_SR_ERR_ALL_MASK);
    if(curr_hw_bd_ptr != NULL)
        xil_printf("curr_bd in hardware: src 0x%lx, dst 0x%lx, size %u\n", 
                XAxiCdma_BdGetSrcBufAddr(curr_hw_bd_ptr),
                XAxiCdma_BdGetDstBufAddr(curr_hw_bd_ptr),
                XAxiCdma_BdGetLength(curr_hw_bd_ptr));
}

static bool cdma_check_error()
{
    uint32_t error;

    error = cdma_get_error(cdma_inst);
    if (error != 0) {
        xil_printf("CDMA error %08x, trying reset\n", error);

        XAxiCdma_Reset(cdma_inst);
        while (!XAxiCdma_ResetIsDone(cdma_inst));

        error = cdma_get_error(cdma_inst);
        if (error != 0) {
            xil_printf("CDMA error %08x after reset; something is wrong\n");
            return false;
        }
    }

    return true;
}

void cdma_init_ptrs()
{
    transfer_seq = &m->transfer_seq;
    cdma_cfg = m->cdma_cfg;
    cdma_inst = &m->cdma_inst;
    cdma_lock = &m->cdma_lock;
}

bool cdma_init_bd_ring();

bool cdma_init()
{
    uint32_t status;

    m->cdma_cfg = XAxiCdma_LookupConfig(XPAR_AXICDMA_0_DEVICE_ID);
    if (m->cdma_cfg == NULL) {
        xil_printf("XAxiCdma_LookupConfig failed\n");
        return false;
    }
    spinlock_init(&m->cdma_lock);

    cdma_init_ptrs();

    *transfer_seq = 1;

    status = XAxiCdma_CfgInitialize(cdma_inst, cdma_cfg, cdma_cfg->BaseAddress);
    if (status != XST_SUCCESS) {
        xil_printf("XAxiCdma_CfgInitialize failed\n");
        return false;
    }

    XAxiCdma_IntrDisable(cdma_inst, XAXICDMA_XR_IRQ_ALL_MASK);

    cdma_init_bd_ring();

    return cdma_check_error();
}


#define MARK_UNCACHEABLE 0x701

bool cdma_init_bd_ring()
{
    uint32_t status;
    XAxiCdma_Bd BdTemplate;
    int bd_cnt;
    
    // init BD ring for SG transfers
    bd_cnt = XAxiCdma_BdRingCntCalc(XAXICDMA_BD_MINIMUM_ALIGNMENT, 
                                BD_SPACE_HIGH - BD_SPACE_BASE + 1, 
                                (UINTPTR)BD_SPACE_BASE);
    status = XAxiCdma_BdRingCreate(cdma_inst, 
                BD_SPACE_BASE, BD_SPACE_BASE, 
                XAXICDMA_BD_MINIMUM_ALIGNMENT,bd_cnt);
    if (status != XST_SUCCESS) {
        xil_printf("XAxiCdma_BdRingCreate failed, status = %d\n", status);
    }

    XAxiCdma_BdClear(&BdTemplate);
	status = XAxiCdma_BdRingClone(cdma_inst, &BdTemplate);
	if (status != XST_SUCCESS) {
		xil_printf("Clone BD ring failed %d\r\n", status);
	}

    return cdma_check_error(); 
}

#define CDMA_RESET_TIMEOUT_US 1000000
bool cdma_reset()
{
    int status;
    unsigned long long timeout_us = CDMA_RESET_TIMEOUT_US;

    XAxiCdma_Reset(cdma_inst);
    while (timeout_us && !XAxiCdma_ResetIsDone(cdma_inst)){
        nsleep(1*1000);
        timeout_us -= 1;
    }

    XAxiCdma_IntrDisable(cdma_inst, XAXICDMA_XR_IRQ_ALL_MASK);
    
    cdma_init_bd_ring();

    cdma_status = 0;
    curr_hw_bd_ptr = NULL;
    
    return cdma_check_error();
}

static void reset_sg_handler_fifo(XAxiCdma *cdma_instance)
{
    cdma_instance->SgHandlerHead = cdma_instance->SgHandlerTail;
}

uint64_t cdma_transfer(volatile void *dst, volatile void *src, size_t size, bool flush_src,
                       bool flush_dst, bool check_error, bool synchronous)
{
    uint64_t ret = ++(*transfer_seq);
    uint32_t status;

    /* wait for previous async transfer to complete */
    while (XAxiCdma_IsBusy(cdma_inst));

    spinlock_acquire(cdma_lock);
    if (check_error && !cdma_check_error())
        return 0;

    if (flush_src)
        FLUSH_CACHE(src, size);
    if (flush_dst)
        FLUSH_CACHE(dst, size);

    m->cdma_io_bytes += size;
    status = XAxiCdma_SimpleTransfer(cdma_inst, (UINTPTR)src, (UINTPTR)dst, size, NULL, NULL);
    if (status != XST_SUCCESS) {
        xil_printf("XAxiCdma_SimpleTransfer failed\n");
        cdma_check_error();
        xil_printf("cdma_transfer: dst %p, src %p, size %u, flush_src %d, flush_dst %d\n",
                    dst, src, size, flush_src, flush_dst);
        return 0;
    }

    while (synchronous && XAxiCdma_IsBusy(cdma_inst));

    if (synchronous && check_error && !cdma_check_error()){
        ret = 0;
    }

    if(synchronous)
        spinlock_release(cdma_lock);

    return ret;
}

// modified from https://github.com/Xilinx/embeddedsw/blob/master/XilinxProcessorIPLib/drivers/axicdma/examples/xaxicdma_example_sg_poll.c#L250
static int CheckSgTransferCompletion(XAxiCdma *InstancePtr, int *Done, int *Error)
{
	int BdCount;
	XAxiCdma_Bd *BdPtr;
	XAxiCdma_Bd *BdCurPtr;
	int Status;
	int Index;

	/* Check whether the hardware has encountered any problems.
	 * In some error cases, the DMA engine may not able to update the
	 * BD that has caused the problem.
	 */
	if (cdma_get_error(InstancePtr) != 0x0) {
		// xil_printf("Transfer error %x\r\n",
		// 	    (unsigned int)XAxiCdma_GetError(InstancePtr));
        BdPtr = XAxiCdma_BdRingGetCurrBd(InstancePtr);
        // xil_printf("curr_bd in hardware: src 0x%lx, dst 0x%lx, size %u\n", 
        //         XAxiCdma_BdGetSrcBufAddr(BdPtr),
        //         XAxiCdma_BdGetDstBufAddr(BdPtr),
        //         XAxiCdma_BdGetLength(BdPtr));
        *Error = 1;
        bd_submitted = 0;
        bd_done = 0;
        // ASSERT(0);
		return 0;
	}

	/* Get all processed BDs from hardware
	 */
	BdCount = XAxiCdma_BdRingFromHw(InstancePtr, XAXICDMA_ALL_BDS, &BdPtr);

	/* Check finished BDs then release them
	 */
	if (BdCount > 0) {
		BdCurPtr = BdPtr;
		for (Index = 0; Index < BdCount; Index++) {
			/* If the completed BD has error bit set,
			 * then the example fails
			 */
			if (XAxiCdma_BdGetSts(BdCurPtr) &
			    XAXICDMA_BD_STS_ALL_ERR_MASK)	{
                *Error = 1;
				return 0;
			}
			BdCurPtr = XAxiCdma_BdRingNext(InstancePtr, BdCurPtr);
		}

		/* Release the BDs so later submission can use them
		 */
		Status = XAxiCdma_BdRingFree(InstancePtr, BdCount, BdPtr);
		if (Status != XST_SUCCESS) {
			xil_printf("Error free BD %x\r\n", Status);
            *Error = 1;
			return 0;
		}

		(*Done) += BdCount;
        bd_done += BdCount;
        if(bd_done == bd_submitted){
            bd_done = 0;
            bd_submitted = 0;
            spinlock_release(cdma_lock);
        }
	}

    if(InstancePtr->HwBdCnt == 0){
        // xil_printf("sg transfer done, reset sg handler FIFO\n" 
        //             "before reset, sg handler head %lx, sg handler tail %lx\n",
        //              (unsigned long)InstancePtr->SgHandlerHead, 
        //              (unsigned long)InstancePtr->SgHandlerTail);
        reset_sg_handler_fifo(InstancePtr);
    }

	return (*Done);
}

// see https://xilinx.github.io/embeddedsw.github.io/axicdma/doc/html/api/group__axicdma.html
static uint32_t do_sg_transfer(struct sg_descriptor *sgd, bool synchronous)
{
    uint32_t ret;
    XAxiCdma_Bd *bd_set = NULL, *bd_cur = NULL;
    int done = 0, error = 0;
    int timeout_us = 1000000; // 1s

    ASSERT(bd_submitted == 0);
    bd_submitted = sgd->nr_transfers;
    bd_done = 0;
    ret = XAxiCdma_BdRingAlloc(cdma_inst, sgd->nr_transfers, &bd_set);
    if (ret != XST_SUCCESS) {
        xil_printf("XAxiCdma_BdRingAlloc failed, ret = %u\n", ret);
        return ret;
    }

    bd_cur = bd_set;
    for(int i = 0; i < sgd->nr_transfers; i++) {
        struct transfer_descriptor *td = &sgd->td[i];

        ret = XAxiCdma_BdSetSrcBufAddr(bd_cur, (UINTPTR) td->src);
        if (ret != XST_SUCCESS) {
			xil_printf("Set src addr failed %d, %lx/%lx\r\n",
				    ret, (unsigned long)bd_cur,
				    (unsigned long)td->src);
			return XST_FAILURE;
		}
        XAxiCdma_BdSetDstBufAddr(bd_cur, (UINTPTR) td->dst);
        XAxiCdma_BdSetLength(bd_cur, td->size);

        // xil_printf("set bd %d: src %lx, dst %lx, size %u\n", i, 
        //         XAxiCdma_BdGetSrcBufAddr(bd_cur),
        //         XAxiCdma_BdGetDstBufAddr(bd_cur),
        //         XAxiCdma_BdGetLength(bd_cur));
        bd_cur = XAxiCdma_BdRingNext(cdma_inst, bd_cur);
    }

    ret = XAxiCdma_BdRingToHw(cdma_inst, sgd->nr_transfers, bd_set, NULL, NULL);
    if (ret != XST_SUCCESS) {
		xil_printf("Failed to hw %d\r\n", ret);
		return XST_FAILURE;
	}
    // xil_printf("submitted sg transfer to hw\n" 
    //         "after submission, sg handler head %lx, sg handler tail %lx\n",
    //             (unsigned long)cdma_inst->SgHandlerHead, 
    //             (unsigned long)cdma_inst->SgHandlerTail);

    if(!synchronous)
        return XST_SUCCESS;

    while(timeout_us){
        if((CheckSgTransferCompletion(cdma_inst, &done, 
            &error) >= sgd->nr_transfers) && !error){
            break;
        }
        timeout_us--;
        nsleep(1*1000);
    }

    if(error){
        xil_printf("SG transfer failed\n");
        return XST_FAILURE;
    }

    return XST_SUCCESS;
}

uint64_t cdma_transfer_sg(struct sg_descriptor *sgd, bool flush_src, 
                            bool flush_dst, bool check_error, bool synchronous)
{
    uint64_t ret = ++(*transfer_seq);
    uint32_t status;

    /* wait for previous async transfer to complete */
    while (XAxiCdma_IsBusy(cdma_inst));

    spinlock_acquire(cdma_lock);

    if (check_error && !cdma_check_error())
        return 0;

    ASSERT(sgd->nr_transfers <= CDMA_SG_DESC_MAX);
    if(flush_src)
        for(int i = 0; i < sgd->nr_transfers; i++)
            FLUSH_CACHE(sgd->td[i].src, sgd->td[i].size);
    if(flush_dst)
        for(int i = 0; i < sgd->nr_transfers; i++)
            FLUSH_CACHE(sgd->td[i].dst, sgd->td[i].size);

    m->cdma_io_bytes += sgd->transfer_size;

    status = do_sg_transfer(sgd, synchronous);

    if (status != XST_SUCCESS) {
        cdma_check_error();
        ASSERT(0);
        return 0;
    }

    if (check_error && !cdma_check_error())
        ret = 0;

    return ret;
}

bool cdma_is_busy()
{
    return XAxiCdma_IsBusy(cdma_inst);
}

bool cdma_simple_transfer_done(uint64_t seq, uint32_t *error)
{
    bool done = seq != *transfer_seq || !XAxiCdma_IsBusy(cdma_inst);
    if(done)
        spinlock_release(cdma_lock);
    *error = cdma_get_error(cdma_inst);
    return done;
}

int cdma_sg_transfer_done(uint64_t seq, int *error)
{
    int done = 0;
    CheckSgTransferCompletion(cdma_inst, &done, error);
    return done;
}

void test_cdma_bw(int rw, volatile void *buf, uint64_t offset, uint64_t length)
{
    unsigned long long t1, t2;
    void *storage_addr = (void *)(DDR4_BUFFER_BASE_ADDR + offset);
    t1 = get_time_ns();
    if(rw==0)//read
        cdma_transfer(buf, storage_addr, length, 1, 1, 1, 1);
    else
        cdma_transfer(storage_addr, buf, length, 1, 1, 1, 1);
    t2 = get_time_ns();
    xil_printf("bare cdma transfer bandwidth: %llu MB/s\n", 
            length*1000000000ULL/(t2-t1)/(1<<20));

    t1 = get_time_ns();
    if(rw==0)//read
        cdma_transfer(buf, storage_addr, length, 0, 0, 1, 1);
    else
        cdma_transfer(storage_addr, buf, length, 0, 0, 1, 1);
    t2 = get_time_ns();
    xil_printf("bare cdma transfer bandwidth(no flush): %llu MB/s\n", 
            length*1000000000ULL/(t2-t1)/(1<<20));
}

// ddr4 to ddr4 cdma
void test_cdma_bw_d2d(uint64_t offset1, uint64_t offset2, uint64_t length)
{
    unsigned long long t1, t2;
    void *storage_addr1 = (void *)(DDR4_BUFFER_BASE_ADDR + offset1);
    void *storage_addr2 = (void *)(DDR4_BUFFER_BASE_ADDR + offset2);
    t1 = get_time_ns();
    cdma_transfer(storage_addr2, storage_addr1, length, 1, 1, 1, 1);
    t2 = get_time_ns();
    xil_printf("bare cdma transfer bandwidth: %llu MB/s\n", 
            length*1000000000ULL/(t2-t1)/(1<<20));

    t1 = get_time_ns();
    cdma_transfer(storage_addr2, storage_addr1, length, 0, 0, 1, 1);
    t2 = get_time_ns();
    xil_printf("bare cdma transfer bandwidth(no flush): %llu MB/s\n", 
            length*1000000000ULL/(t2-t1)/(1<<20));
}

void test_cdma_sg_bw_d2d(uint64_t src_ddr4_offset, uint64_t dst_ddr4_offset, 
            uint64_t nr_transfers, uint64_t interval, uint64_t transfer_size)
{
    volatile struct shared_mem *m = (volatile struct shared_mem *) SHARED_MEM_BASE_ADDR;
    unsigned long long t1, t2;
    struct sg_descriptor sgd;
    struct transfer_descriptor *td = sgd.td;
    void *storage_addr1 = (void *)(DDR4_BUFFER_BASE_ADDR + src_ddr4_offset);
    void *storage_addr2 = (void *)(DDR4_BUFFER_BASE_ADDR + dst_ddr4_offset);
    void *buf = linear_malloc(&m->dma_noncache_allocator, 
            transfer_size*nr_transfers, 0);
    int i;
    
    xil_printf("test cdma sg bandwidth, nr_transfers=%lu, "
            "interval=%lu, transfer_size=%lu\n", 
            nr_transfers, interval, transfer_size);
    
    sgd.nr_transfers = nr_transfers;
    sgd.transfer_size = nr_transfers * transfer_size;
    for(int i = 0; i < nr_transfers; i++){
        td[i].src = storage_addr1 + i*interval*transfer_size;
        td[i].dst = storage_addr2 + i*transfer_size;
        td[i].size = transfer_size;
    }

    xil_printf("init src buffer\n");
    for(i = 0; i < nr_transfers; i++){
        memset(buf+i*transfer_size, i, transfer_size);
    }
    
    t1 = get_time_ns();
    for(i = 0; i < nr_transfers; i++){
        cdma_transfer(td[i].src, buf+i*transfer_size, transfer_size, 
                0, 0, 1, 1);        
    }
    t2 = get_time_ns();
    xil_printf("write %lu buffers (%lu B)to ddr4 source address, "
                "takes %llu us, bandwidth=%lluMB/s\n", nr_transfers, transfer_size,
                (t2-t1)/1000, sgd.transfer_size*1000000000ULL/(t2-t1)/(1<<20));     

    xil_printf("start cdma sg transfer\n");
    t1 = get_time_ns();
    cdma_transfer_sg(&sgd, 0, 0, 1, 1);  
    t2 = get_time_ns();
    xil_printf("cdma sg transfer takes %llu us, bandwidth=%lluMB/s, "
                "total transfer size: %lu, src buffer interval: %lu\n",
                (t2-t1)/1000, sgd.transfer_size*1000000000ULL/(t2-t1)/(1<<20),
                sgd.transfer_size, interval);
    

    xil_printf("read result from dst\n");
    t1 = get_time_ns();
    for(i = 0; i < nr_transfers; i++){
        cdma_transfer(buf+i*transfer_size, td[i].dst, transfer_size, 
                0, 0, 1, 1);        
    }
    t2 = get_time_ns();
    xil_printf("read %lu buffers (%lu B)from ddr4 dst address, "
                "takes %llu us, bandwidth=%lluMB/s\n", nr_transfers, transfer_size,
                (t2-t1)/1000, sgd.transfer_size*1000000000ULL/(t2-t1)/(1<<20));
    
    xil_printf("check data validity\n");
    for(i = 0; i < nr_transfers; i++){
        for(int j = 0; j < transfer_size; j++){
            if(((char *)buf)[i*transfer_size+j] != i){
                xil_printf("data mismatch(%hhu!=%hhu) at %d-th transfer, "
                    "offset = %d\n", ((char *)buf)[i*transfer_size+j], i, i, j);
                break;
            }
        }
        xil_printf("%d-th transfer data is correct\n", i);
    }

    xil_printf("transfer data respectively\n");
    t1 = get_time_ns();
    for(i = 0; i < nr_transfers; i++){
        cdma_transfer(td[i].dst, td[i].src, td[i].size, 
                   0, 0, 1, 1);
    }
    t2 = get_time_ns();
    xil_printf("transfer %lu buffers (%lu B)from ddr4 src address to ddr4 dst address, "
                "takes %llu us, bandwidth = %lluMB/s\n", nr_transfers, transfer_size,
                (t2-t1)/1000, sgd.transfer_size*1000000000ULL/(t2-t1)/(1<<20));

    linear_malloc_reset(&m->dma_noncache_allocator);
}
