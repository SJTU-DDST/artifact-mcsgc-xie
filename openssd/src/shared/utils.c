#include <assert.h>
#include "xil_cache.h"
#include "utils.h"
#include "memory_map.h"
#include "string.h"

void check_elf_size(uintptr_t segment_end_addr)
{
    extern char _end;

    assert((uintptr_t)&_end < segment_end_addr);
}

void init_linear_allocator(struct linear_allocator *allocator, 
        uintptr_t base, uintptr_t end)
{
    allocator->default_align = 64;
    allocator->base = ALIGN_CEILING(base, allocator->default_align);
    allocator->curr = allocator->base;
    allocator->end = ALIGN_CEILING(end, allocator->default_align);
}

unsigned long long linear_allocator_get_mem_usage(struct linear_allocator *allocator, bool print)
{
    unsigned long long ret = allocator->curr - allocator->base;
    if(print)
        xil_printf("Memory usage: %llu B, %llu KB, %llu MB\n", 
                ret, ret / 1024, ret / 1024 / 1024);
    return ret;
}

void *linear_malloc(struct linear_allocator *allocator,size_t size, size_t align)
{
    void *ret;

    if (align > 0)
        allocator->curr = ALIGN_CEILING(allocator->curr, align);
    ret = (void *)allocator->curr;

    if (allocator->curr + size > allocator->end){
        ASSERT(0);
        return NULL;
    }

    allocator->curr = ALIGN_CEILING(allocator->curr + size, allocator->default_align);

    return ret;
}

void *linear_zalloc(struct linear_allocator *allocator,size_t size, size_t align)
{
    void *ret;

    if (align > 0)
        allocator->curr = ALIGN_CEILING(allocator->curr, align);
    ret = (void *)allocator->curr;

    if (allocator->curr + size > allocator->end){
        ASSERT(0);
        return NULL;
    }

    allocator->curr = ALIGN_CEILING(allocator->curr + size, allocator->default_align);
    memset(ret, 0, size);

    return ret;
}

void linear_malloc_reset(struct linear_allocator *allocator)
{
    allocator->curr = allocator->base;
}

void linear_malloc_set_base(struct linear_allocator *allocator)
{
    allocator->base = allocator->curr;
}

void linear_malloc_set_default_align(struct linear_allocator *allocator, size_t align)
{
    allocator->default_align = align;
}

uint64_t get_time_cycle()
{
    XTime time;
    XTime_GetTime(&time);
    return time;
}

uint64_t get_time_ns()
{
    XTime time;

    XTime_GetTime(&time);

#ifdef CONFIG_GET_TIME_FLOAT
    return (double)time * 1000000000.0 / COUNTS_PER_SECOND;
#else
    uint64_t seconds = time / COUNTS_PER_SECOND;
    uint64_t remaining_counts = time % COUNTS_PER_SECOND;

    return seconds * 1000000000 + (remaining_counts * 1000000000) / COUNTS_PER_SECOND;
#endif
}

void nsleep(uint64_t ns)
{
    uint64_t start = get_time_ns();

    while (get_time_ns() - start < ns);
}


void queue_init(struct linear_allocator *alloctor, struct ring_queue *queue, 
        int nr_entries, int qe_size)
{
    memset(queue, 0, sizeof(struct ring_queue));

    queue->nr_entries = nr_entries;
    queue->qe_size = qe_size;

    queue->payload = linear_malloc(alloctor, nr_entries * qe_size, 0);

    MEMORY_BARRIER();

}

void *queue_alloc_entry(volatile struct ring_queue *queue)
{
    int nr_entries = queue->nr_entries;
    int head, tail;

    while (1) {
        head = queue->head;
        tail = queue->tail;

        if ((tail + 1) % nr_entries != head)
            break;
    }

    return queue->payload + tail * queue->qe_size;
}

void queue_submit_entry(volatile struct ring_queue *queue, void *entry)
{
    ASSERT(entry == queue->payload + queue->tail * queue->qe_size);

    MEMORY_BARRIER();
    queue->tail = (queue->tail + 1) % queue->nr_entries;
}

void *queue_peek_entry(volatile struct ring_queue *queue)
{
    return queue->head != queue->tail ?
           queue->payload + queue->head * queue->qe_size :
           NULL;
}

void queue_consume_entry(volatile struct ring_queue *queue, void *entry)
{
    ASSERT(entry == queue->payload + queue->head * queue->qe_size);

    MEMORY_BARRIER();
    queue->head = (queue->head + 1) % queue->nr_entries;
}

void qpair_init(struct linear_allocator *allocator, struct qpair *qp, 
        int nr_entries, int sqe_size, int cqe_size)
{
    memset(qp, 0, sizeof(struct qpair));

    qp->nr_entries = nr_entries;
    qp->sqe_size = sqe_size;
    qp->cqe_size = cqe_size;

    qp->sq_payload = linear_malloc(allocator, nr_entries * sqe_size, 0);
    qp->cq_payload = linear_malloc(allocator, nr_entries * cqe_size, 0);

    MEMORY_BARRIER();
}

void *qpair_alloc_sqe(volatile struct qpair *qp)
{
    int nr_entries = qp->nr_entries;
    int sq_head, sq_tail;

    while (1) {
        sq_head = qp->sq_head;
        sq_tail = qp->sq_tail;

        if ((sq_tail + 1) % nr_entries != sq_head)
            break;
    }

    return qp->sq_payload + sq_tail * qp->sqe_size;
}

void qpair_submit_sqe(volatile struct qpair *qp, void *sqe)
{
    ASSERT(sqe == qp->sq_payload + qp->sq_tail * qp->sqe_size);

    MEMORY_BARRIER();
    qp->sq_tail = (qp->sq_tail + 1) % qp->nr_entries;
}

void *qpair_peek_sqe(volatile struct qpair *qp)
{
    return qp->sq_head != qp->sq_tail ?
           qp->sq_payload + qp->sq_head * qp->sqe_size :
           NULL;
}

void qpair_consume_sqe(volatile struct qpair *qp, void *sqe)
{
    ASSERT(sqe == qp->sq_payload + qp->sq_head * qp->sqe_size);

    MEMORY_BARRIER();
    qp->sq_head = (qp->sq_head + 1) % qp->nr_entries;
}

void *qpair_alloc_cqe(volatile struct qpair *qp)
{
    int nr_entries = qp->nr_entries;
    int cq_head, cq_tail;

    while (1) {
        cq_head = qp->cq_head;
        cq_tail = qp->cq_tail;

        if ((cq_tail + 1) % nr_entries != cq_head)
            break;
    }

    return qp->cq_payload + cq_tail * qp->cqe_size;
}

void qpair_submit_cqe(volatile struct qpair *qp, void *cqe)
{
    ASSERT(cqe == qp->cq_payload + qp->cq_tail * qp->cqe_size);

    MEMORY_BARRIER();
    qp->cq_tail = (qp->cq_tail + 1) % qp->nr_entries;
}

void *qpair_peek_cqe(volatile struct qpair *qp)
{
    return qp->cq_head != qp->cq_tail ?
           qp->cq_payload + qp->cq_head * qp->cqe_size :
           NULL;
}

void qpair_consume_cqe(volatile struct qpair *qp, void *cqe)
{
    ASSERT(cqe == qp->cq_payload + qp->cq_head * qp->cqe_size);

    MEMORY_BARRIER();
    qp->cq_head = (qp->cq_head + 1) % qp->nr_entries;
}


void spinlock_init(spinlock_t *lock) {
    *lock = 0;
    MEMORY_BARRIER();
}

void spinlock_acquire(spinlock_t *lock) {
    uint32_t tmp;
    asm volatile(
    "1: ldaxr   %w0, [%1]\n"            // Load acquire exclusive
    "   cbnz    %w0, 1b\n"
    "   stlxr   %w0, %w2, [%1]\n"       // Store release exclusive
    "   cbnz    %w0, 1b"
    : "=&r" (tmp)
    : "r" (lock), "r" (1)
    : "memory");
}

void spinlock_release(spinlock_t *lock) {
    asm volatile(
    "   stlr    %w1, [%0]\n"
    : : "r" (lock), "r" (0) : "memory");
}