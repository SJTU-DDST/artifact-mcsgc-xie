#include "shared_mem.h"
#include "memory_map.h"
#include "utils.h"

#include "f2fs_cs.h"
#include "xil_printf.h"

extern struct linear_allocator allocator;

void cs_hello_world()
{
    struct csgc_package *package;
    struct csgc_header *header;
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    uint32_t *num;

    xil_printf("Received CS request\n");
    package = (struct csgc_package *)(m->cs_args_buf);
    header = &package->header;

    xil_printf("CSGC package header:\n");
    xil_printf("capacity = %u, npages = %u, pages_pointer1 = %016llx,"
			" pages_pointer2 = %016llx, segno = %u, status = %d\n", 
            header->capacity, header->npages, (unsigned long long)header->pages, 
			(unsigned long long)header->pages_recv, header->segno, header->status);
	num = (uint32_t *)package->data;
	xil_printf("num[0]:%x, num[2]:%x, num[2]:%x, num[3]:%x\n", num[0], num[1], num[2], num[3]);
	num[0] = 0xA; num[1] = 0xB; num[2] = 0xC; num[3] = 0xD;
    xil_printf("After CS change\n");
    xil_printf("num[0]:%x, num[2]:%x, num[2]:%x, num[3]:%x\n", num[0], num[1], num[2], num[3]);
}

static void init_print_buffer(struct f2fs_info *fi, struct csgc_header *header)
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    int nr_cs_workers = header->max_nr_cpus > MAX_NR_CS_GC_WORKERS ? MAX_NR_CS_GC_WORKERS : header->max_nr_cpus;
    fi->print_buffer = linear_zalloc(&(m->shared_allocator), CS_PRINT_BUF_SIZE, 0);

    if(nr_cs_workers == 1){
        fi->wi[0].pbi.buf = fi->print_buffer;
        fi->wi[0].pbi.offset = 0;
        fi->wi[0].pbi.size = CS_PRINT_BUF_SIZE;
    }else{
        fi->wi[0].pbi.buf = fi->print_buffer;
        fi->wi[0].pbi.offset = 0;
        fi->wi[0].pbi.size = CS_PRINT_BUF_SIZE/2;
        fi->wi[1].pbi.buf = fi->print_buffer + fi->wi[0].pbi.size;
        fi->wi[1].pbi.offset = 0;
        fi->wi[1].pbi.size = CS_PRINT_BUF_SIZE/4;
        fi->wi[2].pbi.buf = fi->print_buffer + fi->wi[0].pbi.size + fi->wi[1].pbi.size;
        fi->wi[2].pbi.offset = 0;
        fi->wi[2].pbi.size = CS_PRINT_BUF_SIZE/4;
    }
    // xil_printf("pbi size:%u %u %u|%d %d\n", fi->wi[0].pbi.size, fi->wi[1].pbi.size, 
    //         fi->wi[2].pbi.size, nr_cs_workers, header->max_nr_cpus);
}

static void pack_print_outputs(struct f2fs_info *fi)
{
    struct csgc_header *header = &fi->package->header;
    void *base_addr = fi->package->data;

    header->print_offset = CS_PRINT_BUF_OFFSET - sizeof(struct csgc_header);
    header->print_size = 0;
    for(int i = 0; i < fi->nr_cs_workers; i++){
        unsigned int size = fi->wi[i].pbi.offset;
        
        if(header->print_size + size > CS_PRINT_BUF_SIZE)
            size = CS_PRINT_BUF_SIZE - header->print_size;
        
        memcpy(base_addr + header->print_offset + header->print_size, fi->wi[i].pbi.buf, size);
        header->print_size += size;
    }
    ASSERT(header->print_size <= CS_PRINT_BUF_SIZE);
    // xil_printf("pbi size:%u %u %u|%u\n", fi->wi[0].pbi.size, 
    //         fi->wi[1].pbi.size, fi->wi[2].pbi.size, header->print_size);
}

void test_f2fs_migrate()
{
    struct f2fs_info *fi;
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    struct print_buf_info *pbi;
    size_t transfer_size = 8, transfer_interval = 16, nr_transfers = 8;
    block_t blkaddr_src = 100000, blkaddr_dst = 120000;
    uint8_t *buf;
    block_t addr1, addr2;
    size_t nr_blk = 0;
    unsigned int sg_count = 100;

    linear_malloc_reset(&allocator);
    linear_malloc_reset(&m->shared_allocator);

    buf = linear_malloc(&allocator, 4096, 0);
    fi = linear_zalloc(&m->shared_allocator, sizeof(*fi), 0);
    fi->print_buffer = linear_zalloc(&(m->shared_allocator), CS_PRINT_BUF_SIZE, 0);
    m->fi = fi;
    fi->sb = (struct f2fs_sb *)m->sb_buffer;
    fi->sb->main_blkaddr = 0;
    fi->sb->block_count = 1000000;

    pbi = &fi->wi[0].pbi;
    pbi->buf = fi->print_buffer;
    pbi->offset = 0;
    pbi->size = CS_PRINT_BUF_SIZE;

    xil_printf("start f2fs_migrate_block test, from %lu to %lu\n", blkaddr_src, blkaddr_dst);
    xil_printf("parameters: transfer_size=%u, transfer_interval=%u, nr_transfers=%u\n", 
                transfer_size, transfer_interval, nr_transfers);
    
    f2fs_csio_init_all(fi);
    if(transfer_interval < transfer_size)
        transfer_interval = transfer_size;

    xil_printf("set source address\n");
    for(int i = 0; i < nr_transfers; i++){
        for(int j = 0; j < transfer_size; j++){
            addr1 = blkaddr_src + i*transfer_interval + j;
            memset(buf, nr_blk, 4096);
            FLUSH_CACHE(buf, 4096);
            f2fs_write_block(fi, addr1, buf, CSIO_F2FS_DATA, 1, 1, NULL);
            nr_blk ++;
        }
    }

    xil_printf("do migration\n");
    for(int count = 0; count < sg_count; count ++)
    {
        nr_blk = 0;
        for(int i = 0; i < nr_transfers; i++){
            for(int j = 0; j < transfer_size; j++){
                addr1 = blkaddr_src + i*transfer_interval + j;
                addr2 = blkaddr_dst + i*transfer_size + j;
                f2fs_migrate_block(fi, addr1, addr2, CSIO_F2FS_DATA, 0, 0, NULL);
                nr_blk ++;
            }
        }
        f2fs_flush_csio_async(fi, CSIO_F2FS_DATA, CSIO_MIGRATE);
        f2fs_csio_wait_completion(fi, CSIO_MIGRATE, CSIO_F2FS_DATA);
        xil_printf("%d-th sg transfer done\n", count);
    }

    xil_printf("check dst address\n");
    nr_blk = 0;
    for(int i = 0; i < nr_transfers; i++){
        for(int j = 0; j < transfer_size; j++){
            addr2 = blkaddr_dst + i*transfer_size + j;
            INVALIDATE_CACHE(buf, 4096);
            f2fs_read_block(fi, addr2, buf, CSIO_F2FS_DATA, 1, 1, NULL);
            for(int k = 0; k < 4096; k++)
                if(buf[k] != nr_blk){
                    xil_printf("error: [%u][%u][%u]=%u, should be %u\n", 
                                i, j, k, buf[k], nr_blk);
                    ASSERT(0);
                }
            nr_blk ++;
        }
    }

    if(pbi->offset > 0){
        pbi->buf[pbi->offset - 1] = '\0';
        printf("%s", pbi->buf);
    }
    
    xil_printf("finished f2fs_migrate_block test\n");

    linear_malloc_reset(&allocator);
    linear_malloc_reset(&m->shared_allocator);
}

static void do_cs_task(int slot_id)
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    volatile cs_slot_t *cs_slot = &m->cs_slots[slot_id];
    struct f2fs_info *fi;
    struct csgc_header *header;
    volatile void *arg_buf = &(m->cs_args_buf[slot_id][0]);
    int arg_size;
    unsigned long long enter_nsecs, leave_nsecs;

    if (cs_slot->status != CS_STATUS_RUNNING){
        xil_printf("cs status wrong, args not ready.");
        return;
    }

    arg_size = cs_slot->rx_arg_size; // 32KB
    INVALIDATE_CACHE(arg_buf, arg_size); // not sure if this is needed
    MEMORY_BARRIER();
    
    linear_malloc_reset(&m->dma_noncache_allocator);
    linear_malloc_reset(&m->shared_allocator);
    linear_malloc_reset(&allocator);
    fi = linear_zalloc(&m->shared_allocator, sizeof(*fi), 0);
    m->fi = fi;
    header = &(((struct csgc_package *) arg_buf)->header);
#ifdef CS_DEBUG_ARGS
    xil_printf_safe("Received CS request, cs_slot_id=%d, "
        "in_use:%d, status:%d, cs_seq_id:%d, rx_arg_size:%d, tx_arg_size:%d\n",
        slot_id, cs_slot->in_use, cs_slot->status, 
        cs_slot->cs_seq_id, cs_slot->rx_arg_size, cs_slot->tx_arg_size);
    xil_printf_safe("header(%p) info: segno=%u, pages:%p, pages_recv:%p, "
        "nr_node_info:%u, curseg_start:%u, dnode_start:%u, data_size_d2h:%u\n",
        header, header->segno, header->pages, header->pages_recv,
        header->nr_node_info, header->offs.curseg_start, header->offs.dnode_start,
        header->offs.data_size_d2h);
#endif
    init_print_buffer(fi, header);
    CS_INFO(fi, "CS: Received GC request, segno=%u", header->segno);
    CS_INFO(fi, "Shared mem allocator starts at %lx", m->shared_allocator.base);
    // cs_hello_world();

    init_time_stat(fi);

    m->cdma_io_bytes = 0;
    enter_nsecs = get_time_ns();
    f2fs_csgc_leader(fi, arg_buf);
    leave_nsecs = get_time_ns();
    
    update_time_stat(fi, "end");

    show_io_stat(fi);
    show_time_stat(fi);;

    CS_INFO(fi, "shared mem usage: %lu Bytes", 
            m->shared_allocator.curr - m->shared_allocator.base);
    CS_INFO(fi, "CDMA io byte size: %llu, bandwidth: %llu MBPS", m->cdma_io_bytes,
            BANDWIDTH_MBPS(m->cdma_io_bytes, leave_nsecs-enter_nsecs));
    pack_print_outputs(fi);
    f2fs_free_info(fi);

    cs_slot->tx_arg_size = arg_size; // TODO: maybe set a smaller size
#ifdef CS_DEBUG_ARGS
    xil_printf_safe("finished CS request, cs_slot_id=%d, "
        "in_use:%d, status:%d, cs_seq_id:%d, rx_arg_size:%d, tx_arg_size:%d\n",
        slot_id, cs_slot->in_use, cs_slot->status, 
        cs_slot->cs_seq_id, cs_slot->rx_arg_size, cs_slot->tx_arg_size);
    xil_printf_safe("header(%p) info: segno=%u, pages:%p, pages_recv:%p, "
        "nr_node_info:%u, curseg_start:%u, dnode_start:%u, data_size_d2h:%u\n",
        header, header->segno, header->pages, header->pages_recv,
        header->nr_node_info, header->offs.curseg_start, header->offs.dnode_start,
        header->offs.data_size_d2h);
#endif
    // seems that must flush cache here, or dma will transmit the out-dated data
    FLUSH_CACHE(arg_buf, arg_size);
    MEMORY_BARRIER();

    cs_slot->status = CS_STATUS_DONE;
}

static void process_cs_req_queue()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
    struct cs_req_entry *ce;
    while( (ce = queue_peek_entry(&m->cs_req_queue)) != NULL){
        do_cs_task(ce->cs_req.slot_id);
        queue_consume_entry(&m->cs_req_queue, ce);
    }
}

void cs_main()
{
    volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

    asm volatile("msr PMCR_EL0, %0" : : "r" ((1 << 0) | (1 << 2)));
    asm volatile("msr PMCNTENSET_EL0, %0" : : "r" (1 << 31));

    // test_f2fs_migrate();

    while (1) {
        process_cs_req_queue();
    }
}
