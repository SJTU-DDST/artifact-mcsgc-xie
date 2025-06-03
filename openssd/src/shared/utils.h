#ifndef __UTILS_H
#define __UTILS_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "xtime_l.h"
#include "xil_cache.h"
#include "config.h"
#include "xil_printf.h"

#define ASSERT(cond) do { \
        if (!(cond)) { \
            xil_printf("Failed at (%s) %s:%d", __FILE__, __func__, __LINE__); \
            while (1);\
        } \
    } while (0)

#define ALIGN_FLOOR(n, align) ((n) / (align) * (align))
#define ALIGN_CEILING(n, align) (((n) + (align) - 1) / (align) * (align))

#define DIVIDE_CEILING(n, d) (((n) + (d) - 1) / (d))

#define COMPILER_BARRIER() do { __asm__ volatile ("" : : : "memory"); } while (0)
#define MEMORY_BARRIER() do { __asm__ volatile ("dmb sy" : : : "memory"); } while (0)
#define MEMORY_BARRIER_STRONG() do { __asm__ volatile ("dsb sy" : : : "memory"); } while (0)

#define CYCLE2NS(cycles) ((uint64_t) ((cycles) * 1000000000.0 / COUNTS_PER_SECOND))
#define NS2CYCLE(ns) ((uint64_t) (((double) ns) * COUNTS_PER_SECOND / 1000000000.0))
#define ONE_SEC_CYCLES (COUNTS_PER_SECOND)

#define FLUSH_CACHE(addr, size) \
    do { \
        MEMORY_BARRIER_STRONG(); \
        Xil_DCacheFlushRange((UINTPTR)(addr), (UINTPTR)(size)); \
    } while (0)

#define INVALIDATE_CACHE(addr, size) \
    do { \
        MEMORY_BARRIER_STRONG(); \
        Xil_DCacheInvalidateRange((UINTPTR)(addr), (UINTPTR)(size)); \
    } while (0)

struct linear_allocator{
    uintptr_t base;
    uintptr_t curr;
    uintptr_t end;
    size_t default_align;
};

void init_linear_allocator(struct linear_allocator *allocator, 
        uintptr_t base, uintptr_t end);
void *linear_malloc(struct linear_allocator *allocator,size_t size, size_t align);
void *linear_zalloc(struct linear_allocator *allocator,size_t size, size_t align);
void linear_malloc_reset(struct linear_allocator *allocator);
void linear_malloc_set_base(struct linear_allocator *allocator);
void linear_malloc_set_default_align(struct linear_allocator *allocator, size_t align);
unsigned long long linear_allocator_get_mem_usage(struct linear_allocator *allocator, bool print);

uint64_t get_time_cycle();
uint64_t get_time_ns();
void nsleep(uint64_t ns);
void check_elf_size(uintptr_t segment_end_addr);

typedef volatile uint32_t spinlock_t;

void spinlock_init(spinlock_t* lock);
void spinlock_acquire(spinlock_t* lock);
void spinlock_release(spinlock_t* lock);

struct ring_queue {
    int nr_entries;
    int qe_size;
    int head;
    int tail;
    void *payload;
};

struct qpair {
    int nr_entries;
    int sqe_size;
    int cqe_size;
    int sq_head;
    int sq_tail;
    int cq_head;
    int cq_tail;
    void *sq_payload;
    void *cq_payload;
};

void queue_init(struct linear_allocator *alloctor, struct ring_queue *queue, 
        int nr_entries, int qe_size);
void *queue_alloc_entry(volatile struct ring_queue *queue);
void queue_submit_entry(volatile struct ring_queue *queue, void *entry);
void *queue_peek_entry(volatile struct ring_queue *queue);
void queue_consume_entry(volatile struct ring_queue *queue, void *entry);

void qpair_init(struct linear_allocator *allocator, struct qpair *qp, 
        int nr_entries, int sqe_size, int cqe_size);
void *qpair_alloc_sqe(volatile struct qpair *qp);
void qpair_submit_sqe(volatile struct qpair *qp, void *sqe);
void *qpair_peek_sqe(volatile struct qpair *qp);
void qpair_consume_sqe(volatile struct qpair *qp, void *sqe);
void *qpair_alloc_cqe(volatile struct qpair *qp);
void qpair_submit_cqe(volatile struct qpair *qp, void *cqe);
void *qpair_peek_cqe(volatile struct qpair *qp);
void qpair_consume_cqe(volatile struct qpair *qp, void *cqe);
#endif
