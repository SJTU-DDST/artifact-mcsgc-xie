#ifndef __F2FS_SGIO_H_
#define __F2FS_SGIO_H_

#include <stdlib.h>
#include <stdint.h>
#include "config.h"
#include "queue.h"
#include "shared_mem.h"

struct sgio_info{
    uint64_t offset; // storage offset
    uint32_t length; // IO byte size 
    void *buf;  // used for cdma transfer buffer
    uint32_t vc_cnt; // # of IO vecs(i.e. non-continuous memory regions)
    struct csio_vec io_vc[CSIO_VEC_CNT_MAX];
};

// mostly identical to `struct sqe` in shared_mem.h
// an intermediate structure to store parameters of a sgio request
struct sg_io_req{
    int req_idx;
    int worker_id;
    enum csio_op req_op;
    enum csio_dtype req_dtype;
    struct sgio_info sgi;
};

void init_sgio_reqs();
void process_sq();

#endif