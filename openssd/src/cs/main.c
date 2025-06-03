#include "xil_cache.h"
#include "xil_exception.h"
#include "xil_mmu.h"
#include "xscugic_hw.h"
#include "xscugic.h"
#include "memory_map.h"
#include "shared_mem.h"
#include "utils.h"

XScuGic GicInstance;
struct linear_allocator allocator;

static void __attribute__((optimize("O0"))) init_shared_mem()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    uintptr_t shared_mem_alloc_start = SHARED_MEM_BASE_ADDR + \
                (((sizeof(struct shared_mem) >> 12) + 1) << 12);

    m->cs_status = CS_STATUS_IDLE;
    m->fs_ready = 0;
    m->sq_head = 0;
    m->sq_tail = 0;
	for(int i = 0; i < MAX_NR_CS_WORKERS; i++){
		m->cq_head[i] = 0;
		m->cq_tail[i] = 0;
	}
    memset(m->cs_worker_status, 0, sizeof(m->cs_worker_status));
    init_linear_allocator(&m->shared_allocator, 
            shared_mem_alloc_start, SHARED_MEM_END_ADDR);
    linear_malloc_set_default_align(&m->shared_allocator, 64);
    init_linear_allocator(&m->dma_noncache_allocator, 
            DMA_NON_CACHEABLE_BASE_ADDR, DMA_NON_CACHEABLE_END_ADDR);
    linear_malloc_set_default_align(&m->dma_noncache_allocator, 64);
	spinlock_init(&m->sq_lock);
    xil_printf("Shared memory initialized, malloc size = %lu Bytes\n", 
            SHARED_MEM_END_ADDR - shared_mem_alloc_start);

	MEMORY_BARRIER();
}

extern void cs_main();

int main()
{
	setup_page_table();	

    check_memory_map(CPU1_MEMORY_SEGMENTS_END_ADDR);

    // TODO: move this to cpu0
    init_shared_mem();

    signal_cpu_up(1);
    wait_cpu_up(0);

    init_linear_allocator(&allocator, CPU1_LINEAR_MALLOC_BASE_ADDR, CPU1_LINEAR_MALLOC_END_ADDR);
    linear_malloc_set_default_align(&allocator, 64);
    cs_main();

    return 0;
}
