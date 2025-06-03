#ifndef __F2FS_CS_H
#define __F2FS_CS_H

#include "f2fs_meta.h"
#include "list.h"
#include "utils.h"
#include "stdbool.h"
#include "xil_printf.h"
#include "shared_mem.h"
#include "stdio.h"
#include "debug.h"
#include "cs_worker.h"

#ifndef CS_WORKER_ID
#define CS_WORKER_ID 0
#endif

#define CONFIG_MIGRATE_BYPASS_MEMORY
#define CONFIG_LINEAR_MALLOC

#ifdef CONFIG_LINEAR_MALLOC
#define kfree(ptr)
#define kvfree(ptr)
#endif

#define NULL_SEGNO			((uint32_t)(~0))

/*
 * indicate a block allocation direction: RIGHT and LEFT.
 * RIGHT means allocating new sections towards the end of volume.
 * LEFT means the opposite direction.
 */
enum {
	ALLOC_RIGHT = 0,
	ALLOC_LEFT
};

/*
 * In the victim_sel_policy->alloc_mode, there are three block allocation modes.
 * LFS writes data sequentially with cleaning operations.
 * SSR (Slack Space Recycle) reuses obsolete space without cleaning operations.
 * AT_SSR (Age Threshold based Slack Space Recycle) merges fragments into
 * fragmented segment which has similar aging degree.
 */
enum {
	LFS = 0,
	SSR,
	AT_SSR,
};

struct f2fs_info;

struct f2fs_csio{
	struct csio_vec *io_vc;
	struct f2fs_info *fi;
	
    block_t start_blkaddr;		// start logical block address
	unsigned int length; // # of consecutive blocks to read/write

    unsigned int vc_cnt;
    unsigned int vc_cnt_max;
    unsigned int io_size;
	unsigned int io_size_max;
	enum csio_op op_type;
	enum csio_dtype dtype;

	unsigned int pend_io_cnt;

    unsigned long long nsecs_start;
    unsigned long long nsecs_end;
};

struct nat_block_entry {
	struct list_head list;
	union {
		struct f2fs_nat_block *nat_blk;
		struct f2fs_nat_entry *nat_ent;
	};
	block_t nat_blk_addr;
	// if not from journal:
	//		start_nid is the first nat_entry in the nat block;
	// if from journal:
	//		start_nid is the nid of the nat_entry in journal.
	nid_t start_nid; 
	// true: cache an nat_entry from journal; 
	// false: cache an nat_block from storage.
	bool from_journal;
};


// dirty extent in dnode
struct dirty_extent{
	unsigned int ofs_in_node;
	block_t new_addr;
	unsigned int len;
};

struct dirty_extent_list{
	unsigned int capacity;
	unsigned int size;
	struct dirty_extent *ext;
};

// used for node info sent from host
struct node_info {
	nid_t nid;		/* node id */
	nid_t ino;		/* inode number of the node's owner */
	block_t	blk_addr;	/* block address of the node */
	unsigned char version;	/* version of the node */
	unsigned char flag;	/* for node information bits */
};

struct node_entry {
	struct list_head list;
	struct f2fs_node *no; // dnode or inode
	// if node info is sent from host, `ni` will be inited, 
	// or `nat_ent` will be inited
	struct f2fs_nat_entry *nat_ent; // node info read from storage
	struct node_info *ni;	// node info sent from host
	struct dirty_extent_list *dirty_exts[MAX_NR_CS_WORKERS];
	nid_t nid;
};

#define LOCAL_EXT_LIST(node_ent) (node_ent->dirty_exts[CS_WORKER_ID])

struct dnp_entry{
	nid_t nid;
	nid_t ino_nid;
	unsigned int ofs_of_node;
	unsigned int nr_ext;
	struct dirty_extent exts[];
};

struct dirty_node_pack {
	unsigned int nr_dirty_node;
	char dnp_entries[];
};

struct data_block_entry {
	unsigned char *data_block;    // the data block to be migrated
	struct node_entry *ino; // inode of this data block
	struct node_entry *dno; // dnode that contains the address of the data block, can be inode
	struct f2fs_summary *sum; // summary entry of the data block, read only
	unsigned int offs_in_dno; 	// offset in dnode entries, check `offset_in_addr` and 
						 		// `__set_data_blkaddr` in f2fs source code to see how it is used
	unsigned int offs_in_seg; // offset in segment, set by worker 0 before `read_vblocks`;
	block_t old_addr;
	block_t new_addr;
};

struct sit_entry_info{
	struct list_head list;
	unsigned int segno;
	struct f2fs_sit_entry *sentry; // points to the sit entry in sit block or sit journal,
	block_t sit_addr; // read from and write back to
	struct f2fs_sit_block *sit_blk; // need to check summary of CURSEG_COLD_DATA
	unsigned int se_off;  /* sit entry offset in sit block*/
								   // modification to this will be written back to storage
	bool se_from_jrnl; // whether the sentry is from sit block or journal
};

struct dsp_entry{
	unsigned int segno;
	struct f2fs_sit_entry se;
};

// for transmission back to host
struct dirty_sit_pack{
	unsigned int nr_dirty_se;
	struct dsp_entry entry[];
};

struct gc_info {
	struct f2fs_info *fi;
	unsigned int segno; // segno of segment to be freed

	unsigned int vblocks;
	struct f2fs_sit_entry *sentry; 	// sit entry of the segment to be freed
									// ***not null only when sent from host***
	struct sit_entry_info *old_sei; // sei of segment to be freed
									// ***if SIT entry is sent from host, old_sei will not be inited***
	struct sit_entry_info *new_sei; // sei of target segment, to which valid blocks are moved
								    // this is initially set to curseg's sentry, when new curseg
								    // is allocated, it will be updated to the new sentry.
									// ***if has pre allocated segments, new_sei will not be inited***;
	block_t sum_addr;
	struct f2fs_summary_block *sum_blk;

	unsigned int nr_node_info;
	struct node_info *ni;
	
	// 2 nat lists are used only when nat entries are not sent from host
	struct list_head nat_journal_list; // list of cached nat entry from journal
	struct list_head nat_block_list; // list of cached nat block from storage
	struct list_head inode_list;
	struct list_head dnode_list;
	struct list_head se_list; // dirty sit entry list, only used when sit entry is not sent from host
	unsigned int nr_dirty_se;

	struct node_entry *shared_dnode_list; // used in multi-core gc.
	unsigned char *shared_dnode_buffer;
	volatile bool dnode_ready;
	struct f2fs_node *debug_node_buffer;
	
	struct data_block_entry *vblock_list;
	unsigned char *vblock_buffer;
};

#define	NR_CURSEG_DATA_TYPE	(3)
#define NR_CURSEG_NODE_TYPE	(3)
#define NR_CURSEG_INMEM_TYPE	(2)
#define NR_CURSEG_RO_TYPE	(2)
#define NR_CURSEG_PERSIST_TYPE	(NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE)
#define NR_CURSEG_TYPE		(NR_CURSEG_INMEM_TYPE + NR_CURSEG_PERSIST_TYPE)

enum {
	CURSEG_HOT_DATA	= 0,	/* directory entry blocks */
	CURSEG_WARM_DATA,	/* data blocks */
	CURSEG_COLD_DATA,	/* multimedia or GCed data blocks */
	CURSEG_HOT_NODE,	/* direct node blocks of directory files */
	CURSEG_WARM_NODE,	/* direct node blocks of normal files */
	CURSEG_COLD_NODE,	/* indirect node blocks */
	NR_PERSISTENT_LOG,	/* number of persistent log */
	CURSEG_COLD_DATA_PINNED = NR_PERSISTENT_LOG,
				/* pinned file that needs consecutive block address */
	CURSEG_ALL_DATA_ATGC,	/* SSR alloctor in hot/warm/cold data area */
	NO_CHECK_TYPE,		/* number of persistent & inmem log */
};

#define IS_DATASEG(t)	((t) <= CURSEG_COLD_DATA)
#define IS_NODESEG(t)	((t) >= CURSEG_HOT_NODE && (t) <= CURSEG_COLD_NODE)

// Should curseg_info be flushed to CP area if summary is modified?
// Or should we just send it back to host and update it in kernel?
// Seems that the second choice is correct.
// If old curseg space is used up and a new curseg is allocated, 
// we need to flush the old curseg's summary and sit and journal, 
// and the new curseg info is sent back to host and the host curseg
// needs to be updated.
struct curseg_info {
	struct f2fs_journal *journal;		/* cached journal info */
	struct f2fs_summary_block *sum_blk;	/* cached summary block */
	unsigned char alloc_type;		/* current allocation type */
	unsigned short seg_type;		/* segment type like CURSEG_XXX_TYPE */
	unsigned int segno;			/* current segment number */
	unsigned short next_blkoff;		/* next block offset to write */
	bool inited;
};

// for transmission back to host
struct curseg_pack {
	struct f2fs_summary_block sum_blk;
	// the following fields should be exactly the same as the fields 
	// after `sum_blk` in `struct curseg_info`
	unsigned char alloc_type;	
	unsigned short seg_type;	
	unsigned int segno;
	unsigned short next_blkoff;
	bool inited;
};

struct pseg_sum_info{
	unsigned int segno;
	unsigned int seg_type;
	unsigned short start_blkoff; // inclusive
	unsigned short end_blkoff; // exclusive
	unsigned short sum_len;
};

// update all summaries in host
struct pseg_pack {
	unsigned int nr_summaries;
	struct pseg_sum_info sum_info[2];
	struct f2fs_summary summaries[];
};

// pre allocated segment info sent from host, will be translated to `struct pseg_info` before use
struct pre_alloc_seg_info{
	bool is_curseg;
	unsigned int segno;
	unsigned int seg_type;
	unsigned short start_blkoff;	/* start offset of data blocks pre-allocated in the segment */ 
	unsigned short len;		/* number of data blocks pre-allocated in the segment */
};

// pre allocated segment info used by device
struct pseg_info{
	bool is_curseg;		/* whether this seg is curseg in host*/
	unsigned int segno;
	unsigned int seg_type;
	unsigned short start_blkoff;	/* start offset of data blocks pre-allocated in the segment */ 
	unsigned short end_blkoff;		/* end offset, exclusive */
	struct f2fs_summary_block *sum_blk;	/* summary block of the segment */
};

struct offset_range {
	unsigned int start_off;
	unsigned int end_off;
};

// used to transmit the updated summary to the next request
// only used when segs_per_sec > 1 and req_idx != 0
struct sum_relay_info{
	struct f2fs_summary_block sum_block;
	unsigned int prev_segno;
	unsigned nr_offset_range;
	struct offset_range offset_range[];
};

// simmilar to curseg_info, but for pre-allocated segments
struct pre_alloc_info {
	unsigned int nr_psegs;
	struct pseg_info *psegs; // array of pre-allocated segment info, 
							 // inited by data from host
};

#define CS_PRINT_BUF_SIZE 16384
#define CS_PRINT_BUF_OFFSET 16384

struct print_buf_info{
	char *buf;
	unsigned int offset;
	unsigned int size;
};

#define MAX_NR_TIME_STAT_BAR 12

struct time_stat{
	int nr_bar;
	unsigned int cur_phase;
	unsigned long long lock_time;
	const char *time_stat_bar_name[MAX_NR_TIME_STAT_BAR];
	unsigned long long bar_time[MAX_NR_TIME_STAT_BAR];
	unsigned long long io_bar_time[MAX_NR_TIME_STAT_BAR];
	unsigned long long phase_time_interval[MAX_NR_TIME_STAT_BAR];
	unsigned long long io_time_interval[MAX_NR_TIME_STAT_BAR];
	
#ifdef CS_DEBUG_PERF_DETAIL
	unsigned long long read_breakdown[5];
	unsigned long long write_breakdonw[5];
#endif
};

struct partition_info{
	unsigned int start_vblk_off;
	unsigned int end_vblk_off; //exclusive
	unsigned int nr_vblocks;

	unsigned int pseg_off;
	unsigned int nr_pseg;
	unsigned int pseg_blk_off[2];
	unsigned int pseg_blk_len[2];
};

struct csgc_error_info {
	uint32_t src_segno;
	uint32_t dst_segno;
	block_t src_blkaddr;
	block_t dst_blkaddr;
	nid_t dno;
	nid_t ino;
	uint32_t ofs_in_node;
};

// local info for each cs worker
struct worker_info{
	int id;
	struct partition_info partition;
	int cur_pseg_off; // 0 or 1
	int cur_pseg_next_blk_off;
	int cur_vblk_off;
	int vblock_last_blocked_offset;

	struct f2fs_csio *wio[NR_CSIO_DATA_TYPES];
	struct f2fs_csio *rio[NR_CSIO_DATA_TYPES];
	struct f2fs_csio *mio;	// migrate IO, dtype must be CSIO_DATA.
							// use hardware supported scatter/gatther 
							// ddr4 to ddr4 cdma transfer

#ifdef CS_DEBUG_PERF
	uint64_t io_byte_size[NR_CSIO_OP][NR_CSIO_DATA_TYPES];
	uint64_t io_time[NR_CSIO_OP][NR_CSIO_DATA_TYPES];
	struct time_stat ts;
#endif

	struct print_buf_info pbi;
	struct csgc_error_info err_info;
};

struct f2fs_info{
	struct f2fs_sb *sb;
	uint32_t blocks_per_seg;
	uint32_t sit_blocks;

	// needs update before read/write
	bool is_lame;	// if true, checkpoint may not be done to storage, 
					// and ckpt and curseg info is not available
	struct f2fs_checkpoint *cp;
	struct curseg_info *curseg_array;
	int cur_cp_pack;
	char *nat_bitmap;
	char *sit_bitmap;

	bool seginfo_inited; // if true: free_segmap are inited;
	unsigned int free_segments;
	unsigned int free_segmap_size;
	unsigned long *free_segmap;

	struct gc_info *gi;
	struct pre_alloc_info pi;
	struct csgc_package *package;

	unsigned int nr_cs_workers;
	struct worker_info wi[MAX_NR_CS_WORKERS];
	char *print_buffer;
};

#define WORKER_I(fi) (&(fi)->wi[CS_WORKER_ID])

// for status in csgc_header
enum {
	CSGC_SUCCESS,
	CSGC_NOMEM,			// package size too small to contain all info
	CSGC_INCONSISTENT,		// data block address inconsistency between dnode and 
							// blkaddr(calculated directly from segno) or other inconsistency
	CSGC_FAILREAD,			// fail to read valid block from storage	
	CSGC_WRONG_SIT,	// sit bitmap wrongly set or cleared
	CSGC_NO_FREE_SEG,	// no free segment to allocate
	CSGC_NO_FREE_PSEG,	// no pre-allocated segment to allocate
	CSGC_NO_NAT_INFO,	// no nat info to read
	CSGC_MULTICORE_ERR,	// error exists in other cs workers
	CSGC_ERR,			// other errors
};

struct offset_info {
	unsigned int nat_start;
	unsigned int sit_start_h2d;
	unsigned int prealloc_start;
	unsigned int data_size_h2d;	// host to device data size
	unsigned int sit_start;
	unsigned int dirty_sum_start;
	unsigned int dnode_start;
	unsigned int debug_start;
	unsigned int data_size_d2h;	// device to host data size
};

struct csgc_header {
	uint32_t capacity;
	uint32_t npages;
	void *pages;
	void *pages_recv;
	uint32_t segno;
	unsigned int head_segno; // segno of the head request
	int status[MAX_NR_CS_WORKERS];
	uint32_t prealloc_curseg_segno; // segno of curseg when pre-allocation of last data block is done
	uint32_t nr_pre_alloc;
	unsigned int nr_node_info;
	bool meta_sent_from_host;	// whether meta data(sit and nat) is sent from host
	unsigned int max_nr_cpus;

	uint32_t print_offset;
	uint32_t print_size;

	union{
		struct offset_info offs;
		struct csgc_error_info err_info;
	};
};

struct csgc_package {
	struct csgc_header header;
	// before request csgc: fill in pre-allocated segment info 
	// after recv csgc result: read packed meta data including changed sit, curseg, dnode info
	char data[];	
};

// bitmap codes taken from f2fs source codes
// should only be applied to bitmap stored in storage
// this is big endian bitmap
// 1 0 0 1 0 1 1 1
// ^			 ^
// 0th bit		 7th bit
static inline int bm_test_bit(unsigned int nr, char *addr)
{
	int mask;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	return mask & *addr;
}

static inline void bm_set_bit(unsigned int nr, char *addr)
{
	int mask;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	*addr |= mask;
}

static inline void bm_clear_bit(unsigned int nr, char *addr)
{
	int mask;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	*addr &= ~mask;
}

static inline int bm_test_and_set_bit(unsigned int nr, char *addr)
{
	int mask;
	int ret;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	ret = mask & *addr;
	*addr |= mask;
	return ret;
}

static inline int bm_test_and_clear_bit(unsigned int nr, char *addr)
{
	int mask;
	int ret;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	ret = mask & *addr;
	*addr &= ~mask;
	return ret;
}

static inline void bm_change_bit(unsigned int nr, char *addr)
{
	int mask;

	addr += (nr >> 3);
	mask = 1 << (7 - (nr & 0x07));
	*addr ^= mask;
}

// #define CS_INFO(string, args...) xil_printf("%s: " string "\n", "<DaisyCS>", ##args)
// #define CS_ERROR(string, args...) xil_printf("%s: " string "\n", "<DaisyCS>[ERROR]:", ##args)
#define LOCAL_PBI(fi) ((fi)->wi[CS_WORKER_ID].pbi)

#ifdef CS_DEBUG
#define CS_INFO(fi, string, args...) do{\
		int _tmp = snprintf(LOCAL_PBI(fi).buf + LOCAL_PBI(fi).offset, 	\
				LOCAL_PBI(fi).size - LOCAL_PBI(fi).offset - 1, 				\
				"%s[%d]: " string "\n", "<CS>", CS_WORKER_ID, ##args);		\
		if(_tmp > 0) LOCAL_PBI(fi).offset += _tmp;					\
	}while(0)
#else
#define CS_INFO(fi, string, args...)
#endif

#define CS_ERROR(fi, string, args...) do{\
		int _tmp = snprintf(LOCAL_PBI(fi).buf + LOCAL_PBI(fi).offset, 	\
				LOCAL_PBI(fi).size - LOCAL_PBI(fi).offset - 1, 				\
				"%s[worker_id=%d] (%s:line%d): " string "\n", "<CS>[ERROR]:", \
				CS_WORKER_ID, __FILE__, __LINE__, ##args);	\
		if(_tmp > 0) LOCAL_PBI(fi).offset += _tmp;					\
	}while(0)

#define TIME_DIFF_us(start_ns, now_ns) (now_ns - start_ns)/1000
#define MARK_EVENT(fi, event_name, start_time_ns)	\
		CS_INFO(fi, "| %6llu us|[%s:%d] %s", \
			TIME_DIFF_us(start_time_ns, get_time_ns()),\
			__func__, __LINE__, event_name)

#define GET_SUM_TYPE(footer) ((footer)->entry_type)
#define SET_SUM_TYPE(footer, type) ((footer)->entry_type = (type))

#define le16_to_cpu(x)		((unsigned short)(x))
#define GET_SIT_VBLOCKS(raw_sit)				\
	(le16_to_cpu((raw_sit)->vblocks) & SIT_VBLOCKS_MASK)
#define GET_SIT_TYPE(raw_sit)					\
	((le16_to_cpu((raw_sit)->vblocks) & ~SIT_VBLOCKS_MASK)	\
	 >> SIT_VBLOCKS_SHIFT)

// don't use these shit macros, use segno2blkaddr instead
#define SEG0_BLKADDR(sb) (sb)->segment0_blkaddr
#define GET_SEGOFF_FROM_SEG0(sb, blk_addr)	((blk_addr) - SEG0_BLKADDR(sb))
#define GET_SEGNO_FROM_SEG0(sb, blk_addr)				\
	(GET_SEGOFF_FROM_SEG0(sb, blk_addr) >> (sb)->log_blocks_per_seg)
#define GET_BLKOFF_FROM_SEG0(fi, blk_addr)				\
	(GET_SEGOFF_FROM_SEG0((fi)->sb, blk_addr) & ((fi)->blocks_per_seg - 1))
/* L: Logical segment # in volume, R: Relative segment # in main area */
#define GET_L2R_SEGNO(sb, segno)	((segno) - GET_SEGNO_FROM_SEG0(sb, (sb)->main_blkaddr))
#define GET_R2L_SEGNO(sb, segno)	((segno) + GET_SEGNO_FROM_SEG0(sb, (sb)->main_blkaddr))
#define START_BLOCK(sb, segno) (SEG0_BLKADDR(sb) +		\
	 (GET_R2L_SEGNO(sb, segno) << (sb)->log_blocks_per_seg))

#define GET_SEGNO(sb, blk_addr)					\
	((!__is_valid_data_blkaddr(blk_addr)) ?			\
	NULL_SEGNO : GET_L2R_SEGNO(sb,			\
		GET_SEGNO_FROM_SEG0(sb, blk_addr)))


#define NEXT_FREE_BLKADDR(sb, curseg)					\
	(START_BLOCK(sb, (curseg)->segno) + (curseg)->next_blkoff)

#define NEXT_FREE_BLKADDR_PSEG(sb, curpseg)					\
	(START_BLOCK(sb, (curpseg)->segno) + (curpseg)->next_blkoff)

static inline block_t get_pseg_next_blkaddr(struct worker_info *wi, 
		struct f2fs_sb *sb, struct pseg_info *curpseg)
{
	return sb->main_blkaddr + \
			(curpseg->segno << sb->log_blocks_per_seg) + \
			wi->cur_pseg_next_blk_off;
}

static inline unsigned long long total_io_time_ns(struct f2fs_info *fi, int rw){
	unsigned long long total = 0;
	for(int i = CSIO_READ; i < NR_CSIO_OP; i++){
		for(int j = CSIO_F2FS_DATA; j < NR_CSIO_DATA_TYPES; j++){
			if(rw==-1 || rw==i)
				total += fi->wi[CS_WORKER_ID].io_time[i][j];
		}
	}
	return total;
}

static inline unsigned long long total_io_size(struct f2fs_info *fi, int rw){
	unsigned long long total = 0;
	for(int i = CSIO_READ; i < NR_CSIO_OP; i++){
		for(int j = CSIO_F2FS_DATA; j < NR_CSIO_DATA_TYPES; j++){
			if(rw==-1 || rw==i)
				total += fi->wi[CS_WORKER_ID].io_byte_size[i][j];
		}
	}
	return total;
}

#define BANDWIDTH_MBPS(size_byte, time_ns) ((size_byte)*1000000000ULL/((time_ns)+1)/1024/1024)

static inline void reset_io_stat(struct f2fs_info *fi)
{
	for(int i = CSIO_READ; i < NR_CSIO_OP; i++){
		for(int j = CSIO_F2FS_DATA; j < NR_CSIO_DATA_TYPES; j++){
			fi->wi[CS_WORKER_ID].io_byte_size[i][j] = 0;
			fi->wi[CS_WORKER_ID].io_time[i][j] = 0;
		}
	}
}

#define LOCAL_TS(fi) ((fi)->wi[CS_WORKER_ID].ts)


#ifdef CS_DEBUG_PERF

#ifdef CS_DEBUG_PERF_DETAIL
#define GET_TIME_PERF_DEBUG(t) do{\
		t = get_time_ns();\
	}while(0)
#else
#define GET_TIME_PERF_DEBUG(t)
#endif

static inline void init_time_stat(struct f2fs_info *fi)
{
	LOCAL_TS(fi).time_stat_bar_name[0] = "start";
	
	for(int i = 0; i < MAX_NR_TIME_STAT_BAR; i++){
		LOCAL_TS(fi).bar_time[i] = 0;
		LOCAL_TS(fi).phase_time_interval[i] = 0;
		LOCAL_TS(fi).io_time_interval[i] = 0;
	}

	LOCAL_TS(fi).bar_time[0] = get_time_ns();
	LOCAL_TS(fi).io_time_interval[0] = 0;
	LOCAL_TS(fi).cur_phase = 1;
}

static inline void update_time_stat(struct f2fs_info *fi, const char *bar_name)
{
	int cur_phase = LOCAL_TS(fi).cur_phase;
	ASSERT(cur_phase < MAX_NR_TIME_STAT_BAR);

	LOCAL_TS(fi).time_stat_bar_name[cur_phase] = bar_name;
	LOCAL_TS(fi).bar_time[cur_phase] = get_time_ns();
	LOCAL_TS(fi).phase_time_interval[cur_phase] = 
		LOCAL_TS(fi).bar_time[cur_phase] - LOCAL_TS(fi).bar_time[cur_phase - 1];
#ifdef CS_DEBUG_PERF_IO
	LOCAL_TS(fi).io_bar_time[cur_phase] = total_io_time_ns(fi,-1);
	LOCAL_TS(fi).io_time_interval[cur_phase] = 
		LOCAL_TS(fi).io_bar_time[cur_phase] - LOCAL_TS(fi).io_bar_time[cur_phase - 1];
#endif
	LOCAL_TS(fi).cur_phase++;
}

static inline void show_time_stat(struct f2fs_info *fi)
{
	CS_INFO(fi, "Time breakdown(us)");
	CS_INFO(fi, "+--time--+---io---+------phase name------");
	for(int i = 0; i < LOCAL_TS(fi).cur_phase; i++){
		CS_INFO(fi, "| %6llu | %6llu | %20s", LOCAL_TS(fi).phase_time_interval[i]/1000, 
			LOCAL_TS(fi).io_time_interval[i]/1000, LOCAL_TS(fi).time_stat_bar_name[i]);
	}
	CS_INFO(fi, "+--------+--------+----------------------");

	CS_INFO(fi, "GC takes %llu us", (LOCAL_TS(fi).bar_time[LOCAL_TS(fi).cur_phase - 1] - LOCAL_TS(fi).bar_time[0])/1000);
#ifdef CS_DEBUG_PERF_IO
	CS_INFO(fi, "GC calculation takes %llu us", 
		(LOCAL_TS(fi).bar_time[LOCAL_TS(fi).cur_phase - 1] - LOCAL_TS(fi).bar_time[0] - total_io_time_ns(fi,-1))/1000);
#endif
	CS_INFO(fi, "GC lock time %llu", LOCAL_TS(fi).lock_time/1000);
}

#ifdef CS_DEBUG_PERF_IO
static inline void show_io_stat(struct f2fs_info *fi){
	CS_INFO(fi, "===================================================================");
	for(int i = CSIO_READ; i < NR_CSIO_OP; i++){
		for(int j = CSIO_F2FS_DATA; j < NR_CSIO_DATA_TYPES; j++){
			CS_INFO(fi, "IO %s %s: %lu bytes, %lu us, [bandwidth=%llu MiB/s]", 
				i == CSIO_READ ? "READ" : "WRITE",
				j == CSIO_F2FS_DATA ? "DATA" : 
				(j == CSIO_F2FS_NODE ? "NODE" : "META"),
				fi->wi[CS_WORKER_ID].io_byte_size[i][j],
				fi->wi[CS_WORKER_ID].io_time[i][j]/1000,
				BANDWIDTH_MBPS(fi->wi[CS_WORKER_ID].io_byte_size[i][j], 
					fi->wi[CS_WORKER_ID].io_time[i][j]));
		}
	}
	CS_INFO(fi, "total io size: %llu B (R=%llu/W=%llu)", total_io_size(fi,-1),
		total_io_size(fi,CSIO_READ), total_io_size(fi,CSIO_WRITE));
	CS_INFO(fi, "total io time: %llu us (R=%llu/W=%llu)", total_io_time_ns(fi,-1)/1000,
		total_io_time_ns(fi,CSIO_READ)/1000, total_io_time_ns(fi,CSIO_WRITE)/1000);
	CS_INFO(fi, "total io bandwidth: %llu MiB/s (R=%llu/W=%llu)", 
		BANDWIDTH_MBPS(total_io_size(fi,-1),total_io_time_ns(fi,-1)),
		BANDWIDTH_MBPS(total_io_size(fi,CSIO_READ),total_io_time_ns(fi,CSIO_READ)),
		BANDWIDTH_MBPS(total_io_size(fi,CSIO_WRITE),total_io_time_ns(fi,CSIO_WRITE)));
	CS_INFO(fi, "===================================================================");
}
#else
static inline void show_io_stat(struct f2fs_info *fi) {}
#endif

#else
static inline void init_time_stat(struct f2fs_info *fi) {}
static inline void update_time_stat(struct f2fs_info *fi, const char *bar_name) {}
static inline void show_time_stat(struct f2fs_info *fi) {}
static inline void show_io_stat(struct f2fs_info *fi) {}
#endif

static inline bool __is_valid_data_blkaddr(block_t blkaddr)
{
	if (blkaddr == NEW_ADDR || blkaddr == NULL_ADDR ||
			blkaddr == COMPRESS_ADDR)
		return false;
	return true;
}

static inline struct f2fs_sb *F2FS_SB(struct f2fs_info *fi)
{
	return fi->sb;
}

static inline struct f2fs_checkpoint *F2FS_CKPT(struct f2fs_info *fi)
{
	return fi->cp;
}

static inline struct curseg_info *CURSEG_I(struct f2fs_info *fi, int type)
{
	return fi->curseg_array + type;
}

static inline struct pre_alloc_info *F2FS_PI(struct f2fs_info *fi)
{
	return &fi->pi;
}

static inline bool __is_valid_pseg(struct pseg_info *pseg)
{
    return pseg && (pseg->segno != NULL_SEGNO) && 
            (pseg->seg_type < NR_CURSEG_PERSIST_TYPE);
}

static inline struct pseg_info *CUR_PSEG_I(struct f2fs_info *fi)
{
	struct pre_alloc_info *pi = F2FS_PI(fi);
	struct worker_info *wi = WORKER_I(fi);
	int pseg_off = wi->partition.pseg_off + wi->cur_pseg_off;
	ASSERT(pseg_off < 2);
	return &pi->psegs[pseg_off];
}

// no validity check
static inline struct pseg_info *LAST_PSEG_I(struct f2fs_info *fi)
{
	struct pre_alloc_info *pi = F2FS_PI(fi);
	if(!pi->nr_psegs) 
		return NULL;
	return pi->psegs + pi->nr_psegs - 1;
}

// host pre allocates segments for ssd, so that ssd does not need 
// to allocate segments and manage sit entries of pre-allocated segments
static inline bool F2FS_HAS_PRE_ALLOC_SEGS(struct f2fs_info *fi)
{
	return F2FS_PI(fi)->nr_psegs > 0; 
}

static inline bool CS_REQ_IS_HEAD(struct gc_info *gi)
{
	return gi->fi->package->header.segno == gi->fi->package->header.head_segno;
}

static inline bool F2FS_NEED_CKPT_INFO(struct csgc_header *header)
{
	return !(header->meta_sent_from_host);
}

static inline bool F2FS_IS_LAME(struct f2fs_info *fi)
{
	return fi->is_lame;
}

static inline bool __exist_node_summaries(struct f2fs_info *fi)
{
	return (F2FS_CKPT(fi)->ckpt_flags & CP_UMOUNT_FLAG ||
			F2FS_CKPT(fi)->ckpt_flags & CP_FASTBOOT_FLAG);
}

static inline block_t __start_cp_addr(struct f2fs_info *fi)
{
	block_t start_addr = F2FS_SB(fi)->cp_blkaddr;

	if (fi->cur_cp_pack == 2)
		start_addr += fi->blocks_per_seg;
	return start_addr;
}

static inline block_t start_sum_block(struct f2fs_info *fi)
{
	return __start_cp_addr(fi) +
		F2FS_CKPT(fi)->cp_pack_start_sum;
}

static inline block_t sum_blk_addr(struct f2fs_info *fi, int base, int type)
{
	return __start_cp_addr(fi) +
		F2FS_CKPT(fi)->cp_pack_total_block_count
				- (base + 1) + type;
				// -1 => the tail checkpoint block
}

// logical segno
static inline unsigned int get_segno(struct f2fs_info *fi, block_t block_addr)
{
	struct f2fs_sb *sb = F2FS_SB(fi);
	if(!__is_valid_data_blkaddr(block_addr)) return NULL_SEGNO;
	return (block_addr - sb->segment0_blkaddr) << sb->log_blocks_per_seg;
}

// relative segno used when accessing SIT and SSA
static inline unsigned int get_segno_from_main(struct f2fs_info *fi, block_t block_addr)
{
	struct f2fs_sb *sb = F2FS_SB(fi);
	return (block_addr - sb->main_blkaddr) >> sb->log_blocks_per_seg;
}

// input is relative segno
static inline block_t segno2blkaddr(struct f2fs_info *fi, unsigned int segno)
{
	struct f2fs_sb *sb = F2FS_SB(fi);
	return sb->main_blkaddr + (segno << sb->log_blocks_per_seg);
}

// Wrong implementation, only used in simulation
static inline block_t get_sit_addr(struct f2fs_info *fi, unsigned int segno)
{
	// TODO: Use SIT bitmap
	return F2FS_SB(fi)->sit_blkaddr + segno;
}

static inline block_t get_ssa_addr(struct f2fs_info *fi, unsigned int segno)
{
	return F2FS_SB(fi)->ssa_blkaddr + segno;
}

static inline block_t current_nat_addr(struct f2fs_info *fi, nid_t start)
{
    block_t block_off;
	block_t block_addr;

	ASSERT(!F2FS_IS_LAME(fi));
	block_off = start/NAT_ENTRY_PER_BLOCK;

	// block_addr_old = block_off/512*2*512 + block_off%512
	// block_addr_new = block_off/512*2*512 + 512 + block_off%512
	// block_off/512*2*512 = block_off/512*512*2 = block_off*2 - block_off%512 * 2
	// thus, block_addr_old = block_off*2 - block_off%512
	block_addr = (block_t)(fi->sb->nat_blkaddr +
		(block_off << 1) -
		(block_off & (fi->blocks_per_seg - 1)));

	if (bm_test_bit(block_off, fi->nat_bitmap))
		block_addr += fi->blocks_per_seg;

	return block_addr;
}

//                               SIT structure
// |===========|===========|===========||===========|===========|===========|
// |   seg 0   |   seg 1   |    ...    ||seg 0(back)|seg 1(back)|    ...    |
// |===========|===========|===========||===========|===========|===========|
//  <-----------SIT Area 0------------>  <-----------SIT Area 1------------> 
//
// get current address of the block containing sit entry of the segment(the segno is specified by `start`)
static inline block_t current_sit_addr(struct f2fs_info *fi, unsigned int start)
{
    unsigned int offset;
    block_t block_addr;

	ASSERT(!F2FS_IS_LAME(fi));
    offset = start / SIT_ENTRY_PER_BLOCK;
    block_addr = F2FS_SB(fi)->sit_blkaddr + offset;

    if(start > F2FS_SB(fi)->segment_count - 1) 
        return 0;
    
    if(bm_test_bit(offset, fi->sit_bitmap))
        block_addr += fi->sit_blocks;
    
    return block_addr;
}

// @block_addr is the block address of the current SIT block
// returns the address of the next SIT block to be used
static inline block_t next_sit_addr(struct f2fs_info *fi, block_t block_addr)
{
    unsigned int sit_blocks = fi->sit_blocks;
    unsigned int sit_blkaddr = F2FS_SB(fi)->sit_blkaddr;
    block_addr -= sit_blkaddr;
    if(block_addr < sit_blocks)
        block_addr += sit_blocks;
    else
        block_addr -= sit_blocks;
    
    return block_addr + sit_blkaddr;

}

static inline void set_to_next_sit(struct f2fs_info *fi, unsigned int start)
{
	unsigned int block_off = start / SIT_ENTRY_PER_BLOCK;

	bm_change_bit(block_off, fi->sit_bitmap);
}

static inline struct f2fs_nat_entry *lookup_journal_nat(struct f2fs_info *fi, unsigned int nid)
{
    struct curseg_info *curseg = CURSEG_I(fi, CURSEG_HOT_DATA);
    struct nat_journal *nat_j = &curseg->journal->nat_j;
    struct f2fs_nat_entry *ret = NULL;
    unsigned int n_nats = curseg->journal->n_nats;
    int i;

	ASSERT(!F2FS_IS_LAME(fi));
    for(i = 0; i < n_nats; i++){
        if(nat_j->entries[i].nid == nid){
            ret = &nat_j->entries[i].ne;
            break;
        }
    }
    return ret;
}

static inline struct f2fs_sit_entry *lookup_journal_sit(struct f2fs_info *fi, unsigned int segno)
{
    struct curseg_info *curseg = CURSEG_I(fi, CURSEG_COLD_DATA);
    struct sit_journal *sit_j = &curseg->journal->sit_j;
    struct f2fs_sit_entry *ret = NULL;
    unsigned int n_sits = curseg->journal->n_sits;
    int i;

	ASSERT(!F2FS_IS_LAME(fi));
    for(i = 0; i < n_sits; i++){
        if(sit_j->entries[i].segno == segno){
            ret = &sit_j->entries[i].se;
            break;
        }
    }
    return ret;
}

static inline bool __has_curseg_space(struct f2fs_info *fi, struct curseg_info *curseg)
{
	return curseg->next_blkoff < fi->blocks_per_seg;
}

static inline bool f2fs_has_extra_isize(struct f2fs_inode *inode)
{
	return (inode->i_inline & F2FS_EXTRA_ATTR);
}

static inline int __get_extra_isize(struct f2fs_inode *inode)
{
	if (f2fs_has_extra_isize(inode))
		return inode->i_extra_isize / sizeof(__le32);
	return 0;
}

static inline int get_inline_xattr_addrs(struct f2fs_info *fi, struct f2fs_inode *inode)
{
	if (F2FS_SB(fi)->feature & F2FS_FEATURE_FLEXIBLE_INLINE_XATTR)
		return inode->i_inline_xattr_size;
	else if (inode->i_inline & F2FS_INLINE_XATTR ||
			inode->i_inline & F2FS_INLINE_DENTRY)
		return DEFAULT_INLINE_XATTR_ADDRS;
	else
		return 0;
}

static inline int cur_addrs_per_inode(struct f2fs_inode *inode)
{
	return DEF_ADDRS_PER_INODE - __get_extra_isize(inode) ;
}

static inline unsigned int addrs_per_inode(struct f2fs_info *fi, struct f2fs_inode *inode)
{
	// if (!LINUX_S_ISREG(le16_to_cpu(inode->i_mode)) ||
	// 		!(le32_to_cpu(inode->i_flags) & F2FS_COMPR_FL))
	return cur_addrs_per_inode(inode) - get_inline_xattr_addrs(fi, inode);
}

static inline bool IS_INODE(struct f2fs_node *node)
{
	return node->footer.ino == node->footer.nid;
}

static inline int offset_in_addr(struct f2fs_inode *i)
{
	return f2fs_has_extra_isize(i) ?
			(i->i_extra_isize / sizeof(__le32)) : 0;
}

static inline __le32 *blkaddr_in_node(struct f2fs_node *node)
{
	return IS_INODE(node) ? node->i.i_addr : node->dn.addr;
}

static inline block_t data_blkaddr(struct f2fs_node *node, unsigned int offset)
{
	__le32 *addr_array;
	int base = 0;

	if (IS_INODE(node)) {
		base = offset_in_addr(&node->i);
	}
	
	addr_array = blkaddr_in_node(node);
	return addr_array[base + offset];
}

static inline void __set_data_blkaddr(struct f2fs_node *dn, unsigned int offset, block_t new_addr)
{
	__le32 *addr_array;
	int base = 0;

	if (IS_INODE(dn))
		base = offset_in_addr(&dn->i);

	/* Get physical address of data block */
	addr_array = blkaddr_in_node(dn);
	addr_array[base + offset] = new_addr;
}

static inline unsigned int ofs_of_node(struct f2fs_node *rn)
{
	unsigned flag = rn->footer.flag;
	return flag >> OFFSET_BIT_SHIFT;
}

static inline unsigned int get_sit_pack_size(unsigned int nr_se){
	return sizeof(struct dirty_sit_pack) + nr_se * sizeof(struct dsp_entry); 
}

static inline unsigned int get_dnp_entry_size(struct dnp_entry *dnp_entry){
	return sizeof(struct dnp_entry) + dnp_entry->nr_ext * sizeof(struct dirty_extent);
}

static inline unsigned int get_dnp_size(struct dirty_node_pack *dnp)
{
	unsigned int total_size = sizeof(struct dirty_node_pack);
	unsigned int ent_size;
	unsigned int offset = 0;
	for(unsigned int i = 0; i < dnp->nr_dirty_node; i++){
		ent_size = get_dnp_entry_size((struct dnp_entry *) (dnp->dnp_entries + offset));
		offset += ent_size;
		total_size += ent_size;
	}
	return total_size;
}

static inline unsigned int get_dnp_size_from_list(struct gc_info *gi)
{
	unsigned int size = sizeof(struct dirty_node_pack);
	struct node_entry *dno;
	for(int i = 0; i < gi->nr_node_info; i++){
		dno = &gi->shared_dnode_list[i];
		size += sizeof(struct dnp_entry);
		for(int j = 0; j < gi->fi->nr_cs_workers; j++)
			size += dno->dirty_exts[j]->size * sizeof(struct dirty_extent);
	}
	return size;
}
	
static inline void set_sentry_segtype(struct f2fs_sit_entry *sentry, int segtype){
	sentry->vblocks = (sentry->vblocks & SIT_VBLOCKS_MASK) | (segtype << SIT_VBLOCKS_SHIFT) ;
}

static inline void set_sentry_vblocks(struct f2fs_sit_entry *sentry, unsigned int vblocks){
	sentry->vblocks = (sentry->vblocks & ~SIT_VBLOCKS_MASK) | (vblocks & SIT_VBLOCKS_MASK) ;
}

static inline void set_package_err(struct csgc_package *package, int err)
{
	package->header.status[CS_WORKER_ID] = err;
}

/* f2fs_io.c */
void f2fs_csio_init_all(struct f2fs_info *fi);
void f2fs_csio_init(struct f2fs_info *fi, enum csio_dtype dtype);
void f2fs_csio_free_all(struct f2fs_info *fi);
void f2fs_csio_free(struct f2fs_info *fi, enum csio_dtype dtype);
void f2fs_csio_wait_all_completion(struct f2fs_info *fi);
void f2fs_csio_wait_completion(struct f2fs_info *fi, enum csio_op op, enum csio_dtype dtype);
void f2fs_csio_wait_all_worker_completion(struct f2fs_info *fi, enum csio_op op, enum csio_dtype dtype);
int f2fs_flush_csio(struct f2fs_info *fi, enum csio_dtype type, enum csio_op csio_op);
int f2fs_flush_csio_async(struct f2fs_info *fi, enum csio_dtype type, enum csio_op csio_op);
int f2fs_read_block(struct f2fs_info *fi, block_t blk_addr, void *buffer, 
                enum csio_dtype type, int force_submit, int sync, int *flushed);
int f2fs_write_block(struct f2fs_info *fi, block_t blk_addr, void *buffer, 
                enum csio_dtype type, int force_submit, int sync, int *flushed);
int f2fs_migrate_block(struct f2fs_info *fi, block_t src_addr, block_t dst_addr, 
                enum csio_dtype type, int force_submit, int sync, int *flushed);

/* f2fs_gc.c */
int f2fs_csgc_worker();
int f2fs_csgc_leader(struct f2fs_info *fi, void *arg_buf);

/* f2fs_super.c */
int f2fs_build_info(struct f2fs_info *fi, void *arg_buf);
void f2fs_free_info(struct f2fs_info *fi);


#endif