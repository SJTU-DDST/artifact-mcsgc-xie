#include "xil_cache.h"
#include "xil_exception.h"
#include "xil_mmu.h"
#include "xscugic_hw.h"
#include "xscugic.h"
#include "memory_map.h"
#include "shared_mem.h"
#include "utils.h"
#include "interval-mapping/mapping_test.h"

XScuGic GicInstance;
struct linear_allocator allocator;
struct linear_allocator ssd_allocator;

extern void emu_main();

int main()
{
	setup_page_table();	

    check_memory_map(CPU3_MEMORY_SEGMENTS_END_ADDR);
    
    init_linear_allocator(&allocator, 
            CPU3_REQ_MEMORY_BASE_ADDR, CPU3_REQ_MEMORY_END_ADDR);
    linear_malloc_set_default_align(&allocator, 4);

    init_linear_allocator(&ssd_allocator, 
            CPU3_SSD_MEMORY_BASE_ADDR, CPU3_SSD_MEMORY_END_ADDR);
    linear_malloc_set_default_align(&ssd_allocator, 4);

    wait_cpu_up(1);
    wait_cpu_up(0);
    emu_main();
//     test_mapseg();

    return 0;
}
