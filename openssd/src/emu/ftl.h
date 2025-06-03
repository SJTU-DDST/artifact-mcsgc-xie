#ifndef __FEMU_FTL_H
#define __FEMU_FTL_H

#include <stdbool.h>
#include <stdio.h>
#include "config.h"
#include "shared_mem.h"
#include "utils.h"
#include "pqueue.h"
#include "queue.h"
#include "cs_io.h"


#define INVALID_PPA     (~(0ULL))
#define INVALID_LPN     (~(0ULL))
#define UNMAPPED_PPA    (~(0ULL))

enum {
    NAND_READ = 0,
    NAND_WRITE = 1,
    NAND_ERASE = 2,
};

enum {
    USER_IO = 0,
    GC_IO = 1,
};

enum {
    SEC_FREE = 0,
    SEC_INVALID = 1,
    SEC_VALID = 2,

    PG_FREE = 0,
    PG_INVALID = 1,
    PG_VALID = 2
};

enum {
    FEMU_ENABLE_GC_DELAY = 1,
    FEMU_DISABLE_GC_DELAY = 2,

    FEMU_ENABLE_DELAY_EMU = 3,
    FEMU_DISABLE_DELAY_EMU = 4,

    FEMU_RESET_ACCT = 5,
    FEMU_ENABLE_LOG = 6,
    FEMU_DISABLE_LOG = 7,
};


#define BLK_BITS    (16)
#define PG_BITS     (16)
#define SEC_BITS    (8)
#define PL_BITS     (8)
#define LUN_BITS    (8)
#define CH_BITS     (7)

/* describe a physical page addr */
typedef struct {
    union {
        struct {
            uint64_t blk : BLK_BITS;
            uint64_t pg  : PG_BITS;
            uint64_t sec : SEC_BITS;
            uint64_t pl  : PL_BITS;
            uint64_t lun : LUN_BITS;
            uint64_t ch  : CH_BITS;
            uint64_t rsv : 1;
        } g;

        uint64_t ppa;
    };
} ppa_t;

typedef unsigned char nand_sec_status_t;

struct nand_page {
    // nand_sec_status_t sec[CONFIG_NAND_SECTORS_PER_PAGE];
    unsigned char status;
};

struct nand_block {
    struct nand_page *pg;
    short npgs;
    short ipc; /* invalid page count */
    short vpc; /* valid page count */
    short wp; /* current write pointer */
    // int erase_cnt;
};

struct nand_plane {
    struct nand_block *blk;
    int nblks;
    int avail_blks;
};

struct nand_lun {
    struct nand_plane *pl;
    int npls;
    int avail_pls;
    int avail_blks;
    uint64_t next_lun_avail_time;
    bool busy;
    uint64_t gc_endtime;
};

struct ssd_channel {
    struct nand_lun *lun;
    int nluns;
    int avail_luns;
    uint64_t next_ch_avail_time;
    bool busy;
    uint64_t gc_endtime;
};

struct ssdparams {
    int secsz;        /* sector size in bytes */
    int secs_per_pg;  /* # of sectors per page */
    int pgs_per_blk;  /* # of NAND pages per block */
    int blks_per_pl;  /* # of blocks per plane */
    int pls_per_lun;  /* # of planes per LUN (Die) */
    int luns_per_ch;  /* # of LUNs per channel */
    int nchs;         /* # of channels in the SSD */

    int pg_rd_lat;    /* NAND page read latency in nanoseconds */
    int pg_wr_lat;    /* NAND page program latency in nanoseconds */
    int blk_er_lat;   /* NAND block erase latency in nanoseconds */
    int ch_xfer_lat;  /* channel transfer latency for one page in nanoseconds
                       * this defines the channel bandwith
                       */

    double gc_thres_pcent;
    int gc_thres_lines;
    double gc_thres_pcent_high;
    int gc_thres_lines_high;
    bool enable_gc_delay;

    /* below are all calculated values */
    int secs_per_blk; /* # of sectors per block */
    int secs_per_pl;  /* # of sectors per plane */
    int secs_per_lun; /* # of sectors per LUN */
    int secs_per_ch;  /* # of sectors per channel */
    int tt_secs;      /* # of sectors in the SSD */
    int tt_avail_secs;

    int pgsz;         /* page size in bytes */
    int pgs_per_pl;   /* # of pages per plane */
    int pgs_per_lun;  /* # of pages per LUN (Die) */
    int pgs_per_ch;   /* # of pages per channel */
    int tt_pgs;       /* total # of pages in the SSD */
    int tt_avail_pgs;

    int blksz;        /* block size in bytes */
    int blks_per_lun; /* # of blocks per LUN */
    int blks_per_ch;  /* # of blocks per channel */
    int tt_blks;      /* total # of blocks in the SSD */
    int tt_avail_blks;

    int secs_per_line;
    int pgs_per_line;
    int blks_per_line;
    int tt_lines;
    int tt_avail_lines;

    int pls_per_ch;   /* # of planes per channel */
    int tt_pls;       /* total # of planes in the SSD */
    int tt_avail_pls;

    int tt_luns;      /* total # of LUNs in the SSD */
    int tt_avail_luns;

    int tt_avail_chs;
};

typedef struct line {
    int id;  /* line id, the same as corresponding block id */
    int ipc; /* invalid page count in this line */
    int vpc; /* valid page count in this line */
    int avail_blk_cnt; // available block count in this line
    int avail_pg_cnt;  // available page count in this line
    QTAILQ_ENTRY(line) entry; /* in either {free,victim,full} list */
    /* position in the priority queue for victim lines */
    size_t                  pos;
} line;

/* wp: record next write addr */
struct write_pointer {
    struct line *curline;
    int ch;
    int lun;
    int pg;
    int blk;
    int pl;
};

struct line_mgmt {
    struct line *lines;
    /* free line list, we only need to maintain a list of blk numbers */
    QTAILQ_HEAD(free_line_list, line) free_line_list;
    pqueue_t *victim_line_pq;
    //QTAILQ_HEAD(victim_line_list, line) victim_line_list;
    QTAILQ_HEAD(full_line_list, line) full_line_list;
    int tt_lines;
    int tt_avail_lines;
    int free_line_cnt;
    int victim_line_cnt;
    int full_line_cnt;
};

struct nand_cmd {
    int type;
    int cmd;
    uint64_t stime; /* Coperd: request arrival time */
};

// only simulate cdma transfer latency, no real data movement
// do `nr_pages` read followed by a write, to the same address
struct nand_rw_io_req {
    int nr_ios;
    int max_nr_ios;
    int nr_complete_ios;
    struct cs_io_handle *nrw_handles;
};

struct ssd {
    struct ssdparams sp;
    struct ssd_channel *ch;
    ppa_t *maptbl; /* page level mapping table */
    uint64_t *rmap;     /* reverse mapptbl, assume it's stored in OOB */
    struct write_pointer wp;
    struct line_mgmt lm;
    struct ssd_config cfg;
    struct nand_rw_io_req *nrw_req;
    struct ssd_stat *stat;
    uint64_t dm_start_lba;  // start lba for direct l2p mapping, 
                            // used for `SEPARATE_L2P_MAPPING`
};

#define SSD_L2P_MAPPING_TYPE(ssd) ((ssd)->cfg.l2p_mapping_type)
#define SSD_NAND_LATENCY_EMU_ENABLED(ssd) ((ssd)->cfg.nand_latency_emu_enabled)

void ssd_init(struct ssd *ssd, bool reset);
uint64_t ssd_read(struct ssd *ssd, struct host_io_req *req, uint64_t stime);
uint64_t ssd_write(struct ssd *ssd, struct host_io_req *req, uint64_t stime);
uint64_t ssd_dsm(struct ssd *ssd, struct host_io_req *req, uint64_t stime);

uint64_t ssd_get_pba(struct ssd *ssd, uint64_t lba);

void ssd_test_interval_mapping(struct ssd *ssd);

static inline bool should_use_interval_mapping(struct ssd *ssd)
{
    return (ssd->cfg.l2p_mapping_type == L2P_MAPPING_INTERVAL);
}

static inline bool lba_should_passthru(struct ssd *ssd, uint64_t lba)
{
    return !should_use_interval_mapping(ssd) && lba >= ssd->dm_start_lba;
}

static inline bool lpn_should_passthru(struct ssd *ssd, uint64_t lpn)
{
    return lba_should_passthru(ssd, lpn*ssd->sp.secs_per_pg);
}

static inline uint64_t storage_offset_l2p(struct ssd *ssd, uint64_t loff)
{
    uint64_t lba, pba;
    lba = loff / CONFIG_NAND_SECTORS_SIZE;
    pba = ssd_get_pba(ssd, lba);
    return pba * CONFIG_NAND_SECTORS_SIZE + loff % CONFIG_NAND_SECTORS_SIZE;
}

#ifdef FEMU_DEBUG_FTL
#define ftl_debug(fmt, ...) \
    do { printf("[FEMU] FTL-Dbg: " fmt, ## __VA_ARGS__); } while (0)
#else
#define ftl_debug(fmt, ...) \
    do { } while (0)
#endif

#define ftl_err(fmt, ...) \
    do { printf("[FEMU] FTL-Err: " fmt, ## __VA_ARGS__); } while (0)

#define ftl_log(fmt, ...) \
    do { printf("[FEMU] FTL-Log: " fmt, ## __VA_ARGS__); } while (0)


/* FEMU assert() */
// #ifdef FEMU_DEBUG_FTL
#define ftl_assert(expression) ASSERT(expression)
// #else
// #define ftl_assert(expression)
// #endif

#endif
