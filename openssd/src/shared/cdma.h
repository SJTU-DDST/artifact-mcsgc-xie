#ifndef __CDMA_H
#define __CDMA_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "xaxicdma.h"
#include "xdebug.h"
#include "xil_cache.h"
#include "xparameters.h"
#include "xil_util.h"

#define CDMA_SG_DESC_MAX 32
struct transfer_descriptor {
    volatile void *src;
    volatile void *dst;
    size_t size;
};

struct sg_descriptor {
    unsigned int nr_transfers;
    size_t transfer_size;
    struct transfer_descriptor td[CDMA_SG_DESC_MAX];
};

void cdma_print_status();
void cdma_init_ptrs();
bool cdma_init();
bool cdma_init_bd_ring();
bool cdma_reset();
uint64_t cdma_transfer(volatile void *dst, volatile void *src, size_t size, bool flush_src,
                       bool flush_dst, bool check_error, bool synchronous);
uint64_t cdma_transfer_sg(struct sg_descriptor *sgd, bool flush_src, 
                        bool flush_dst, bool check_error, bool synchronous);
bool cdma_is_busy();
bool cdma_simple_transfer_done(uint64_t seq, uint32_t *error);
int cdma_sg_transfer_done(uint64_t seq, int *error);

void test_cdma_bw(int rw, volatile void *buf, uint64_t offset, uint64_t length);
void test_cdma_bw_d2d(uint64_t offset1, uint64_t offset2, uint64_t length);
void test_cdma_sg_bw_d2d(uint64_t src_ddr4_offset, uint64_t dst_ddr4_offset, 
            uint64_t nr_transfers, uint64_t interval, uint64_t transfer_size);


#endif
