#include "shared_mem.h"
#include "memory_map.h"
#include "utils.h"
#include "cs_worker.h"
#include "cs_io.h"
#include "f2fs_sgio.h"
#include "cdma.h"

#include "ftl.h"
#include "queue.h"
#include "f2fs_probe.h"

#include "xil_printf.h"

struct pq_entry {
    struct host_io_req *req;
    uint64_t end_time;
    size_t pos;
    QTAILQ_ENTRY(pq_entry) qent;
};

extern struct linear_allocator allocator;
extern struct linear_allocator ssd_allocator;
static struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
static struct ssd *ssd;
static struct pqueue_t *req_pqueue;
static struct pq_entry *pq_entries;
static QTAILQ_HEAD(free_pq_entries, pq_entry) free_pq_entries;

static void spinlock_test_slave()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    spinlock_t *lock = &m->sq_lock;
    int *sum = &m->cs_worker_status[0];
    int count = 1000000;
    unsigned long long start_nsecs;

    while(m->cs_worker_status[CS_WORKER_ID] != CS_WORKER_RUNNING) {
        ;
    }
    MEMORY_BARRIER();

    start_nsecs = get_time_ns();
    for(int i = 0; i < count; i++) {
        spinlock_acquire(lock);
        (*sum)++;
        spinlock_release(lock);
    }

    xil_printf("spinlock test: slave done, time used: %llu ns\n", 
        get_time_ns() - start_nsecs);
    MEMORY_BARRIER();
    m->cs_worker_status[CS_WORKER_ID] = CS_WORKER_DONE;

}

static int req_cmp_pri(pqueue_pri_t next, pqueue_pri_t curr)
{
    return next > curr;
}

static pqueue_pri_t req_get_pri(void *a)
{
    return ((struct pq_entry *)a)->end_time;
}

static void req_set_pri(void *a, pqueue_pri_t pri)
{
    ((struct pq_entry *)a)->end_time = pri;
}

static size_t req_get_pos(void *a)
{
    return ((struct pq_entry *)a)->pos;
}

static void req_set_pos(void *a, size_t pos)
{
    ((struct pq_entry *)a)->pos = pos;
}

// ssd time emulation requests
static void init_req_pqueue(bool reset)
{
    req_pqueue = pqueue_init(&req_pqueue, EMU_REQ_QP_ENTRIES + 1, req_cmp_pri, req_get_pri, 
            req_set_pri, req_get_pos, req_set_pos, !reset);
    if(!reset)
        pq_entries = linear_malloc(&allocator, EMU_REQ_QP_ENTRIES * sizeof(struct pq_entry), 0);
    QTAILQ_INIT(&free_pq_entries);
    for (int i = 0; i < EMU_REQ_QP_ENTRIES; i++)
        QTAILQ_INSERT_TAIL(&free_pq_entries, &pq_entries[i], qent);
}

static void process_emu_req(struct emu_req_sqe *sqe)
{
    struct host_io_req *req = sqe->host_io_req;
    struct pq_entry *entry;
    uint64_t now = get_time_cycle();
    uint64_t lat = 0;

    switch(req->type){
        case NVME_OP_DSM:
            if(ssd->cfg.dsm_enabled == 1 && ssd->cfg.l2p_mapping_type != L2P_MAPPING_NON)
                lat = ssd_dsm(ssd, req, now);
            break;
        case NVME_OP_READ:
            lat = ssd_read(ssd, req, now);
            break;
        case NVME_OP_WRITE:
            lat = ssd_write(ssd, req, now);
            break;
        default:
            xil_printf("not supported emu req type: %d\n", req->type);
    }

    ASSERT(!QTAILQ_EMPTY(&free_pq_entries));
    entry = QTAILQ_FIRST(&free_pq_entries);
    QTAILQ_REMOVE(&free_pq_entries, entry, qent);

    entry->req = req;
    entry->end_time = now + lat;
    pqueue_insert(req_pqueue, entry);

    qpair_consume_sqe(&m->emu_req_qp, sqe);
}

static void send_emu_req_cqes()
{
    while (1) {
        uint64_t now;
        struct pq_entry *entry = pqueue_peek(req_pqueue);
        struct emu_req_cqe *cqe;

        if (entry == NULL || (now = get_time_cycle()) < entry->end_time)
            break;

        pqueue_pop(req_pqueue);

        cqe = qpair_alloc_cqe(&m->emu_req_qp);
        cqe->host_io_req = entry->req;
        qpair_submit_cqe(&m->emu_req_qp, cqe);

        QTAILQ_INSERT_HEAD(&free_pq_entries, entry, qent);
    }
}

static void process_emu_req_passthru(struct emu_req_sqe *sqe)
{
    struct emu_req_cqe *cqe;

    if(is_read_req(sqe->host_io_req))
        m->ssd->stat->nand_read_bytes += ALIGN_CEILING((sqe->host_io_req->nlb + 1) * BYTES_PER_NVME_BLOCK, m->ssd->sp.pgsz);
    else if(is_write_req(sqe->host_io_req))
        m->ssd->stat->nand_write_bytes += ALIGN_CEILING((sqe->host_io_req->nlb + 1) * BYTES_PER_NVME_BLOCK, m->ssd->sp.pgsz);

    cqe = qpair_alloc_cqe(&m->emu_req_qp);
    cqe->host_io_req = sqe->host_io_req;
    qpair_submit_cqe(&m->emu_req_qp, cqe);

    qpair_consume_sqe(&m->emu_req_qp, sqe);

}

static void process_emu_req_sq()
{
    struct emu_req_sqe *sqe;

    while ((sqe = qpair_peek_sqe(&m->emu_req_qp)) != NULL)
    {
        struct host_io_req *req = sqe->host_io_req;

        if(!is_dsm_req(req) && !CONFIG_ACCESS_EXACT_PPA && 
            lba_should_passthru(m->ssd, req->slba))
            process_emu_req_passthru(sqe);
        else
            process_emu_req(sqe);
    }
}

static void print_ssd_config(struct ssd_config *cfg)
{
    xil_printf("============SSD CONFIG============\n");
    xil_printf("l2p_mapping_type: %d\n", cfg->l2p_mapping_type);
    xil_printf("nand_latency_emu_enabled: %d\n", cfg->nand_latency_emu_enabled);
    xil_printf("main_area_lba: %d\n", cfg->main_area_lba);
    xil_printf("dsm_enabled: %d\n", cfg->dsm_enabled);
    xil_printf("==================================\n");
}

// modify ssd config, and reset the ssd
static void modify_ssd_config(struct ssd_config *new_cfg)
{
    if(new_cfg->l2p_mapping_type >= NR_L2P_MAPPING_TYPES){
        xil_printf("invalid l2p_mapping_type: %d\n", new_cfg->l2p_mapping_type);
        return;
    }
    if(new_cfg->nand_latency_emu_enabled != 0 && new_cfg->nand_latency_emu_enabled != 1){
        xil_printf("invalid nand_latency_emu_enabled value: %d\n", new_cfg->nand_latency_emu_enabled);
        return;
    }

    if(new_cfg->dsm_enabled != 0 && new_cfg->dsm_enabled != 1){
        xil_printf("invalid dsm_enabled value: %d\n", new_cfg->nand_latency_emu_enabled);
        return;
    }
    
    xil_printf("modifying ssd config, old config:\n");
    print_ssd_config(&ssd->cfg);

    ssd->cfg = *new_cfg;
    if(ssd->cfg.l2p_mapping_type == L2P_MAPPING_SEPARATE){
        // for now just treat it as noFTL temporarily for mkfs to work
        // should use fs-ready command to mannually set main_area_lba later
        ssd->cfg.main_area_lba = 0;
    }

    xil_printf("reset ssd\n");
    // wait until processed emu requests complete
    while(pqueue_peek(req_pqueue) != NULL)
        send_emu_req_cqes();
    
    init_req_pqueue(1);

    ssd_init(ssd, 1);

    xil_printf("new config:\n");
    print_ssd_config(&ssd->cfg);
}

static void set_ssd_opu_lba(struct ssd_config *new_cfg)
{
    if(ssd->cfg.l2p_mapping_type != L2P_MAPPING_SEPARATE){
        xil_printf("set opu lba only works for L2P_MAPPING_SEPARATE\n");
        return;
    }

    ssd->cfg.main_area_lba = new_cfg->main_area_lba;
    xil_printf("reset ssd, set start lba of OPU area to %u\n", new_cfg->main_area_lba);
    print_ssd_config(&ssd->cfg);

    // wait until processed emu requests complete
    while(pqueue_peek(req_pqueue) != NULL)
        send_emu_req_cqes();
    
    init_req_pqueue(1);

    ssd_init(ssd, 1);
}

static void execute_ssd_admin_req(struct ssd_admin_req *req)
{
    int is_ready;
    switch (req->op) {
    case SSD_ADMIN_GET_CFG:
        print_ssd_config(&ssd->cfg);
        break;
    case SSD_ADMIN_SET_CFG:
        modify_ssd_config(&req->new_cfg);
        break;
    case SSD_ADMIN_RESET_STAT:
        memset(ssd->stat, 0, sizeof(struct ssd_stat));
        break;
    case SSD_ADMIN_PROBE_FS:
        is_ready = req->new_cfg.main_area_lba;
        f2fs_probe(is_ready, &req->new_cfg.main_area_lba);
        set_ssd_opu_lba(&req->new_cfg);
        break;
    default:
        break;
    }
}

static void process_ssd_admin_sq()
{
    struct ssd_admin_sqe *sqe;
    struct ssd_admin_cqe *cqe;
    struct ssd_admin_req *req;

    while ((sqe = qpair_peek_sqe(&m->ssd_admin_qp)) != NULL) {
        req = sqe->ssd_admin_req;
        execute_ssd_admin_req(req);
        qpair_consume_sqe(&m->ssd_admin_qp, sqe);
        
        cqe = qpair_alloc_cqe(&m->ssd_admin_qp);
        cqe->ssd_admin_req = req;
        qpair_submit_cqe(&m->ssd_admin_qp, cqe);
    }
}

void do_low_level_tasks()
{
    /* anything here must be reentrant */
    schedule_cs_io_reqs();
}

void emu_main()
{
    asm volatile("msr PMCR_EL0, %0" : : "r" ((1 << 0) | (1 << 2)));
    asm volatile("msr PMCNTENSET_EL0, %0" : : "r" (1 << 31));
    
    cdma_init_ptrs();
    init_cs_io_reqs();
    init_sgio_reqs();

    qpair_init(&allocator, &m->emu_req_qp, EMU_REQ_QP_ENTRIES, sizeof(struct emu_req_sqe), sizeof(struct emu_req_cqe));
    qpair_init(&allocator, &m->ssd_admin_qp, SSD_ADMIN_REQ_QP_ENTRIES, sizeof(struct ssd_admin_req), sizeof(struct ssd_admin_req));
    init_req_pqueue(false);
    m->ssd = linear_malloc(&allocator, sizeof(struct ssd), 0);
    xil_printf("requests and queue pairs allocated\n");
    linear_allocator_get_mem_usage(&allocator, true);
    
    ssd_init(m->ssd, false);
    ssd = m->ssd;

    // ssd_test_interval_mapping(ssd);
    // ssd_init(m->ssd, true);

    signal_cpu_up(3);
    xil_printf("cpu3 is up, cs worker2(emu, io) is ready.\n");

    while(1){
        do_low_level_tasks();

        // ssd admin requests
        process_ssd_admin_sq();
        
        // CS IO requests
        process_sq();

        // host IO requests
        process_emu_req_sq();
        send_emu_req_cqes();
    }

}

