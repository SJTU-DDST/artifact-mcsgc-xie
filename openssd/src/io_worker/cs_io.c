#include <assert.h>
#include "cs_io.h"
#include "f2fs_sgio.h"
#include "utils.h"
#include "memory_map.h"
#include "cdma.h"

static struct cs_io_req *cs_io_reqs;
static struct cs_io_req *current_cs_io_req; /* this is a dangling node */
static unsigned int finished_vc_cnt; // used for sg transfer
static bool current_req_retrying;
static unsigned int sg_retry_cnt;
static QTAILQ_HEAD(free_cs_io_reqs, cs_io_req) free_cs_io_reqs;
static QTAILQ_HEAD(pending_cs_io_reqs, cs_io_req) pending_cs_io_reqs;
static uint32_t cs_io_seq;
static struct sg_descriptor sg_desc;
extern struct linear_allocator allocator;

extern void do_low_level_tasks();
void signal_sgio_req_done(struct sg_io_req *sg_req);

static void sync_free_cs_io_reqs()
{
    while (QTAILQ_EMPTY(&free_cs_io_reqs))
        do_low_level_tasks();
}

static bool check_simple_cdma_transfer_done(uint64_t seq)
{
    bool busy = cdma_is_busy();
    uint32_t error = 0;

    if (cdma_simple_transfer_done(current_cs_io_req->cdma_seq, &error)) {
        if(error){
            xil_printf("cdma req(%c) failed, membuf=0x%p, storage offset=0x%p, len=%lu\n", 
                    current_cs_io_req->is_read?'r':'w', current_cs_io_req->buf, 
                    (void *)current_cs_io_req->offset, current_cs_io_req->length);
        }
        assert(current_cs_io_req->unfinished);
        current_cs_io_req->unfinished = 0;
        if (current_cs_io_req->sg_req != NULL)
            signal_sgio_req_done(current_cs_io_req->sg_req);
        // TODO: callback for nand gc io requst, if they need to be done asynchronously
        QTAILQ_INSERT_HEAD(&free_cs_io_reqs, current_cs_io_req, qent);
        current_cs_io_req = NULL;
    }

    return !busy;
}

static void do_run_sg_csio_req(struct cs_io_req *req);
#define SG_CDMA_RETRY_MAX_CNT 3

static bool check_sg_cdma_transfer_done(uint64_t seq)
{
    bool done = 0;
    int error = 0;

    finished_vc_cnt += cdma_sg_transfer_done(current_cs_io_req->cdma_seq, &error);
    done = (finished_vc_cnt == current_cs_io_req->sg_req->sgi.vc_cnt);
    if(error){
        if(sg_retry_cnt==SG_CDMA_RETRY_MAX_CNT){
            xil_printf("SG transfer failed after %d retrys\n", sg_retry_cnt);
            cdma_print_status();
            ASSERT(0);
        }
        current_req_retrying = 1;
        sg_retry_cnt++;

        // xil_printf("SG transfer failed, retrying %d-th time\n", sg_retry_cnt);
        cdma_reset();
        do_run_sg_csio_req(current_cs_io_req);
        done = false;
    }
    if (done) {
        assert(!cdma_is_busy());
        assert(current_cs_io_req->unfinished);
        current_cs_io_req->unfinished = 0;
        if (current_cs_io_req->sg_req != NULL)
            signal_sgio_req_done(current_cs_io_req->sg_req);
        QTAILQ_INSERT_HEAD(&free_cs_io_reqs, current_cs_io_req, qent);
        // xil_printf("sg csio req finished seq=%lu\n", seq);
        current_cs_io_req = NULL;
        finished_vc_cnt = 0;
        if(current_req_retrying){
            current_req_retrying = 0;
            sg_retry_cnt = 0;
        }
    }

    return done;
}

/* returns true if a new req can be submitted */
static bool check_cs_io_req_done()
{
    if(current_cs_io_req==NULL)
        return true;
    if(current_cs_io_req->is_simple)
        return check_simple_cdma_transfer_done(current_cs_io_req->seq);
    else
        return check_sg_cdma_transfer_done(current_cs_io_req->seq);
}

static void do_run_simple_csio_req(struct cs_io_req *req)
{
    void *storage_addr;
    bool should_flush = 1;

    if(req->sg_req && req->sg_req->req_op == CSIO_MIGRATE){
        req->buf = (void *)(DDR4_BUFFER_BASE_ADDR + (uint64_t)req->buf);
    }
    storage_addr = (void *)(DDR4_BUFFER_BASE_ADDR + req->offset);
    should_flush = !(req->is_buf_noncacheable);
    if (req->is_read) {
        req->cdma_seq = cdma_transfer(req->buf, storage_addr,
                                      req->length, should_flush, should_flush, 1, 0);
    } else {
        req->cdma_seq = cdma_transfer(storage_addr, req->buf,
                                      req->length, should_flush, should_flush, 1, 0);
    }
}

static void build_sg_descriptor(struct sg_descriptor *sgd, struct sgio_info *sgi)
{
    sgd->transfer_size = 0;
    for(int i = 0; i < sgi->vc_cnt; i++){
        // xil_printf("bd[0]: src=0x%lx, dst=0x%lx, size=%lu\n", 
        //         (uint64_t)(sgi->io_vc[i].buffer),
        //         (uint64_t)(sgi->offset + sgd->transfer_size),
        //         sgi->io_vc[i].len);
        sgd->td[i].src = (void *)(DDR4_BUFFER_BASE_ADDR + (uint64_t)(sgi->io_vc[i].buffer));
        sgd->td[i].dst = (void *)(DDR4_BUFFER_BASE_ADDR + sgi->offset + sgd->transfer_size);
        sgd->td[i].size = sgi->io_vc[i].len;
        sgd->transfer_size += sgd->td[i].size;
    }
    sgd->nr_transfers = sgi->vc_cnt;
    assert(sgd->transfer_size == sgi->length);
}


static void do_run_sg_csio_req(struct cs_io_req *req)
{
    struct sg_io_req *sg_req = req->sg_req;

    assert(sg_req);
    assert(sg_req->sgi.vc_cnt > 1);

    build_sg_descriptor(&sg_desc, &sg_req->sgi);

    // xil_printf("received sg csio request, seq = %lu,"
    //             " nr_transfers=%lu, transfer_size=%lu\n", 
    //         req->seq, sg_desc.nr_transfers, sg_desc.transfer_size);
    req->cdma_seq = cdma_transfer_sg(&sg_desc, 0, 0, 1, 0);
}

static void run_cs_io_req()
{
    struct cs_io_req *req;
    void *storage_addr;
    bool should_flush = 1;

    assert(current_cs_io_req == NULL);

    if (QTAILQ_EMPTY(&pending_cs_io_reqs))
        return;

    req = QTAILQ_FIRST(&pending_cs_io_reqs);
    QTAILQ_REMOVE(&pending_cs_io_reqs, req, qent);

    assert(req->unfinished);

    if(req->is_simple)
        do_run_simple_csio_req(req);
    else
        do_run_sg_csio_req(req);
    
    assert(req->cdma_seq != 0);
    current_cs_io_req = req;
}

static struct cs_io_req *alloc_cs_io_req()
{
    struct cs_io_req *req;

    sync_free_cs_io_reqs();

    req = QTAILQ_FIRST(&free_cs_io_reqs);
    QTAILQ_REMOVE(&free_cs_io_reqs, req, qent);

    return req;
}

static void do_storage_io(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req, struct nand_rw_io_req *nrw_req, int op, struct cs_io_handle *handle)
{
    struct cs_io_req *req;

    // assert(offset % 512 == 0 && length % 512 == 0);

    req = alloc_cs_io_req();
    assert(req->unfinished == 0);

    req->seq = ++cs_io_seq;
    req->unfinished = 1;
    req->is_read = (op == CSIO_READ);
    req->buf = buf;
    req->offset = offset;
    req->length = length;
    req->sg_req = sg_req;
    req->nrw_req = nrw_req;
    req->is_buf_noncacheable = 0;
    req->is_simple = 1;

    ASSERT(!(sg_req&&nrw_req));
    if(sg_req){
        req->is_buf_noncacheable = (sg_req->req_dtype==CSIO_F2FS_DATA ? 1 : 0); 
        req->is_simple = (!(op == CSIO_MIGRATE) || sg_req->sgi.vc_cnt == 1);
    }
    if(nrw_req){
        // nand gc io request must be simple
        req->is_buf_noncacheable = 1;
    }

    handle->seq = req->seq;
    handle->req = req;

    QTAILQ_INSERT_TAIL(&pending_cs_io_reqs, req, qent);

    schedule_cs_io_reqs();
}

void schedule_cs_io_reqs()
{
    if (check_cs_io_req_done())
        run_cs_io_req();
}

void do_sync_cs_io_req(struct cs_io_handle *handle)
{
    while (handle->seq == handle->req->seq && handle->req->unfinished)
        do_low_level_tasks();
}

struct cs_io_handle read_from_storage(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req, struct nand_rw_io_req *nrw_req)
{
    struct cs_io_handle handle;

    do_storage_io(buf, offset, length, sg_req, nrw_req, CSIO_READ, &handle);

    return handle;
}

struct cs_io_handle write_to_storage(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req, struct nand_rw_io_req *nrw_req)
{
    struct cs_io_handle handle;

    do_storage_io(buf, offset, length, sg_req, nrw_req, CSIO_WRITE, &handle);

    return handle;
}

// This API is really shit for compatibility reasons. the @buf is not used if is sgio, 
// and the io_vecs in @sg_req will be used instead. the @offset is the migration destination. 
// the source address is either in @buf or in @sg_req->sgi.io_vc
struct cs_io_handle in_storage_migration(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req)
{
    struct cs_io_handle handle;

    ASSERT(sg_req);
    do_storage_io(buf, offset, length, sg_req, NULL, CSIO_MIGRATE, &handle);

    return handle;

}

void init_cs_io_reqs()
{
    cs_io_reqs = linear_malloc(&allocator, CONFIG_NR_CS_IO_REQS * sizeof(struct cs_io_req), 0);
    assert(cs_io_reqs != NULL);
    current_cs_io_req = NULL;
    QTAILQ_INIT(&free_cs_io_reqs);
    QTAILQ_INIT(&pending_cs_io_reqs);
    cs_io_seq = 0;

    for (int i = 0; i < CONFIG_NR_CS_IO_REQS; i++) {
        memset(&cs_io_reqs[i], 0, sizeof(*cs_io_reqs));
        QTAILQ_INSERT_TAIL(&free_cs_io_reqs, &cs_io_reqs[i], qent);
    }
}
