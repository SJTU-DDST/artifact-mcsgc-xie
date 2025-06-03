#ifndef DMA_DATA_BUFFER_H_
#define DMA_DATA_BUFFER_H_

#include "../shared_mem.h"

#define DMA_DATA_BUF_MAX_LEVELS 3

#define DMA_DATA_BUF_LEVEL_0_UNIT_SIZE 4
#define DMA_DATA_BUF_LEVEL_1_UNIT_SIZE 32
#define DMA_DATA_BUF_LEVEL_2_UNIT_SIZE 256 // host max I/O size?
#define DMA_DATA_BUF_LEVEL_0_UNIT_SIZE_BYTES (DMA_DATA_BUF_LEVEL_0_UNIT_SIZE * 4096)
#define DMA_DATA_BUF_LEVEL_1_UNIT_SIZE_BYTES (DMA_DATA_BUF_LEVEL_1_UNIT_SIZE * 4096)
#define DMA_DATA_BUF_LEVEL_2_UNIT_SIZE_BYTES (DMA_DATA_BUF_LEVEL_2_UNIT_SIZE * 4096)

struct list_allocator{
    QTAILQ_HEAD(free_data_buf_list, data_buffer_qent) free_list;
    QTAILQ_HEAD(used_data_buf_list, data_buffer_qent) used_list;
    struct data_buffer_qent *ents;
    unsigned int free_count;
    unsigned int used_count;
    unsigned int total_count;
    unsigned int unit_size;
};

struct data_buf_allocator{
    uintptr_t base_addr;
    uintptr_t end_addr;
    unsigned int size_bytes;
    struct list_allocator level_allocators[DMA_DATA_BUF_MAX_LEVELS];
    unsigned int levels;
};

void init_buf_allocator(uintptr_t base_addr, uintptr_t end_addr);
struct data_buffer_qent *allocate_dma_data_buf(unsigned int n_nvme_blocks);
void dealloc_dma_data_buf(struct data_buffer_qent *data_buf);

#endif