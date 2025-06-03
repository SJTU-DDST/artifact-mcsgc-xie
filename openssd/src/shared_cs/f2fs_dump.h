#ifndef _F2FS_DUMP_H
#define _F2FS_DUMP_H

#include "f2fs_cs.h"

enum{
    F2FS_DUMP_INO=0,
    F2FS_DUMP_NAT,
    F2FS_DUMP_NODE,
    F2FS_DUMP_RAW,
    F2FS_DUMP_CKPT,
    F2FS_DUMP_SB,
    F2FS_DUMP_JRNL_SUM,
    F2FS_DUMP_SIT,
    NR_DUMP_TYPE,
};

void __dump_inode_blk(struct f2fs_info *fi, struct f2fs_node *node, bool addrs_only);
void __dump_nat_blk(struct f2fs_info *fi, struct f2fs_nat_block *nat, int off_dump, int nr_dump, int use_hex);
void __dump_node_blk(struct f2fs_info *fi, struct f2fs_node *node, int nr_dump, int use_hex);
void __dump_blk_raw(struct f2fs_info *fi, char *block);
void __dump_ckpt(struct f2fs_info *fi, struct f2fs_checkpoint *ckpt, bool version_only);
void __dump_sb(struct f2fs_info *fi, struct f2fs_sb *sb);
void __dump_journal(struct f2fs_info *fi, struct f2fs_journal *journal, int type);
void __dump_sum_entry(struct f2fs_info *fi, struct f2fs_summary_block *sum, int index, bool compact);
void __dump_summary(struct f2fs_info *fi, struct f2fs_summary_block *sum);
void __dump_summary_range(struct f2fs_info *fi, struct f2fs_summary_block *sum, int start, int end);
void __dump_sit_entry(struct f2fs_info *fi, struct f2fs_sit_entry *se);
void __dump_sentry_info(struct f2fs_info *fi, struct sit_entry_info *sei);


#endif