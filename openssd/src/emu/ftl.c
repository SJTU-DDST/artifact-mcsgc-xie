#include <stdbool.h>
#include "ftl.h"
#include "interval-mapping/functional_mapping.h"

//#define FEMU_DEBUG_FTL
static struct shared_mem *m = (struct shared_mem *)SHARED_MEM_BASE_ADDR;
extern struct linear_allocator ssd_allocator;
void *nand_gc_io_buf;


static inline bool should_gc(struct ssd *ssd)
{
    return (ssd->lm.free_line_cnt <= ssd->sp.gc_thres_lines);
}

static inline bool should_gc_high(struct ssd *ssd)
{
    return (ssd->lm.free_line_cnt <= ssd->sp.gc_thres_lines_high);
}

static uint64_t ppa2pgidx(struct ssd *ssd, ppa_t *ppa)
{
    struct ssdparams *spp = &ssd->sp;
    uint64_t pgidx;

    pgidx = ppa->g.ch  * spp->pgs_per_ch  + \
            ppa->g.lun * spp->pgs_per_lun + \
            ppa->g.pl  * spp->pgs_per_pl  + \
            ppa->g.blk * spp->pgs_per_blk + \
            ppa->g.pg;

    ftl_assert(pgidx < spp->tt_pgs);

    return pgidx;
}

static ppa_t pgidx2ppa(struct ssd *ssd, uint64_t pgidx)
{
    struct ssdparams *spp = &ssd->sp;
    ppa_t ppa;

    ppa.g.ch  = pgidx / spp->pgs_per_ch;
    pgidx %= spp->pgs_per_ch;
    ppa.g.lun = pgidx / spp->pgs_per_lun;
    pgidx %= spp->pgs_per_lun;
    ppa.g.pl  = pgidx / spp->pgs_per_pl;
    pgidx %= spp->pgs_per_pl;
    ppa.g.blk = pgidx / spp->pgs_per_blk;
    pgidx %= spp->pgs_per_blk;
    ppa.g.pg  = pgidx;

    return ppa;
}

static inline ppa_t get_maptbl_ent(struct ssd *ssd, uint64_t lpn)
{
    uint64_t pgidx;
    ppa_t ppa;
    if(!should_use_interval_mapping(ssd))
        return ssd->maptbl[lpn];
    pgidx = mapseg_get_mapping(lpn);
    if(pgidx==VSA_NONE){
        ppa.ppa = UNMAPPED_PPA;
        return ppa;
    }
    // ppa = pgidx2ppa(ssd, pgidx);
    // ASSERT(ppa2pgidx(ssd, &ppa) == pgidx);
    return pgidx2ppa(ssd, pgidx);
}

static inline void set_maptbl_ent(struct ssd *ssd, uint64_t lpn, ppa_t *ppa)
{
    if(!should_use_interval_mapping(ssd)){
        ftl_assert(lpn < ssd->sp.tt_avail_pgs);
        ssd->maptbl[lpn] = *ppa;
    }else
        mapseg_set_mapping(lpn, ppa2pgidx(ssd, ppa), false);
}

static inline uint64_t get_rmap_ent(struct ssd *ssd, ppa_t *ppa)
{
    uint64_t pgidx = ppa2pgidx(ssd, ppa);

    return ssd->rmap[pgidx];
}

/* set rmap[page_no(ppa)] -> lpn */
static inline void set_rmap_ent(struct ssd *ssd, uint64_t lpn, ppa_t *ppa)
{
    uint64_t pgidx = ppa2pgidx(ssd, ppa);

    ftl_assert(pgidx < ssd->sp.tt_avail_pgs);

    ssd->rmap[pgidx] = lpn;
}

static inline int victim_line_cmp_pri(pqueue_pri_t next, pqueue_pri_t curr)
{
    return (next > curr);
}

static inline pqueue_pri_t victim_line_get_pri(void *a)
{
    return ((struct line *)a)->vpc;
}

static inline void victim_line_set_pri(void *a, pqueue_pri_t pri)
{
    ((struct line *)a)->vpc = pri;
}

static inline size_t victim_line_get_pos(void *a)
{
    return ((struct line *)a)->pos;
}

static inline void victim_line_set_pos(void *a, size_t pos)
{
    ((struct line *)a)->pos = pos;
}

static void ssd_init_lines(struct ssd *ssd, bool alloc)
{
    struct ssdparams *spp = &ssd->sp;
    struct line_mgmt *lm = &ssd->lm;
    struct line *line;
    uint64_t tt_avail_blks = ssd->dm_start_lba / spp->secs_per_blk;

    lm->tt_lines = spp->blks_per_pl;
    ftl_assert(lm->tt_lines == spp->tt_lines);
    if(alloc)
        lm->lines = linear_malloc(&ssd_allocator, sizeof(struct line) * lm->tt_lines, 0);

    QTAILQ_INIT(&lm->free_line_list);
    lm->victim_line_pq = pqueue_init(&lm->victim_line_pq, spp->tt_lines, victim_line_cmp_pri,
            victim_line_get_pri, victim_line_set_pri,
            victim_line_get_pos, victim_line_set_pos, alloc);
    QTAILQ_INIT(&lm->full_line_list);

    lm->free_line_cnt = 0;
    for (int i = 0; i < lm->tt_lines; i++) {
        line = &lm->lines[i];
        line->id = i;
        line->ipc = 0;
        line->vpc = 0;
        line->avail_blk_cnt = tt_avail_blks / spp->blks_per_pl + \
                 (i < tt_avail_blks % spp->blks_per_pl);
        line->avail_pg_cnt  = line->avail_blk_cnt * spp->pgs_per_blk;
        line->pos = 0;
        if(line->avail_blk_cnt){
            /* initialize all the lines as free lines */
            QTAILQ_INSERT_TAIL(&lm->free_line_list, line, entry);
            lm->free_line_cnt++;
        }
    }
    lm->tt_avail_lines = lm->free_line_cnt;
    ftl_assert(lm->tt_avail_lines == spp->tt_avail_lines);

    if(tt_avail_blks >= spp->blks_per_pl)
        ftl_assert(lm->free_line_cnt == lm->tt_lines);
    lm->victim_line_cnt = 0;
    lm->full_line_cnt = 0;
}

static void ssd_init_write_pointer(struct ssd *ssd)
{
    struct write_pointer *wpp = &ssd->wp;
    struct line_mgmt *lm = &ssd->lm;
    struct line *curline = NULL;

    if(lm->free_line_cnt == 0){
        ASSERT(ssd->dm_start_lba == 0);
        return;
    }
    
    curline = QTAILQ_FIRST(&lm->free_line_list);
    QTAILQ_REMOVE(&lm->free_line_list, curline, entry);
    lm->free_line_cnt--;

    /* wpp->curline is always our next-to-write super-block */
    wpp->curline = curline;
    wpp->ch = 0;
    wpp->lun = 0;
    wpp->pg = 0;
    wpp->blk = 0;
    wpp->pl = 0;
}

static inline void check_addr(int a, int max)
{
    ftl_assert(a >= 0 && a < max);
}

static struct line *get_next_free_line(struct ssd *ssd)
{
    struct line_mgmt *lm = &ssd->lm;
    struct line *curline = NULL;

    curline = QTAILQ_FIRST(&lm->free_line_list);
    if (!curline) {
        ftl_err("No free lines left!!!\n");
        return NULL;
    }

    QTAILQ_REMOVE(&lm->free_line_list, curline, entry);
    lm->free_line_cnt--;
    return curline;
}

static inline int cur_lun_idx(struct ssdparams *spp, struct write_pointer *wpp)
{
    return wpp->ch * spp->luns_per_ch + wpp->lun;
}

static inline int cur_block_in_lun_idx(struct ssdparams *spp, struct write_pointer *wpp)
{
    return wpp->lun * spp->blks_per_lun + wpp->pl * spp->blks_per_pl + wpp->blk;
}

static void ssd_advance_write_pointer(struct ssd *ssd)
{
    struct ssdparams *spp = &ssd->sp;
    struct write_pointer *wpp = &ssd->wp;
    struct line_mgmt *lm = &ssd->lm;

    check_addr(wpp->ch, spp->tt_avail_chs);
    wpp->ch++;
    if (wpp->ch == spp->tt_avail_chs || (wpp->ch == spp->tt_avail_chs - 1 && 
            cur_lun_idx(spp, wpp) >= wpp->curline->avail_blk_cnt)) {
        struct ssd_channel *ch;
        wpp->ch = 0;
        ch = &ssd->ch[wpp->ch];
        check_addr(wpp->lun, ch->avail_luns);
        wpp->lun++;
        /* in this case, we should go to next lun */
        if (wpp->lun == ch->avail_luns || (wpp->lun == ch->avail_luns - 1 && 
                wpp->blk >= ch->lun[wpp->lun].avail_blks)) {
            wpp->lun = 0;
            /* go to next page in the block */
            check_addr(wpp->pg, spp->pgs_per_blk);
            wpp->pg++;
            if (wpp->pg == spp->pgs_per_blk) {
                wpp->pg = 0;
                /* move current line to {victim,full} line list */
                if (wpp->curline->vpc == wpp->curline->avail_pg_cnt) {
                    /* all pgs are still valid, move to full line list */
                    ftl_assert(wpp->curline->ipc == 0);
                    QTAILQ_INSERT_TAIL(&lm->full_line_list, wpp->curline, entry);
                    lm->full_line_cnt++;
                } else {
                    ftl_assert(wpp->curline->vpc >= 0 && wpp->curline->vpc < wpp->curline->avail_pg_cnt);
                    /* there must be some invalid pages in this line */
                    ftl_assert(wpp->curline->ipc > 0);
                    pqueue_insert(lm->victim_line_pq, wpp->curline);
                    lm->victim_line_cnt++;
                }
                /* current line is used up, pick another empty line */
                check_addr(wpp->blk, spp->blks_per_pl);
                wpp->curline = NULL;
                wpp->curline = get_next_free_line(ssd);
                if (!wpp->curline) {
                    /* TODO */
                    ASSERT(0);
                }
                wpp->blk = wpp->curline->id;
                check_addr(wpp->blk, spp->tt_avail_lines);
                /* make sure we are starting from page 0 in the super block */
                ftl_assert(wpp->pg == 0);
                ftl_assert(wpp->lun == 0);
                ftl_assert(wpp->ch == 0);
                /* TODO: assume # of pl_per_lun is 1, fix later */
                ftl_assert(wpp->pl == 0);
            }
        }
    }
}

static ppa_t get_new_page(struct ssd *ssd)
{
    struct write_pointer *wpp = &ssd->wp;
    ppa_t ppa;
    ppa.ppa = 0;
    ppa.g.ch = wpp->ch;
    ppa.g.lun = wpp->lun;
    ppa.g.pg = wpp->pg;
    ppa.g.blk = wpp->blk;
    ppa.g.pl = wpp->pl;
    ftl_assert(ppa.g.pl == 0);

    return ppa;
}

static void check_params(struct ssdparams *spp)
{
    /*
     * we are using a general write pointer increment method now, no need to
     * force luns_per_ch and nchs to be power of 2
     */

    //ftl_assert(is_power_of_2(spp->luns_per_ch));
    //ftl_assert(is_power_of_2(spp->nchs));
}

static void ssd_init_config(struct ssd *ssd)
{
    ssd->cfg.l2p_mapping_type = CONFIG_ENABLE_L2P_MAPPING_DEFAULT;
    ssd->cfg.nand_latency_emu_enabled = CONFIG_ENABLE_NAND_LATENCY_EMU_DEFAULT;
    ssd->cfg.main_area_lba = 0;
    ssd->cfg.dsm_enabled = CONFIG_ENABLE_DSM_DEFAULT;
}

static void ssd_init_params(struct ssd *ssd)
{
    struct ssdparams *spp = &ssd->sp;
    size_t flash_size = SSD_L2P_MAPPING_TYPE(ssd)==L2P_MAPPING_INTERVAL ? SSD_SIZE : FLASH_SIZE;

    spp->secsz = CONFIG_NAND_SECTORS_SIZE;
    spp->secs_per_pg = CONFIG_NAND_SECTORS_PER_PAGE;
    spp->pgs_per_blk = CONFIG_NAND_PAGES_PER_BLOCK;
    spp->pls_per_lun = 1;
    spp->luns_per_ch = CONFIG_NAND_LUNS_PER_CHANNEL;
    spp->nchs = CONFIG_NAND_CHANNELS;
    spp->blks_per_pl = flash_size / ((size_t)spp->secsz * spp->secs_per_pg *
                                     spp->pgs_per_blk * spp->pls_per_lun *
                                    spp->luns_per_ch * spp->nchs);

    if(SSD_NAND_LATENCY_EMU_ENABLED(ssd)){
        spp->pg_rd_lat = NS2CYCLE(CONFIG_NAND_READ_LATENCY);
        spp->pg_wr_lat = NS2CYCLE(CONFIG_NAND_PROG_LATENCY);
        spp->blk_er_lat = NS2CYCLE(CONFIG_NAND_ERASE_LATENCY);
        spp->ch_xfer_lat = 0;
    }else {
        spp->pg_rd_lat = 0;
        spp->pg_wr_lat = 0;
        spp->blk_er_lat = 0;
        spp->ch_xfer_lat = 0;
    }

    /* calculated values */
    spp->secs_per_blk = spp->secs_per_pg * spp->pgs_per_blk;
    spp->secs_per_pl = spp->secs_per_blk * spp->blks_per_pl;
    spp->secs_per_lun = spp->secs_per_pl * spp->pls_per_lun;
    spp->secs_per_ch = spp->secs_per_lun * spp->luns_per_ch;
    spp->tt_secs = spp->secs_per_ch * spp->nchs;

    spp->pgsz = spp->secsz * spp->secs_per_pg;
    spp->pgs_per_pl = spp->pgs_per_blk * spp->blks_per_pl;
    spp->pgs_per_lun = spp->pgs_per_pl * spp->pls_per_lun;
    spp->pgs_per_ch = spp->pgs_per_lun * spp->luns_per_ch;
    spp->tt_pgs = spp->pgs_per_ch * spp->nchs;

    spp->blksz = spp->pgsz * spp->pgs_per_blk;
    spp->blks_per_lun = spp->blks_per_pl * spp->pls_per_lun;
    spp->blks_per_ch = spp->blks_per_lun * spp->luns_per_ch;
    spp->tt_blks = spp->blks_per_ch * spp->nchs;

    spp->pls_per_ch =  spp->pls_per_lun * spp->luns_per_ch;
    spp->tt_pls = spp->pls_per_ch * spp->nchs;

    spp->tt_luns = spp->luns_per_ch * spp->nchs;

    /* line is special, put it at the end */
    spp->blks_per_line = spp->tt_luns; /* TODO: to fix under multiplanes */
    spp->pgs_per_line = spp->blks_per_line * spp->pgs_per_blk;
    spp->secs_per_line = spp->pgs_per_line * spp->secs_per_pg;
    spp->tt_lines = spp->blks_per_lun; /* TODO: to fix under multiplanes */

    ssd->dm_start_lba = 0;
    ASSERT(CONFIG_ACCESS_EXACT_PPA && SSD_L2P_MAPPING_TYPE(ssd)>L2P_MAPPING_NON || \
        !CONFIG_ACCESS_EXACT_PPA && SSD_L2P_MAPPING_TYPE(ssd)==L2P_MAPPING_INTERVAL);
    if(SSD_L2P_MAPPING_TYPE(ssd) == L2P_MAPPING_FULL || 
        SSD_L2P_MAPPING_TYPE(ssd) == L2P_MAPPING_INTERVAL)
        ssd->dm_start_lba = spp->tt_secs;
    if(SSD_L2P_MAPPING_TYPE(ssd) == L2P_MAPPING_SEPARATE){
        ssd->dm_start_lba = ssd->cfg.main_area_lba; 
        // must be aligned with nand block size
        ASSERT(ssd->dm_start_lba % spp->secs_per_blk == 0);
    }

    spp->tt_avail_secs = ssd->dm_start_lba;
    spp->tt_avail_pgs = ssd->dm_start_lba / spp->secs_per_pg;
    spp->tt_avail_blks = ssd->dm_start_lba / spp->secs_per_blk;
    spp->tt_avail_lines = spp->tt_avail_blks >= spp->tt_lines ? \
                spp->tt_lines : spp->tt_avail_blks;
    spp->tt_avail_pls = DIVIDE_CEILING(spp->tt_avail_blks , spp->blks_per_pl);
    spp->tt_avail_luns = DIVIDE_CEILING(spp->tt_avail_pls , spp->pls_per_lun);
    spp->tt_avail_chs = DIVIDE_CEILING(spp->tt_avail_luns , spp->luns_per_ch);

    spp->gc_thres_pcent = CONFIG_GC_THRESHOLD;
    spp->gc_thres_lines = (int)((1 - spp->gc_thres_pcent) * spp->tt_avail_lines);
    spp->gc_thres_pcent_high = CONFIG_GC_THRESHOLD_HIGH;
    spp->gc_thres_lines_high = (int)((1 - spp->gc_thres_pcent_high) * spp->tt_avail_lines);
    spp->enable_gc_delay = true;


    check_params(spp);
}

static void ssd_init_nand_page(struct nand_page *pg, struct ssdparams *spp)
{
    // for (int i = 0; i < CONFIG_NAND_SECTORS_PER_PAGE; i++) {
    //     pg->sec[i] = SEC_FREE;
    // }
    pg->status = PG_FREE;
}

static void ssd_init_nand_blk(struct nand_block *blk, struct ssdparams *spp, bool alloc)
{
    blk->npgs = spp->pgs_per_blk;
    if(alloc)
        blk->pg = linear_malloc(&ssd_allocator, sizeof(struct nand_page) * blk->npgs, 0);
    for (int i = 0; i < blk->npgs; i++) {
        ssd_init_nand_page(&blk->pg[i], spp);
    }
    blk->ipc = 0;
    blk->vpc = 0;
    // blk->erase_cnt = 0;
    blk->wp = 0;
}

static int ssd_init_nand_plane(struct nand_plane *pl, 
        struct ssdparams *spp, int *avail_blks, bool alloc)
{
    int pl_is_available = 0;
    pl->nblks = spp->blks_per_pl;
    pl->avail_blks = *avail_blks > spp->blks_per_pl ? spp->blks_per_pl : *avail_blks;
    *avail_blks -= pl->avail_blks;
    if(pl->avail_blks > 0)
        pl_is_available = 1;

    if(alloc)
        pl->blk = linear_malloc(&ssd_allocator, sizeof(struct nand_block) * pl->nblks, 0);
    for (int i = 0; i < pl->nblks; i++) {
        ssd_init_nand_blk(&pl->blk[i], spp, alloc);
    }

    return pl_is_available;
}

static int ssd_init_nand_lun(struct nand_lun *lun, 
        struct ssdparams *spp, int *avail_blks, bool alloc)
{
    int lun_is_available = 0;

    lun->npls = spp->pls_per_lun;
    lun->avail_pls = 0;
    lun->avail_blks = 0;
    if(alloc)
        lun->pl = linear_malloc(&ssd_allocator, sizeof(struct nand_plane) * lun->npls, 0);
    for (int i = 0; i < lun->npls; i++) {
        lun->avail_pls += ssd_init_nand_plane(&lun->pl[i], spp, avail_blks, alloc);
        lun->avail_blks += lun->pl[i].avail_blks;
    }
    if(lun->avail_pls > 0)
        lun_is_available = 1;

    lun->next_lun_avail_time = 0;
    lun->busy = false;

    return lun_is_available;
}

static int ssd_init_ch(struct ssd_channel *ch, 
        struct ssdparams *spp, int *avail_blks, bool alloc)
{
    int ch_is_available = 0;

    ch->nluns = spp->luns_per_ch;
    ch->avail_luns = 0;
    if(alloc)
        ch->lun = linear_malloc(&ssd_allocator, sizeof(struct nand_lun) * ch->nluns, 0);
    for (int i = 0; i < ch->nluns; i++) {
        ch->avail_luns += ssd_init_nand_lun(&ch->lun[i], spp, avail_blks, alloc);
    }
    if(ch->avail_luns > 0)
        ch_is_available = 1;

    ch->next_ch_avail_time = 0;
    ch->busy = 0;

    return ch_is_available;
}

static void ssd_init_maptbl(struct ssd *ssd, bool alloc)
{
    struct ssdparams *spp = &ssd->sp;

    if(alloc)
        ssd->maptbl = linear_malloc(&ssd_allocator, sizeof(ppa_t) * spp->tt_pgs, 0);
    for (int i = 0; i < spp->tt_pgs; i++) {
        ssd->maptbl[i].ppa = UNMAPPED_PPA;
    }
}

static void ssd_init_rmap(struct ssd *ssd, bool alloc)
{
    struct ssdparams *spp = &ssd->sp;

    if(alloc)
        ssd->rmap = linear_malloc(&ssd_allocator, sizeof(uint64_t) * spp->tt_pgs, 0);
    for (int i = 0; i < spp->tt_pgs; i++) {
        ssd->rmap[i] = INVALID_LPN;
    }
}

static void ssd_reset_nrw_req(struct ssd *ssd)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;

    ASSERT(nrw_req->nr_ios == nrw_req->nr_complete_ios);
    nrw_req->nr_ios = 0;
    nrw_req->nr_complete_ios = 0;
}

static void ssd_init_nrw_req(struct ssd *ssd, bool alloc)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;

    if(alloc){
        nrw_req = linear_malloc(&ssd_allocator, sizeof(struct nand_rw_io_req), 0);
        nrw_req->nrw_handles = linear_malloc(&ssd_allocator, sizeof(struct cs_io_req) * ssd->sp.pgs_per_line * 2, 0);
        nand_gc_io_buf = linear_malloc(&ssd_allocator, ssd->sp.pgsz, 0);
        ssd->nrw_req = nrw_req;
    }

    nrw_req->max_nr_ios = 2 * ssd->sp.pgs_per_line;
    nrw_req->nr_ios = 0;
    nrw_req->nr_complete_ios = 0;
}

void ssd_init(struct ssd *ssd, bool reset)
{
    struct shared_mem *m = (struct shared_mem *) SHARED_MEM_BASE_ADDR;
    struct ssdparams *spp = &ssd->sp;
    int avail_blks, tt_avail_chs = 0;

    ftl_assert(ssd);

    if(reset){
        xil_printf("reset ssd allocator\n");
        linear_malloc_reset(&ssd_allocator);
        ssd->ch = NULL;
        ssd->maptbl = NULL;
        ssd->rmap = NULL;
        ssd->lm.lines = NULL;
        ssd->nrw_req = NULL;
    }
    
    if(!reset)
        ssd_init_config(ssd);
    
    ssd_init_params(ssd);

    avail_blks = spp->tt_avail_blks;
    /* initialize ssd internal layout architecture */
    ssd->ch = linear_malloc(&ssd_allocator, sizeof(struct ssd_channel) * spp->nchs, 0);
    for (int i = 0; i < spp->nchs; i++) {
        tt_avail_chs += ssd_init_ch(&ssd->ch[i], spp, &avail_blks, 1);
    }
    ftl_assert(tt_avail_chs == spp->tt_avail_chs);
    xil_printf("struct ssd initialized\n");
    linear_allocator_get_mem_usage(&ssd_allocator, true);

    /* initialize maptbl */
    if(should_use_interval_mapping(ssd))
        mapseg_init();
    else
        ssd_init_maptbl(ssd, 1);
    xil_printf("l2p mapping initialized\n");
    linear_allocator_get_mem_usage(&ssd_allocator, true);

    /* initialize rmap */
    ssd_init_rmap(ssd, 1);
    xil_printf("p2l mapping initialized\n");
    linear_allocator_get_mem_usage(&ssd_allocator, true);

    /* initialize all the lines */
    ssd_init_lines(ssd, 1);

    /* initialize write pointer, this is how we allocate new pages for writes */
    ssd_init_write_pointer(ssd);

    ssd_init_nrw_req(ssd, 1);
        
    ssd->stat = &m->ssd_stat;
    memset(ssd->stat, 0, sizeof(struct ssd_stat));

    m->ssd = ssd;
    xil_printf("ssd totally initialized\n");
    linear_allocator_get_mem_usage(&ssd_allocator, true);
}

static inline bool valid_ppa(struct ssd *ssd, ppa_t *ppa)
{
    struct ssdparams *spp = &ssd->sp;
    int ch = ppa->g.ch;
    int lun = ppa->g.lun;
    int pl = ppa->g.pl;
    int blk = ppa->g.blk;
    int pg = ppa->g.pg;
    int sec = ppa->g.sec;

    if (ch >= 0 && ch < spp->nchs && lun >= 0 && lun < spp->luns_per_ch && pl >=
        0 && pl < spp->pls_per_lun && blk >= 0 && blk < spp->blks_per_pl && pg
        >= 0 && pg < spp->pgs_per_blk && sec >= 0 && sec < spp->secs_per_pg)
        return true;

    return false;
}

static inline bool valid_lpn(struct ssd *ssd, uint64_t lpn)
{
    return should_use_interval_mapping(ssd) || (lpn < ssd->sp.tt_pgs);
}

static inline bool mapped_ppa(ppa_t *ppa)
{
    return !(ppa->ppa == UNMAPPED_PPA);
}

static inline struct ssd_channel *get_ch(struct ssd *ssd, ppa_t *ppa)
{
    return &(ssd->ch[ppa->g.ch]);
}

static inline struct nand_lun *get_lun(struct ssd *ssd, ppa_t *ppa)
{
    struct ssd_channel *ch = get_ch(ssd, ppa);
    return &(ch->lun[ppa->g.lun]);
}

static inline struct nand_plane *get_pl(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_lun *lun = get_lun(ssd, ppa);
    return &(lun->pl[ppa->g.pl]);
}

static inline struct nand_block *get_blk(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_plane *pl = get_pl(ssd, ppa);
    return &(pl->blk[ppa->g.blk]);
}

static inline struct line *get_line(struct ssd *ssd, ppa_t *ppa)
{
    return &(ssd->lm.lines[ppa->g.blk]);
}

static inline struct nand_page *get_pg(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_block *blk = get_blk(ssd, ppa);
    return &(blk->pg[ppa->g.pg]);
}

static uint64_t ssd_advance_status(struct ssd *ssd, ppa_t *ppa, struct
        nand_cmd *ncmd)
{
    int c = ncmd->cmd;
    uint64_t cmd_stime = ncmd->stime == 0 ? get_time_cycle() : ncmd->stime;
    uint64_t nand_stime;
    struct ssdparams *spp = &ssd->sp;
    struct nand_lun *lun = get_lun(ssd, ppa);
    uint64_t lat = 0;

    switch (c) {
    case NAND_READ:
        /* read: perform NAND cmd first */
        nand_stime = (lun->next_lun_avail_time < cmd_stime) ? cmd_stime : \
                     lun->next_lun_avail_time;
        lun->next_lun_avail_time = nand_stime + spp->pg_rd_lat;
        lat = lun->next_lun_avail_time - cmd_stime;
#if 0
        lun->next_lun_avail_time = nand_stime + spp->pg_rd_lat;

        /* read: then data transfer through channel */
        chnl_stime = (ch->next_ch_avail_time < lun->next_lun_avail_time) ? \
            lun->next_lun_avail_time : ch->next_ch_avail_time;
        ch->next_ch_avail_time = chnl_stime + spp->ch_xfer_lat;

        lat = ch->next_ch_avail_time - cmd_stime;
#endif
        break;

    case NAND_WRITE:
        /* write: transfer data through channel first */
        nand_stime = (lun->next_lun_avail_time < cmd_stime) ? cmd_stime : \
                     lun->next_lun_avail_time;
        if (ncmd->type == USER_IO) {
            lun->next_lun_avail_time = nand_stime + spp->pg_wr_lat;
        } else {
            lun->next_lun_avail_time = nand_stime + spp->pg_wr_lat;
        }
        lat = lun->next_lun_avail_time - cmd_stime;

#if 0
        chnl_stime = (ch->next_ch_avail_time < cmd_stime) ? cmd_stime : \
                     ch->next_ch_avail_time;
        ch->next_ch_avail_time = chnl_stime + spp->ch_xfer_lat;

        /* write: then do NAND program */
        nand_stime = (lun->next_lun_avail_time < ch->next_ch_avail_time) ? \
            ch->next_ch_avail_time : lun->next_lun_avail_time;
        lun->next_lun_avail_time = nand_stime + spp->pg_wr_lat;

        lat = lun->next_lun_avail_time - cmd_stime;
#endif
        break;

    case NAND_ERASE:
        /* erase: only need to advance NAND status */
        nand_stime = (lun->next_lun_avail_time < cmd_stime) ? cmd_stime : \
                     lun->next_lun_avail_time;
        lun->next_lun_avail_time = nand_stime + spp->blk_er_lat;

        lat = lun->next_lun_avail_time - cmd_stime;
        break;

    default:
        ftl_err("Unsupported NAND command: 0x%x\n", c);
    }

    ASSERT(lat < ONE_SEC_CYCLES);

    return lat;
}

/* update SSD status about one page from PG_VALID -> PG_VALID */
static void mark_page_invalid(struct ssd *ssd, ppa_t *ppa)
{
    struct line_mgmt *lm = &ssd->lm;
    struct ssdparams *spp = &ssd->sp;
    struct nand_block *blk = NULL;
    struct nand_page *pg = NULL;
    bool was_full_line = false;
    struct line *line;

    /* update corresponding page status */
    pg = get_pg(ssd, ppa);
    ftl_assert(pg->status == PG_VALID);
    pg->status = PG_INVALID;

    /* update corresponding block status */
    blk = get_blk(ssd, ppa);
    ftl_assert(blk->ipc >= 0 && blk->ipc < spp->pgs_per_blk);
    blk->ipc++;
    ftl_assert(blk->vpc > 0 && blk->vpc <= spp->pgs_per_blk);
    blk->vpc--;

    /* update corresponding line status */
    line = get_line(ssd, ppa);
    ftl_assert(line->ipc >= 0 && line->ipc < line->avail_pg_cnt);
    if (line->vpc == line->avail_pg_cnt) {
        ftl_assert(line->ipc == 0);
        was_full_line = true;
    }
    line->ipc++;
    ftl_assert(line->vpc > 0 && line->vpc <= line->avail_pg_cnt);
    /* Adjust the position of the victime line in the pq under over-writes */
    if (line->pos) {
        /* Note that line->vpc will be updated by this call */
        pqueue_change_priority(lm->victim_line_pq, line->vpc - 1, line);
    } else {
        line->vpc--;
    }

    if (was_full_line) {
        /* move line: "full" -> "victim" */
        QTAILQ_REMOVE(&lm->full_line_list, line, entry);
        lm->full_line_cnt--;
        pqueue_insert(lm->victim_line_pq, line);
        lm->victim_line_cnt++;
    }
}

static void mark_page_valid(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_block *blk = NULL;
    struct nand_page *pg = NULL;
    struct line *line;

    /* update page status */
    pg = get_pg(ssd, ppa);
    ftl_assert(pg->status == PG_FREE);
    pg->status = PG_VALID;

    /* update corresponding block status */
    blk = get_blk(ssd, ppa);
    ftl_assert(blk->vpc >= 0 && blk->vpc < ssd->sp.pgs_per_blk);
    blk->vpc++;

    /* update corresponding line status */
    line = get_line(ssd, ppa);
    ftl_assert(line->vpc >= 0 && line->vpc < line->avail_pg_cnt);
    line->vpc++;
}

static void mark_block_free(struct ssd *ssd, ppa_t *ppa)
{
    struct ssdparams *spp = &ssd->sp;
    struct nand_block *blk = get_blk(ssd, ppa);
    struct nand_page *pg = NULL;

    for (int i = 0; i < spp->pgs_per_blk; i++) {
        /* reset page status */
        pg = &blk->pg[i];
        pg->status = PG_FREE;
    }

    /* reset block status */
    ftl_assert(blk->npgs == spp->pgs_per_blk);
    blk->ipc = 0;
    blk->vpc = 0;
    // blk->erase_cnt++;
}

static void sync_nand_rw_io_req(struct ssd *ssd)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;

    ASSERT(nrw_req->nr_ios <= nrw_req->max_nr_ios);
    
    while (nrw_req->nr_complete_ios < nrw_req->nr_ios) {
        do_sync_cs_io_req(&nrw_req->nrw_handles[nrw_req->nr_complete_ios]);
        nrw_req->nr_complete_ios++;
    }   
}

// TODO: use real ppa
#define NAND_GC_IO_ADDR 0x514 //sector addr

// no callback is implemented for nand gc IO requests, 
// must call `sync_nand_rw_io_req` to schedule them
static void do_gc_read_page(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;
    uint64_t offset = NAND_GC_IO_ADDR*ssd->sp.pgsz;
    if(CONFIG_ACCESS_EXACT_PPA)
        offset = ppa2pgidx(ssd, ppa) * ssd->sp.pgsz;
    nrw_req->nrw_handles[nrw_req->nr_ios] =  read_from_storage(nand_gc_io_buf, 
            offset, ssd->sp.pgsz, NULL, nrw_req);
    nrw_req->nr_ios++;

    if(CONFIG_ACCESS_EXACT_PPA)
        sync_nand_rw_io_req(ssd);
}

static void do_gc_write_page(struct ssd *ssd, ppa_t *ppa)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;
    uint64_t offset = NAND_GC_IO_ADDR*ssd->sp.pgsz;
    if(CONFIG_ACCESS_EXACT_PPA)
        offset = ppa2pgidx(ssd, ppa) * ssd->sp.pgsz;
    nrw_req->nrw_handles[nrw_req->nr_ios] =  write_to_storage(nand_gc_io_buf, 
            offset, ssd->sp.pgsz, NULL, nrw_req);
    nrw_req->nr_ios++;

    if(CONFIG_ACCESS_EXACT_PPA)
        sync_nand_rw_io_req(ssd);
}

static void gc_read_page(struct ssd *ssd, ppa_t *ppa)
{
    /* advance ssd status, we don't care about how long it takes */
    if (ssd->sp.enable_gc_delay) {
        struct nand_cmd gcr;
        gcr.type = GC_IO;
        gcr.cmd = NAND_READ;
        gcr.stime = 0;
        ssd_advance_status(ssd, ppa, &gcr);
    }
    do_gc_read_page(ssd, ppa);

    ssd->stat->nand_gc_read_bytes += ssd->sp.pgsz;
    ssd->stat->nand_read_bytes += ssd->sp.pgsz;
}

/* move valid page data (already in DRAM) from victim line to a new page */
static uint64_t gc_write_page(struct ssd *ssd, ppa_t *old_ppa)
{
    ppa_t new_ppa;
    struct nand_lun *new_lun;
    uint64_t lpn = get_rmap_ent(ssd, old_ppa);

    ftl_assert(valid_lpn(ssd, lpn));
    new_ppa = get_new_page(ssd);
    /* update maptbl */
    set_maptbl_ent(ssd, lpn, &new_ppa);
    /* update rmap */
    set_rmap_ent(ssd, lpn, &new_ppa);

    mark_page_valid(ssd, &new_ppa);

    /* need to advance the write pointer here */
    ssd_advance_write_pointer(ssd);

    if (ssd->sp.enable_gc_delay) {
        struct nand_cmd gcw;
        gcw.type = GC_IO;
        gcw.cmd = NAND_WRITE;
        gcw.stime = 0;
        ssd_advance_status(ssd, &new_ppa, &gcw);
    }

    /* advance per-ch gc_endtime as well */
#if 0
    new_ch = get_ch(ssd, &new_ppa);
    new_ch->gc_endtime = new_ch->next_ch_avail_time;
#endif

    new_lun = get_lun(ssd, &new_ppa);
    new_lun->gc_endtime = new_lun->next_lun_avail_time;

    do_gc_write_page(ssd, &new_ppa);

    ssd->stat->nand_gc_write_bytes += ssd->sp.pgsz;
    ssd->stat->nand_write_bytes += ssd->sp.pgsz;

    return 0;
}

static struct line *select_victim_line(struct ssd *ssd, bool force)
{
    struct line_mgmt *lm = &ssd->lm;
    struct line *victim_line = NULL;

    victim_line = pqueue_peek(lm->victim_line_pq);
    if (!victim_line) {
        return NULL;
    }

    if (!force && victim_line->ipc < victim_line->avail_pg_cnt / 8) {
        return NULL;
    }

    pqueue_pop(lm->victim_line_pq);
    victim_line->pos = 0;
    lm->victim_line_cnt--;

    /* victim_line is a danggling node now */
    return victim_line;
}

/* here ppa identifies the block we want to clean */
static void clean_one_block(struct ssd *ssd, ppa_t *ppa)
{
    struct ssdparams *spp = &ssd->sp;
    struct nand_page *pg_iter = NULL;
    int cnt = 0;

    for (int pg = 0; pg < spp->pgs_per_blk; pg++) {
        ppa->g.pg = pg;
        pg_iter = get_pg(ssd, ppa);
        /* there shouldn't be any free page in victim blocks */
        ftl_assert(pg_iter->status != PG_FREE);
        if (pg_iter->status == PG_VALID) {
            gc_read_page(ssd, ppa);
            /* delay the maptbl update until "write" happens */
            gc_write_page(ssd, ppa);
            cnt++;
        }
    }

    ftl_assert(get_blk(ssd, ppa)->vpc == cnt);
}

static void mark_line_free(struct ssd *ssd, ppa_t *ppa)
{
    struct line_mgmt *lm = &ssd->lm;
    struct line *line = get_line(ssd, ppa);
    line->ipc = 0;
    line->vpc = 0;
    /* move this line to free line list */
    QTAILQ_INSERT_TAIL(&lm->free_line_list, line, entry);
    lm->free_line_cnt++;
}

static int do_gc(struct ssd *ssd, bool force)
{
    struct line *victim_line = NULL;
    struct ssdparams *spp = &ssd->sp;
    struct nand_lun *lunp;
    ppa_t ppa;
    int ch, lun;
    int line_blk_idx = 0;

    
    victim_line = select_victim_line(ssd, force);
    if (!victim_line) {
        return -1;
    }

    ftl_assert(victim_line->avail_blk_cnt);

    ppa.g.blk = victim_line->id;
    ftl_debug("GC-ing line:%d,ipc=%d,victim=%d,full=%d,free=%d\n", ppa.g.blk,
              victim_line->ipc, ssd->lm.victim_line_cnt, ssd->lm.full_line_cnt,
              ssd->lm.free_line_cnt);

    ssd_reset_nrw_req(ssd);
    /* copy back valid data */
    for (ch = 0; ch < spp->nchs; ch++) {
        for (lun = 0; lun < spp->luns_per_ch; lun++) {
            if(line_blk_idx >= victim_line->avail_blk_cnt)
                break;
            ppa.g.ch = ch;
            ppa.g.lun = lun;
            ppa.g.pl = 0;
            lunp = get_lun(ssd, &ppa);
            clean_one_block(ssd, &ppa);
            mark_block_free(ssd, &ppa);

            if (spp->enable_gc_delay) {
                struct nand_cmd gce;
                gce.type = GC_IO;
                gce.cmd = NAND_ERASE;
                gce.stime = 0;
                ssd_advance_status(ssd, &ppa, &gce);
            }

            lunp->gc_endtime = lunp->next_lun_avail_time;
            line_blk_idx++;
        }
    }

    /* update line status */
    mark_line_free(ssd, &ppa);

    sync_nand_rw_io_req(ssd);

    ssd->stat->nand_gc_cnt ++;

    return 0;
}

static void do_read_page(struct ssd *ssd, uint64_t pgidx, uint8_t *buf, uint64_t offset, bool sync)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;
    uint64_t addr = pgidx * ssd->sp.pgsz;
    nrw_req->nrw_handles[nrw_req->nr_ios] = read_from_storage(buf + offset, addr, ssd->sp.pgsz, NULL, nrw_req);
    nrw_req->nr_ios++;

    if(sync)
        sync_nand_rw_io_req(ssd);
}

static void do_write_page(struct ssd *ssd, uint64_t pgidx, uint8_t *buf, uint64_t offset, bool sync)
{
    struct nand_rw_io_req *nrw_req = ssd->nrw_req;
    uint64_t addr = pgidx * ssd->sp.pgsz;
    nrw_req->nrw_handles[nrw_req->nr_ios] = write_to_storage(buf + offset, addr, ssd->sp.pgsz, NULL, nrw_req);
    nrw_req->nr_ios++;

    if(sync)
        sync_nand_rw_io_req(ssd);
}

// TODO: support page size!= sector size
uint64_t ssd_read(struct ssd *ssd, struct host_io_req *req, uint64_t stime)
{
    struct ssdparams *spp = &ssd->sp;
    uint64_t lba = req->slba;
    int nsecs = req->nlb;
    ppa_t ppa;
    uint64_t start_lpn = lba / spp->secs_per_pg;
    uint64_t end_lpn = (lba + nsecs - 1) / spp->secs_per_pg;
    uint64_t lpn;
    uint64_t sublat, maxlat = 0;

    if(!should_use_interval_mapping(ssd)){
        ASSERT(end_lpn < SSD_SIZE / 4096);
        if (end_lpn >= spp->tt_pgs) 
            ftl_err("start_lpn=%lu,tt_pgs=%d\n", start_lpn, ssd->sp.tt_pgs);
    }


    ssd_reset_nrw_req(ssd);

    /* normal IO read path */
    for (lpn = start_lpn; lpn <= end_lpn; lpn++) {
        uint64_t pgidx;
        if(lpn_should_passthru(ssd, lpn)){
            pgidx = lpn;
        }else{
            ppa = get_maptbl_ent(ssd, lpn);
            if (!mapped_ppa(&ppa) || !valid_ppa(ssd, &ppa)) {
                //printf("%s,lpn(%" PRId64 ") not mapped to valid ppa\n", ssd->ssdname, lpn);
                //printf("Invalid ppa,ch:%d,lun:%d,blk:%d,pl:%d,pg:%d,sec:%d\n",
                //ppa.g.ch, ppa.g.lun, ppa.g.blk, ppa.g.pl, ppa.g.pg, ppa.g.sec);
                continue;
            }
            pgidx = ppa2pgidx(ssd, &ppa);
        }

        if(CONFIG_ACCESS_EXACT_PPA)
            do_read_page(ssd, pgidx, req->dma_buf_ent->buf, (lpn-start_lpn)*ssd->sp.pgsz, false);
        
        struct nand_cmd srd;
        srd.type = USER_IO;
        srd.cmd = NAND_READ;
        srd.stime = stime;
        sublat = ssd_advance_status(ssd, &ppa, &srd);
        maxlat = (sublat > maxlat) ? sublat : maxlat;

        ssd->stat->nand_read_bytes += ssd->sp.pgsz;
    }

    if(CONFIG_ACCESS_EXACT_PPA)
        sync_nand_rw_io_req(ssd);

    // if (should_gc(ssd))
    //     do_gc(ssd, false);

    return maxlat;
}

// TODO: support page size!= sector size
uint64_t ssd_write(struct ssd *ssd, struct host_io_req *req, uint64_t stime)
{
    uint64_t lba = req->slba;
    struct ssdparams *spp = &ssd->sp;
    int len = req->nlb;
    uint64_t start_lpn = lba / spp->secs_per_pg;
    uint64_t end_lpn = (lba + len - 1) / spp->secs_per_pg;
    ppa_t ppa;
    uint64_t lpn;
    uint64_t curlat = 0, maxlat = 0;
    int r;

    if(!should_use_interval_mapping(ssd)){
        ASSERT(end_lpn < SSD_SIZE / 4096);
        if (end_lpn >= spp->tt_pgs)
            ftl_err("start_lpn=%lu,tt_pgs=%d\n", start_lpn, ssd->sp.tt_pgs);
    }

    while (should_gc_high(ssd)) {
        /* perform GC here until !should_gc(ssd) */
        r = do_gc(ssd, true);
        if (r == -1)
            break;
    }

    ssd_reset_nrw_req(ssd);

    for (lpn = start_lpn; lpn <= end_lpn; lpn++) {
        uint64_t pgidx;
        if(lpn_should_passthru(ssd, lpn)){
            pgidx = lpn;
        }else{
            ppa = get_maptbl_ent(ssd, lpn);
            // mapped ppa can be invalid because of dsm
            if (mapped_ppa(&ppa) && get_pg(ssd, &ppa)->status == PG_VALID) {
                if(should_use_interval_mapping(ssd))
                    mapseg_remove(lpn, false);
                /* update old page information first */
                mark_page_invalid(ssd, &ppa);
                set_rmap_ent(ssd, INVALID_LPN, &ppa);
            }

            /* new write */
            ppa = get_new_page(ssd);
            pgidx = ppa2pgidx(ssd, &ppa);
            /* update maptbl */
            set_maptbl_ent(ssd, lpn, &ppa);
            /* update rmap */
            set_rmap_ent(ssd, lpn, &ppa);

            mark_page_valid(ssd, &ppa);

            /* need to advance the write pointer here */
            ssd_advance_write_pointer(ssd);
        }

        if(CONFIG_ACCESS_EXACT_PPA)
            do_write_page(ssd, pgidx, req->dma_buf_ent->buf, (lpn-start_lpn)*ssd->sp.pgsz, false);

        struct nand_cmd swr;
        swr.type = USER_IO;
        swr.cmd = NAND_WRITE;
        swr.stime = stime;
        /* get latency statistics */
        curlat = ssd_advance_status(ssd, &ppa, &swr);
        maxlat = (curlat > maxlat) ? curlat : maxlat;

        ssd->stat->nand_write_bytes += ssd->sp.pgsz;
    }

    if(CONFIG_ACCESS_EXACT_PPA)
        sync_nand_rw_io_req(ssd);

    // if (should_gc(ssd))
    //     do_gc(ssd, false);

    return maxlat;
}

// should not exist, a patch to allow cs worker read IPU area and bypass the ssd_read function
// TODO: unify the ssd read/write API with f2fs_read/write_block used by CS 
uint64_t ssd_get_pba(struct ssd *ssd, uint64_t lba)
{
    ppa_t ppa;
    uint64_t pba;
    if(lba_should_passthru(ssd, lba))
        return lba;
    ppa = get_maptbl_ent(ssd, lba);
    pba = ppa2pgidx(ssd, &ppa);
    ftl_assert(pba < ssd->sp.tt_avail_secs);
    return pba;
}

uint64_t ssd_dsm(struct ssd *ssd, struct host_io_req *req, uint64_t stime)
{
    struct ssdparams *spp = &ssd->sp;
    uint64_t tmp_lpn, start_lpn, startOffset, end_lpn, endOffset;
    ppa_t ppa;

    dsm_range_t *dsmRange = (dsm_range_t *) req->dma_buf_ent->buf;
    for (int i = 0; i < req->dsm_i.numRanges; i++, dsmRange++)
    {
        if(dsmRange->ContextAttributes != 0){
            xil_printf("ftl core: req=0x%p, buf_ent=0x%p, buf=0x%p\n", 
                req, req->dma_buf_ent, req->dma_buf_ent->buf);
            xil_printf("[%d] ctx=0x%x, len=0x%x, lba=0x%lx\n", i, dsmRange->ContextAttributes, dsmRange->lengthInLogicalBlocks, dsmRange->startingLBA[0]);
            ASSERT(0);
        }
        // only works when secs_per_pg is 1
        start_lpn = dsmRange->startingLBA[0] / spp->secs_per_pg;
        // startOffset = dsmRange->startingLBA[0] % spp->secs_per_pg;
        end_lpn = (dsmRange->startingLBA[0] + dsmRange->lengthInLogicalBlocks) / spp->secs_per_pg;
        // endOffset = (dsmRange->startingLBA[0] + dsmRange->lengthInLogicalBlocks) % spp->secs_per_pg;

		for (tmp_lpn = start_lpn; tmp_lpn < end_lpn; tmp_lpn++) {
            if(lpn_should_passthru(ssd, tmp_lpn))
                continue;

            ppa = get_maptbl_ent(ssd, tmp_lpn);
            if (mapped_ppa(&ppa) && get_pg(ssd, &ppa)->status == PG_VALID)
            {
                if(should_use_interval_mapping(ssd))
                    mapseg_remove(tmp_lpn, tmp_lpn==end_lpn-1);
                mark_page_invalid(ssd, &ppa);
                m->ssd_stat.nand_page_dsm_cnt++;
                set_rmap_ent(ssd, INVALID_LPN, &ppa);
            }
        }
    }

    return 0;
}

void ssd_test_interval_mapping(struct ssd *ssd)
{
    struct host_io_req req;
    struct data_buffer_qent buf_ent;
    unsigned int lba, nlb = 16;
    uint64_t *buffer_w, *buffer_r;
    ppa_t ppa;

    ssd->cfg.l2p_mapping_type = L2P_MAPPING_INTERVAL;
    ssd->cfg.dsm_enabled = 1;
    ssd->cfg.nand_latency_emu_enabled = 0;
    
    ssd_init(ssd, true);

    // buffer_w = linear_malloc(&m->dma_noncache_allocator, nlb * 4096, 0);
    // buffer_r = linear_malloc(&m->dma_noncache_allocator, nlb * 4096, 0);
    buffer_w = linear_malloc(&ssd_allocator, nlb * 4096, 0);
    buffer_r = linear_malloc(&ssd_allocator, nlb * 4096, 0);
    req.dma_buf_ent = &buf_ent;
    req.dma_buf_ent->size = nlb * 4096;


    for(unsigned int msb = 0; msb < 8; msb++){
        xil_printf("msb = %u\n", msb);
        linear_allocator_get_mem_usage(&ssd_allocator, true);
        for(unsigned long i = 0; i < 6 * 256 * 1024; i+=nlb){
            if(i % (256*1024) == 0) // 1GB
                xil_printf("passed %u GB\n", i * 4096 / (1024*1024*1024));
            // prepare data
            for(int blk_idx = 0; blk_idx < nlb; blk_idx ++){
                uint64_t *tmp_buf = (uint64_t *)((uint8_t *)buffer_w + 4096 * blk_idx);
                for(int j = 0; j < 4096 / sizeof(uint64_t); j++){
                    tmp_buf[j] = ((i + blk_idx) << 32) | j;
                }
            }
            // write
            lba = (msb << 27) + i;
            req.slba = lba;
            req.nlb = nlb;
            req.dma_buf_ent->buf = (uint8_t *) buffer_w;
            FLUSH_CACHE(buffer_w, nlb*4096);
            ssd_write(ssd, &req, 0);

            // mapseg_get_mapping(lba);

            //read 
            req.dma_buf_ent->buf = (uint8_t *) buffer_r;
            FLUSH_CACHE(buffer_r, nlb*4096);
            ssd_read(ssd, &req, 0);
            FLUSH_CACHE(buffer_r, nlb*4096);

            //verify
            for(int blk_idx = 0; blk_idx < nlb; blk_idx ++){
                uint64_t *tmp_buf = (uint64_t *)((uint8_t *)buffer_r + 4096 * blk_idx);
                for(int j = 0; j < 4096 / sizeof(uint64_t); j++){
                    if(tmp_buf[j] != (((i + blk_idx) << 32) | j)){
                        xil_printf("data mismatch at lba %lu, blk_idx %d, j %d, expected %lx(%lx), got %lx\n", lba, blk_idx, j, ((i + blk_idx) << 32) | j, buffer_w[4096 * blk_idx/8 + j], tmp_buf[j]);
                        ASSERT(0);
                    }
                }
            }
        }
    }
    linear_malloc_reset(&ssd_allocator);
}