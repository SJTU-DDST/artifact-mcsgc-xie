#include "memory_map.h"
#include "shared_mem.h"
#include "xil_mmu.h"
#include "xil_cache.h"

void check_memory_map(uintptr_t segment_end_addr)
{
    extern char _end;

    ASSERT((uintptr_t)&_end < segment_end_addr);
    ASSERT(sizeof(struct shared_mem) <= SHARED_MEM_END_ADDR - SHARED_MEM_BASE_ADDR);
}

void setup_page_table()
{
    UINTPTR u;

	Xil_ICacheDisable();
	Xil_DCacheDisable();

	// Paging table set
	#define MB (1024*1024)
	for (u = 0; u < 4096; u+=2)
	{
		if (u < 0x2)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < 0x180)
			Xil_SetTlbAttributes(u * MB, NORM_NONCACHE);
		else if (u < 0x400)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < CPU0_CACHED_MEMORY_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < CPU0_UNCACHED_MEMORY_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_NONCACHE);
		else if (u < CPU1_CACHED_MEMORY_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < CPU2_CACHED_MEMORY_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < CPU3_CACHED_MEMORY_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else if (u < DMA_NON_CACHEABLE_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_NONCACHE);
		else if (u < BD_RING_NON_CACHEABLE_END_ADDR / MB)
			Xil_SetTlbAttributes(u * MB, NORM_NONCACHE);
		else if (u < SHARED_MEM_END_ADDR / MB)
			// Xil_SetTlbAttributes(u * MB, NORM_NONCACHE);
			Xil_SetTlbAttributes(u * MB, NORM_WB_CACHE);
		else
			Xil_SetTlbAttributes(u * MB, STRONG_ORDERED);
	}

	// #define GB (1024ULL * 1024 * 1024)
	// for (u = 0; u < 64; u++)
	// 	Xil_SetTlbAttributes(DDR4_BUFFER_BASE_ADDR + u * GB, NORM_NONCACHE);

	Xil_ICacheEnable();
	Xil_DCacheEnable();
}