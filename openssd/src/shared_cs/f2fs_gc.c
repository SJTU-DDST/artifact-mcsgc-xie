#include "f2fs_cs.h"
#include "f2fs_dump.h"
#include "debug.h"

extern struct linear_allocator allocator; // local allocator
static volatile struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;

static void __dump_node_info(struct f2fs_info *fi, struct node_info *ni)
{
    CS_INFO(fi, "Node info of nid=%u, blkaddr=%u, ino=%u, v=%u", 
            ni->nid, ni->blk_addr, ni->ino, ni->version);
}

static void dump_node_info(struct gc_info *gi)
{
    for(int i = 0; i < gi->nr_node_info; i++){
        __dump_node_info(gi->fi, &gi->ni[i]);
    }
}

static void dump_nat_block_list(struct f2fs_info *fi, struct list_head *nat_block_list)
{
    struct nat_block_entry *nat_block_entry;
    int count = 0;
    list_for_each_entry(nat_block_entry, nat_block_list, list){
        CS_INFO(fi, "nat block entry %d, from_journal=%d", 
                count, nat_block_entry->from_journal);
        if(nat_block_entry->from_journal)
            CS_ERROR(fi, "nid=%u, blkaddr=%u, ino=%u, ver=%u", 
                nat_block_entry->start_nid, nat_block_entry->nat_ent->block_addr, 
                nat_block_entry->nat_ent->ino, nat_block_entry->nat_ent->version);
        else{
            CS_INFO(fi, "nat_blk_addr=%u, start_nid=%u", 
                    nat_block_entry->nat_blk_addr, nat_block_entry->start_nid);
            __dump_nat_blk(fi, nat_block_entry->nat_blk, 0, NAT_ENTRY_PER_BLOCK, 0);
        }
        count++;
    }
}

// **** NOT USED **** //
static struct f2fs_nat_entry *find_gc_nat(struct list_head *nat_block_list, 
            nid_t nid, struct nat_block_entry **blk_ent)
{
    struct nat_block_entry *nat_block_entry;
    unsigned int blk_off = nid % NAT_ENTRY_PER_BLOCK;
    nid_t start_nid = nid - blk_off;
    list_for_each_entry_reverse(nat_block_entry, nat_block_list, list){
        if(!nat_block_entry->from_journal && nat_block_entry->start_nid == start_nid){
            if(blk_ent) 
                *blk_ent = nat_block_entry;
            return &nat_block_entry->nat_blk->entries[blk_off];
        } else if (nat_block_entry->from_journal && nat_block_entry->start_nid == nid) {
            if(blk_ent) 
                *blk_ent = nat_block_entry;
            return nat_block_entry->nat_ent;
        }
    }
    return NULL;
}

// **** NOT USED **** //
// If already in list, finds it and return.
// If not in list, read from storage immediately.
static struct f2fs_nat_entry *add_gc_nat(struct gc_info *gi, nid_t nid, 
        struct nat_block_entry **blk_ent)
{
    struct list_head *nat_block_list = &gi->nat_block_list;
    struct list_head *nat_journal_list = &gi->nat_journal_list;
    struct nat_block_entry *new_ent;
    struct f2fs_nat_entry *ret;
    
    ASSERT(!F2FS_IS_LAME(gi->fi));
    
    // 1. find in cached journal nat entries first
    ret = find_gc_nat(nat_journal_list, nid, blk_ent);
    if(ret)
        goto got_it;
    
    // 2. find in journal
    ret = lookup_journal_nat(gi->fi, nid);
    if(ret){
        new_ent = linear_zalloc(&allocator, sizeof(*new_ent), 0);
        new_ent->from_journal = true;
        new_ent->nat_ent = ret;
        new_ent->start_nid = nid;
        list_add_tail(&new_ent->list, nat_journal_list);
        if(blk_ent) 
            *blk_ent = new_ent;
        goto got_it;
    }

    // 3. find in cached nat blocks
    ret = find_gc_nat(nat_block_list, nid, blk_ent);
    if(ret)
        goto got_it;
    
    // 4. read nat block from storage
    new_ent = linear_zalloc(&allocator, sizeof(*new_ent), 0);
    new_ent->from_journal = false;
    new_ent->nat_blk = linear_malloc(&allocator, sizeof(*new_ent->nat_blk), 0);
    new_ent->nat_blk_addr = current_nat_addr(gi->fi, nid);
    if(f2fs_read_block(gi->fi, new_ent->nat_blk_addr, new_ent->nat_blk, 
                    CSIO_F2FS_META, 1, 1, NULL))
        goto fail;
    new_ent->start_nid = nid - nid % NAT_ENTRY_PER_BLOCK;

    list_add_tail(&new_ent->list, nat_block_list);

    ret = &new_ent->nat_blk->entries[nid % NAT_ENTRY_PER_BLOCK];

#ifdef CS_DEBUG_GC_READ
    CS_INFO(gi->fi, "Read nat block, addr=%u, pack=%d, nid=[%u,%lu)",
        new_ent->nat_blk_addr, 
        bm_test_bit(nid/NAT_ENTRY_PER_BLOCK, gi->fi->nat_bitmap) ? 2 : 1,
        new_ent->start_nid, new_ent->start_nid + NAT_ENTRY_PER_BLOCK);
    CS_INFO(gi->fi, "nid=%u, off_in_blk=%lu, blkaddr=%u, ino=%u, ver=%u", 
        nid, nid % NAT_ENTRY_PER_BLOCK, ret->block_addr, ret->ino, ret->version);
#endif

got_it:
    return ret;
fail:
    CS_ERROR(gi->fi, "Fail to read nat block, nid = %u, nat_blk_addr = %u, nat_bit = %x", 
            nid, new_ent->nat_blk_addr, 
            bm_test_bit(nid/NAT_ENTRY_PER_BLOCK,gi->fi->nat_bitmap));
    CS_ERROR(gi->fi, "nat block starts at blkaddr=%u", F2FS_SB(gi->fi)->nat_blkaddr);
    kfree(new_ent->nat_blk);
    kfree(new_ent);
    return NULL;
}

// **** NOT USED **** //
static void free_gc_nat(struct gc_info *gi, struct list_head *nat_list)
{
    struct nat_block_entry *nat_block_entry, *tmp;
    list_for_each_entry_safe(nat_block_entry, tmp, nat_list, list){
        list_del(&nat_block_entry->list);
        if(!nat_block_entry->from_journal)
            kfree(nat_block_entry->nat_blk);
        kfree(nat_block_entry);
    }
}

static inline struct dirty_extent_list *init_dirty_ext_list(unsigned int cap)
{
	struct dirty_extent_list *del;
	del = linear_malloc(&m->shared_allocator, sizeof(struct dirty_extent_list), 0);
    del->capacity = cap;
    del->size = 0;
    del->ext = linear_malloc(&m->shared_allocator, del->capacity * sizeof(struct dirty_extent), 0);
	return del;
}

static inline void free_dirty_ext_list(struct dirty_extent_list *del)
{
	kvfree(del->ext);
	kfree(del);
}

static inline bool ext_mergable(struct dirty_extent *ext, 
            unsigned int off, block_t addr)
{
    return (ext->ofs_in_node + ext->len == off) && \
            (ext->new_addr + ext->len == addr);
}

static void add_dirty_ext(struct f2fs_info *fi, struct dirty_extent_list *del, 
		    unsigned int off, block_t addr)
{
	struct dirty_extent *new_ext, *last_ext;

    if(del->size > 0){
        last_ext = &del->ext[del->size - 1];
        if(ext_mergable(last_ext, off, addr)){
            last_ext->len ++;
            return;
        }
    }

	if(del->size==del->capacity){
        CS_INFO(fi, "dirty extent list space not enough");
		del->capacity *= 2;
		new_ext = linear_malloc(&allocator, del->capacity * sizeof(struct dirty_extent), 0);
		memcpy(new_ext, del->ext, del->size * sizeof(struct dirty_extent));
		kvfree(del->ext);
		del->ext = new_ext;
	}
    new_ext = &del->ext[del->size++];
    new_ext->ofs_in_node = off;
    new_ext->new_addr = addr;
    new_ext->len = 1;

}

// **** NOT USED **** //
static struct node_info *find_node_info(struct gc_info *gi, nid_t nid)
{
    for(int i = 0; i < gi->nr_node_info; i++){
        if(gi->ni[i].nid == nid)
            return &gi->ni[i];
    }
    return NULL;
}

// **** NOT USED **** //
static struct node_entry *find_gc_node(struct list_head *node_list, nid_t nid)
{
    struct node_entry *node_entry;
    list_for_each_entry_reverse(node_entry, node_list, list){
        if(node_entry->nid == nid)
            return node_entry;
    }
    return NULL;
}

enum { 
    GC_DNODE_LIST,
    GC_INODE_LIST
};

// **** NOT USED **** //
// If already in list, finds it and return.
// If not in list, read from storage.
// The read is blocked, don't touch the node block until a flush is done.
static struct node_entry *add_gc_node(struct gc_info *gi, nid_t nid, int list_type, unsigned int *count)
{
    bool is_dnode = (list_type == GC_DNODE_LIST);
    struct list_head *node_list = (is_dnode ? &gi->dnode_list : &gi->inode_list);
    struct node_entry *new, *ret;
    struct nat_block_entry *nat_blk_ent = NULL;
    block_t blk_addr;
    bool has_node_info = gi->nr_node_info;

    ret = find_gc_node(node_list, nid);
    if(ret)
        return ret;
    
    // insert if not exits
    new = linear_zalloc(&allocator, sizeof(*new), 0);
    new->nid = nid;
    new->no = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    if(has_node_info){
        new->ni = find_node_info(gi, nid);
        if(!new->ni){
            CS_ERROR(gi->fi, "Fail to find node info of nid=%u", nid);
            return NULL;
        }
    }else {
        new->nat_ent = add_gc_nat(gi, nid, &nat_blk_ent);
        if(!new->nat_ent)
            goto fail;
    }
    if(is_dnode){
        new->dirty_exts[0] = init_dirty_ext_list(gi->fi->blocks_per_seg);
    }

    blk_addr = (has_node_info ? new->ni->blk_addr : new->nat_ent->block_addr);
    if(f2fs_read_block(gi->fi, blk_addr, new->no,
             CSIO_F2FS_NODE, 0, 0, NULL))
        goto fail;

    list_add_tail(&new->list, node_list);
    (*count) ++;
#ifdef CS_DEBUG_GC_READ
    if(list_type==GC_DNODE_LIST)
        CS_INFO(gi->fi, "Add dnode with nid=%u to cached list, now %u dnodes cached", new->nid, *count);
    else
        CS_INFO(gi->fi, "Add inode with nid=%u to cached list, now %u inodes cached", new->nid, *count);
#endif
    return new;
fail:
    CS_ERROR(gi->fi, "Fail to read node block, nid = %u, is_dnode(%d)", nid, is_dnode);
    if(new->nat_ent){
        if(nat_blk_ent->from_journal)
            CS_ERROR(gi->fi, "nat is from journal, nid = %u", nid);
        else
            CS_ERROR(gi->fi, "nat is from storage, nat_blk_addr = %u, start_nid = %u", 
                    nat_blk_ent->nat_blk_addr, nat_blk_ent->start_nid);
        __dump_journal(gi->fi, CURSEG_I(gi->fi, CURSEG_HOT_DATA)->journal, NAT_JOURNAL);
        CS_ERROR(gi->fi, "nid=%u, blkaddr=%u, ino=%u, ver=%u", 
                nid, new->nat_ent->block_addr, new->nat_ent->ino, new->nat_ent->version);
        if(!nat_blk_ent->from_journal)
            __dump_nat_blk(gi->fi, nat_blk_ent->nat_blk, nid%NAT_ENTRY_PER_BLOCK, 20, 0);
    }
    kfree(new->no);
    kfree(new);
    return NULL;
}

// **** NOT USED | OUTDATED **** //
static void free_gc_node(struct gc_info *gi, int list_type){
    struct list_head *node_list = (list_type == GC_DNODE_LIST ? &gi->dnode_list : &gi->inode_list);
    struct node_entry *node_entry, *tmp;
    list_for_each_entry_safe(node_entry, tmp, node_list, list){
        list_del(&node_entry->list);
        kfree(node_entry->no);
        if(list_type==GC_DNODE_LIST)
            free_dirty_ext_list(node_entry->dirty_exts);
        kfree(node_entry);
    }
}

#ifdef CS_DEBUG_GC_WRITE
static void dump_gc_node_list(struct gc_info *gi, int list_type)
{
    struct list_head *node_list = (list_type == GC_DNODE_LIST ? &gi->dnode_list : &gi->inode_list);
    struct node_entry *node_entry;
    bool is_inode;
    list_for_each_entry(node_entry, node_list, list){
        is_inode = IS_INODE(node_entry->no);
        CS_INFO(gi->fi, "Ready to dump node nid=%u, is_inode(%d)", node_entry->nid, is_inode);
        if(is_inode){
            __dump_inode_blk(gi->fi, node_entry->no, 1);
        }else{
            __dump_node_blk(node_entry->no, DEF_ADDRS_PER_BLOCK, 0);
        }
    }
}
#endif

// **** NOT USED | OUTDATED **** //
static void build_sentry_info(struct f2fs_info *fi, 
        struct sit_entry_info **sei_pp, unsigned int segno, int force)
{
    struct sit_entry_info *sei;
    struct gc_info *gi = fi->gi;
    unsigned long long t_start = get_time_ns();
    
    ASSERT(!F2FS_IS_LAME(fi));

    *sei_pp = linear_zalloc(&allocator, sizeof(struct sit_entry_info), 0);
    sei = *sei_pp;
    if(!sei)
        return;
    sei->sit_blk = linear_malloc(&allocator, F2FS_BLKSIZE, 0);
    if(!sei->sit_blk){
        kfree(sei);
        return;
    }
    
    sei->segno = segno;
    sei->se_off = segno % SIT_ENTRY_PER_BLOCK;
    sei->sit_addr = current_sit_addr(fi, segno);
    f2fs_read_block(fi, sei->sit_addr, sei->sit_blk, 
                CSIO_F2FS_META, force, force, NULL);
    MARK_EVENT(fi, "read sit block end", t_start);
    
    sei->sentry = lookup_journal_sit(fi, segno); // sit in journal can be newer than in sit block
    if(sei->sentry){
        sei->se_from_jrnl = true;
    }else{
        sei->se_from_jrnl = false;
        sei->sentry = &sei->sit_blk->entries[sei->se_off];
    }
    MARK_EVENT(fi, "lookup journal end", t_start);

#ifdef CS_DEBUG_BUILD_SEI
    f2fs_flush_csio(fi, CSIO_F2FS_META, CSIO_READ);
    CS_INFO(gi->fi, "build sit entry of segment(segno=%u) from %s:", 
            segno, sei->se_from_jrnl ? "journal" : "sit block");
    CS_INFO(gi->fi, "sit addr=%u, sit off=%u", sei->sit_addr, sei->se_off);
    __dump_sit_entry(gi->fi, sei->sentry);
#endif

    if(gi){
        gi->nr_dirty_se ++;
        list_add_tail(&sei->list, &gi->se_list);
    }
    MARK_EVENT(fi, "add tail end", t_start);
}

static void free_sentry_info(struct gc_info *gi)
{
    struct list_head *se_list = &gi->se_list;
    struct sit_entry_info *sei, *tmp;
    list_for_each_entry_safe(sei, tmp, se_list, list){
        list_del(&sei->list);
        kfree(sei->sit_blk);
        kfree(sei);
    }
}

static void flush_sit_entry(struct f2fs_info *fi, struct sit_entry_info *sei, int force)
{
    if(sei->se_from_jrnl)
        sei->sit_blk->entries[sei->se_off] = *sei->sentry;
    f2fs_write_block(fi, sei->sit_addr, sei->sit_blk, 
            CSIO_F2FS_META, force, force, NULL);
}

// read summary (from storage) and sit (from package or storage) of source segment, 
// read needed nat from package, if host sends it
// read sit of target segment(if no pre-allocation in host)
// in currenty implementation, sit of src/tgt segments and node info are always sent from host
static int read_gc_meta(struct gc_info *gi)
{
    int err = 0;
    struct f2fs_info *fi = gi->fi;
    unsigned int segno = gi->segno;
    int sync = 0;
    unsigned long long t_start = get_time_ns();

    // read summary of source segment
    gi->sum_addr = get_ssa_addr(fi, segno);
    err = f2fs_read_block(fi, gi->sum_addr, gi->sum_blk, 
            CSIO_F2FS_META, 1, sync, NULL);

    if(fi->package->header.meta_sent_from_host){
        struct csgc_package *package = fi->package;
        struct csgc_header *header = &package->header;

        gi->nr_node_info = header->nr_node_info;
        gi->sentry = linear_malloc(&m->shared_allocator, sizeof(struct f2fs_sit_entry), 0);
        gi->ni = linear_malloc(&m->shared_allocator, gi->nr_node_info * sizeof(struct node_info), 0);

        memcpy(gi->sentry, package->data + header->offs.sit_start_h2d, 
                sizeof(struct f2fs_sit_entry));
        memcpy(gi->ni, package->data + header->offs.nat_start, 
                header->nr_node_info * sizeof(struct node_info));
    }else {
        build_sentry_info(fi, &gi->old_sei, segno, sync);
    }

    if(!F2FS_HAS_PRE_ALLOC_SEGS(fi))
        build_sentry_info(fi, &gi->new_sei, 
            CURSEG_I(gi->fi, CURSEG_COLD_DATA)->segno, sync);
    MARK_EVENT(fi, "build sentry end", t_start);

    // if(!sync)
    //     f2fs_flush_csio(gi->fi, CSIO_F2FS_META, 
    //         CSIO_READ);
    // only supports data segment gc

#ifdef CS_DEBUG_GC_READ
    CS_INFO(gi->fi, "SIT entry info of segment(segno=%u) to be GCed:", gi->segno);
    __dump_sit_entry(gi->fi, gi->sentry);
    __dump_sentry_info(gi->fi, gi->old_sei);
    CS_INFO(gi->fi, "SIT entry info of curseg_cold_data(segno=%u):", CURSEG_I(gi->fi, CURSEG_COLD_DATA)->segno);
    __dump_sentry_info(gi->fi, gi->new_sei);
    CS_INFO(gi->fi, "Summary block of segment(segno=%u) to be GCed:", gi->segno);
    __dump_summary(gi->fi, gi->sum_blk);
#endif

    MARK_EVENT(fi, "build gi end", t_start);
    return err;
}

static int gc_info_init(struct f2fs_info *fi, unsigned int segno)
{
    int err = 0;
    unsigned long long t_start = get_time_ns();
    struct gc_info *gi;
    
    gi = fi->gi;
    gi->fi = fi;
    gi->segno = segno;
    gi->sum_blk = linear_malloc(&m->shared_allocator, F2FS_BLKSIZE, 0);

    INIT_LIST_HEAD(&gi->nat_journal_list);
    INIT_LIST_HEAD(&gi->nat_block_list);
    INIT_LIST_HEAD(&gi->inode_list);
    INIT_LIST_HEAD(&gi->dnode_list);
    INIT_LIST_HEAD(&gi->se_list);

    MARK_EVENT(fi, "ready to enter read_gc_meta", t_start);
    err = read_gc_meta(gi);
    if(err)
        goto ret;

    gi->vblocks = GET_SIT_VBLOCKS(gi->sentry ? gi->sentry : gi->old_sei->sentry);
    gi->vblock_list = linear_malloc(&m->shared_allocator, gi->vblocks * sizeof(struct data_block_entry), 0);
    gi->vblock_buffer = linear_malloc(&m->dma_noncache_allocator, gi->vblocks * F2FS_BLKSIZE, 0);

    MARK_EVENT(fi, "malloc end", t_start);
ret:
    return err;
}

static void gc_info_free(struct gc_info *gi)
{
    gi->fi->gi = NULL;
#ifndef CONFIG_LINEAR_MALLOC
    kfree(gi->sentry);
    kfree(gi->ni);
    kfree(gi->sum_blk);

    // free nat list, inode list and dnode list
    free_gc_nat(gi, &gi->nat_journal_list);
    free_gc_nat(gi, &gi->nat_block_list);
    free_gc_node(gi, GC_INODE_LIST);
    free_gc_node(gi, GC_DNODE_LIST);

    free_sentry_info(gi);

    // free valid blocks
    kfree(gi->vblock_list);
    kvfree(gi->vblock_buffer);
    kfree(gi);
#endif
}
#ifdef CS_DEBUG_GC_READ
static void dump_vblocks(struct gc_info *gi)
{
    struct data_block_entry *vblk_ent;
    struct node_entry *ino_ent, *dno_ent;
    CS_INFO(gi->fi, "Valid blocks info in segment(segno=%u)", gi->segno);
    for(int i = 0; i < gi->vblocks; i++){
        vblk_ent = &gi->vblock_list[i];
        ino_ent = vblk_ent->ino;
        dno_ent = vblk_ent->dno;

        CS_INFO(gi->fi, "Valid Data Block %d: blkaddr=%u, ino=%u, dno=%u, off_in_dno=%u",
                i, vblk_ent->old_addr, ino_ent->nid, dno_ent->nid, vblk_ent->offset);
        CS_INFO(gi->fi, "\t dnode info: nid=%u, blkaddr=%u, ino=%u, v=%u", dno_ent->nid, 
                dno_ent->nat_ent->block_addr, dno_ent->nat_ent->ino, dno_ent->nat_ent->version);
        CS_INFO(gi->fi, "\t inode info: nid=%u, blkaddr=%u, ino=%u, v=%u", ino_ent->nid, 
                ino_ent->nat_ent->block_addr, ino_ent->nat_ent->ino, ino_ent->nat_ent->version);
    }
}
#endif

static void __pack_vblock_err_node_info(struct gc_info *gi, unsigned int blkaddr, 
        unsigned int ofs_in_node, nid_t dno, nid_t ino)
{
    struct csgc_error_info *err_info= &WORKER_I(gi->fi)->err_info;
    err_info->src_blkaddr = blkaddr;
    err_info->ofs_in_node = ofs_in_node;
    err_info->dno = dno;
    err_info->ino = ino;
}

static void __pack_vblock_err_seg_info(struct gc_info *gi, unsigned int src_segno, 
        unsigned int dst_segno, unsigned int dst_blkaddr)
{
    struct csgc_error_info *err_info= &WORKER_I(gi->fi)->err_info;
    err_info->src_segno = src_segno;
    err_info->dst_segno = dst_segno;
    err_info->dst_blkaddr = dst_blkaddr;
}

static void pack_vblock_err_info(struct gc_info *gi, struct data_block_entry *vblk_ent)
{
    unsigned int dst_segno;
    __pack_vblock_err_node_info(gi, vblk_ent->old_addr, vblk_ent->offs_in_dno, 
            vblk_ent->dno->nid, gi->nr_node_info ? vblk_ent->dno->ni->nid:vblk_ent->dno->nat_ent->ino);

    dst_segno = F2FS_HAS_PRE_ALLOC_SEGS(gi->fi) ? 
            CUR_PSEG_I(gi->fi)->segno : gi->new_sei->segno;
    __pack_vblock_err_seg_info(gi, gi->segno, dst_segno, 
            vblk_ent->new_addr);
}

static int read_shared_dnodes(struct gc_info *gi)
{
    struct f2fs_info *fi;
    int nr_node;
    int err = 0;

    fi = gi->fi;
    nr_node = gi->nr_node_info;
    if(nr_node == 0 || nr_node > fi->blocks_per_seg){
        CS_ERROR(fi, "No node info in package or Invalid node info number (nr_node=%d)", nr_node);
        return -CSGC_NO_NAT_INFO;
    }

    gi->shared_dnode_list = linear_zalloc(&m->shared_allocator, 
                nr_node * sizeof(struct node_entry), 0);
    gi->shared_dnode_buffer = linear_malloc(&m->shared_allocator, 
                nr_node * F2FS_BLKSIZE, 0);
    
    for(int i = 0; i < nr_node; i++){
        struct node_entry *node_ent = &gi->shared_dnode_list[i];

        node_ent->ni = &gi->ni[i];
        for(int j = 0; j < fi->nr_cs_workers; j++)
            node_ent->dirty_exts[j] = init_dirty_ext_list(fi->blocks_per_seg);
        node_ent->nid = gi->ni[i].nid;
        node_ent->no = (struct f2fs_node *) (gi->shared_dnode_buffer + i * F2FS_BLKSIZE);

        if(!node_ent->nid){
            CS_ERROR(fi, "Invalid nid (%u)", node_ent->nid);
            goto fail_read;
        }

        if(f2fs_read_block(fi, node_ent->ni->blk_addr, node_ent->no, 
                        CSIO_F2FS_NODE, 0, 0, NULL)){
            CS_ERROR(fi, "Fail to read dnode block, nid=%u, blkaddr=%u", 
                    node_ent->nid, node_ent->ni->blk_addr);
            goto fail_read;
        }
    }

    // submit the last blocked node read
    f2fs_flush_csio_async(fi, CSIO_F2FS_NODE, CSIO_READ);

    return 0;

fail_read:
    return -CSGC_FAILREAD;
}

struct node_entry *find_shared_dnode(struct gc_info *gi, nid_t nid){
    int nr_node = gi->nr_node_info;
    for(int i = 0; i < nr_node; i++){
        if(gi->shared_dnode_list[i].nid == nid)
            return &gi->shared_dnode_list[i];
    }
    return NULL;
}

// Read valid blocks in the segment, along with their dnodes,
// inodes, and nat blocks needed.
static int read_vblocks(struct gc_info *gi)
{
    struct f2fs_info *fi = gi->fi;
    struct worker_info *wi = WORKER_I(fi);
    struct partition_info *partition = &wi->partition;
    struct data_block_entry *vblk_ent;
    struct f2fs_summary *sum_ent;
    nid_t dno_nid;
    unsigned int buffer_off = partition->start_vblk_off * F2FS_BLKSIZE;
    int vblk_off, offs_in_seg;
    int err = 0;
    int flushed = 0;
#ifdef CS_DEBUG_PERF_DETAIL
    unsigned long long t1, t2, t3, t12_1;
    memset(LOCAL_TS(fi).read_breakdown, 0, sizeof(LOCAL_TS(fi).read_breakdown));
#endif

    wi->vblock_last_blocked_offset = partition->start_vblk_off;

    // read valid data blocks
    for(vblk_off = partition->start_vblk_off; vblk_off < partition->end_vblk_off; vblk_off++){
        
        GET_TIME_PERF_DEBUG(t1);
        
        vblk_ent = &gi->vblock_list[vblk_off];
        offs_in_seg = vblk_ent->offs_in_seg;
        sum_ent = &gi->sum_blk->entries[offs_in_seg];
        // read info from summary entry
        dno_nid = sum_ent->nid;
        vblk_ent->sum = sum_ent;
        vblk_ent->offs_in_dno = sum_ent->ofs_in_node;
        // read valid data blocks
        vblk_ent->data_block = gi->vblock_buffer + buffer_off;
        vblk_ent->old_addr = segno2blkaddr(fi, gi->segno) + offs_in_seg;

        GET_TIME_PERF_DEBUG(t12_1);

#ifndef CONFIG_MIGRATE_BYPASS_MEMORY        
        err = f2fs_read_block(fi, vblk_ent->old_addr, vblk_ent->data_block, 
                            CSIO_F2FS_DATA, 0, 0, &flushed);
        if(err){
            CS_ERROR(gi->fi, "Fail to read data block, segno=%u, offset=%u blkaddr=%u", 
                    gi->segno, offs_in_seg, vblk_ent->old_addr);
            __pack_vblock_err_node_info(gi, vblk_ent->old_addr, 
                    vblk_ent->offs_in_dno, dno_nid, 0);
            goto ret;
        }
        if(flushed){
            CS_INFO(fi, "submitted %d blocked data block reads", vblk_off - wi->vblock_last_blocked_offset);
            wi->vblock_last_blocked_offset = vblk_off;
        }
#endif
        GET_TIME_PERF_DEBUG(t2);

        // find dnode entry in shared dnode list, 
        // **DNODE BLOCKS ARE NOT READY NOW, DON'T TOUCH!**
        vblk_ent->dno = find_shared_dnode(gi, dno_nid);
        if(!vblk_ent->dno){
            CS_ERROR(fi, "dnode %u not found in dnode lists", dno_nid);
            __pack_vblock_err_node_info(gi, vblk_ent->old_addr, 
                    vblk_ent->offs_in_dno, dno_nid, 0);
            goto fail_read;
        }

        // no need to read inode

#ifndef CONFIG_MIGRATE_BYPASS_MEMORY        
        buffer_off += F2FS_BLKSIZE;
#endif

        GET_TIME_PERF_DEBUG(t3);

#ifdef CS_DEBUG_PERF_DETAIL
        LOCAL_TS(fi).read_breakdown[0] += t12_1 - t1;
        LOCAL_TS(fi).read_breakdown[1] += t2 - t12_1;
        LOCAL_TS(fi).read_breakdown[2] += t3 - t2;
#endif
    }

#ifndef CONFIG_MIGRATE_BYPASS_MEMORY        
    f2fs_flush_csio_async(fi, CSIO_F2FS_DATA, CSIO_READ);
#endif

#ifdef CS_DEBUG_PERF_DETAIL
    CS_INFO(fi, "Read vblocks breakdown: %llu | %llu | %llu", 
        LOCAL_TS(fi).read_breakdown[0], LOCAL_TS(fi).read_breakdown[1], 
        LOCAL_TS(fi).read_breakdown[2]);
#endif
    
#ifdef CS_DEBUG_GC_READ
    dump_vblocks(gi);
#endif

ret:
    return err;
fail_read:
    return -CSGC_FAILREAD;
}

// **** NOT USED **** //
static void reset_curseg(struct f2fs_info *fi, unsigned int segno, int type)
{
    struct curseg_info *curseg = CURSEG_I(fi, type);
    struct summary_footer *sum_footer;
	unsigned short seg_type = curseg->seg_type;

    curseg->inited = true;
    curseg->segno = segno;
    curseg->next_blkoff = 0;

    sum_footer = &(curseg->sum_blk->footer);
	memset(sum_footer, 0, sizeof(struct summary_footer));

    if (IS_DATASEG(seg_type))
		SET_SUM_TYPE(sum_footer, SUM_TYPE_DATA);
	if (IS_NODESEG(seg_type))
		SET_SUM_TYPE(sum_footer, SUM_TYPE_NODE);
}

// **** NOT USED **** //
/*
 * If a segment is written by LFS manner, next block offset is just obtained
 * by increasing the current block offset. Other mode is not supported yet.
 */
static void __refresh_next_blkoff(struct f2fs_info *fi,
				struct curseg_info *seg)
{
    if(seg->alloc_type != LFS){
        CS_ERROR(fi, "Not supported segment allocation type: %d\n", seg->alloc_type);
        return;
    }
    seg->next_blkoff++;
}

static inline void __advance_pseg_ptr(struct worker_info *wi)
{
    struct partition_info *partition = &wi->partition;
    int off = wi->cur_pseg_off;
    ASSERT(off < 2);
    if(wi->cur_pseg_next_blk_off + 1 < partition->pseg_blk_off[off] + partition->pseg_blk_len[off])
        wi->cur_pseg_next_blk_off ++;
    else{
        wi->cur_pseg_off ++;
        wi->cur_pseg_next_blk_off = partition->pseg_blk_off[wi->cur_pseg_off];
    };
}

// **** NOT USED **** //
/*
 * `sum` is the summary entry of the block `curseg->next_blkoff`
 * This function updates summary entry in curseg by summary entry `sum`
 */
static void __add_sum_entry_curseg(struct f2fs_info *fi, int type,
					struct f2fs_summary *sum)
{
	struct curseg_info *curseg = CURSEG_I(fi, type);
	void *addr = curseg->sum_blk;

	addr += curseg->next_blkoff * sizeof(struct f2fs_summary);
	memcpy(addr, sum, sizeof(struct f2fs_summary));
}

static inline void __add_sum_entry_curpseg(struct worker_info *wi, 
        struct pseg_info *pseg, struct f2fs_summary *sum)
{
	void *addr = pseg->sum_blk;

	addr += wi->cur_pseg_next_blk_off * sizeof(struct f2fs_summary);
	memcpy(addr, sum, sizeof(struct f2fs_summary));
}

// **** NOT USED **** //
static int update_sit_entry(struct f2fs_info *fi, 
                    struct f2fs_sit_entry *se, block_t blkaddr, int del)
{
    unsigned int offset;
	long int new_vblocks;
	bool exist;

    offset = GET_BLKOFF_FROM_SEG0(fi, blkaddr);
    new_vblocks = GET_SIT_VBLOCKS(se) + del;

    if(new_vblocks < 0 || new_vblocks > fi->blocks_per_seg){
        CS_ERROR(fi, "Invalid new vblocks: %ld\n", new_vblocks);
        goto err;
    }
    set_sentry_vblocks(se, new_vblocks);

    if(del > 0){
        exist = bm_test_and_set_bit(offset, (char *)se->valid_map);
        if(exist){
            CS_INFO(fi, "Invalid set: bit already set! blkaddr=%u, off=%u, segno=%u",
            blkaddr, offset, GET_SEGNO(F2FS_SB(fi), blkaddr));
            __dump_sit_entry(fi, se);
            goto err;
        }
    }else{
        exist = bm_test_and_clear_bit(offset, (char *)se->valid_map);
        if(!exist){
            CS_INFO(fi, "Invalid clear: bit already cleared! blkaddr=%u, off=%u, segno=%u",
            blkaddr, offset, GET_SEGNO(F2FS_SB(fi), blkaddr));
            __dump_sit_entry(fi, se);
            goto err;
        }
    }
    return 0;
err:
    return -CSGC_WRONG_SIT;
}

// get data block from pre-allocated segment. no need to update SIT of 
// old block and new block, this has been done in host pre-allocation.
int f2fs_get_pre_alloc_data_block(struct f2fs_info *fi, 
            block_t *new_blkaddr, struct f2fs_summary *sum, int type)
{
    int ret = 0;
    struct worker_info *wi = WORKER_I(fi);
    struct f2fs_sb *sb = F2FS_SB(fi);
    struct pseg_info *pseg = CUR_PSEG_I(fi);

    if(!__is_valid_pseg(pseg)){
        CS_ERROR(fi, "Invalid pre-allocated segment:%p", pseg);
        ret = -CSGC_NO_FREE_PSEG;
        goto out;
    }
    
    if(type != pseg->seg_type){
        CS_ERROR(fi, "pre allocated segment type mismatch, type=%d, pseg->seg_type=%d", 
                type, pseg->seg_type);
        xil_printf("error at %s:%d\n", __FILE__, __LINE__);
        ret = -CSGC_ERR;
        goto out;
    }

    *new_blkaddr = get_pseg_next_blkaddr(wi, sb, pseg);

    __add_sum_entry_curpseg(wi, pseg, sum);

    __advance_pseg_ptr(wi);
    // CS_INFO(fi, "p%u b%u", wi->cur_pseg_off, wi->cur_pseg_next_blk_off);

out:
    return ret;
}

// caller should call f2fs_flush_csio later 
static int outplace_write(struct gc_info *gi, int vblk_off)
{
    int err = 0;
    struct data_block_entry *vblk_ent;
    struct node_entry *dno_ent;
    struct f2fs_summary *sum;
    bool has_node_info = gi->nr_node_info;
    unsigned char version;
#ifdef CS_DEBUG_PERF_DETAIL
    unsigned long long t1, t2, t3, t4, t5;
#endif
    GET_TIME_PERF_DEBUG(t1);

    vblk_ent = &gi->vblock_list[vblk_off];
    dno_ent = vblk_ent->dno;
    sum = vblk_ent->sum;

    version = has_node_info ? dno_ent->ni->version : dno_ent->nat_ent->version;
    if(version != sum->version){
        CS_ERROR(gi->fi, "Inconsistent version in nat(%d) and summary(%d) of node(%d)\n",
                   version, sum->version, dno_ent->nid);
        err = -CSGC_INCONSISTENT;
        goto ret;
    }

    if(vblk_ent->old_addr != data_blkaddr(dno_ent->no, vblk_ent->offs_in_dno)){
        CS_ERROR(gi->fi, "Inconsistent data block addr between addr in dnode(%u)"
                    " and addr computed from segno(%u)\n", 
                    data_blkaddr(dno_ent->no, vblk_ent->offs_in_dno),
                    vblk_ent->old_addr);
        err = -CSGC_INCONSISTENT;
        goto ret;
    }
    GET_TIME_PERF_DEBUG(t2);

    // allocate new address
    if(F2FS_HAS_PRE_ALLOC_SEGS(gi->fi))
        err = f2fs_get_pre_alloc_data_block(gi->fi, &vblk_ent->new_addr, 
                    vblk_ent->sum, CURSEG_COLD_DATA);
    else{
        xil_printf("error at %s:%d\n", __FILE__, __LINE__);
        err = -CSGC_ERR;
        CS_ERROR(gi->fi, "IN-STORAGE BLOCK ALLOCATION IS NOT SUPPPORTED, NEED PRE_ALLOCATION IN HOST");
        // err = f2fs_allocate_data_block(gi->fi, vblk_ent->old_addr, &vblk_ent->new_addr, 
        //             gi->old_sei, &gi->new_sei, vblk_ent->sum, CURSEG_COLD_DATA);
    }
    if(err)
        goto ret;

    GET_TIME_PERF_DEBUG(t3);

#ifdef CONFIG_MIGRATE_BYPASS_MEMORY
    // migrate valid blocks, bypass memory
    f2fs_migrate_block(gi->fi, vblk_ent->old_addr, vblk_ent->new_addr, CSIO_F2FS_DATA, 0, 0, NULL);
#else
    // write to new address, blocked, should not fail
    f2fs_write_block(gi->fi, vblk_ent->new_addr, 
        gi->vblock_buffer + vblk_off * F2FS_BLKSIZE, 
        CSIO_F2FS_DATA, 0, 0, NULL);
#endif
    
    GET_TIME_PERF_DEBUG(t4);
    
    // update address in dnode, should update extent tree in host to keep consistency
    __set_data_blkaddr(dno_ent->no, vblk_ent->offs_in_dno, vblk_ent->new_addr);
    add_dirty_ext(gi->fi, LOCAL_EXT_LIST(dno_ent), vblk_ent->offs_in_dno, vblk_ent->new_addr);

    GET_TIME_PERF_DEBUG(t5);
#ifdef CS_DEBUG_PERF_DETAIL
    LOCAL_TS(gi->fi).write_breakdonw[0] += t2 - t1;
    LOCAL_TS(gi->fi).write_breakdonw[1] += t3 - t2;
    LOCAL_TS(gi->fi).write_breakdonw[2] += t4 - t3;
    LOCAL_TS(gi->fi).write_breakdonw[3] += t5 - t4;
#endif

ret:
    return err;
}

static void dump_vblock_info(struct gc_info *gi, struct data_block_entry *vblk_ent)
{
    nid_t ino_nid = gi->nr_node_info ? vblk_ent->dno->ni->ino : vblk_ent->dno->nat_ent->ino;
    block_t blk_addr = gi->nr_node_info ? vblk_ent->dno->ni->blk_addr : vblk_ent->dno->nat_ent->block_addr;
    unsigned char version = gi->nr_node_info ? vblk_ent->dno->ni->version : vblk_ent->dno->nat_ent->version;
    struct f2fs_node *tmp_node_buffer;
    
    CS_INFO(gi->fi, "Valid Data Block: blkaddr=%u, ino=%u, dno=%u, off_in_dno=%u",
                vblk_ent->old_addr, ino_nid, vblk_ent->dno->nid, vblk_ent->offs_in_dno);
    CS_INFO(gi->fi, "\t dnode info: nid=%u, blkaddr=%u, v=%u, ino=%u", vblk_ent->dno->nid, 
                blk_addr, version, ino_nid);
    if(!F2FS_IS_LAME(gi->fi)){
        __dump_journal(gi->fi, CURSEG_I(gi->fi, CURSEG_HOT_DATA)->journal, NAT_JOURNAL);
        CS_INFO(gi->fi, "Nat journal list:");
        dump_nat_block_list(gi->fi, &gi->nat_journal_list);
        CS_INFO(gi->fi, "Nat block list:");
        dump_nat_block_list(gi->fi, &gi->nat_block_list);
    }else {
        dump_node_info(gi);
    }
    __dump_sum_entry(gi->fi, gi->sum_blk, vblk_ent->old_addr%512, false);
#ifdef CS_DEBUG_NODE_READ
    tmp_node_buffer = linear_zalloc(&m->shared_allocator, 4096, 0);
    f2fs_read_block(gi->fi, vblk_ent->dno->ni->blk_addr, tmp_node_buffer, 
            CSIO_F2FS_NODE, 1, 1, NULL);
    CS_INFO(gi->fi, "blkaddr_in_node[ofs=%u]: %u(after wait completion) | %u (now)| %u (now read again)", 
        vblk_ent->offs_in_dno, data_blkaddr(gi->debug_node_buffer, vblk_ent->offs_in_dno),
        data_blkaddr(vblk_ent->dno->no, vblk_ent->offs_in_dno),
        data_blkaddr(tmp_node_buffer, vblk_ent->offs_in_dno));
#endif
    if(!IS_INODE(vblk_ent->dno->no))
        __dump_node_blk(gi->fi, vblk_ent->dno->no, DEF_ADDRS_PER_BLOCK, 0);
    else
        __dump_inode_blk(gi->fi, vblk_ent->dno->no, true);
}

static int check_node_consistency(struct gc_info *gi)
{
    struct node_entry *dnode_entry;
    struct f2fs_node *node;
    int nr_node = gi->fi->package->header.nr_node_info;
    struct csgc_error_info *err_info = &WORKER_I(gi->fi)->err_info;
    
    for(int i = 0; i < nr_node; i++){
        dnode_entry = &gi->shared_dnode_list[i];
        node = dnode_entry->no;
        if(dnode_entry->nid != node->footer.nid){
            CS_ERROR(gi->fi, "Inconsistent nid in node footer, nid=%u, footer.nid=%u",
                        dnode_entry->nid, node->footer.nid);
            CS_INFO(gi->fi, "node footer in storage: "
                    "NID:%u, INODE_NR:%u, FLAG:%#x, CP_VER:%#lx, NXT_BLK:%u ", 
                    node->footer.nid, node->footer.ino, node->footer.flag, 
                    node->footer.cp_ver, node->footer.next_blkaddr);
            memset(err_info, 0, sizeof(*err_info));
            err_info->dno = dnode_entry->nid;
            if(dnode_entry->ni)
                err_info->ino = dnode_entry->ni->ino;
            err_info->src_segno = gi->segno;
            return -CSGC_INCONSISTENT;
        }

    }
    return 0;
}

static int move_vblocks(struct gc_info *gi, int *count)
{
    struct worker_info *wi = WORKER_I(gi->fi);
    struct partition_info *partition = &wi->partition;
    int ret = 0, vblk_off;
#ifdef CS_DEBUG_PERF_DETAIL
    unsigned long long t1, t2;
#endif

#ifdef CS_DEBUG_GC_WRITE
    CS_INFO(gi->fi, "Before migration, addresses in dnodes:");
    dump_gc_node_list(gi, GC_DNODE_LIST);
#endif

    ret = check_node_consistency(gi);
    if(ret)
        return ret;
    
    for(vblk_off = partition->start_vblk_off; vblk_off < partition->end_vblk_off; vblk_off++){

#ifndef CONFIG_MIGRATE_BYPASS_MEMORY        
        if(vblk_off >= wi->vblock_last_blocked_offset){
            CS_INFO(gi->fi, "waiting read data completion, last_blocked_offset=%u", 
                wi->vblock_last_blocked_offset);
            f2fs_csio_wait_completion(gi->fi, CSIO_READ, CSIO_F2FS_DATA);
            wi->vblock_last_blocked_offset = partition->end_vblk_off;
        }
#endif

        ret = outplace_write(gi, vblk_off);
        if(ret){
            CS_INFO(gi->fi, "==========================================================================================================");
            CS_INFO(gi->fi, "Fail to move vblock %u of segment %u", vblk_off, gi->segno);
            dump_vblock_info(gi, &gi->vblock_list[vblk_off]);
            CS_INFO(gi->fi, "==========================================================================================================");
            pack_vblock_err_info(gi, &gi->vblock_list[vblk_off]);
            return ret;
        }
        (*count)++;
    }

    GET_TIME_PERF_DEBUG(t1);

#ifdef CONFIG_MIGRATE_BYPASS_MEMORY
    f2fs_flush_csio_async(gi->fi, CSIO_F2FS_DATA, CSIO_MIGRATE);
#else
    f2fs_flush_csio_async(gi->fi, CSIO_F2FS_DATA, CSIO_WRITE);
#endif

    GET_TIME_PERF_DEBUG(t2);

#ifdef CS_DEBUG_PERF_DETAIL
    LOCAL_TS(gi->fi).write_breakdonw[4] = t2 - t1;
    CS_INFO(gi->fi, "Write vblocks breakdown: %llu | %llu | %llu | %llu | %llu", 
        LOCAL_TS(gi->fi).write_breakdonw[0], LOCAL_TS(gi->fi).write_breakdonw[1], 
        LOCAL_TS(gi->fi).write_breakdonw[2], LOCAL_TS(gi->fi).write_breakdonw[3], \
        LOCAL_TS(gi->fi).write_breakdonw[4]);
#endif

#ifdef CS_DEBUG_GC_WRITE
    CS_INFO(gi->fi, "Migrated %u blocks in segment %u", *count, gi->segno);
    CS_INFO(gi->fi, "SIT entry of old segment:");
    __dump_sit_entry(gi->fi, gi->sentry);
    __dump_sentry_info(gi->fi, gi->old_sei);
    CS_INFO(gi->fi, "SIT entry of new segment:");
    __dump_sentry_info(gi->fi, gi->new_sei);
    CS_INFO(gi->fi, "Summary of curseg_cold_data:");
    __dump_summary(gi->fi, CURSEG_I(gi->fi, CURSEG_COLD_DATA)->sum_blk);
    CS_INFO(gi->fi, "Addresses in dnodes are changed:");
    dump_gc_node_list(gi, GC_DNODE_LIST);
#endif

    return ret;
}

// **** NOT USED **** //
static void clear_relay_sum()
{
    struct sum_relay_info *sum_relay = &m->meta_relay_buf[0];
    memset(sum_relay, 0, sizeof(struct sum_relay_info));
}

// **** NOT USED **** //
// store the updated summaries of the last pseg in the relay buffer
// they will be used by the next contiguous request
static void set_relay_sum(struct gc_info *gi)
{
    struct f2fs_info *fi = gi->fi;
    struct pseg_info *pseg;
    struct sum_relay_info *sum_relay = &m->meta_relay_buf[0];
    unsigned int start, end;
    void *src, *dest;
    unsigned int size;
    
    pseg = LAST_PSEG_I(fi);
    start = pseg->start_blkoff;
    end = pseg->end_blkoff;
    if(sum_relay->prev_segno != pseg->segno || CS_REQ_IS_HEAD(gi)){
        clear_relay_sum();
        sum_relay->prev_segno = pseg->segno;
        sum_relay->nr_offset_range = 1;
        sum_relay->offset_range[0].start_off = start;
        sum_relay->offset_range[0].end_off = end;
    }else{
        if(sum_relay->offset_range[sum_relay->nr_offset_range-1].end_off == start)
            sum_relay->offset_range[sum_relay->nr_offset_range-1].end_off = end;
        else{
            sum_relay->offset_range[sum_relay->nr_offset_range].start_off = start;
            sum_relay->offset_range[sum_relay->nr_offset_range].end_off = end;
            sum_relay->nr_offset_range++;
        }
    }

    src = &pseg->sum_blk->entries[start];
    dest = &sum_relay->sum_block.entries[start];
    size = (end - start) * sizeof(struct f2fs_summary);
    memcpy(dest, src, size);
}

// **** NOT USED **** //
static void merge_relay_sum(struct gc_info *gi, struct pseg_info *pseg)
{
    struct sum_relay_info *sum_relay = &m->meta_relay_buf[0];
    unsigned int start, end;
    void *src, *dest;
    unsigned int size;

    // No need to merge, since this pseg is not shared by this and previous requests.
    if(pseg->segno != sum_relay->prev_segno)
        return;
    
    for(int i = 0; i < sum_relay->nr_offset_range; i++){
        start = sum_relay->offset_range[i].start_off;
        end = sum_relay->offset_range[i].end_off;
        ASSERT(end <= pseg->start_blkoff);
        src = &sum_relay->sum_block.entries[start];
        dest = &pseg->sum_blk->entries[start];
        size = (end - start) * sizeof(struct f2fs_summary);
        memcpy(dest, src, size);

        CS_INFO(gi->fi, "Merged summary of pseg %u, start=%u, end=%u", pseg->segno, start, end);
        // __dump_summary_range(gi->fi, pseg->sum_blk, start, end);
    }
}

/*
 * In current implementation, actually nothing is flushed in this function.
 * Since flushing summaries or dnodes complicates metadata synchronization 
 * more than benefits the performance, the dirty summaries and dnodes are
 * sent to host for updates.
 */
static int flush_gc_meta(struct gc_info *gi)
{
    struct f2fs_info *fi = gi->fi;
    struct pre_alloc_info *pi = F2FS_PI(fi);
    struct pseg_info *pseg;
    block_t addr;
    struct node_entry *dnode_entry;
    int nr_dnode = fi->package->header.nr_node_info;
    int req_is_head = CS_REQ_IS_HEAD(gi);

    // don't write dnodes, since host will update it to page cache

    // for(int i = 0; i < nr_dnode; i++){
    //     dnode_entry = &gi->shared_dnode_list[i];
    //     addr = gi->nr_node_info ? dnode_entry->ni->blk_addr : dnode_entry->nat_ent->block_addr;
    //     f2fs_write_block(fi, addr, dnode_entry->no, 
    //             CSIO_F2FS_NODE, 0, 0, NULL);
    // }
    // f2fs_flush_csio_async(fi, CSIO_F2FS_NODE, CSIO_WRITE);

    if(!F2FS_HAS_PRE_ALLOC_SEGS(fi)){
        struct list_head *se_list = &gi->se_list;
        struct sit_entry_info *sei;
        list_for_each_entry(sei, se_list, list){
            flush_sit_entry(fi, sei, 0);
        }
    }

    //************************************************************************//
    //**************Just leave these FXXKING summaries to host****************//
    //************************************************************************//
    // // the last pseg must be curseg in host(at the moment it was allocated), 
    // // if this segment is still curseg when csgc return, its summary in memory should 
    // // be updated. if it is not curseg, its summary should also be updated, we just may 
    // // have to read it from storage first.
    // // in all, the last pseg's summary should be sent back to host and previous psegs'
    // // summaries can be updated in storage.
    // for(int i = 0; i < fi->pi.nr_psegs; i++){
    //     pseg = &pi->psegs[i];
    //     if(!pseg->is_curseg){
    //         CS_INFO(fi, "pseg %u is not curseg, update its summary in storage", pseg->segno);
    //         if(!req_is_head)
    //             merge_relay_sum(gi, pseg);
    //         __dump_summary_range(gi->fi, pseg->sum_blk, 256, 512);
    //         f2fs_write_block(fi, get_ssa_addr(fi, pseg->segno), pseg->sum_blk, 
    //                 CSIO_F2FS_META, 1, 0, NULL);
    //     }
    // }
    // set_relay_sum(gi);
    
    return 0;
}

static void set_empty_sit_pack(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct csgc_header *header = &gi->fi->package->header;
    struct dirty_sit_pack *sit_pack;
    struct dsp_entry *pack_ent;

    header->offs.sit_start = *offset;
    sit_pack = (struct dirty_sit_pack *) (base_addr + *offset);
    sit_pack->nr_dirty_se = 0;
    pack_ent = sit_pack->entry;
    *offset = (void *)pack_ent - base_addr;
}

static void pack_dirty_sit(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct csgc_header *header = &gi->fi->package->header;
    struct list_head *se_list = &gi->se_list;
    struct sit_entry_info *sei;
    struct dirty_sit_pack *sit_pack;
    struct dsp_entry *pack_ent;

    header->offs.sit_start = *offset;
    sit_pack = (struct dirty_sit_pack *) (base_addr + *offset);
    sit_pack->nr_dirty_se = gi->nr_dirty_se;
    pack_ent = sit_pack->entry;

    list_for_each_entry(sei, se_list, list){
        pack_ent->segno = sei->segno;
        memcpy(&pack_ent->se, sei->sentry, sizeof(pack_ent->se));
        pack_ent ++;        
    }
    *offset = (void *)pack_ent - base_addr;
}

// should not be used, outdated 
static void pack_curseg(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct curseg_info *curseg = CURSEG_I(gi->fi, CURSEG_COLD_DATA);
    struct curseg_pack *curseg_pack;
    struct csgc_header *header = &gi->fi->package->header;

    header->offs.dirty_sum_start = *offset;
    curseg_pack = (struct curseg_pack *)(base_addr + *offset);
    *offset += sizeof(*curseg_pack);

    memcpy(&curseg_pack->sum_blk, curseg->sum_blk, sizeof(*curseg->sum_blk));
    curseg_pack->alloc_type = curseg->alloc_type;
    curseg_pack->seg_type = curseg->seg_type;
    curseg_pack->segno = curseg->segno;
    curseg_pack->next_blkoff = curseg->next_blkoff;
    curseg_pack->inited = curseg->inited;
}

static void pack_curpseg(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct pseg_info *pseg;
    struct pseg_pack *pseg_pack;
    struct csgc_header *header = &gi->fi->package->header;

    header->offs.dirty_sum_start = *offset;
    pseg_pack = (struct pseg_pack *)(base_addr + *offset);
    *offset += sizeof(*pseg_pack);

    ASSERT(header->nr_pre_alloc <= 2);
    pseg_pack->nr_summaries = 0;
    for(int i = 0; i < header->nr_pre_alloc; i++){
        struct pseg_sum_info *sum_info = &pseg_pack->sum_info[i];
        void *src, *dest;
        unsigned int size;
        pseg = &gi->fi->pi.psegs[i];
        sum_info->segno = pseg->segno;
        sum_info->seg_type = pseg->seg_type;
        sum_info->start_blkoff = pseg->start_blkoff;
        sum_info->end_blkoff = pseg->end_blkoff;
        sum_info->sum_len = pseg->end_blkoff - pseg->start_blkoff;

        src = &pseg->sum_blk->entries[pseg->start_blkoff];
        dest = &pseg_pack->summaries[pseg_pack->nr_summaries];
        size = sum_info->sum_len * sizeof(struct f2fs_summary);
        memcpy(dest, src, size);
        pseg_pack->nr_summaries += sum_info->sum_len;
        *offset += size;
    }
}

static void pack_dirty_dnode(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct csgc_header *header = &gi->fi->package->header;
    struct node_entry *dnode_entry;    
    struct dirty_node_pack *dnode_pack;
    struct dnp_entry *dnp_ent;
    int nr_dnode = header->nr_node_info;

    header->offs.dnode_start = *offset;
    dnode_pack = (struct dirty_node_pack *)(base_addr + *offset);
    dnode_pack->nr_dirty_node = 0;
    *offset += sizeof(dnode_pack->nr_dirty_node);

    for(int i = 0; i < nr_dnode; i++){
        dnode_entry = &gi->shared_dnode_list[i];
        dnode_pack->nr_dirty_node++;

        dnp_ent = (struct dnp_entry *)(base_addr + *offset);
        dnp_ent->nid = dnode_entry->nid;
        dnp_ent->ofs_of_node = ofs_of_node(dnode_entry->no);
        dnp_ent->ino_nid = dnode_entry->no->footer.ino;
        dnp_ent->nr_ext = 0;
        for(int j = 0; j < gi->fi->nr_cs_workers; j++){
            memcpy(dnp_ent->exts + dnp_ent->nr_ext, dnode_entry->dirty_exts[j]->ext, 
                    dnode_entry->dirty_exts[j]->size * sizeof(struct dirty_extent));
            dnp_ent->nr_ext += dnode_entry->dirty_exts[j]->size;
        }
        *offset += get_dnp_entry_size(dnp_ent);
    }
    
}

static void pack_debug_info(struct gc_info *gi, void *base_addr, unsigned int *offset)
{
    struct csgc_header *header = &gi->fi->package->header;
    struct f2fs_summary_block *sum_blk;

    header->offs.debug_start = *offset;

#ifdef CS_PACK_DEBUG_INFO
    sum_blk = (struct f2fs_summary_block *)(base_addr + *offset);
    *offset += sizeof(*sum_blk);

    memcpy(sum_blk, gi->sum_blk, sizeof(*sum_blk));
#endif
}

static inline int check_pack_size(struct gc_info *gi, unsigned int offset, 
        unsigned int size, unsigned int cap)
{
    int err = 0;
    if(offset + size > cap){
        CS_INFO(gi->fi, "pack_dirty_meta: not enough memory, %u + %u > %u", 
                offset, size, cap);
        set_package_err(gi->fi->package, -CSGC_NOMEM);
        err = -1;
    }
    return err;
}

static void dump_dirty_sit_pack(struct f2fs_info *fi, struct dirty_sit_pack *dsp)
{
    struct dsp_entry *dsp_ent;
    int i;
    CS_INFO(fi, "Dirty SIT pack, nr_dirty_se = %u:", dsp->nr_dirty_se);
    for(i = 0; i < dsp->nr_dirty_se; i++){
        dsp_ent = &dsp->entry[i];
        CS_INFO(fi, "segno = %u", dsp_ent->segno);
        __dump_sit_entry(fi, &dsp_ent->se);
    }
}

static void dump_curpseg_pack(struct f2fs_info *fi, struct pseg_pack *psp)
{
    CS_INFO(fi, "Pre-allocated segment pack:");
    for(int i = 0; i < fi->pi.nr_psegs ; i++){
        CS_INFO(fi, "segno = %u, seg_type = %d, start_blkoff = %u, end_blkoff = %u",
            psp->sum_info[i].segno, psp->sum_info[i].seg_type, 
            psp->sum_info[i].start_blkoff, psp->sum_info[i].end_blkoff);
    }
}

static void dump_curseg_pack(struct f2fs_info *fi, struct curseg_pack *csp)
{
    CS_INFO(fi, "Curseg pack:");
    CS_INFO(fi, "alloc_type = %d, seg_type = %d, segno = %u, next_blkoff = %u, inited = %d", 
            csp->alloc_type, csp->seg_type, csp->segno, csp->next_blkoff, csp->inited);
    __dump_summary(fi, &csp->sum_blk);
}

static void dump_dirty_node_pack(struct f2fs_info *fi, struct dirty_node_pack *dnp)
{
    struct dnp_entry *dnp_ent;
    unsigned int offset = 0;
    int i, j;
    CS_INFO(fi, "Dirty node pack, nr_dirty_node = %u:", dnp->nr_dirty_node);
    for(i = 0; i < dnp->nr_dirty_node; i++){
        dnp_ent = (struct dnp_entry *)(dnp->dnp_entries + offset);
        offset += get_dnp_entry_size(dnp_ent);
        CS_INFO(fi, "-----<nid = %u, nr_ext = %u>-----", dnp_ent->nid, dnp_ent->nr_ext);
        for(j = 0; j < dnp_ent->nr_ext; j++){
            CS_INFO(fi, "ext[%d]: ofs_in_node = %d, new_addr = %u, len = %u", 
                    j, dnp_ent->exts[j].ofs_in_node, dnp_ent->exts[j].new_addr, dnp_ent->exts[j].len);
        }
    }

}

static void dump_csgc_package(struct gc_info *gi) 
{
    struct csgc_package *package = gi->fi->package;
    struct csgc_header *header = &package->header;
    struct offset_info *offs = &header->offs;
    void *base_addr = package->data;
    struct dirty_sit_pack *dsp;
    struct curseg_pack *csp;
    struct pseg_pack *psp;
    struct dirty_node_pack *dnp;

    CS_INFO(gi->fi, "CSGC package header:");
    CS_INFO(gi->fi, "capacity = %u, npages = %u, pages_pointer = %016llx, segno = %u, status = [%d|%d|%d]", 
            header->capacity, header->npages, (unsigned long long)header->pages,
            header->segno, header->status[0], header->status[1], header->status[2]);
    CS_INFO(gi->fi, "data_pointer = %016llx, sit_start = %u, dirty_sum_start = %u, dnode_start = %u, data_size = %u", 
            (unsigned long long)package->data, offs->sit_start, offs->dirty_sum_start, 
            offs->dnode_start, offs->data_size_d2h);
    
    dsp = (struct dirty_sit_pack *)(base_addr + offs->sit_start);
    dump_dirty_sit_pack(gi->fi, dsp);

    if(F2FS_HAS_PRE_ALLOC_SEGS(gi->fi)){
        psp = (struct pseg_pack *)(base_addr + offs->dirty_sum_start);
        dump_curpseg_pack(gi->fi, psp);
    }else{
        csp = (struct curseg_pack *)(base_addr + offs->dirty_sum_start);
        dump_curseg_pack(gi->fi, csp);
    }

    dnp = (struct dirty_node_pack *)(base_addr + offs->dnode_start);
    dump_dirty_node_pack(gi->fi, dnp);

} 

#pragma GCC diagnostic ignored "-Wunused-function"
static int pack_dirty_meta(struct gc_info *gi)
{
    struct csgc_package *package = gi->fi->package;
    void *base_addr = package->data;
    unsigned int offset = package->header.offs.data_size_h2d;
    unsigned int cap = package->header.capacity - sizeof(struct csgc_header) - CS_PRINT_BUF_SIZE;

    if(F2FS_HAS_PRE_ALLOC_SEGS(gi->fi)){
        // TODO: ugly, remove this when old_sei is sent from host
        set_empty_sit_pack(gi, base_addr, &offset);
        
        if(check_pack_size(gi, offset, sizeof(struct pseg_pack), cap))
            goto err;
        pack_curpseg(gi, base_addr, &offset);
    }else{
        if(check_pack_size(gi, offset, get_sit_pack_size(gi->nr_dirty_se), cap))
            goto err;
        pack_dirty_sit(gi, base_addr, &offset);

        if(check_pack_size(gi, offset, sizeof(struct curseg_pack), cap))
            goto err;
        pack_curseg(gi, base_addr, &offset);
    }

    if(check_pack_size(gi, offset, get_dnp_size_from_list(gi), cap))
        goto err;
    pack_dirty_dnode(gi, base_addr, &offset);

    if(check_pack_size(gi, offset, sizeof(struct f2fs_summary_block), cap))
        goto err;
    pack_debug_info(gi, base_addr, &offset);

    package->header.offs.data_size_d2h = offset;
    set_package_err(package, CSGC_SUCCESS);
#ifdef CS_DEBUG_PACK
    dump_csgc_package(gi);
#endif
    return 0;

err:
    package->header.offs.data_size_d2h = offset;
    return -1;

}

static void pack_err_info(struct gc_info *gi)
{
    struct f2fs_info *fi = gi->fi;
    struct csgc_package *package = fi->package;

    for(int i = 0; i < fi->nr_cs_workers; i++){
        if(package->header.status[i] != CSGC_SUCCESS){
            package->header.err_info = fi->wi[i].err_info;
            break;
        }
    }
}


// set partition info for each cs worker
static void allocate_cs_jobs(struct f2fs_info *fi)
{
    struct gc_info *gi = fi->gi;
    struct f2fs_sit_entry *sentry = gi->sentry ? gi->sentry : gi->old_sei->sentry;
    struct data_block_entry *data_ent;
    int max_nr_cpus = fi->package->header.max_nr_cpus;
    int vblocks_per_worker, vblock_off, vblocks_left;
    struct pre_alloc_info *pi = F2FS_PI(fi);
    struct pseg_info *pseg = pi->psegs;
    int cur_pseg_blk_off = pseg->start_blkoff;
    int i;
    
    fi->nr_cs_workers = max_nr_cpus > MAX_NR_CS_GC_WORKERS ? MAX_NR_CS_GC_WORKERS : max_nr_cpus;

    vblock_off = 0;
    for(i = 0; i < fi->blocks_per_seg; i++){
        if(!bm_test_bit(i, (char *)sentry->valid_map))
            continue;
        
        data_ent = &gi->vblock_list[vblock_off];
        data_ent->offs_in_seg = i;
        vblock_off++;
    }
    ASSERT(vblock_off==gi->vblocks);
    
    vblock_off = 0;
    vblocks_left =  gi->vblocks;
    vblocks_per_worker = vblocks_left / fi->nr_cs_workers; 
    for(i = 0; i < fi->nr_cs_workers; i++){
        struct worker_info *wi = &fi->wi[i];
        struct partition_info *partition = &wi->partition;

        wi->id = i;
        partition->start_vblk_off = vblock_off;
        partition->nr_vblocks = i == fi->nr_cs_workers - 1 ? vblocks_left : vblocks_per_worker;
        partition->end_vblk_off = partition->start_vblk_off + partition->nr_vblocks;

        partition->nr_pseg = 1;
        partition->pseg_off = pseg - pi->psegs;
        partition->pseg_blk_off[0] = cur_pseg_blk_off;
        // continuous blocks in one pseg
        if(cur_pseg_blk_off + partition->nr_vblocks < pseg->end_blkoff){
            partition->pseg_blk_len[0] = partition->nr_vblocks;
            partition->pseg_blk_len[1] = 0;
            cur_pseg_blk_off += partition->nr_vblocks;
        } else { // non-continuous blocks in two psegs
            partition->pseg_blk_len[0] = pseg->end_blkoff - cur_pseg_blk_off;
            pseg++;
            partition->pseg_blk_off[1] = pseg->start_blkoff;
            partition->pseg_blk_len[1] = partition->nr_vblocks - partition->pseg_blk_len[0];
            cur_pseg_blk_off = pseg->start_blkoff + partition->pseg_blk_len[1];
            ASSERT(cur_pseg_blk_off <= pseg->end_blkoff);
        }
        wi->cur_pseg_off = 0;
        wi->cur_pseg_next_blk_off = partition->pseg_blk_off[0];
        wi->cur_vblk_off = partition->start_vblk_off;
        CS_INFO(fi, "Worker %d: pseg_off = %u start_vblk_off = %u, end_vblk_off = %u, nr_vblocks = %u"
                    " off[0] = %u, len[0] = %u, off[1] = %u, len[1] = %u", 
                    i, partition->pseg_off, partition->start_vblk_off, partition->end_vblk_off, partition->nr_vblocks, 
                    partition->pseg_blk_off[0], partition->pseg_blk_len[0], 
                    partition->pseg_blk_off[1], partition->pseg_blk_len[1]);

        vblock_off = partition->end_vblk_off;
        vblocks_left -= partition->nr_vblocks;
    }
}

static void setup_cs_workers(int nr_cs_workers)
{
    for(int i = 1; i < nr_cs_workers; i++)
        ASSERT(GET_CS_WORKER_STATUS(m, i) == CS_WORKER_IDLE);
    MEMORY_BARRIER();
    for(int i = 1; i < nr_cs_workers; i++)
        SET_CS_WORKER_STATUS(m, i, CS_WORKER_RUNNING);
}

static void wait_cs_workers_completion(int nr_cs_workers)
{
    for(int i = 1; i < nr_cs_workers; i++){
        while( GET_CS_WORKER_STATUS(m, i) != CS_WORKER_DONE) ;
        SET_CS_WORKER_STATUS(m, i, CS_WORKER_IDLE);
    }
}

static bool check_completion_status(struct f2fs_info *fi, int nr_cs_workers)
{
    for(int i = 1; i < nr_cs_workers; i++){
        if(fi->package->header.status[i] != CSGC_SUCCESS)
            return false;
    }
    return true;
}

// scan SIT of victim, get valid blocks
// get ssa block for the victim segment
// for each valid block, do:
//      get ssa entry of the block, get its dnode and inode
//      (summary->nid => dnode, nat->nid => inode)
//      move the valid block to new segment
//      modify the block's dnode: OLD_ADDR => NEW_ADDR
//          the updated dnode entries are stored in memory and will be sent back 
//          to host for update, they will not be flushed to storage
//      modify the new block's meta data
//          set SIT to valid
//          set summary entry
// modify sit of victim, all invalid
static int do_garbage_collect_leader(struct f2fs_info *fi, unsigned int segno)
{
    struct gc_info *gi = fi->gi;
    int blks_moved = 0;
    int count = 0, err = 0;

    err = gc_info_init(fi, segno);
    update_time_stat(fi, "build fi gi end");
    if(err){
        set_package_err(fi->package, err);
        goto ret;
    }

    if(gi->vblocks == 0){ // no need to do migration
        count++;
        goto ret;
    }

    allocate_cs_jobs(fi);

    err = read_shared_dnodes(gi);
    if(err){
        set_package_err(fi->package, err);
        goto ret;
    }
    update_time_stat(fi, "alloc jobs and read dnodes");

    // wait until `curpseg_sum_blk` and `gi->sum_blk` are ready
    f2fs_csio_wait_completion(fi, CSIO_READ, CSIO_F2FS_META);
    if(gi->sum_blk->footer.entry_type != SUM_TYPE_DATA){
        // struct f2fs_summary_block *debug_buf = linear_zalloc(&m->shared_allocator, 4096, 0);
        // f2fs_read_block(fi, gi->sum_addr, debug_buf, CSIO_F2FS_META, 1, 1, NULL);
        __dump_summary(fi, gi->sum_blk);
        xil_printf("error at %s:%d\n", __FILE__, __LINE__);
        set_package_err(fi->package, -CSGC_ERR);
        goto pack_multicore_err;
    }

    // ============================signal cs workers to start============================
    setup_cs_workers(fi->nr_cs_workers);
    update_time_stat(fi, "wait meta, setup workers");
    
    err = read_vblocks(gi);
    update_time_stat(fi, "read_vblocks_end");
    if(err){
        set_package_err(fi->package, err);
        goto multicore_err;
    }

    // wait until dnodes are ready
    f2fs_csio_wait_completion(fi, CSIO_READ, CSIO_F2FS_NODE);
    FLUSH_CACHE(gi->shared_dnode_buffer, gi->nr_node_info * 4096);
    MEMORY_BARRIER();
#ifdef CS_DEBUG_NODE_READ
    gi->debug_node_buffer = linear_zalloc(&m->shared_allocator, 4096, 0);
    memcpy(gi->debug_node_buffer, gi->shared_dnode_list[0].no, 4096);
    MEMORY_BARRIER();
#endif
    fi->gi->dnode_ready = true;
    // ------------------------------- dnode barrier -----------------------------------
    update_time_stat(fi, "wait dnode barrier");
    
    err = move_vblocks(gi, &blks_moved);
    update_time_stat(fi, "move_vblocks_end");
    if(err < 0){
        CS_ERROR(fi, "Fail to move valid blocks, error code = %d", err);
        set_package_err(fi->package, err);
        goto multicore_err;
    }
    if(blks_moved == WORKER_I(fi)->partition.nr_vblocks){
        CS_INFO(fi, "Worker%d moved %u valid blocks in segment%u, total vblocks# %u", 
                CS_WORKER_ID, blks_moved, gi->segno, gi->vblocks);
    } else {
        xil_printf("Worker%d error at %s:%d\n", CS_WORKER_ID, __FILE__, __LINE__);
        CS_ERROR(fi, "Moved %u valid blocks, should move %u blocks", 
                blks_moved, WORKER_I(fi)->partition.nr_vblocks);
        set_package_err(fi->package, -CSGC_ERR);
        goto multicore_err;
    }
    wait_cs_workers_completion(fi->nr_cs_workers);
    update_time_stat(fi, "wait worker completion");
    // ============================wait cs workers completion============================
    
    if(!check_completion_status(fi, fi->nr_cs_workers)){
        // leader core succeeds, workers fail
        set_package_err(fi->package, CSGC_SUCCESS);
        goto pack_multicore_err;
    }
    count++;

    // in current implementation, `flush_gc_meta` actually flushes nothing
    // dirty metadata are all sent to host and updated in memory  
    flush_gc_meta(gi);
    update_time_stat(fi, "flush_meta_end");
    
    pack_dirty_meta(gi);
    update_time_stat(fi, "pack_meta_end");

#ifdef CONFIG_MIGRATE_BYPASS_MEMORY
    f2fs_csio_wait_all_worker_completion(fi, CSIO_MIGRATE, CSIO_F2FS_DATA);
#else
    f2fs_csio_wait_all_worker_completion(fi, CSIO_WRITE, CSIO_F2FS_DATA);
#endif
    f2fs_csio_wait_completion(fi, CSIO_WRITE, CSIO_F2FS_NODE);
    f2fs_csio_wait_completion(fi, CSIO_WRITE, CSIO_F2FS_META);
    CS_INFO(fi, "Moved %u valid blocks in segment %u", gi->vblocks, gi->segno);

ret:
    gc_info_free(gi);
    return count;

multicore_err:
    // ASSERT(0);
    wait_cs_workers_completion(fi->nr_cs_workers);
pack_multicore_err:
    pack_err_info(fi->gi);
    return count;
}

static int do_garbage_collect_worker(struct f2fs_info *fi)
{
    struct gc_info *gi = fi->gi;
    int blks_moved = 0;
    int count = 0, err = 0;

    err = read_vblocks(gi);
    update_time_stat(fi, "read_vblocks_end");
    if(err){
        goto ret;
    }

    // ------------------------------- dnode barrier -----------------------------------
    while(!gi->dnode_ready) ;
    update_time_stat(fi, "wait dnode barrier");
    
    err = move_vblocks(gi, &blks_moved);
    update_time_stat(fi, "move_vblocks_end");
    if(err < 0){
        CS_ERROR(fi, "Fail to move valid blocks, error code = %d", err);
        goto ret;
    }
    if(blks_moved == WORKER_I(fi)->partition.nr_vblocks){
        CS_INFO(fi, "Worker%d moved %u valid blocks in segment%u, total vblocks# %u", 
                CS_WORKER_ID, blks_moved, gi->segno, gi->vblocks);
    } else {
        xil_printf("Worker%d error at %s:%d\n", CS_WORKER_ID, __FILE__, __LINE__);
        CS_ERROR(fi, "Moved %u valid blocks, should move %u blocks", 
                blks_moved, WORKER_I(fi)->partition.nr_vblocks);
        goto ret;
    }

ret:
    set_package_err(fi->package, err);
    return err;
}

int f2fs_csgc_leader(struct f2fs_info *fi, void *arg_buf)
{
    unsigned int segno;
    int err;
    if(!m->fs_ready){
        CS_INFO(fi, "FS not ready");
        return 0;
    }

    err = f2fs_build_info(fi, arg_buf);
    if(err)
        return 0;

    segno = fi->package->header.segno;

    // CS_INFO(".................Dump CS meta data before migration.................");
    // f2fs_dump_blk(fi, F2FS_DUMP_CKPT, 2, 0);
    // f2fs_dump_blk(fi, F2FS_DUMP_JRNL_SUM, segno2blkaddr(fi, segno), 512);
    // f2fs_dump_blk(fi, F2FS_DUMP_SIT, segno2blkaddr(fi, segno), 0);
    // CS_INFO("....................................................................");

    do_garbage_collect_leader(fi, segno);
    
    return 0;
}

int f2fs_csgc_worker()
{
    struct f2fs_info * volatile fi;
    
    while(1){

        if(GET_CS_WORKER_STATUS(m, CS_WORKER_ID) != CS_WORKER_RUNNING)
            continue;
        
        fi = m->fi;
        init_time_stat(fi);
        // reset local allocator
        linear_malloc_reset(&allocator);

        f2fs_csio_init(fi, CSIO_F2FS_DATA);
        update_time_stat(fi, "init csio");

        do_garbage_collect_worker(fi);
        
        f2fs_csio_free(fi, CSIO_F2FS_DATA);
        update_time_stat(fi, "end");

        show_time_stat(fi);

        MEMORY_BARRIER();
        SET_CS_WORKER_STATUS(m, CS_WORKER_ID, CS_WORKER_DONE);

        // wait leader to set status as idle
        while (GET_CS_WORKER_STATUS(m, CS_WORKER_ID) != CS_WORKER_IDLE) ;
    }

    return 0;
}