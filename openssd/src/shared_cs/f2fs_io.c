#include "f2fs_cs.h"
#include "debug.h"
#include "utils.h"

static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
static unsigned int curr_req_idx = 0;
extern struct linear_allocator allocator;

//==========================================================================================
//=========================Submission/Completion Queue Manipulation=========================
//==========================================================================================

static void process_cq(struct f2fs_info *fi, int cs_worker_id){
    int cq_head = m->cq_head[cs_worker_id];
    int cq_tail = m->cq_tail[cs_worker_id];
    struct f2fs_csio *csio;

    while (cq_head != cq_tail) {
        volatile struct cqe *cqe = &m->cq[cs_worker_id][cq_head];

        ASSERT(cqe->req_op < NR_CSIO_OP && cqe->req_dtype < NR_CSIO_DATA_TYPES);
        if(cqe->req_op == CSIO_MIGRATE)
            csio = fi->wi[cs_worker_id].mio;
        else
            csio = (cqe->req_op == CSIO_READ) ? fi->wi[cs_worker_id].rio[cqe->req_dtype] : 
                fi->wi[cs_worker_id].wio[cqe->req_dtype];
        ASSERT(csio->pend_io_cnt > 0);
        csio->pend_io_cnt--;
        cq_head = (cq_head + 1) % NR_CQ_ENTRIES;
    }

    MEMORY_BARRIER();
    m->cq_head[cs_worker_id] = cq_head;
}

static volatile struct sqe *get_sqe()
{
    while (1) {
        int sq_head = m->sq_head;
        int sq_tail = m->sq_tail;

        if ((sq_tail + 1) % NR_SQ_ENTRIES != sq_head)
            break;
    }

    return &m->sq[m->sq_tail];
}

static void submit_sqe(volatile struct sqe *sqe)
{
    ASSERT(sqe - m->sq == m->sq_tail);

    MEMORY_BARRIER();
    m->sq_tail = (m->sq_tail + 1) % NR_SQ_ENTRIES;
}


//==========================================================================================
//===================================Internal API of CSIO===================================
//==========================================================================================
static inline bool csio_is_mergeable(struct f2fs_csio *csio, block_t new_addr){
    return csio->vc_cnt > 0 && new_addr == csio->start_blkaddr + csio->length &&
            csio->vc_cnt < csio->vc_cnt_max &&
            csio->io_size + PAGE_SIZE <= csio->io_size_max ;
}

// csio is allocated on LOCAL ALLOCATOR
static int csio_init(struct f2fs_info *fi, struct f2fs_csio **csio, unsigned int vc_cnt_max, 
        unsigned int io_size_max, enum csio_op op_type, enum csio_dtype dtype)
{
    *csio = linear_zalloc(&allocator, sizeof(struct f2fs_csio), 1);

    if(*csio==NULL)
        goto nomem;

    if(vc_cnt_max >= CSIO_VEC_CNT_MAX)
        vc_cnt_max = CSIO_VEC_CNT_MAX;
    if(io_size_max >= CSIO_BYTE_SIZE_MAX)
        io_size_max = CSIO_BYTE_SIZE_MAX;

    (*csio)->vc_cnt_max = vc_cnt_max;
    (*csio)->io_size_max = io_size_max;
    (*csio)->op_type = op_type;
    (*csio)->dtype = dtype;
    (*csio)->fi = fi;

    (*csio)->io_vc = linear_zalloc(&allocator, vc_cnt_max * sizeof(struct csio_vec), 1);

    if((*csio)->io_vc == NULL)
        goto free;
    
    return 0;

free:
nomem:
    CS_INFO((*csio)->fi, "NOMEM: failed to allocate memory for csio");
    return -CSGC_NOMEM;
}


static void csio_reset(struct f2fs_csio *csio)
{
    csio->start_blkaddr = NULL_ADDR;
    csio->length = 0;
    
    memset(csio->io_vc, 0, csio->vc_cnt * sizeof(*csio->io_vc));
    csio->vc_cnt = 0;
    csio->io_size = 0;

    csio->nsecs_start = 0;
    csio->nsecs_end = 0;
}

static void csio_free(struct f2fs_csio *csio)
{
    return;
}

static void dump_csio_info(struct f2fs_csio *csio)
{
    CS_INFO(csio->fi, "csio info: start_blkaddr = %u, length = %u, vc_cnt = %u, io_size = %u", 
                csio->start_blkaddr, csio->length, csio->vc_cnt, csio->io_size);
    if(csio->vc_cnt > 0)
        CS_INFO(csio->fi, "last iovc info: buffer = %p, offset = %u, len = %u", 
                csio->io_vc[csio->vc_cnt - 1].buffer, csio->io_vc[csio->vc_cnt - 1].offset, 
                csio->io_vc[csio->vc_cnt - 1].len);
}

// currently all csio requests are filled into one sq/cq pair, and process_cq 
// treats them equally, so this function actually waits for all IOs(R/W, DATA/NODE/META)
// submitted before the last pended IO in the `csio` to be done
static void csio_wait_completion(struct f2fs_csio *csio, int cs_worker_id)
{
    while(csio->pend_io_cnt){
        process_cq(csio->fi, cs_worker_id);
    }
}

static void enqueue_csio(struct f2fs_csio *csio)
{
    volatile struct sqe *sqe;
#ifdef CS_DEBUG_PERF_LOCK
    unsigned long long t1, t2;
#endif
    if(csio->vc_cnt == 0)
        return;

    csio->pend_io_cnt++;

#ifdef CS_DEBUG_PERF_LOCK
    t1 = get_time_ns();
#endif
    spinlock_acquire(&m->sq_lock);
#ifdef  CS_DEBUG_PERF_LOCK
    t2 = get_time_ns();
    WORKER_I(csio->fi)->ts.lock_time += t2 - t1;
#endif
    sqe = get_sqe();
    sqe->req_idx = curr_req_idx;
    sqe->worker_id = CS_WORKER_ID;
    curr_req_idx = (curr_req_idx + 1) % REQ_IDX_MAX;
    sqe->req_op = csio->op_type;
    sqe->req_dtype = csio->dtype;
    sqe->offset = (uint64_t)csio->start_blkaddr * F2FS_BLKSIZE;
    sqe->length = csio->length * F2FS_BLKSIZE;
    sqe->vc_cnt = csio->vc_cnt;

    if(sqe->vc_cnt==1){
        sqe->buf = csio->io_vc[0].buffer;
    }else {
        volatile struct csio_vec *dest = sqe->io_vc;
        struct csio_vec *src = csio->io_vc;
        for (size_t i = 0; i < csio->vc_cnt; i++) {
            dest[i] = src[i];
        }
        // memcpy(sqe->io_vc, csio->io_vc, sizeof(struct csio_vec) * csio->vc_cnt);
    }
    submit_sqe(sqe);
    spinlock_release(&m->sq_lock);
}

// if sync == true, wait until IO is fully done
// if sync == false, return after the submission of IO
int csio_flush(struct f2fs_csio *csio, bool sync)
{   
    ASSERT(csio);
    if(csio->vc_cnt == 0 && csio->pend_io_cnt == 0)
        return 0;
    
#ifdef CS_DEBUG_IO
    csio->nsecs_start = get_time_ns();
    CS_INFO(csio->fi, "submit CSIO, sync = %d", sync);
#endif
         
    enqueue_csio(csio);
    if(sync){ // wait until IO is finished
        csio_wait_completion(csio, CS_WORKER_ID);
    }
    
#ifdef CS_DEBUG_IO
    csio->nsecs_end = get_time_ns();
    CS_INFO(csio->fi, "CSIO returns: is_read:%d dtype:%d start_blkaddr:%u length:%u |takes %llu us", 
                csio->op_type==CSIO_READ, csio->dtype, 
                csio->start_blkaddr, csio->length,
                (csio->nsecs_end - csio->nsecs_start)/1000);
#endif

    csio_reset(csio);

    return 0;
}

// for now, offset should be 0, and len should be F2FS_BLKSIZE
// force_submit = 1: the current and previously blocked IOs are guaranteed to be submitted
// sync = 1: if the there are IOs submitted, wait until the IO is done
// *flushed : is set to 1 if the previously blocked IO is flushed(for example, when 
// force_submit = 0 and csio is full), whether the newly added IO is flushed or not
// does not affect this value;
int csio_add_vec(struct f2fs_csio *csio, block_t blk_addr, uint8_t *buffer, unsigned int offset,
                unsigned int len, int force_submit, int sync, int *flushed)
{
    int err = 0;
    bool done = false;
    struct csio_vec *iovec;

    ASSERT(csio);

    if(flushed)
        *flushed = 0;

#ifdef CS_DEBUG_IO_VV
    dump_csio_info(csio);
    CS_INFO(csio->fi, "blkaddr = %u, mergeable=%d", blk_addr, csio_is_mergeable(csio, blk_addr));
#endif
    // if blkaddr is continuous and current csio has space, merge into current csio
    if(csio_is_mergeable(csio, blk_addr)){
        iovec = &csio->io_vc[csio->vc_cnt - 1];
        // memory is continuous, merge to last iovec
        if(buffer + offset == iovec->buffer + iovec->offset + iovec->len){    
            iovec->len += len;
        // memory is not continuous, add a new iovec
        }else{
            iovec = &csio->io_vc[csio->vc_cnt];
            iovec->buffer = buffer;
            iovec->offset = offset;
            iovec->len = len;
            csio->vc_cnt++;
        }
        csio->length ++;
        csio->io_size += len;

#ifdef CS_DEBUG_IO_VV
        CS_INFO(csio->fi, "blkaddr = %u, merged", blk_addr);
        dump_csio_info(csio);
#endif
        done = true;
    }

    if(flushed && !done)
        *flushed = 1;
flush:
    if((done && force_submit) || (!done && csio->vc_cnt)){
        csio_flush(csio, sync);
    }

    if(done){
        return err;
    }

    iovec = &csio->io_vc[csio->vc_cnt];
    iovec->buffer = buffer;
    iovec->offset = offset;
    iovec->len = len;

    csio->start_blkaddr = blk_addr;
    csio->vc_cnt++;
    csio->length ++;
    csio->io_size += len;
    
#ifdef CS_DEBUG_IO_VV
    CS_INFO(csio->fi, "blkaddr = %u, added", blk_addr);
    dump_csio_info(csio);
#endif
    done = true;
    goto flush;

// never reach here
    return err;
}

//==========================================================================================
//=================================FS layer CS IO interface=================================
//==========================================================================================

// init all csio of current worker
void f2fs_csio_init_all(struct f2fs_info *fi)
{
    for(int i = 0; i < NR_CSIO_DATA_TYPES; i++){
        csio_init(fi, &(WORKER_I(fi)->wio[i]), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_WRITE, i);
        csio_init(fi, &(WORKER_I(fi)->rio[i]), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_READ, i);
    }
    csio_init(fi, &(WORKER_I(fi)->mio), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_MIGRATE, CSIO_F2FS_DATA);
}

// init csio of given type of current worker
void f2fs_csio_init(struct f2fs_info *fi, enum csio_dtype dtype)
{
    csio_init(fi, &(WORKER_I(fi)->wio[dtype]), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_WRITE, dtype);
    csio_init(fi, &(WORKER_I(fi)->rio[dtype]), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_READ, dtype);
    if(dtype == CSIO_F2FS_DATA){
        csio_init(fi, &(WORKER_I(fi)->mio), CSIO_VEC_CNT_MAX, CSIO_BYTE_SIZE_MAX, CSIO_MIGRATE, dtype);
    }
}

void f2fs_csio_free_all(struct f2fs_info *fi)
{
    for(int i = 0; i < NR_CSIO_DATA_TYPES; i++){
        csio_free(WORKER_I(fi)->wio[i]);
        csio_free(WORKER_I(fi)->rio[i]);
    }
    csio_free(WORKER_I(fi)->mio);
}

void f2fs_csio_free(struct f2fs_info *fi, enum csio_dtype dtype)
{
    csio_free(WORKER_I(fi)->wio[dtype]);
    csio_free(WORKER_I(fi)->rio[dtype]);
    if(dtype == CSIO_F2FS_DATA){
        csio_free(WORKER_I(fi)->mio);
    }
}

// wait for the completion of the all csio(of current cs workers)
void f2fs_csio_wait_all_completion(struct f2fs_info *fi)
{
    unsigned long long t1 = 0, t2 = 0, t3 = 0;
    for(int i = 0; i < NR_CSIO_DATA_TYPES; i++){
#ifdef CS_DEBUG_PERF_IO
        t1 = get_time_ns();
#endif
        csio_wait_completion(WORKER_I(fi)->wio[i], CS_WORKER_ID);
#ifdef CS_DEBUG_PERF_IO
        t2 = get_time_ns();
#endif
        csio_wait_completion(WORKER_I(fi)->rio[i], CS_WORKER_ID);
#ifdef CS_DEBUG_PERF_IO
        t3 = get_time_ns();
        WORKER_I(fi)->io_time[CSIO_WRITE][i] += t2 - t1;
        WORKER_I(fi)->io_time[CSIO_READ][i] += t3 - t2;
#endif
    }
    csio_wait_completion(WORKER_I(fi)->mio, CS_WORKER_ID);
}

// wait for the given csio(of current cs worker) and all previously submmitted csio's completion
void f2fs_csio_wait_completion(struct f2fs_info *fi, enum csio_op op, enum csio_dtype dtype)
{
    unsigned long long enter_nsecs = 0, leave_nsecs = 0;
#ifdef CS_DEBUG_PERF_IO
    enter_nsecs = get_time_ns();
#endif

    if(op == CSIO_MIGRATE)
        csio_wait_completion(WORKER_I(fi)->mio, CS_WORKER_ID);
    else
        csio_wait_completion(op == CSIO_READ ? WORKER_I(fi)->rio[dtype] : WORKER_I(fi)->wio[dtype], CS_WORKER_ID);

#ifdef CS_DEBUG_PERF_IO
    leave_nsecs = get_time_ns();
    WORKER_I(fi)->io_time[op][dtype] += leave_nsecs - enter_nsecs;
#endif
#ifdef CS_DEBUG_IO
    CS_INFO(fi, "CSIO [%s] [%s] wait completion takes %llu us", 
            op == CSIO_READ ? "READ" : "WRITE",
            dtype == CSIO_F2FS_DATA ? "DATA" : 
            (dtype == CSIO_F2FS_NODE ? "NODE" : "META"),
            TIME_DIFF_us(enter_nsecs, leave_nsecs));
#endif
}

// wait for the completion of the given csio(of all cs workers)
void f2fs_csio_wait_all_worker_completion(struct f2fs_info *fi, enum csio_op op, enum csio_dtype dtype)
{
    unsigned long long enter_nsecs = 0, leave_nsecs = 0;
    for(int i = 0; i < fi->nr_cs_workers; i++){
#ifdef CS_DEBUG_PERF_IO
        enter_nsecs = get_time_ns();
#endif
        if(op == CSIO_MIGRATE)
            csio_wait_completion(fi->wi[i].mio , i);
        else
            csio_wait_completion(op == CSIO_READ ? fi->wi[i].rio[dtype] : fi->wi[i].wio[dtype], i);
#ifdef CS_DEBUG_PERF_IO
        leave_nsecs = get_time_ns();
        fi->wi[i].io_time[op][dtype] += leave_nsecs - enter_nsecs;
#endif    
    }
}

int f2fs_flush_csio(struct f2fs_info *fi, enum csio_dtype type, 
                enum csio_op csio_op)
{
    int ret = 0;
    unsigned long long enter_nsecs = 0, leave_nsecs = 0;
    struct f2fs_csio *csio;

    if(type >= NR_CSIO_DATA_TYPES){
        CS_INFO(fi, "Not supported data type: %d", type);
        return -1;
    }
    if(csio_op >= NR_CSIO_OP){
        CS_INFO(fi, "Not supported csio op: %d", csio_op);
        return -1;
    }

    if(csio_op == CSIO_MIGRATE)
        csio = WORKER_I(fi)->mio;
    else
        csio = (csio_op == CSIO_READ) ? WORKER_I(fi)->rio[type] : WORKER_I(fi)->wio[type];

#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO [%s] [%s] explicit flush", 
            csio_op == CSIO_READ ? "READ" : "WRITE",
            type == CSIO_F2FS_DATA ? "DATA" : 
            (type == CSIO_F2FS_NODE ? "NODE" : "META"));
#endif
#ifdef CS_DEBUG_PERF_IO
    enter_nsecs = get_time_ns();
#endif

    ret = csio_flush(csio, 1);

#ifdef CS_DEBUG_PERF_IO
    leave_nsecs = get_time_ns();
    WORKER_I(fi)->io_time[csio_op][type] += leave_nsecs - enter_nsecs;
#endif
#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO explicit flush returns, time = %llu ns", leave_nsecs - enter_nsecs);
#endif

    return ret;
}

int f2fs_flush_csio_async(struct f2fs_info *fi, enum csio_dtype type, 
                enum csio_op csio_op)
{
    int ret = 0;
    unsigned long long enter_nsecs = 0, leave_nsecs = 0;
    struct f2fs_csio *csio;

    if(type >= NR_CSIO_DATA_TYPES){
        CS_INFO(fi, "Not supported data type: %d", type);
        return -1;
    }
    if(csio_op >= NR_CSIO_OP){
        CS_INFO(fi, "Not supported csio op: %d", csio_op);
        return -1;
    }

    if(csio_op == CSIO_MIGRATE)
        csio = WORKER_I(fi)->mio;
    else
        csio = (csio_op == CSIO_READ) ? WORKER_I(fi)->rio[type] : WORKER_I(fi)->wio[type];

#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO [%s] [%s] explicit flush", 
            csio_op == CSIO_READ ? "READ" : "WRITE",
            type == CSIO_F2FS_DATA ? "DATA" : 
            (type == CSIO_F2FS_NODE ? "NODE" : "META"));
#endif
#ifdef CS_DEBUG_PERF_IO
    enter_nsecs = get_time_ns();
#endif

    ret = csio_flush(csio, 0);

#ifdef CS_DEBUG_PERF_IO
    leave_nsecs = get_time_ns();
    WORKER_I(fi)->io_time[csio_op][type] += leave_nsecs - enter_nsecs;
#endif
#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO explicit flush returns, time = %llu ns", leave_nsecs - enter_nsecs);
#endif

    return ret;
}

static inline bool valid_addr(struct f2fs_info *fi, block_t blk_addr, int type)
{
#ifdef CS_NOCHECK_F2FS_IO_ADDR
    return true;
#else
    if(type == CSIO_F2FS_META)
        return blk_addr >= F2FS_SB(fi)->segment0_blkaddr && \
             blk_addr < F2FS_SB(fi)->main_blkaddr ;
    else if(type < NR_CSIO_DATA_TYPES)
        return blk_addr >= F2FS_SB(fi)->main_blkaddr && \
             blk_addr < F2FS_SB(fi)->main_blkaddr + F2FS_SB(fi)->block_count ;
    else 
        return false;
#endif
}

// if force == 1, submiteed IOs will be sent to FTL core immediately and the function 
//      returns synchronously when the IO is done,
// if force == 0, submitted IOs will be merged and pended as long as it is mergeable to
//      already pended IOs. The IOs will be pended until when a non-mergeable IO arrives or
//      when struct f2fs_csio is full, or when a flush is called.
static int f2fs_submit_csio(struct f2fs_info *fi, block_t blk_addr, void *buffer,
             enum csio_dtype type, enum csio_op csio_op, int force_submit, int sync, int *flushed)
{
    int ret = 0;
    unsigned long long enter_nsecs, leave_nsecs;
    struct f2fs_csio *csio;

    if(type >= NR_CSIO_DATA_TYPES){
        CS_INFO(fi, "Not supported data type: %d", type);
        return -1;
    }
    if(csio_op >= NR_CSIO_OP){
        CS_INFO(fi, "Not supported csio op: %d", type);
        return -1;
    }
    if(!valid_addr(fi, blk_addr, type)){
        CS_INFO(fi, "Invalid block addr %u of data type %d", blk_addr, type);
        return -1;
    }

    if(csio_op == CSIO_MIGRATE)
        csio = WORKER_I(fi)->mio;
    else    
        csio = (csio_op == CSIO_READ) ? WORKER_I(fi)->rio[type] : WORKER_I(fi)->wio[type];

#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO [%s] [%s]: force = %d, sync = %d, addr = %u", 
            csio_op == CSIO_READ ? "READ" : "WRITE",
            type == CSIO_F2FS_DATA ? "DATA" : 
            (type == CSIO_F2FS_NODE ? "NODE" : "META"),
            force_submit, sync, blk_addr);
#endif
#ifdef CS_DEBUG_PERF_IO
    WORKER_I(fi)->io_byte_size[csio_op][type] += F2FS_BLKSIZE;
    enter_nsecs = get_time_ns();
#endif

    ret = csio_add_vec(csio, blk_addr, buffer, 0, F2FS_BLKSIZE, force_submit, sync, flushed);

#ifdef CS_DEBUG_PERF_IO
    leave_nsecs = get_time_ns();
    WORKER_I(fi)->io_time[csio_op][type] += leave_nsecs - enter_nsecs;
#endif
#ifdef CS_DEBUG_IO_V
    CS_INFO(fi, "CSIO returns, time = %llu ns", leave_nsecs - enter_nsecs);
#endif

    return ret;
}

// Note that the content of the buffer should not be touched until a flush is done
int f2fs_read_block(struct f2fs_info *fi, block_t blk_addr, void *buffer, 
                enum csio_dtype type, int force_submit, int sync, int *flushed)
{
    return f2fs_submit_csio(fi, blk_addr, buffer, 
            type, CSIO_READ, force_submit, sync, flushed);
}

int f2fs_write_block(struct f2fs_info *fi, block_t blk_addr, void *buffer, 
                enum csio_dtype type, int force_submit, int sync, int *flushed)
{
    return f2fs_submit_csio(fi, blk_addr, buffer, 
            type, CSIO_WRITE, force_submit, sync, flushed);
}

int f2fs_migrate_block(struct f2fs_info *fi, block_t src_addr, block_t dst_addr, 
                enum csio_dtype type, int force_submit, int sync, int *flushed)
{
    ASSERT(type==CSIO_F2FS_DATA);
    return f2fs_submit_csio(fi, dst_addr, (void *) (src_addr * 4096ULL), 
            type, CSIO_MIGRATE, force_submit,sync,flushed);
}