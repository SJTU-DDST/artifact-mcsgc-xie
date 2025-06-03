#include "f2fs_dump.h"

void __dump_inode_blk(struct f2fs_info *fi, struct f2fs_node *node, bool addrs_only)
{
    int dp_count = 0;
    if(addrs_only){
        int addrs = addrs_per_inode(fi, &node->i);
        CS_INFO(fi, "<INODE DUMP> NID:%u, INODE_NR:%u, FLAG:%#x, CP_VER:%#lx, NXT_BLK:%u ", 
            node->footer.nid, node->footer.ino, node->footer.flag, 
            node->footer.cp_ver, node->footer.next_blkaddr);
        CS_INFO(fi, "<INODE DUMP> direct pointer info");
        if(addrs < 0 || addrs >= DEF_ADDRS_PER_INODE){
            CS_INFO(fi, "Invalid naddrs in inode %d", addrs);
            addrs = DEF_ADDRS_PER_INODE;
        }
        for(int i = 0; i < addrs; i++){
            CS_INFO(fi, "      [%d]: %u", i, data_blkaddr(node, i));
        }
        return;
    }
    for(int j = 0; j < DEF_ADDRS_PER_INODE; j++)
        if(node->i.i_addr[j]!=NULL_ADDR)
            dp_count++;
    CS_INFO(fi, "<INODE DUMP> inode_number: %u, node_id: %u", node->footer.ino, node->footer.nid);
    CS_INFO(fi, "<INODE DUMP> next node page block address: %u", node->footer.next_blkaddr);
    CS_INFO(fi, "<INODE DUMP> file mode: %hhu%hhu%hhu, file inline flags: %hhx",
                 ((node->i.i_mode)>>6)&0x7, ((node->i.i_mode)>>3)&0x7, (node->i.i_mode)&0x7,
                  node->i.i_inline);
    CS_INFO(fi, "<INODE DUMP> file size: %lu bytes , %lu blocks", node->i.i_size, node->i.i_blocks);
    CS_INFO(fi, "<INODE DUMP> atime %lu, atime_nsec %u", node->i.i_atime, node->i.i_atime_nsec);
    CS_INFO(fi, "<INODE DUMP> ctime %lu, ctime_nsec %u", node->i.i_ctime, node->i.i_ctime_nsec);
    CS_INFO(fi, "<INODE DUMP> mtime %lu, mtime_nsec %u", node->i.i_mtime, node->i.i_mtime_nsec);
    CS_INFO(fi, "<INODE DUMP> direct pointers count: %d", dp_count);
    for(int j = 0; j < 20; j++){
        CS_INFO(fi, "<INODE DUMP> direct pointers %d: %u", j, node->i.i_addr[j]);
    }
    CS_INFO(fi, "<INODE DUMP> Single indirect nodeids: 0x%04x, 0x%04x ", node->i.i_nid[0], node->i.i_nid[1]);
    CS_INFO(fi, "<INODE DUMP> Double indirect nodeids: 0x%04x, 0x%04x ", node->i.i_nid[2], node->i.i_nid[3]);
    CS_INFO(fi, "<INODE DUMP> Triple indirect nodeid : 0x%04x ", node->i.i_nid[4]);
}

void __dump_nat_blk(struct f2fs_info *fi, struct f2fs_nat_block *nat, int off_dump, int nr_dump, int use_hex)
{
    int dump_start, dump_end;
    dump_start = off_dump - 5 >= 0 ? off_dump - 5 : 0;
    dump_end = dump_start + nr_dump;
    if(dump_end > NAT_ENTRY_PER_BLOCK)
        dump_end = NAT_ENTRY_PER_BLOCK;
    for(int i = dump_start; i < dump_end; i++){
        if(use_hex){
            CS_INFO(fi, "<NAT DUMP> Entry %d: ver=%hhx, inode_nr=%04x, block_addr=%04x (HEX)",
                 i, nat->entries[i].version, nat->entries[i].ino, nat->entries[i].block_addr);
        }else{
            CS_INFO(fi, "<NAT DUMP> Entry %d: ver=%hhu, inode_nr=%u, block_addr=%u",
                 i, nat->entries[i].version, nat->entries[i].ino, nat->entries[i].block_addr);
        }
    }
}

void __dump_node_blk(struct f2fs_info *fi, struct f2fs_node *node, int nr_dump, int use_hex)
{
    if(nr_dump < 0 || nr_dump > DEF_ADDRS_PER_BLOCK)
        nr_dump = DEF_ADDRS_PER_BLOCK;
    CS_INFO(fi, "<NODE DUMP> NID:%u, INODE_NR:%u, FLAG:%#x, CP_VER:%#lx, NXT_BLK:%u ", 
            node->footer.nid, node->footer.ino, node->footer.flag, 
            node->footer.cp_ver, node->footer.next_blkaddr);
    for(int i = 0; i < nr_dump; i++){
        if(use_hex){
            CS_INFO(fi, "<NODE DUMP> Entry %d: nid/block_addr=0x%04x",
                 i, data_blkaddr(node,i));
        }else{
            CS_INFO(fi, "<NODE DUMP> Entry %d: nid/block_addr=%u",
                 i, data_blkaddr(node,i));
        }
    }
}

void __dump_sb(struct f2fs_info *fi, struct f2fs_sb *sb)
{
    if(!sb)
        return;
    CS_INFO(fi, "superblock size: %lu", sizeof(struct f2fs_sb)); 
    CS_INFO(fi, "log2 block size in bytes: %u", sb->log_blocksize);
    CS_INFO(fi, "log2 # of blocks per segment: %u", sb->log_blocks_per_seg);
    CS_INFO(fi, "# of segments per section: %u", sb->segs_per_sec);
    CS_INFO(fi, "# of sections per zone: %u", sb->secs_per_zone);

    CS_INFO(fi, "total # of user blocks: %lu", sb->block_count);
    CS_INFO(fi, "total # of user segments: %u", sb->segment_count);
    CS_INFO(fi, "# of segments for checkpoint: %u", sb->segment_count_ckpt);
    CS_INFO(fi, "# of blocks for checkpoint payload: %u", sb->cp_payload);
    CS_INFO(fi, "# of segments for SIT: %u", sb->segment_count_sit);
    CS_INFO(fi, "# of segments for NAT: %u", sb->segment_count_nat);
    CS_INFO(fi, "# of segments for SSA: %u", sb->segment_count_ssa);
    CS_INFO(fi, "# of segments for main area: %u", sb->segment_count_main);
    
    CS_INFO(fi, "start block address of segment 0: %u", sb->segment0_blkaddr);
    CS_INFO(fi, "start block address of checkpoint: %u", sb->cp_blkaddr);
    CS_INFO(fi, "start block address of SIT: %u", sb->sit_blkaddr);
    CS_INFO(fi, "start block address of NAT: %u", sb->nat_blkaddr);
    CS_INFO(fi, "start block address of SAT: %u", sb->ssa_blkaddr);
    CS_INFO(fi, "start block address of main area: %u", sb->main_blkaddr);
    
    CS_INFO(fi, "defined features: %08x", sb->feature);
}

void __dump_ckpt(struct f2fs_info *fi, struct f2fs_checkpoint *ckpt, bool version_only)
{
    if(!ckpt)
        return;
    
    CS_INFO(fi, "<CKPT DUMP> checkpoint version: %#lx", ckpt->checkpoint_ver);
    if(version_only)
        return;
    CS_INFO(fi, "<CKPT DUMP> # of user blocks: %lu", ckpt->user_block_count);
    CS_INFO(fi, "<CKPT DUMP> # of valid blocks in main area: %lu", ckpt->valid_block_count);
    CS_INFO(fi, "<CKPT DUMP> # of reserved segments for gc: %u", ckpt->rsvd_segment_count);
    CS_INFO(fi, "<CKPT DUMP> # of overprovision segments: %u", ckpt->overprov_segment_count);
    CS_INFO(fi, "<CKPT DUMP> # of free segments in main area: %u", ckpt->free_segment_count);

    CS_INFO(fi, "<CKPT DUMP> current node segment numbers: %#x, %#x, %#x, %#x, %#x, %#x, %#x, %#x ",
                 ckpt->cur_node_segno[0], ckpt->cur_node_segno[1], ckpt->cur_node_segno[2], ckpt->cur_node_segno[3],
                 ckpt->cur_node_segno[4], ckpt->cur_node_segno[5], ckpt->cur_node_segno[6], ckpt->cur_node_segno[6]
                 );
    CS_INFO(fi, "<CKPT DUMP> current node segment blkoffs: %#hx, %#hx, %#hx, %#hx, %#hx, %#hx, %#hx, %#hx ",
                 ckpt->cur_node_blkoff[0], ckpt->cur_node_blkoff[1], ckpt->cur_node_blkoff[2], ckpt->cur_node_blkoff[3],
                 ckpt->cur_node_blkoff[4], ckpt->cur_node_blkoff[5], ckpt->cur_node_blkoff[6], ckpt->cur_node_blkoff[6]
                 );
    CS_INFO(fi, "<CKPT DUMP> current data segment numbers: %#x, %#x, %#x, %#x, %#x, %#x, %#x, %#x ",
                 ckpt->cur_data_segno[0], ckpt->cur_data_segno[1], ckpt->cur_data_segno[2], ckpt->cur_data_segno[3],
                 ckpt->cur_data_segno[4], ckpt->cur_data_segno[5], ckpt->cur_data_segno[6], ckpt->cur_data_segno[6]
                 );
    CS_INFO(fi, "<CKPT DUMP> current data segment blkoffs: %#hx, %#hx, %#hx, %#hx, %#hx, %#hx, %#hx, %#hx ",
                 ckpt->cur_data_blkoff[0], ckpt->cur_data_blkoff[1], ckpt->cur_data_blkoff[2], ckpt->cur_data_blkoff[3],
                 ckpt->cur_data_blkoff[4], ckpt->cur_data_blkoff[5], ckpt->cur_data_blkoff[6], ckpt->cur_data_blkoff[6]
                 );
    
    CS_INFO(fi, "<CKPT DUMP> checkpoint flags: %#08x", ckpt->ckpt_flags);
    if(ckpt->ckpt_flags & CP_COMPACT_SUM_FLAG)
        CS_INFO(fi, "<CKPT DUMP> CP_COMPACT_SUM_FLAG is set");
    if(ckpt->ckpt_flags & CP_LARGE_NAT_BITMAP_FLAG)
        CS_INFO(fi, "<CKPT DUMP> CP_LARGE_NAT_BITMAP_FLAG is set");

    CS_INFO(fi, "<CKPT DUMP> total # of one cp pack: %u", ckpt->cp_pack_total_block_count);
    CS_INFO(fi, "<CKPT DUMP> start block number of data summary: %u", ckpt->cp_pack_start_sum);
    CS_INFO(fi, "<CKPT DUMP> Total number of valid nodes: %u", ckpt->valid_node_count);
    CS_INFO(fi, "<CKPT DUMP> Total number of valid inodes: %u", ckpt->valid_inode_count);
    CS_INFO(fi, "<CKPT DUMP> Total number of valid inodes: %u", ckpt->valid_inode_count);
    CS_INFO(fi, "<CKPT DUMP> Next free node number: %u", ckpt->next_free_nid);
    CS_INFO(fi, "<CKPT DUMP> sit version bitmap bytesize: %u", ckpt->sit_ver_bitmap_bytesize);
    CS_INFO(fi, "<CKPT DUMP> nat version bitmap bytesize: %u", ckpt->nat_ver_bitmap_bytesize);
}

void __dump_sit_entry(struct f2fs_info *fi, struct f2fs_sit_entry *se)
{
    unsigned int mask = (1<<10) - 1;

    if(!se)
        return;
    CS_INFO(fi, "<SIT DUMP>\t seg type=%u, valid_blocks=%u, mtime=%lu",
                se->vblocks >> 10,
                se->vblocks & mask, 
                se->mtime);
    for(int j = 0; j < SIT_VBLOCK_MAP_SIZE; j+=16)
    {   
        CS_INFO(fi, "<SIT DUMP>     %02hhx %02hhx %02hhx %02hhx "
                    "%02hhx %02hhx %02hhx %02hhx "
                    "%02hhx %02hhx %02hhx %02hhx "
                    "%02hhx %02hhx %02hhx %02hhx\n",
                    se->valid_map[j], se->valid_map[j+1], se->valid_map[j+2], se->valid_map[j+3],
                    se->valid_map[j+4], se->valid_map[j+5], se->valid_map[j+6], se->valid_map[j+7],
                    se->valid_map[j+8], se->valid_map[j+9], se->valid_map[j+10], se->valid_map[j+11],
                    se->valid_map[j+12], se->valid_map[j+13], se->valid_map[j+14], se->valid_map[j+15]
                    );
    }
}

void __dump_sentry_info(struct f2fs_info *fi, struct sit_entry_info *sei)
{
    if(!sei) 
        return;
    CS_INFO(fi, "sit_addr=%8u\t se_off=%8u\t se_from_jrnl=%d", 
                sei->sit_addr, sei->se_off, sei->se_from_jrnl);
    __dump_sit_entry(fi, sei->sentry);
}

void __dump_journal(struct f2fs_info *fi, struct f2fs_journal *journal, int type)
{
    unsigned short n, i;
    unsigned int id;
    struct nat_journal *nat_j;
    struct sit_journal *sit_j;
	struct f2fs_nat_entry *ne ;
	struct f2fs_sit_entry *se;

    if(type==NAT_JOURNAL){
        n = journal->n_nats;
        nat_j = &journal->nat_j;
        CS_INFO(fi, "<JRNL DUMP> Dumping nat journal(%hu entries)", n);
        for(i = 0; i < n; i++){
            id = nat_j->entries[i].nid;
            ne = &nat_j->entries[i].ne;
            CS_INFO(fi, "<JRNL DUMP> nid=%u, blk_arr=%u, ino=%u, ver=%u",
                        id, ne->block_addr, ne->ino, ne->version);
        }
    }
    if(type==SIT_JOURNAL){
        n = journal->n_sits;
        sit_j = &journal->sit_j;
        CS_INFO(fi, "<JRNL DUMP> Dumping sit journal(%hu entries)", n);
        for(i = 0; i < n; i++){
            id = sit_j->entries[i].segno;
            se = &sit_j->entries[i].se;
            CS_INFO(fi, "<JRNL DUMP> Dumping sit entry %d, segno = %u", i, id);
            __dump_sit_entry(fi, se);  
        }
    }

}

void __dump_sum_entry(struct f2fs_info *fi, struct f2fs_summary_block *sum, int index, bool compact)
{
    if(sum->footer.entry_type==SUM_TYPE_NODE){
        if(compact)
            CS_INFO(fi, "[%3d: nid=%8u,v=%3u]%c", 
                    index, sum->entries[index].nid, sum->entries[index].version, ' ');
        else
            CS_INFO(fi, "<SUM DUMP> entry %d(node): node id = %u, version = %hhx",
                    index, sum->entries[index].nid, sum->entries[index].version);
    }else if(sum->footer.entry_type==SUM_TYPE_DATA){
        if(compact)
            CS_INFO(fi, "[%3d: nid=%8u,off=%4u,v=%3u]%c",
                    index, sum->entries[index].nid, sum->entries[index].ofs_in_node,
                    sum->entries[index].version, ' ');
        else
            CS_INFO(fi, "<SUM DUMP> entry %d(data): parent dnode id = %u, ofs_in_node = %hu, version = %hhx",
                    index, sum->entries[index].nid, sum->entries[index].ofs_in_node,
                    sum->entries[index].version);
    }
}

void __dump_summary(struct f2fs_info *fi, struct f2fs_summary_block *sum)
{
    CS_INFO(fi, "summary footer, type: %hhu, chksum: %u", 
            sum->footer.entry_type, sum->footer.check_sum);
    for(int i = 0; i < ENTRIES_IN_SUM; i++){
        __dump_sum_entry(fi, sum, i, 1);
    }
}

void __dump_summary_range(struct f2fs_info *fi, struct f2fs_summary_block *sum, int start, int end)
{
    if(start < 0) start = 0;
    if(end > ENTRIES_IN_SUM) end = ENTRIES_IN_SUM;
    for(int i = start; i < end; i++){
        __dump_sum_entry(fi, sum, i, 1);
    }
}

void __dump_blk_raw(struct f2fs_info *fi, char *block)
{
    int nr_bytes_per_line = 32, offset = 0;
    uint64_t block_sum = 0;
    for(int i = 0; i < F2FS_BLKSIZE; i++){
        block_sum += ((uint8_t *) block)[i];
    }
    for(int i = 0; i < F2FS_BLKSIZE/nr_bytes_per_line;i++){
        CS_INFO(fi, "<RAW DUMP> 0x%08x: %02hhx %02hhx %02hhx %02hhx | %02hhx %02hhx %02hhx %02hhx |"
                                      "%02hhx %02hhx %02hhx %02hhx | %02hhx %02hhx %02hhx %02hhx |"
                                      "%02hhx %02hhx %02hhx %02hhx | %02hhx %02hhx %02hhx %02hhx |"
                                      "%02hhx %02hhx %02hhx %02hhx | %02hhx %02hhx %02hhx %02hhx |",
                    offset, block[offset+0], block[offset+1], block[offset+2], block[offset+3],
                    block[offset+4], block[offset+5], block[offset+6], block[offset+7],
                    block[offset+8], block[offset+9], block[offset+10], block[offset+11],
                    block[offset+12], block[offset+13], block[offset+14], block[offset+15],
                    block[offset+16], block[offset+17], block[offset+18], block[offset+19],
                    block[offset+20], block[offset+21], block[offset+22], block[offset+23],
                    block[offset+24], block[offset+25], block[offset+26], block[offset+27],
                    block[offset+28], block[offset+29], block[offset+30], block[offset+31]);
        offset+=nr_bytes_per_line;
    }
    CS_INFO(fi, "<RAW DUMP> block uint8 sum: %lu", block_sum);
}