#include "f2fs_probe.h"
#include "f2fs_meta.h"
#include "ftl.h"
#include "shared_mem.h"
#include "cs_io.h"

extern struct linear_allocator allocator;
static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

void f2fs_read_super()
{
    struct f2fs_sb *sb = (struct f2fs_sb *)m->sb_buffer;
    struct cs_io_handle handle;

    FLUSH_CACHE(sb, sizeof(struct f2fs_sb));
    handle = read_from_storage(sb, 
        storage_offset_l2p(m->ssd, F2FS_SUPER_OFFSET), 
        sizeof(struct f2fs_sb), NULL, NULL);
    sync_cs_io_req(&handle);
}


int f2fs_probe(int is_ready, unsigned int *main_blkaddr)
{
    struct f2fs_sb *local_sb = (struct f2fs_sb *)m->sb_buffer;
    struct cs_io_handle handle;
    uint64_t sb_poff = storage_offset_l2p(m->ssd, F2FS_SUPER_OFFSET);
    uint64_t sb_backup_poff = storage_offset_l2p(m->ssd, 4096+F2FS_SUPER_OFFSET);

    // if (local_sb == NULL) {
    //     local_sb = linear_malloc(&allocator, sizeof(struct f2fs_sb), 4096);
    //     assert(local_sb != NULL);
    // }

    m->fs_ready = is_ready;

    if(!is_ready) 
        return 0;
    
    FLUSH_CACHE(local_sb, sizeof(struct f2fs_sb));
    handle = read_from_storage(local_sb, sb_poff, sizeof(struct f2fs_sb), NULL, NULL);
    sync_cs_io_req(&handle);
    
    if(local_sb->magic==0xF2F52010){
        // memset(F2FS_I(nvmev_vdev), 0, sizeof(struct f2fs_info));
        // nvmev_vdev->fs_info.sb.f2fs = sb;
        // f2i->sb = &(nvmev_vdev->fs_info.sb.f2fs);
        // f2i->blocks_per_seg = 1 << sb.log_blocks_per_seg;
        // f2i->sit_blocks = sb.segment_count_sit / 2 * f2i->blocks_per_seg;

        xil_printf("Found f2fs superblock\n");
        
        xil_printf("superblock size: %lu\n", sizeof(struct f2fs_sb)); 
        xil_printf("log2 block size in bytes: %u\n", local_sb->log_blocksize);
        xil_printf("log2 # of blocks per segment: %u\n", local_sb->log_blocks_per_seg);
        xil_printf("# of segments per section: %u\n", local_sb->segs_per_sec);
        xil_printf("# of sections per zone: %u\n", local_sb->secs_per_zone);

        xil_printf("total # of user blocks: %llu\n", local_sb->block_count);
        xil_printf("total # of user segments: %u\n", local_sb->segment_count);
        xil_printf("# of segments for checkpoint: %u\n", local_sb->segment_count_ckpt);
        xil_printf("# of blocks for checkpoint payload: %u\n", local_sb->cp_payload);
        xil_printf("# of segments for SIT: %u\n", local_sb->segment_count_sit);
        xil_printf("# of segments for NAT: %u\n", local_sb->segment_count_nat);
        xil_printf("# of segments for SSA: %u\n", local_sb->segment_count_ssa);
        xil_printf("# of segments for main area: %u\n", local_sb->segment_count_main);
        *main_blkaddr = local_sb->main_blkaddr;
        
        xil_printf("start block address of segment 0: %u\n", local_sb->segment0_blkaddr);
        xil_printf("start block address of checkpoint: %u\n", local_sb->cp_blkaddr);
        xil_printf("start block address of SIT: %u\n", local_sb->sit_blkaddr);
        xil_printf("start block address of NAT: %u\n", local_sb->nat_blkaddr);
        xil_printf("start block address of SAT: %u\n", local_sb->ssa_blkaddr);
        xil_printf("start block address of main area: %u\n", local_sb->main_blkaddr);
        
        xil_printf("defined features: %08x\n", local_sb->feature);

        
        FLUSH_CACHE(local_sb, sizeof(struct f2fs_sb));
        handle = read_from_storage(local_sb, sb_backup_poff, sizeof(struct f2fs_sb), NULL, NULL);
        sync_cs_io_req(&handle);
        if (local_sb->magic == 0xF2F52010) {
            xil_printf("Found backup superblock in block 1; everything seems to be fine.\n");
        } else {
            xil_printf("Didn't find backup superblock in block 1; maybe something's wrong?\n");
        }

        return 0;
    }

    /* didn't find superblock */
    m->fs_ready = 0;
    xil_printf("didn't find f2fs superblock\n");

    return -1;
}