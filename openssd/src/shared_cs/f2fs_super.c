#include "f2fs_cs.h"
#include "f2fs_dump.h"

extern struct linear_allocator allocator;
static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

// slightly modified from f2fs source codes
static inline void *__bitmap_ptr(struct f2fs_info *fi, int flag)
{
	struct f2fs_checkpoint *ckpt = F2FS_CKPT(fi);
	void *tmp_ptr = &ckpt->sit_nat_version_bitmap;
	int offset;

	if (ckpt->ckpt_flags & CP_LARGE_NAT_BITMAP_FLAG) {
		offset = (flag == SIT_BITMAP) ?
			ckpt->nat_ver_bitmap_bytesize : 0;
		/*
		 * if large_nat_bitmap feature is enabled, leave checksum
		 * protection for all nat/sit bitmaps.
		 */
		return tmp_ptr + offset + sizeof(__le32); //sizeof(__le32) for checksum?
	}

	if (F2FS_SB(fi)->cp_payload > 0) {
		if (flag == NAT_BITMAP)
			return tmp_ptr;
		else
			return (unsigned char *)ckpt + F2FS_BLKSIZE;
	} else {
		offset = (flag == NAT_BITMAP) ?
			ckpt->sit_ver_bitmap_bytesize : 0;
		return tmp_ptr + offset;
	}
}

void *validate_checkpoint(struct f2fs_info *fi, block_t cp_addr, unsigned long long *version)
{
    struct f2fs_sb *sb = fi->sb;
    struct f2fs_checkpoint *cp1 = NULL, *cp2 = NULL;
    unsigned int cp_blks;

    cp1 = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    cp2 = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    if(!cp1 || !cp2) 
        goto invalid_cp;

    // get cp1 and cp2 and check if the pack is valid
    // TODO: check checksum
    f2fs_read_block(fi, cp_addr, cp1, 
            CSIO_F2FS_META, 1, 1, NULL);
    cp_blks = cp1->cp_pack_total_block_count;
    if( cp_blks > (1<<sb->log_blocks_per_seg) ||
        cp_blks <=F2FS_CP_PACKS){
            CS_INFO(fi, "Invalid cp_pack_total_block_count:%u", cp_blks);
            __dump_ckpt(fi, cp1, false);
            goto invalid_cp;
        }
    
    cp_addr += cp_blks - 1;
    f2fs_read_block(fi, cp_addr, cp2, 
            CSIO_F2FS_META, 1, 1, NULL);
    if(cp1->checkpoint_ver != cp2->checkpoint_ver){
        CS_INFO(fi, "checkpoint1 not valid, v1(%#lx)!=v2(%#lx)", cp1->checkpoint_ver, cp2->checkpoint_ver);
        __dump_ckpt(fi, cp1, false);
        __dump_ckpt(fi, cp2, false);
        goto invalid_cp;
    }
    *version = cp1->checkpoint_ver;
    kfree(cp2);
    return cp1;

invalid_cp:
    if(cp1)
        kfree(cp1);
    if(cp2)
        kfree(cp2);
    return NULL;
}

int f2fs_get_valid_checkpoint(struct f2fs_info *fi, struct f2fs_checkpoint **cp)
{
    void *cp1=NULL, *cp2=NULL;
    void *cur_cp=NULL;
    struct f2fs_sb *sb = fi->sb;
    unsigned long long cp_start_blk_no, cur_cp_blkaddr;
	unsigned int cp_blks = 1 + sb->cp_payload;
    unsigned long long version1=0, version2=0;

    *cp = linear_malloc(&allocator, cp_blks*F2FS_BLKSIZE, 0);
    cp_start_blk_no = sb->cp_blkaddr;
    cp1 = validate_checkpoint(fi, cp_start_blk_no, &version1);

    cp_start_blk_no += fi->blocks_per_seg;
    cp2 = validate_checkpoint(fi, cp_start_blk_no, &version2);

    if(cp1&&cp2){
        if(version1>version2){
            cur_cp = cp1;
        }else{
            cur_cp = cp2;
        }
    }else if(cp1){
        cur_cp = cp1;
    }else if(cp2){
        cur_cp = cp2;
    }else{
        CS_INFO(fi, "No valid checkpoint");
        goto failed;
    }

    memcpy(*cp, cur_cp, F2FS_BLKSIZE);
    if(cur_cp==cp1){
        fi->cur_cp_pack = 1;
        cur_cp_blkaddr = cp_start_blk_no - fi->blocks_per_seg;
    }
    else{
        fi->cur_cp_pack = 2;
        cur_cp_blkaddr = cp_start_blk_no;
    }
    
    // no payload blocks
    if(cp_blks<=1)
        goto ret;
    
    // read payload blocks
    // payload blocks are used to store SIT
    for(int i = 1; i < cp_blks; i++){
        f2fs_read_block(fi, cur_cp_blkaddr+i, (char *)(*cp) + i * F2FS_BLKSIZE,
                         CSIO_F2FS_META, 0, 0, NULL);
    }
    f2fs_flush_csio(fi, CSIO_F2FS_META, CSIO_READ);

ret:
    if(cp1)
        kfree(cp1);
    if(cp2)
        kfree(cp2);
    return 0;

failed:
    kfree(*cp);
    return -1;
}

//         BLOCK1               BLOCK2                BLOCK3
// +--------------------+--------------------+--------------------+
// |       nat_j        |                    |                    |
// |       sit_j        | compact summaries2 | compact summaries3 |
// | compact summaries1 |                    |                    |
// |       footer       |       footer       |       footer       |
// +--------------------+--------------------+--------------------+
// 
// read summaries stored in compact format(only used for CURSEG_XXX_DATA types)
static int read_compact_summaries(struct f2fs_info *fi)
{
    struct f2fs_checkpoint *ckpt = F2FS_CKPT(fi); 
    struct curseg_info *curseg;
    unsigned char *block_buffer;
	block_t blk_addr = 0;
    int i, j, offset;
    int err = 0;

    block_buffer = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    
    blk_addr = start_sum_block(fi);
    if(f2fs_read_block(fi, blk_addr++, block_buffer, 
            CSIO_F2FS_META, 1, 1, NULL)){
        err = -CSGC_ERR;
        goto ret;
    }
    
    // restore nat cache
    curseg = CURSEG_I(fi, CURSEG_HOT_DATA);
    memcpy(curseg->journal, block_buffer, SUM_JOURNAL_SIZE);

    // restore sit cache
    curseg = CURSEG_I(fi, CURSEG_COLD_DATA);
    memcpy(curseg->journal, block_buffer+SUM_JOURNAL_SIZE, SUM_JOURNAL_SIZE);
    offset = 2 * SUM_JOURNAL_SIZE;

#ifdef CS_DEBUG_BUILD_INFO
    CS_INFO(fi, "Restored journal from compact summaries");
    CS_INFO(fi, "NAT JOURNAL");
    __dump_journal(CURSEG_I(fi, CURSEG_HOT_DATA)->journal, NAT_JOURNAL);
    CS_INFO(fi, "SIT JOURNAL");
    __dump_journal(CURSEG_I(fi, CURSEG_COLD_DATA)->journal, SIT_JOURNAL);
#endif

    // restore summaries
    for(i = CURSEG_HOT_DATA; i<=CURSEG_COLD_DATA; i++){
        unsigned short blk_off;
		unsigned int segno;

        curseg = CURSEG_I(fi, i);

        segno = ckpt->cur_data_segno[i];
        blk_off = ckpt->cur_data_blkoff[i];
        
        curseg->segno = segno;
        curseg->next_blkoff = blk_off;
        curseg->alloc_type = ckpt->alloc_type[i];
        curseg->inited = true;
#ifdef CS_DEBUG_BUILD_INFO
    CS_INFO(fi, "Restore compact summary of curseg type(%d) from cp", i);
    CS_INFO(fi, "curseg->segno: %u, curseg->next_blkoff: %u, curseg->alloc_type: %u", 
            curseg->segno, curseg->next_blkoff, curseg->alloc_type);
    CS_INFO(fi, "Restoring summary entries from block %u at offset %u:", blk_addr-1, offset);
#endif

        for(j = 0; j < blk_off; j++){
            struct f2fs_summary *s;
            
            s = (struct f2fs_summary *) (block_buffer + offset);
            curseg->sum_blk->entries[j] = *s;
#ifdef CS_DEBUG_BUILD_INFO
            __dump_sum_entry(curseg->sum_blk, j, false);
#endif
            offset += SUMMARY_SIZE;
            if(offset + SUMMARY_SIZE <= F2FS_BLKSIZE - SUM_FOOTER_SIZE)
                continue;

            if(f2fs_read_block(fi, blk_addr++, block_buffer, 
                    CSIO_F2FS_META, 1, 1, NULL)){
                err = -CSGC_ERR;
                goto ret;
            }
            offset = 0;
        }
    }

ret:
    kfree(block_buffer);
    return err;
}

// read summaries stored in normal format
static int read_normal_summaries(struct f2fs_info *fi, int type)
{
    struct f2fs_checkpoint *ckpt = F2FS_CKPT(fi); 
    struct f2fs_summary_block *sum;
    struct curseg_info *curseg;
    unsigned short blk_off;
	unsigned int segno = 0;
	block_t blk_addr = 0;
    int err = 0;

    if(type > CURSEG_COLD_NODE)
        return -CSGC_ERR;

    sum = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    if(IS_DATASEG(type)){
        segno = ckpt->cur_data_segno[type];
        blk_off = ckpt->cur_data_blkoff[type];
        if(__exist_node_summaries(fi))
            blk_addr = sum_blk_addr(fi, NR_CURSEG_PERSIST_TYPE, type);
        else
            blk_addr = sum_blk_addr(fi, NR_CURSEG_DATA_TYPE, type);
    }else{
        segno = ckpt->cur_node_segno[type - CURSEG_HOT_NODE];
        blk_off = ckpt->cur_node_blkoff[type - CURSEG_HOT_NODE];
        if (__exist_node_summaries(fi)){
			blk_addr = sum_blk_addr(fi, NR_CURSEG_NODE_TYPE,
							type - CURSEG_HOT_NODE);
        } else
			blk_addr = get_ssa_addr(fi, segno);
    }
    
    if(f2fs_read_block(fi, blk_addr, sum, CSIO_F2FS_META, 1, 1, NULL)){
        err = -CSGC_ERR;
        goto ret;
    }
    
    // ===================DON'T TOUCH NODE CURSEG SUMMARY===================
    // seems that f2fs writes node summary to storage only when umount or 
    //  fastboot. So the node summary block here is meaningless because CS
    //  only requires host to do a normal checkpoint. so if node summary 
    //  is needed, it'd better be sent from host, rather than read from storage.
    // check `read_normal_summaries` and `write_checkpoint` in f2fs source 
    //  code for more details.
    // =====================================================================
    
    curseg = CURSEG_I(fi, type);
    memcpy(curseg->journal, &sum->journal, SUM_JOURNAL_SIZE);
    memcpy(curseg->sum_blk->entries, sum->entries, SUM_ENTRY_SIZE);
	memcpy(&curseg->sum_blk->footer, &sum->footer, SUM_FOOTER_SIZE);

    curseg->alloc_type = ckpt->alloc_type[type];
    curseg->segno = segno;
    curseg->next_blkoff = blk_off;
    curseg->inited = true;
    
ret:
    kfree(sum);
    return err;
}

// restore summaries for each segtype
static int restore_summaries(struct f2fs_info *fi)
{
    struct f2fs_journal *nat_j = CURSEG_I(fi, CURSEG_HOT_DATA)->journal;
	struct f2fs_journal *sit_j = CURSEG_I(fi, CURSEG_COLD_DATA)->journal;
	int type = CURSEG_HOT_DATA;
	int err;

    // read compact summaries for data segs if compact flag is set
    if(F2FS_CKPT(fi)->ckpt_flags & CP_COMPACT_SUM_FLAG){
#ifdef CS_DEBUG_BUILD_INFO
    CS_INFO(fi, "Ready to read compact summaries");
#endif
        err = read_compact_summaries(fi);
        if(err)
            return err;
        type = CURSEG_HOT_NODE;
    }

    // read normal summaries
    for(; type <= CURSEG_COLD_NODE; type++){
        err = read_normal_summaries(fi, type);
        if(err)
            return err;
    }

    // sanity check
    if(nat_j->n_nats > NAT_JOURNAL_ENTRIES || 
        sit_j->n_sits > SIT_JOURNAL_ENTRIES ){
        CS_INFO(fi, "Invalid journal entries nat(%u) sits(%u)", nat_j->n_nats, sit_j->n_sits);
        return -CSGC_ERR;
    }

    return 0;
}

// alloc space for curseg, set seg_type, set inited=false
// call read_compact_summaries 
// the function does not support INMEM curseg type for now
static int build_curseg(struct f2fs_info *fi)
{
    struct curseg_info *array;
	int i;

    array = linear_zalloc(&allocator, NR_PERSISTENT_LOG*sizeof(*array), 0);

    fi->curseg_array = array;

    for(i = 0; i < NR_PERSISTENT_LOG; i++){
        array[i].journal = linear_zalloc(&allocator, sizeof(struct f2fs_journal), 0);
        array[i].sum_blk = linear_zalloc(&allocator, sizeof(struct f2fs_summary_block), 0);
        array[i].seg_type = CURSEG_HOT_DATA + i;
        array[i].inited = false;
    }

    return restore_summaries(fi);
}

// set the pseg_info to null. null pseg_info marks the end of
// the pre-allocated segments
static void set_null_pseg_info(struct pseg_info *pseg)
{
    memset(pseg, 0, sizeof(struct pseg_info));
    pseg->segno = NULL_SEGNO;
}

static void pseg_info_host2device(struct pseg_info *pseg_dev, 
                struct pre_alloc_seg_info *pseg_host)
{
    pseg_dev->is_curseg = pseg_host->is_curseg;
    pseg_dev->segno = pseg_host->segno;
    pseg_dev->seg_type = pseg_host->seg_type;
    pseg_dev->start_blkoff = pseg_host->start_blkoff;
    pseg_dev->end_blkoff = pseg_host->start_blkoff + pseg_host->len;
}

static int build_pre_alloc_info(struct f2fs_info *fi)
{
    struct csgc_package *package = fi->package;
    struct csgc_header *header = &package->header;
    struct pre_alloc_info *pi = F2FS_PI(fi);
    struct pre_alloc_seg_info *pi_pkg;
    struct pseg_info *cur_pseg;

    pi->nr_psegs = header->nr_pre_alloc;
    if(!pi->nr_psegs)
        return 0;
    
    pi->psegs = linear_malloc(&m->shared_allocator, (pi->nr_psegs + 1)* sizeof(struct pseg_info), 0);
    
    pi_pkg = (struct pre_alloc_seg_info *) (package->data + header->offs.prealloc_start);
    for(int i = 0; i < pi->nr_psegs; i++){
        cur_pseg = &pi->psegs[i]; 
        pseg_info_host2device(cur_pseg, &pi_pkg[i]);
        cur_pseg->sum_blk = linear_zalloc(&m->shared_allocator, sizeof(struct f2fs_summary_block), 0);
        // dont' read, since we will not update it to storage in CS, leave the updates to host
//         if(!cur_pseg->is_curseg){
//             f2fs_read_block(fi, get_ssa_addr(fi, cur_pseg->segno), 
//                             cur_pseg->sum_blk, CSIO_F2FS_META, 1, 0, NULL);
// #ifdef CS_DEBUG_GC_PREALLOC
//             CS_INFO(fi, "Read current prealloc segment summary block");
//             __dump_summary_range(pi->curpseg_sum_blk, 0, cur_pseg->start_blkoff);
// #endif
//         }
    }
    set_null_pseg_info(&pi->psegs[pi->nr_psegs]);

    return 0;
}

int f2fs_build_info(struct f2fs_info *fi, void *arg_buf)
{
    int err;

    f2fs_csio_init_all(fi);
    fi->package = (struct csgc_package *)arg_buf;
    
    fi->sb = (struct f2fs_sb *)m->sb_buffer;
    fi->blocks_per_seg = 1 << fi->sb->log_blocks_per_seg;
    fi->sit_blocks = fi->sb->segment_count_sit / 2 * fi->blocks_per_seg;
    fi->gi = linear_zalloc(&m->shared_allocator, sizeof(struct gc_info), 0);
    
    if(F2FS_NEED_CKPT_INFO(&fi->package->header)){
        err = f2fs_get_valid_checkpoint(fi, &(fi->cp));
        if(err){
            CS_INFO(fi, "Fail to get valid checkpoint.");
            return err;
        } 
        CS_INFO(fi, "Read checkpoint from block, ckpt_ver = %lx", 
                    F2FS_CKPT(fi)->checkpoint_ver);
        fi->nat_bitmap = __bitmap_ptr(fi, NAT_BITMAP);
        fi->sit_bitmap = __bitmap_ptr(fi, SIT_BITMAP);

        err = build_curseg(fi);
        if(err){
            CS_INFO(fi, "Fail to build curseg info.");
            return err;
        }
        fi->is_lame = false;
    } else {
        ASSERT(fi->package->header.nr_node_info);
        fi->is_lame = true;
    }

    err = build_pre_alloc_info(fi);
    if(err){
        CS_INFO(fi, "Fail to read pre allocation info.");
        return err;
    }
    
    if(!F2FS_HAS_PRE_ALLOC_SEGS(fi)){
        xil_printf("error at %s:%d\n", __FILE__, __LINE__);
        err = -CSGC_ERR;
        set_package_err(fi->package, err);
        CS_ERROR(fi, "No pre-allocated segments");
        return err;
    }
    return 0;
}

void f2fs_free_info(struct f2fs_info *fi)
{
    int i;

    if(F2FS_CKPT(fi)) 
        kfree(F2FS_CKPT(fi));
    fi->cp = NULL;

    if(fi->curseg_array){
        for(i = 0; i < NR_PERSISTENT_LOG; i++){
            kfree(fi->curseg_array[i].journal);
            kfree(fi->curseg_array[i].sum_blk);
        }
        kfree(fi->curseg_array);
        fi->curseg_array = NULL;
    }
    if(fi->seginfo_inited){
        kvfree(fi->free_segmap);
        fi->seginfo_inited = false;
    }
    if(F2FS_PI(fi)->nr_psegs){
        kfree(F2FS_PI(fi)->psegs);
        kfree(F2FS_PI(fi)->curpseg_sum_blk);    
    }
    f2fs_csio_free_all(fi);
}