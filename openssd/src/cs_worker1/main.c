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

extern void cs_main();

int main()
{
	setup_page_table();	

    check_memory_map(CPU2_MEMORY_SEGMENTS_END_ADDR);
    
    init_linear_allocator(&allocator, 
            CPU2_LINEAR_MALLOC_BASE_ADDR, CPU2_LINEAR_MALLOC_END_ADDR);
    linear_malloc_set_default_align(&allocator, 64);

    wait_cpu_up(1);
    wait_cpu_up(0);
    cs_main();

    return 0;
}
