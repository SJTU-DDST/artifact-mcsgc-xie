#include "dma_data_buffer.h"

struct data_buf_allocator buf_allocator;
extern struct linear_allocator allocator;

void init_buf_allocator(uintptr_t base_addr, uintptr_t end_addr)
{
    unsigned int l0_ratio = 1, l1_ratio = 1, l2_ratio = 18;
    unsigned int level_size[DMA_DATA_BUF_MAX_LEVELS];
    unsigned int total_size = end_addr - base_addr;

    ASSERT(base_addr % DMA_DATA_BUF_LEVEL_2_UNIT_SIZE_BYTES == 0);

    xil_printf("buf allocator, addr = 0x%p, size = %u B\n", &buf_allocator, sizeof(buf_allocator));
    
    buf_allocator.base_addr = base_addr;
    buf_allocator.end_addr = end_addr;
    buf_allocator.size_bytes = total_size;
    buf_allocator.levels = DMA_DATA_BUF_MAX_LEVELS;

    level_size[2] = ALIGN_CEILING(total_size * 
            l2_ratio / (l0_ratio + l1_ratio + l2_ratio), 
            DMA_DATA_BUF_LEVEL_2_UNIT_SIZE_BYTES);
    
    total_size -= level_size[2];
    level_size[1] = ALIGN_CEILING(total_size * 
            l1_ratio / (l0_ratio + l1_ratio), 
            DMA_DATA_BUF_LEVEL_1_UNIT_SIZE_BYTES);
    total_size -= level_size[1];
    level_size[0] = total_size;
    
    for (int i = 0; i < DMA_DATA_BUF_MAX_LEVELS; i++)
    {        
        unsigned int unit_size = (i == 0 ? DMA_DATA_BUF_LEVEL_0_UNIT_SIZE_BYTES : 
                                (i == 1 ? DMA_DATA_BUF_LEVEL_1_UNIT_SIZE_BYTES : 
                                DMA_DATA_BUF_LEVEL_2_UNIT_SIZE_BYTES));
        uintptr_t level_base_addr = base_addr + (i == 0 ? 0 : 
                                    (i == 1 ? level_size[0] : 
                                    (level_size[0] + level_size[1])));
        uintptr_t level_end_addr = level_base_addr + level_size[i];
        buf_allocator.level_allocators[i].unit_size = unit_size;
        buf_allocator.level_allocators[i].total_count = level_size[i] / unit_size;
        buf_allocator.level_allocators[i].free_count = buf_allocator.level_allocators[i].total_count;
        buf_allocator.level_allocators[i].used_count = 0;

        buf_allocator.level_allocators[i].ents = linear_malloc(&allocator,
                buf_allocator.level_allocators[i].total_count * sizeof(struct data_buffer_qent), 0);
        
        QTAILQ_INIT(&buf_allocator.level_allocators[i].free_list);
        QTAILQ_INIT(&buf_allocator.level_allocators[i].used_list);
        for(int j = 0; j < buf_allocator.level_allocators[i].total_count; j++)
        {
            struct data_buffer_qent *ent = &buf_allocator.level_allocators[i].ents[j];
            ent->buf = (uint8_t *) (level_base_addr + j * unit_size);
            ent->size = unit_size;
            QTAILQ_INSERT_TAIL(&buf_allocator.level_allocators[i].free_list, ent, qent);
        }
        xil_printf("dma data buffer level %d: %u units, unit size %uB, level size %uB\n", 
            i, buf_allocator.level_allocators[i].total_count, unit_size, level_size[i]);
    }
}

struct data_buffer_qent *allocate_dma_data_buf(unsigned int n_nvme_blocks)
{
    struct data_buffer_qent *data_buf = NULL;
    struct list_allocator *level_allocator = NULL;
    unsigned int level = 0;

    if (n_nvme_blocks <= DMA_DATA_BUF_LEVEL_0_UNIT_SIZE)
        level = 0;
    else if (n_nvme_blocks <= DMA_DATA_BUF_LEVEL_1_UNIT_SIZE)
        level = 1;
    else if (n_nvme_blocks <= DMA_DATA_BUF_LEVEL_2_UNIT_SIZE)
        level = 2;
    else
        ASSERT(0);

    while(level < DMA_DATA_BUF_MAX_LEVELS && 
            buf_allocator.level_allocators[level].free_count == 0)
        level++;
    if(level >= DMA_DATA_BUF_MAX_LEVELS)
        return NULL;

    level_allocator = &buf_allocator.level_allocators[level];
    ASSERT(!QTAILQ_EMPTY(&level_allocator->free_list));
    data_buf = QTAILQ_FIRST(&level_allocator->free_list);
    QTAILQ_REMOVE(&level_allocator->free_list, data_buf, qent);
    QTAILQ_INSERT_TAIL(&level_allocator->used_list, data_buf, qent);
    level_allocator->free_count--;
    level_allocator->used_count++;

    return data_buf;
}

void dealloc_dma_data_buf(struct data_buffer_qent *data_buf)
{
    struct list_allocator *level_allocator = NULL;
    unsigned int level = 0;

    if (data_buf->size <= DMA_DATA_BUF_LEVEL_0_UNIT_SIZE_BYTES)
        level = 0;
    else if (data_buf->size <= DMA_DATA_BUF_LEVEL_1_UNIT_SIZE_BYTES)
        level = 1;
    else if (data_buf->size <= DMA_DATA_BUF_LEVEL_2_UNIT_SIZE_BYTES)
        level = 2;
    else
        ASSERT(0);

    level_allocator = &buf_allocator.level_allocators[level];
    QTAILQ_REMOVE(&level_allocator->used_list, data_buf, qent);
    QTAILQ_INSERT_TAIL(&level_allocator->free_list, data_buf, qent);
    level_allocator->free_count++;
    level_allocator->used_count--;
}