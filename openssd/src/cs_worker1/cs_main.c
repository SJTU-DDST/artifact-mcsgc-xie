#include "shared_mem.h"
#include "memory_map.h"
#include "utils.h"
#include "cs_worker.h"
#include "f2fs_cs.h"

#include "xil_printf.h"

extern struct linear_allocator allocator;

static void spinlock_test_master()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    spinlock_t *lock = &m->sq_lock;
    int *sum = &m->cs_worker_status[0];
    int tmp = 0;
    int count = 1000000;
    unsigned long long start_nsecs;

    xil_printf("spinlock test started in cpu2\n");
    start_nsecs = get_time_ns();

    for(int i = 0; i < 2*count; i++) {
        tmp++;
    }
    xil_printf("sum in single core without lock takes %lluns\n", 
                get_time_ns() - start_nsecs);

    spinlock_init(lock);
    start_nsecs = get_time_ns();
    for(int i = 0; i < count; i++) {
        spinlock_acquire(lock);
        (*sum)++;
        spinlock_release(lock);
    }
    xil_printf("sum in single core with lock but without contention takes %lluns\n", 
                get_time_ns() - start_nsecs);


    start_nsecs = get_time_ns();
    *sum = 0;
    m->cs_worker_status[CS_WORKER_ID] = CS_WORKER_IDLE;
    m->cs_worker_status[2] = CS_WORKER_IDLE;

    MEMORY_BARRIER();
    m->cs_worker_status[2] = CS_WORKER_RUNNING;

    m->cs_worker_status[CS_WORKER_ID] = CS_WORKER_RUNNING;

    for(int i = 0; i < count; i++) {
        spinlock_acquire(lock);
        (*sum)++;
        spinlock_release(lock);
    }

    m->cs_worker_status[CS_WORKER_ID] = CS_WORKER_DONE;
    xil_printf("spinlock test: master done, time used: %llu ns\n", 
        get_time_ns() - start_nsecs);

    while (m->cs_worker_status[2] != CS_WORKER_DONE) {
        ;
    }
    xil_printf("spinlock test finished, sum = %d, time used: %llu ns\n", 
            *sum, get_time_ns() - start_nsecs);

}

void cs_main()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    asm volatile("msr PMCR_EL0, %0" : : "r" ((1 << 0) | (1 << 2)));
    asm volatile("msr PMCNTENSET_EL0, %0" : : "r" (1 << 31));

    signal_cpu_up(2);
    xil_printf("cpu2 is up, cs worker1 is ready.\n");

    // nsleep(30*1000000000ULL);
    // spinlock_test_master();
    
    f2fs_csgc_worker();
}
