#include <stdio.h>
#include <string.h>
#include "cs_io.h"
#include "f2fs_sgio.h"
#include "memory_map.h"
#include "shared_mem.h"
#include "utils.h"
#include "xil_printf.h"
#include "stdbool.h"
#include "ftl.h"

static struct sg_io_req *sg_reqs;
uint8_t *sgio_buf;

static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
extern struct linear_allocator allocator;

static volatile struct cqe *get_cqe(int worker_id)
{
    while (1) {
        int cq_head = m->cq_head[worker_id];
        int cq_tail = m->cq_tail[worker_id];

        if ((cq_tail + 1) % NR_CQ_ENTRIES != cq_head)
            break;
    }

    return &m->cq[worker_id][m->cq_tail[worker_id]];
}

static void submit_cqe(volatile struct cqe *cqe, int worker_id)
{
    ASSERT(cqe - m->cq[worker_id] == m->cq_tail[worker_id]);

    MEMORY_BARRIER();
    m->cq_tail[worker_id] = (m->cq_tail[worker_id] + 1) % NR_CQ_ENTRIES;
}


static bool inline is_sg_io(struct sgio_info *sgi)
{
    return sgi->vc_cnt > 1;
}

enum {
    IOVEC_TO_BUF,
    BUF_TO_IOVEC,
};

static void memcpy_iovec_buf(void *buf, struct csio_vec *iovc, uint32_t vc_cnt, int direction)
{
    uint32_t i;
    for (i = 0; i < vc_cnt; i++) {
        if(direction == IOVEC_TO_BUF)
            memcpy(buf, iovc[i].buffer + iovc[i].offset, iovc[i].len);
        else
            memcpy(iovc[i].buffer + iovc[i].offset, buf, iovc[i].len);
        buf = (uint8_t *) buf + iovc[i].len;
    }
}

static struct sg_io_req *alloc_sgio_req()
{
    for (int i = 0; i < CONFIG_CS_SGIO_MAX_PENDING_REQS; i++)
        if (sg_reqs[i].req_idx == -1) {
            return &sg_reqs[i];
        }

    return NULL;
}

void signal_sgio_req_done(struct sg_io_req *sg_req)
{
    volatile struct cqe *cqe;
    int is_read;

    ASSERT(sg_req->req_op >= 0 && sg_req->req_op < NR_CSIO_OP);
    ASSERT(sg_req->req_dtype >= 0 && sg_req->req_dtype < NR_CSIO_DATA_TYPES);
    ASSERT(sg_req->req_idx >= 0 && sg_req->req_idx < REQ_IDX_MAX);

    is_read = sg_req->req_op == CSIO_READ;
    if(is_sg_io(&sg_req->sgi) && is_read)
        memcpy_iovec_buf(sg_req->sgi.buf, sg_req->sgi.io_vc, 
            sg_req->sgi.vc_cnt, BUF_TO_IOVEC);
    
    cqe = get_cqe(sg_req->worker_id);

    cqe->req_idx = sg_req->req_idx;
    cqe->req_op = sg_req->req_op;
    cqe->req_dtype = sg_req->req_dtype;

    submit_cqe(cqe, sg_req->worker_id);
    sg_req->req_idx = -1;
}

static uint64_t cs_sg_io(struct sg_io_req *sg_req)
{
    struct sgio_info *sgi = &sg_req->sgi; 
    int is_read = sg_req->req_op == CSIO_READ;
    ASSERT(sgi->length > 0);
    ASSERT(sgi->vc_cnt > 0);

    if(sg_req->req_op == CSIO_MIGRATE){
        // TODO: consider page size alignment
        m->ssd_stat.nand_read_bytes += sgi->length;
        m->ssd_stat.nand_write_bytes += sgi->length;
        m->ssd_stat.nand_cs_read_bytes += sgi->length;
        m->ssd_stat.nand_cs_write_bytes += sgi->length;
        in_storage_migration(sgi->buf, sgi->offset, sgi->length, sg_req);
        return sgi->length;
    }

    if(is_sg_io(sgi) && !is_read)
        memcpy_iovec_buf(sgi->buf, sgi->io_vc, sgi->vc_cnt, IOVEC_TO_BUF);

    if(is_read){
        m->ssd_stat.nand_read_bytes += sgi->length;
        m->ssd_stat.nand_cs_read_bytes += sgi->length;
        read_from_storage(sgi->buf, sgi->offset, sgi->length, sg_req, NULL);
    }else{
        m->ssd_stat.nand_write_bytes += sgi->length;
        m->ssd_stat.nand_cs_write_bytes += sgi->length;
        write_to_storage(sgi->buf, sgi->offset, sgi->length, sg_req, NULL);
    }

    return sgi->length;
}

static void sqe2req(volatile struct sqe *sqe, struct sg_io_req *sg_req)
{
    ASSERT(sg_req);
    ASSERT(sqe);

    sg_req->req_idx = sqe->req_idx;
    sg_req->worker_id = sqe->worker_id;
    sg_req->req_op = sqe->req_op;
    sg_req->req_dtype = sqe->req_dtype;
    sg_req->sgi.offset = storage_offset_l2p(m->ssd, sqe->offset);
    sg_req->sgi.length = sqe->length;
    sg_req->sgi.vc_cnt = sqe->vc_cnt;
    if(!is_sg_io(&sg_req->sgi))
        sg_req->sgi.buf = sqe->buf;
    else{
        volatile struct csio_vec *src = sqe->io_vc;
        struct csio_vec *dest = sg_req->sgi.io_vc;
        for (size_t i = 0; i < sqe->vc_cnt; i++) {
            dest[i] = src[i];
        }
        sg_req->sgi.buf = sgio_buf;
        // memcpy(sg_req->sgi.io_vc, sqe->io_vc, sizeof(struct csio_vec) * sqe->vc_cnt);
    }
}

void process_sq()
{
    int sq_head = m->sq_head;
    int sq_tail = m->sq_tail;

    if (sq_head == sq_tail)
        return;

    while (sq_head != sq_tail) {
        volatile struct sqe *sqe = &m->sq[sq_head];
        struct sg_io_req *sg_req;

        switch (sqe->req_op) {
        case CSIO_READ:
        case CSIO_WRITE:
        case CSIO_MIGRATE:
        /* cqe is submitted asynchronously */
            sg_req = alloc_sgio_req();
            if(!sg_req){
                /* can't process this sqe at the moment */
                MEMORY_BARRIER();
                m->sq_head = sq_head;

                return;
            }
            sqe2req(sqe, sg_req);
            cs_sg_io(sg_req);
            break;
        default:
            ASSERT(0);
        }

        sq_head = (sq_head + 1) % NR_SQ_ENTRIES;
    }

    MEMORY_BARRIER();
    m->sq_head = sq_head;
}

void init_sgio_reqs()
{
    sg_reqs = linear_malloc(&allocator, 
            CONFIG_CS_SGIO_MAX_PENDING_REQS * sizeof(struct sg_io_req), 0);
    sgio_buf = linear_malloc(&allocator, CSIO_BYTE_SIZE_MAX, 0);
    for(int i = 0; i < CONFIG_CS_SGIO_MAX_PENDING_REQS; i++)
        sg_reqs[i].req_idx = -1;
}