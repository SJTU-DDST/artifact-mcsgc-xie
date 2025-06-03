#include <stdio.h>
#include <assert.h>
#include "memory_map.h"
#include "shared_mem.h"
#include "cs_args.h"
#include "queue.h"
#include "utils.h"
#include "nvme/host_lld.h"
#include "nvme/nvme.h"

static QTAILQ_HEAD(free_reqs, cs_args_req) free_reqs;
static QTAILQ_HEAD(queued_tx_reqs, cs_args_req) queued_tx_reqs;
static QTAILQ_HEAD(pending_reqs, cs_args_req) pending_reqs;
static struct cs_args_req *cs_args_reqs;
static volatile struct shared_mem *m;
extern struct linear_allocator allocator;

static struct queued_transfer_req {
    unsigned int cmd_slot_tag;
    unsigned int qid;
    unsigned int cid;
    unsigned int nlb_0;
    int type;
} queued_req;

static int has_queued = 0;

static void init_cs_slot()
{
    for(int i = 0; i < NUM_CS_SLOTS; i++) {
        m->cs_slots[i].in_use = 0;
        m->cs_slots[i].status = CS_STATUS_IDLE;
        m->cs_slots[i].cs_seq_id = 0;
        m->cs_slots[i].rx_arg_size = 0;
        m->cs_slots[i].tx_arg_size = 0;
    }
}

static int alloc_cs_slot(unsigned int cs_seq_id)
{
    for(int i = 0; i < NUM_CS_SLOTS; i++) {
        if(!m->cs_slots[i].in_use) {
            ASSERT(m->cs_slots[i].status == CS_STATUS_IDLE);
            m->cs_slots[i].in_use = 1;
            m->cs_slots[i].cs_seq_id = cs_seq_id;
            return i;
        }
    }
    return -1;
}

static int find_cs_slot(int cs_seq_id)
{
    for(int i = 0; i < NUM_CS_SLOTS; i++) {
        if(m->cs_slots[i].in_use && m->cs_slots[i].cs_seq_id == cs_seq_id)
            return i;
    }
    return -1;
}

static void run_cs_slot(int slot_id)
{
    struct cs_req_entry *ce;
    ce = queue_alloc_entry(&m->cs_req_queue);
    ce->cs_req.slot_id = slot_id;
    m->cs_slots[slot_id].status = CS_STATUS_RUNNING;
    queue_submit_entry(&m->cs_req_queue, ce);
}

static void free_cs_slot(int slot_id)
{
    m->cs_slots[slot_id].in_use = 0;
    m->cs_slots[slot_id].status = CS_STATUS_IDLE;
    m->cs_slots[slot_id].cs_seq_id = 0;
    m->cs_slots[slot_id].rx_arg_size = 0;
    m->cs_slots[slot_id].tx_arg_size = 0;
}

void init_cs_args()
{
    QTAILQ_INIT(&free_reqs);
    QTAILQ_INIT(&queued_tx_reqs);
    QTAILQ_INIT(&pending_reqs);
    m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    cs_args_reqs = linear_malloc(&allocator, CONFIG_NR_CS_ARGS_REQS * sizeof(struct cs_args_req), 0);
    assert(cs_args_reqs != NULL);

    for (int i = 0; i < CONFIG_NR_CS_ARGS_REQS; i++) {
        cs_args_reqs[i].type = CS_ARGS_FREE;
        QTAILQ_INSERT_TAIL(&free_reqs, &cs_args_reqs[i], qent);
    }
    init_cs_slot();
    queue_init(&allocator, &m->cs_req_queue, 
        CONFIG_NR_CS_REQS, sizeof(struct cs_req_entry));
}

static inline void build_cs_arg_req(struct cs_args_req *req, 
                            unsigned int cmd_slot_tag,  
                            unsigned int qid, unsigned int cid,
                            unsigned int nlb_0, unsigned int cs_seq_id, 
                            int type)
{
    ASSERT(type == CS_ARGS_RX || type == CS_ARGS_TX);
    ASSERT(cs_seq_id < CS_SEQ_ID_SIZE);
    
    req->cmd_slot_tag = cmd_slot_tag;
    req->qid = qid;
    req->cid = cid;
    req->nlb = nlb_0 + 1;
    ASSERT(req->nlb * BYTES_PER_NVME_BLOCK <= CS_SLOT_ARG_SIZE);
    req->cs_seq_id = cs_seq_id;
    if (type == CS_ARGS_RX){
        req->cs_slot_id = alloc_cs_slot(cs_seq_id);
        m->cs_slots[req->cs_slot_id].rx_arg_size = req->nlb * BYTES_PER_NVME_BLOCK;
    }
    else if (type == CS_ARGS_TX){
        req->cs_slot_id = find_cs_slot(cs_seq_id);
        // `tx_arg_size` is set by cs_worker
    }
    ASSERT(req->cs_slot_id != -1);
    req->type = type;
#ifdef CS_DEBUG_ARGS
    xil_printf_safe("cs_args_req: cmd_slot_tag=%u, qid=%u, cid=%u, nlb=%u,"
                " cs_seq_id=%u, cs_slot_id=%u, type=%s\n",
               req->cmd_slot_tag, req->qid, req->cid, req->nlb, 
               req->cs_seq_id, req->cs_slot_id, req->type==CS_ARGS_RX?"RX":"TX");
#endif 
}

// req should be deleted from any queue before calling this function
static void do_transfer_cs_args(struct cs_args_req *req)
{
    unsigned int cs_slot_id = req->cs_slot_id;
    volatile void *arg_buf = &(m->cs_args_buf[cs_slot_id][0]);
    volatile cs_slot_t *cs_slot = &m->cs_slots[cs_slot_id];
    unsigned int arg_size = req->type == CS_ARGS_RX ? cs_slot->rx_arg_size : cs_slot->tx_arg_size;

    ASSERT(arg_size <= req->nlb*BYTES_PER_NVME_BLOCK);

    // not sure if this is needed
    if(req->type == CS_ARGS_TX)
        INVALIDATE_CACHE(arg_buf, arg_size);

#ifdef CS_DEBUG_ARGS
    xil_printf_safe("Transfering CS args, arg_buf:%p, arg_size:%u, %s\n", 
        arg_buf, arg_size, req->type == CS_ARGS_RX ? "RX" : "TX");
#endif 

    for(int i = 0; i < req->nlb; i++){
        uintptr_t addr = (uintptr_t)arg_buf + i * BYTES_PER_NVME_BLOCK;
        unsigned int addr_hi = (addr >> 32);
        unsigned int addr_lo = (addr & 0xffffffff);

        if(req->type == CS_ARGS_RX)
            set_auto_rx_dma(req->cmd_slot_tag, i, addr_hi, addr_lo, NVME_COMMAND_AUTO_COMPLETION_ON);
        else
            set_auto_tx_dma(req->cmd_slot_tag, i, addr_hi, addr_lo, NVME_COMMAND_AUTO_COMPLETION_ON);
    }

    if(req->type == CS_ARGS_RX){
        req->dma_tail = g_hostDmaStatus.fifoTail.autoDmaRx;
        req->dma_overflow_cnt = g_hostDmaAssistStatus.autoDmaRxOverFlowCnt;
    }else{
        req->dma_tail = g_hostDmaStatus.fifoTail.autoDmaTx;
        req->dma_overflow_cnt = g_hostDmaAssistStatus.autoDmaTxOverFlowCnt;
    }

    if(req->type == CS_ARGS_RX)
        FLUSH_CACHE(arg_buf, arg_size);

    QTAILQ_INSERT_TAIL(&pending_reqs, req, qent);

    MEMORY_BARRIER();

    if(req->type == CS_ARGS_RX)
        cs_slot->status = CS_STATUS_ARGS_RX;
    else
        cs_slot->status = CS_STATUS_ARGS_TX;

}

// must only be used by cs read command to get cs result
void queue_cs_args_req(unsigned int cmd_slot_tag, unsigned int qid, unsigned int cid,
                       unsigned int nlb_0, unsigned int cs_seq_id, int type)
{
    struct cs_args_req *req;

    ASSERT(type == CS_ARGS_TX);

    req = QTAILQ_FIRST(&free_reqs);
    QTAILQ_REMOVE(&free_reqs, req, qent);

    build_cs_arg_req(req, cmd_slot_tag, qid, cid, nlb_0, cs_seq_id, type);
    
    QTAILQ_INSERT_TAIL(&queued_tx_reqs, req, qent);
}

void transfer_cs_args(unsigned int cmd_slot_tag, unsigned int qid, unsigned int cid,
                      unsigned int nlb_0, unsigned int cs_seq_id, int type)
{
    struct cs_args_req *req;

    assert(!QTAILQ_EMPTY(&free_reqs));

    req = QTAILQ_FIRST(&free_reqs);
    QTAILQ_REMOVE(&free_reqs, req, qent);

    assert(req->type == CS_ARGS_FREE);
    build_cs_arg_req(req, cmd_slot_tag, qid, cid, nlb_0, cs_seq_id, type);

    do_transfer_cs_args(req);
}

void execute_queued_cs_args_reqs()
{
    struct cs_args_req *req;
    if(QTAILQ_EMPTY(&queued_tx_reqs))
        return;
    
    req = QTAILQ_FIRST(&queued_tx_reqs);
    if(m->cs_slots[req->cs_slot_id].status == CS_STATUS_DONE){
        QTAILQ_REMOVE(&queued_tx_reqs, req, qent);
        do_transfer_cs_args(req);
    }
}

void check_done_cs_args_reqs()
{
    struct cs_args_req *req, *next_req;

    QTAILQ_FOREACH_SAFE(req, &pending_reqs, qent, next_req) {
        int done = req->type == CS_ARGS_RX ?
                   check_auto_rx_dma_partial_done(req->dma_tail, req->dma_overflow_cnt) :
                   check_auto_tx_dma_partial_done(req->dma_tail, req->dma_overflow_cnt);

        if (done) {
            // printf("%s transfer complete\n", req->type == CS_ARGS_RX ? "Rx" : "Tx");
            // (*((uint32_t *)NVME_DMA_BASE_ADDR))++;
            // set_nvme_cpl(req->qid, req->cid, 0, 0);

            MEMORY_BARRIER();

            if (req->type == CS_ARGS_RX)
                run_cs_slot(req->cs_slot_id);
            else
                free_cs_slot(req->cs_slot_id);
            req->type = CS_ARGS_FREE;

            QTAILQ_REMOVE(&pending_reqs, req, qent);
            QTAILQ_INSERT_HEAD(&free_reqs, req, qent);
        }
    }
}

// int get_cs_status()
// {
//     return m->cs_status;
// }
