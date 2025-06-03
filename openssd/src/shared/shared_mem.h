#ifndef SHARED_MEM_H_
#define SHARED_MEM_H_

#include <stdint.h>
#include <stdbool.h>
#include "memory_map.h"
#include "utils.h"
#include "xaxicdma.h"
#include "queue.h"
#include "stdio.h"

#ifndef BYTES_PER_NVME_BLOCK
#define BYTES_PER_NVME_BLOCK 4096
#endif

// TODO: calculate from storage size, check "mkfs/f2fs_format.c" in f2fs-tools
#define F2FS_MAIN_AREA_BLKADDR 95744

#define CS_ARGS_BUFFER_SIZE (2 * 1024 * 1024)
#define SSD_LOG_BUFFER_SIZE 4096
#define NR_SQ_ENTRIES 1024
#define NR_CQ_ENTRIES 1024

#define EMU_REQ_QP_ENTRIES 65536
#define SSD_ADMIN_REQ_QP_ENTRIES 64

#define CPU0_MAGIC 0x02203321
#define CPU1_MAGIC 0x1234abcd
#define CPU2_MAGIC 0x0d000721
#define CPU3_MAGIC 0x514aa556


#define MAX_NR_CS_WORKERS 3
// if MAX_NR_CS_GC_WORKERS == 3, cs_worker0-2 will work together to do csgc
// FTL core needs to handle both nvme commands and csio requests, when 
// host IO pressure is high, csio requests will be delayed, downgrading 
// CS performance.
// if MAX_NR_CS_GC_WORKERS <= 2, cs_worker2 will serve as IO worker to 
// alleviate the pressure of FTL core
#define MAX_NR_CS_GC_WORKERS 2

struct data_buffer_qent{
    QTAILQ_ENTRY(data_buffer_qent) qent;
    uint8_t *buf;
    unsigned int size;
};

enum {
    CS_WORKER_IDLE = 0,
    CS_WORKER_RUNNING,
    CS_WORKER_DONE,
};

enum {
    CS_STATUS_IDLE = 0,
    CS_STATUS_ARGS_RX,
    CS_STATUS_RUNNING,
    CS_STATUS_DONE,
    CS_STATUS_ARGS_TX,
};

struct csio_vec {
    uint8_t *buffer;
    unsigned int offset;
    unsigned int len;
};

enum csio_op{
	CSIO_READ,
	CSIO_WRITE,
    CSIO_MIGRATE,
	NR_CSIO_OP
};

enum csio_dtype {
	CSIO_F2FS_DATA,
	CSIO_F2FS_NODE,
	CSIO_F2FS_META,
	NR_CSIO_DATA_TYPES
};

// #define CSIO_VEC_CNT_MAX 256
#define CSIO_VEC_CNT_MAX 32
#define CSIO_BYTE_SIZE_MAX (CSIO_VEC_CNT_MAX*4096) 
#define REQ_IDX_MAX 65536

struct sqe {
    int req_idx;
    int worker_id;
    enum csio_op req_op;
    enum csio_dtype req_dtype;
    uint64_t offset;
    uint32_t length;
    uint32_t vc_cnt;
    union {
        void *buf;  // used when vc_cnt == 1,
                    // IO between continuous memory regions and continuous block addresses
        struct csio_vec io_vc[CSIO_VEC_CNT_MAX];
                    // used when vc_cnt > 1, software scatter-gather IO
                    // IO between non-continuous memory regions and continuous block addresses
    };
};

struct cqe {
    int req_idx;
    enum csio_op req_op;
    enum csio_dtype req_dtype;
};

#define CS_SEQ_ID_BITS 5    // TODO: maybe larger fields
#define CS_SEQ_ID_SIZE ((1 << CS_SEQ_ID_BITS))

#define NUM_CS_SLOTS 64
#define CS_SLOT_ARG_SIZE 32*1024

typedef struct _cs_slot{
    uint8_t in_use:1;
    uint8_t status:7;
    uint8_t cs_seq_id;      // TODO: maybe larger fields
    unsigned int rx_arg_size;    // args sent from host
    unsigned int tx_arg_size;    // args to be sent to host
} cs_slot_t;

struct cs_req {
    int slot_id;
};

struct cs_req_entry{
    struct cs_req cs_req;
};

typedef enum {
    NVME_OP_READ,
    NVME_OP_WRITE,
    NVME_OP_DSM
} host_io_req_type;

typedef struct dsm_range_t
{
    unsigned int ContextAttributes;
	unsigned int lengthInLogicalBlocks;
	unsigned int startingLBA[2];
} dsm_range_t;

struct dsm_info {
    uint32_t numRanges;
    bool is_deallocate;
};

struct host_io_req
{
    host_io_req_type type;  // read, write, dsm
    // struct cs_file_req *file_req;
    uint32_t cmd_slot_tag;
    uint32_t qid, cid;      // seems useless, remove later
    struct data_buffer_qent *dma_buf_ent;
    QTAILQ_ENTRY(host_io_req) qent;
    union{
        struct {    // r/w
            uint32_t slba;
            uint32_t nlb;
            uint32_t dma_tail, dma_overflow_cnt;
        };
        struct dsm_info dsm_i; //dsm
    };
};

static inline bool is_read_req(struct host_io_req *req)
{
    return req->type == NVME_OP_READ;
}

static inline bool is_write_req(struct host_io_req *req)
{
    return req->type == NVME_OP_WRITE;
}

static inline bool is_dsm_req(struct host_io_req *req)
{
    return req->type == NVME_OP_DSM;
}

struct emu_req_sqe {
    struct host_io_req *host_io_req;
};

struct emu_req_cqe {
    struct host_io_req *host_io_req;
};

enum {
    L2P_MAPPING_NON = 0,    // no FTL emulation
    L2P_MAPPING_FULL,       // conventional FTL
    L2P_MAPPING_SEPARATE,   // sFTL for CSGC
    L2P_MAPPING_INTERVAL,   // interval mapping for IPLFS
    NR_L2P_MAPPING_TYPES,
};

struct ssd_config{
    unsigned int l2p_mapping_type;
    unsigned int nand_latency_emu_enabled;
    unsigned int main_area_lba;
    unsigned int dsm_enabled;
};

enum ssd_admin_op {
    SSD_ADMIN_GET_CFG = 0,
    SSD_ADMIN_SET_CFG,
    SSD_ADMIN_RESET_STAT,
    SSD_ADMIN_PROBE_FS,
    SSD_ADMIN_NR_OPS,
};

struct ssd_admin_req {
    enum ssd_admin_op op;
    struct ssd_config new_cfg;
    uint32_t cmd_slot_tag;
    QTAILQ_ENTRY(ssd_admin_req) qent;
};

struct ssd_admin_sqe {
    struct ssd_admin_req *ssd_admin_req;
};

struct ssd_admin_cqe {
    struct ssd_admin_req *ssd_admin_req;
};

struct ssd_stat {
    uint64_t host_normal_read_bytes;
    uint64_t host_normal_write_bytes;
    uint64_t host_gc_read_bytes;
    uint64_t host_gc_write_bytes;
    uint64_t nand_cs_read_bytes;
    uint64_t nand_cs_write_bytes;
    uint64_t nand_read_bytes;       // includes nand_normal_read, nand_cs_read, and nand_gc_read  
    uint64_t nand_write_bytes;
    uint64_t nand_gc_cnt;
    uint64_t nand_gc_read_bytes;
    uint64_t nand_gc_write_bytes;
    uint64_t nand_page_dsm_cnt;
};

struct f2fs_info;

#define CACHE_LINE_SIZE_INT32 16

struct shared_mem {
    /* looks like this must be aligned to 4KB boundaries for DMA to work*/
    uint8_t cs_args_buf[NUM_CS_SLOTS][CS_SLOT_ARG_SIZE];
    char ssd_log_buf[SSD_LOG_BUFFER_SIZE];
    uint8_t meta_relay_buf[CS_SLOT_ARG_SIZE]; // buffer used to pass metadata to next request // not used and can be removed
    uint8_t sb_buffer[3072];

    spinlock_t sq_lock; // used to protect sq_tail
    uint32_t pad[CACHE_LINE_SIZE_INT32 - 1];

    spinlock_t cdma_lock; // used to protect cdma_inst
    uint32_t pad_[CACHE_LINE_SIZE_INT32 - 1];

    spinlock_t xil_print_lock; // used to protect xil_printf
    uint32_t pad__[CACHE_LINE_SIZE_INT32 - 1];

    cs_slot_t cs_slots[NUM_CS_SLOTS];

    int cs_worker_status[MAX_NR_CS_WORKERS * CACHE_LINE_SIZE_INT32];
    int boot_magic[4];

    int cs_status;
    int fs_ready;

    // these 2 allocators are used only by cs worker 0, no concurrency protection.
    struct linear_allocator shared_allocator;   
    struct linear_allocator dma_noncache_allocator;  // buffer for data blocks to be migrated is allocated from here

    struct ssd *ssd;
    struct f2fs_info *fi;
    
    uint64_t transfer_seq;
    XAxiCdma_Config *cdma_cfg;
    XAxiCdma cdma_inst;
    unsigned long long cdma_io_bytes;
    
    // ================================================================== 
    // out-dated queue pair implementation, used for f2fs io requests 
    // TODO: replace with queue pair APIs in utils.c
    int sq_head;
    int sq_tail;
    int cq_head[MAX_NR_CS_WORKERS];
    int cq_tail[MAX_NR_CS_WORKERS];

    struct cqe cq[MAX_NR_CS_WORKERS][NR_CQ_ENTRIES];
    struct sqe sq[NR_SQ_ENTRIES];
    // ================================================================== 
    
    struct ring_queue cs_req_queue; // needs not a cq, completion is signaled by 
                                    // the `status` field in cs_slot
                                    // init by nvme core
    struct qpair emu_req_qp;    // init by emu/io_worker core
    struct qpair ssd_admin_qp;  // init by emu/io_worker core
    struct ssd_stat ssd_stat;
    uint32_t ssd_log_buf_offset;
};

static inline void reset_ssd_buffer()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    m->ssd_log_buf_offset = 0;
    for(int i = 0; i < SSD_LOG_BUFFER_SIZE/8; i++)
        ((uint64_t *) (m->ssd_log_buf))[i] = 0;
}

static inline void wait_cpu_up(int cpu_id)
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    unsigned int magic = (cpu_id==0?CPU0_MAGIC:(cpu_id==1?CPU1_MAGIC:(cpu_id==2?CPU2_MAGIC:CPU3_MAGIC)));

    while (m->boot_magic[cpu_id] != magic);

    MEMORY_BARRIER();
}

static inline void __attribute__((optimize("O0"))) signal_cpu_up(int cpu_id)
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    unsigned int magic = (cpu_id==0?CPU0_MAGIC:(cpu_id==1?CPU1_MAGIC:(cpu_id==2?CPU2_MAGIC:CPU3_MAGIC)));
 
    MEMORY_BARRIER();
    m->boot_magic[cpu_id] = magic;
}

static inline void clear_boot_magics()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    MEMORY_BARRIER();
    
    m->boot_magic[0] = 0;
    m->boot_magic[1] = 0;
    m->boot_magic[2] = 0;
    m->boot_magic[3] = 0;
}

#define SET_CS_WORKER_STATUS(m, worker_id, status) \
    do { \
        m->cs_worker_status[worker_id * CACHE_LINE_SIZE_INT32] = status; \
    } while(0)

#define GET_CS_WORKER_STATUS(m, worker_id) \
    m->cs_worker_status[worker_id * CACHE_LINE_SIZE_INT32]

#define SSD_INFO(m, string, args...) do{\
		int _tmp = snprintf(m->ssd_log_buf + m->ssd_log_buf_offset, 	\
				SSD_LOG_BUFFER_SIZE - m->ssd_log_buf_offset - 1, 				\
				"%s: " string "\n", "<OpenSSD>", ##args);		\
		if(_tmp > 0) m->ssd_log_buf_offset += _tmp;					\
	}while(0)

// #define CS_DEBUG_ARGS
#define xil_printf_safe(string, args...) do{    \
    volatile struct shared_mem *m_tmp = (struct shared_mem *) SHARED_MEM_BASE_ADDR; \
    spinlock_acquire(&m_tmp->xil_print_lock);   \
    xil_printf(string, ##args);                 \
    spinlock_release(&m_tmp->xil_print_lock);   \
}while(0)

#endif
