#ifndef CS_IO_H_
#define CS_IO_H_

#include <stdlib.h>
#include <stdint.h>
#include "queue.h"
#include "utils.h"

// struct cs_file_req;
struct nand_rw_io_req;
struct sg_io_req; // scatter/gather I/O request from CS core

struct cs_io_req {
    uint64_t seq : 60;
    uint64_t is_buf_noncacheable : 1;
    uint64_t unfinished : 1;
    uint64_t is_read : 1;
    uint64_t is_simple : 1; // simple transfer or scatter-gather transfer
    uint64_t cdma_seq;
    volatile void *buf;
    uint64_t offset, length;
    struct sg_io_req *sg_req;
    struct nand_rw_io_req *nrw_req;
    QTAILQ_ENTRY(cs_io_req) qent;
};

struct cs_io_handle {
    uint64_t seq;
    struct cs_io_req *req;
};

void schedule_cs_io_reqs();
void do_sync_cs_io_req(struct cs_io_handle *handle);
struct cs_io_handle read_from_storage(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req, struct nand_rw_io_req *nrw_req);
struct cs_io_handle write_to_storage(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req, struct nand_rw_io_req *nrw_req);
struct cs_io_handle in_storage_migration(volatile void *buf, uint64_t offset, uint64_t length, struct sg_io_req *sg_req);
void init_cs_io_reqs();

#define sync_cs_io_req(handle) \
    do { \
        do_sync_cs_io_req(handle); \
        COMPILER_BARRIER(); \
    } while (0)

#endif
