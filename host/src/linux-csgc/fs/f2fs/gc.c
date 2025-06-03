// SPDX-License-Identifier: GPL-2.0
/*
 * fs/f2fs/gc.c
 *
 * Copyright (c) 2012 Samsung Electronics Co., Ltd.
 *             http://www.samsung.com/
 */
#include <linux/fs.h>
#include <linux/module.h>
#include <linux/init.h>
#include <linux/f2fs_fs.h>
#include <linux/kthread.h>
#include <linux/delay.h>
#include <linux/freezer.h>
#include <linux/sched/signal.h>
#include <linux/random.h>
#include <linux/sched/mm.h>

#include "f2fs.h"
#include "node.h"
#include "segment.h"
#include "gc.h"
#include "iostat.h"
#include <trace/events/f2fs.h>

static struct kmem_cache *victim_entry_slab;
extern struct kmem_cache *f2fs_page_entry_slab;
extern struct kmem_cache *f2fs_dnode_entry_slab;
extern struct kmem_cache *f2fs_data_entry_slab;
extern struct kmem_cache *f2fs_folio_entry_slab;

static unsigned int count_bits(const unsigned long *addr,
				unsigned int offset, unsigned int len);

static int gc_thread_func(void *data)
{
	struct f2fs_sb_info *sbi = data;
	struct f2fs_gc_kthread *gc_th = sbi->gc_thread;
	wait_queue_head_t *wq = &sbi->gc_thread->gc_wait_queue_head;
	wait_queue_head_t *fggc_wq = &sbi->gc_thread->fggc_wq;
	unsigned int wait_ms;
	struct f2fs_gc_control gc_control = {
		.victim_segno = NULL_SEGNO,
		.should_migrate_blocks = false,
		.err_gc_skipped = false };

	wait_ms = gc_th->min_sleep_time;

	set_freezable();
	do {
		bool sync_mode, foreground = false;

		wait_event_interruptible_timeout(*wq,
				kthread_should_stop() || freezing(current) ||
				waitqueue_active(fggc_wq) ||
				gc_th->gc_wake,
				msecs_to_jiffies(wait_ms));

		if (test_opt(sbi, GC_MERGE) && waitqueue_active(fggc_wq))
			foreground = true;

		/* give it a try one time */
		if (gc_th->gc_wake)
			gc_th->gc_wake = 0;

		if (try_to_freeze()) {
			stat_other_skip_bggc_count(sbi);
			continue;
		}
		if (kthread_should_stop())
			break;

		if (sbi->sb->s_writers.frozen >= SB_FREEZE_WRITE) {
			increase_sleep_time(gc_th, &wait_ms);
			stat_other_skip_bggc_count(sbi);
			continue;
		}

		if (time_to_inject(sbi, FAULT_CHECKPOINT)) {
			f2fs_show_injection_info(sbi, FAULT_CHECKPOINT);
			f2fs_stop_checkpoint(sbi, false,
					STOP_CP_REASON_FAULT_INJECT);
		}

		if (!sb_start_write_trylock(sbi->sb)) {
			stat_other_skip_bggc_count(sbi);
			continue;
		}

		/*
		 * [GC triggering condition]
		 * 0. GC is not conducted currently.
		 * 1. There are enough dirty segments.
		 * 2. IO subsystem is idle by checking the # of writeback pages.
		 * 3. IO subsystem is idle by checking the # of requests in
		 *    bdev's request list.
		 *
		 * Note) We have to avoid triggering GCs frequently.
		 * Because it is possible that some segments can be
		 * invalidated soon after by user update or deletion.
		 * So, I'd like to wait some time to collect dirty segments.
		 */
		if (sbi->gc_mode == GC_URGENT_HIGH ||
				sbi->gc_mode == GC_URGENT_MID) {
			wait_ms = gc_th->urgent_sleep_time;
			f2fs_down_write(&sbi->gc_lock);
			goto do_gc;
		}

		if (foreground) {
			f2fs_down_write(&sbi->gc_lock);
			goto do_gc;
		} else if (!f2fs_down_write_trylock(&sbi->gc_lock)) {
			stat_other_skip_bggc_count(sbi);
			goto next;
		}

		if (!is_idle(sbi, GC_TIME)) {
			increase_sleep_time(gc_th, &wait_ms);
			f2fs_up_write(&sbi->gc_lock);
			stat_io_skip_bggc_count(sbi);
			goto next;
		}

		if (has_enough_invalid_blocks(sbi))
			decrease_sleep_time(gc_th, &wait_ms);
		else
			increase_sleep_time(gc_th, &wait_ms);
do_gc:
		if (!foreground)
			stat_inc_bggc_count(sbi->stat_info);

		sync_mode = F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_SYNC;

		/* foreground GC was been triggered via f2fs_balance_fs() */
		if (foreground)
			sync_mode = false;

		gc_control.init_gc_type = sync_mode ? FG_GC : BG_GC;
		gc_control.no_bg_gc = foreground;
		gc_control.nr_free_secs = foreground ? 1 : 0;

		/* if return value is not zero, no victim was selected */
		if (f2fs_gc(sbi, &gc_control)) {
			/* don't bother wait_ms by foreground gc */
			if (!foreground)
				wait_ms = gc_th->no_gc_sleep_time;
		}

		if (foreground)
			wake_up_all(&gc_th->fggc_wq);

		trace_f2fs_background_gc(sbi->sb, wait_ms,
				prefree_segments(sbi), free_segments(sbi));

		/* balancing f2fs's metadata periodically */
		f2fs_balance_fs_bg(sbi, true);
next:
		if (sbi->gc_mode == GC_URGENT_HIGH) {
			spin_lock(&sbi->gc_urgent_high_lock);
			if (sbi->gc_urgent_high_remaining) {
				sbi->gc_urgent_high_remaining--;
				if (!sbi->gc_urgent_high_remaining)
					sbi->gc_mode = GC_NORMAL;
			}
			spin_unlock(&sbi->gc_urgent_high_lock);
		}
		sb_end_write(sbi->sb);

	} while (!kthread_should_stop());
	return 0;
}

int f2fs_start_gc_thread(struct f2fs_sb_info *sbi)
{
	struct f2fs_gc_kthread *gc_th;
	dev_t dev = sbi->sb->s_bdev->bd_dev;
	int err = 0;

	gc_th = f2fs_kmalloc(sbi, sizeof(struct f2fs_gc_kthread), GFP_KERNEL);
	if (!gc_th) {
		err = -ENOMEM;
		goto out;
	}

	gc_th->urgent_sleep_time = DEF_GC_THREAD_URGENT_SLEEP_TIME;
	gc_th->min_sleep_time = DEF_GC_THREAD_MIN_SLEEP_TIME;
	gc_th->max_sleep_time = DEF_GC_THREAD_MAX_SLEEP_TIME;
	gc_th->no_gc_sleep_time = DEF_GC_THREAD_NOGC_SLEEP_TIME;

	gc_th->gc_wake = 0;

	sbi->gc_thread = gc_th;
	init_waitqueue_head(&sbi->gc_thread->gc_wait_queue_head);
	init_waitqueue_head(&sbi->gc_thread->fggc_wq);
	sbi->gc_thread->f2fs_gc_task = kthread_run(gc_thread_func, sbi,
			"f2fs_gc-%u:%u", MAJOR(dev), MINOR(dev));
	if (IS_ERR(gc_th->f2fs_gc_task)) {
		err = PTR_ERR(gc_th->f2fs_gc_task);
		kfree(gc_th);
		sbi->gc_thread = NULL;
	}
out:
	return err;
}

void f2fs_stop_gc_thread(struct f2fs_sb_info *sbi)
{
	struct f2fs_gc_kthread *gc_th = sbi->gc_thread;

	if (!gc_th)
		return;
	kthread_stop(gc_th->f2fs_gc_task);
	wake_up_all(&gc_th->fggc_wq);
	kfree(gc_th);
	sbi->gc_thread = NULL;
}

static int select_gc_type(struct f2fs_sb_info *sbi, int gc_type)
{
	int gc_mode;

	if (gc_type == BG_GC) {
		if (sbi->am.atgc_enabled)
			gc_mode = GC_AT;
		else
			gc_mode = GC_CB;
	} else {
		gc_mode = GC_GREEDY;
	}

	switch (sbi->gc_mode) {
	case GC_IDLE_CB:
		gc_mode = GC_CB;
		break;
	case GC_IDLE_GREEDY:
	case GC_URGENT_HIGH:
		gc_mode = GC_GREEDY;
		break;
	case GC_IDLE_AT:
		gc_mode = GC_AT;
		break;
	}

	return gc_mode;
}

static void select_policy(struct f2fs_sb_info *sbi, int gc_type,
			int type, struct victim_sel_policy *p)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);

	if (p->alloc_mode == SSR) {
		p->gc_mode = GC_GREEDY;
		p->dirty_bitmap = dirty_i->dirty_segmap[type];
		p->max_search = dirty_i->nr_dirty[type];
		p->ofs_unit = 1;
	} else if (p->alloc_mode == AT_SSR) {
		p->gc_mode = GC_GREEDY;
		p->dirty_bitmap = dirty_i->dirty_segmap[type];
		p->max_search = dirty_i->nr_dirty[type];
		p->ofs_unit = 1;
	} else {
		p->gc_mode = select_gc_type(sbi, gc_type);
		p->ofs_unit = sbi->segs_per_sec;
		if (__is_large_section(sbi)) {
			p->dirty_bitmap = dirty_i->dirty_secmap;
			p->max_search = count_bits(p->dirty_bitmap,
						0, MAIN_SECS(sbi));
		} else {
			p->dirty_bitmap = dirty_i->dirty_segmap[DIRTY];
			p->max_search = dirty_i->nr_dirty[DIRTY];
		}
	}

	/*
	 * adjust candidates range, should select all dirty segments for
	 * foreground GC and urgent GC cases.
	 */
	if (gc_type != FG_GC &&
			(sbi->gc_mode != GC_URGENT_HIGH) &&
			(p->gc_mode != GC_AT && p->alloc_mode != AT_SSR) &&
			p->max_search > sbi->max_victim_search)
		p->max_search = sbi->max_victim_search;

	/* let's select beginning hot/small space first in no_heap mode*/
	if (f2fs_need_rand_seg(sbi))
		p->offset = prandom_u32_max(MAIN_SECS(sbi) * sbi->segs_per_sec);
	else if (test_opt(sbi, NOHEAP) &&
		(type == CURSEG_HOT_DATA || IS_NODESEG(type)))
		p->offset = 0;
	else
		p->offset = SIT_I(sbi)->last_victim[p->gc_mode];
}

static unsigned int get_max_cost(struct f2fs_sb_info *sbi,
				struct victim_sel_policy *p)
{
	/* SSR allocates in a segment unit */
	if (p->alloc_mode == SSR)
		return sbi->blocks_per_seg;
	else if (p->alloc_mode == AT_SSR)
		return UINT_MAX;

	/* LFS */
	if (p->gc_mode == GC_GREEDY)
		return 2 * sbi->blocks_per_seg * p->ofs_unit;
	else if (p->gc_mode == GC_CB)
		return UINT_MAX;
	else if (p->gc_mode == GC_AT)
		return UINT_MAX;
	else /* No other gc_mode */
		return 0;
}

static unsigned int check_bg_victims(struct f2fs_sb_info *sbi)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);
	unsigned int secno;

	/*
	 * If the gc_type is FG_GC, we can select victim segments
	 * selected by background GC before.
	 * Those segments guarantee they have small valid blocks.
	 */
	for_each_set_bit(secno, dirty_i->victim_secmap, MAIN_SECS(sbi)) {
		if (sec_usage_check(sbi, secno))
			continue;
		clear_bit(secno, dirty_i->victim_secmap);
		return GET_SEG_FROM_SEC(sbi, secno);
	}
	return NULL_SEGNO;
}

static unsigned int get_cb_cost(struct f2fs_sb_info *sbi, unsigned int segno)
{
	struct sit_info *sit_i = SIT_I(sbi);
	unsigned int secno = GET_SEC_FROM_SEG(sbi, segno);
	unsigned int start = GET_SEG_FROM_SEC(sbi, secno);
	unsigned long long mtime = 0;
	unsigned int vblocks;
	unsigned char age = 0;
	unsigned char u;
	unsigned int i;
	unsigned int usable_segs_per_sec = f2fs_usable_segs_in_sec(sbi, segno);

	for (i = 0; i < usable_segs_per_sec; i++)
		mtime += get_seg_entry(sbi, start + i)->mtime;
	vblocks = get_valid_blocks(sbi, segno, true);

	mtime = div_u64(mtime, usable_segs_per_sec);
	vblocks = div_u64(vblocks, usable_segs_per_sec);

	u = (vblocks * 100) >> sbi->log_blocks_per_seg;

	/* Handle if the system time has changed by the user */
	if (mtime < sit_i->min_mtime)
		sit_i->min_mtime = mtime;
	if (mtime > sit_i->max_mtime)
		sit_i->max_mtime = mtime;
	if (sit_i->max_mtime != sit_i->min_mtime)
		age = 100 - div64_u64(100 * (mtime - sit_i->min_mtime),
				sit_i->max_mtime - sit_i->min_mtime);

	return UINT_MAX - ((100 * (100 - u) * age) / (100 + u));
}

static inline unsigned int get_gc_cost(struct f2fs_sb_info *sbi,
			unsigned int segno, struct victim_sel_policy *p)
{
	if (p->alloc_mode == SSR)
		return get_seg_entry(sbi, segno)->ckpt_valid_blocks;

	/* alloc_mode == LFS */
	if (p->gc_mode == GC_GREEDY)
		return get_valid_blocks(sbi, segno, true);
	else if (p->gc_mode == GC_CB)
		return get_cb_cost(sbi, segno);

	f2fs_bug_on(sbi, 1);
	return 0;
}

static unsigned int count_bits(const unsigned long *addr,
				unsigned int offset, unsigned int len)
{
	unsigned int end = offset + len, sum = 0;

	while (offset < end) {
		if (test_bit(offset++, addr))
			++sum;
	}
	return sum;
}

static bool f2fs_check_victim_tree(struct f2fs_sb_info *sbi,
				struct rb_root_cached *root)
{
#ifdef CONFIG_F2FS_CHECK_FS
	struct rb_node *cur = rb_first_cached(root), *next;
	struct victim_entry *cur_ve, *next_ve;

	while (cur) {
		next = rb_next(cur);
		if (!next)
			return true;

		cur_ve = rb_entry(cur, struct victim_entry, rb_node);
		next_ve = rb_entry(next, struct victim_entry, rb_node);

		if (cur_ve->mtime > next_ve->mtime) {
			f2fs_info(sbi, "broken victim_rbtree, "
				"cur_mtime(%llu) next_mtime(%llu)",
				cur_ve->mtime, next_ve->mtime);
			return false;
		}
		cur = next;
	}
#endif
	return true;
}

static struct victim_entry *__lookup_victim_entry(struct f2fs_sb_info *sbi,
					unsigned long long mtime)
{
	struct atgc_management *am = &sbi->am;
	struct rb_node *node = am->root.rb_root.rb_node;
	struct victim_entry *ve = NULL;

	while (node) {
		ve = rb_entry(node, struct victim_entry, rb_node);

		if (mtime < ve->mtime)
			node = node->rb_left;
		else
			node = node->rb_right;
	}
	return ve;
}

static struct victim_entry *__create_victim_entry(struct f2fs_sb_info *sbi,
		unsigned long long mtime, unsigned int segno)
{
	struct atgc_management *am = &sbi->am;
	struct victim_entry *ve;

	ve =  f2fs_kmem_cache_alloc(victim_entry_slab, GFP_NOFS, true, NULL);

	ve->mtime = mtime;
	ve->segno = segno;

	list_add_tail(&ve->list, &am->victim_list);
	am->victim_count++;

	return ve;
}

static void __insert_victim_entry(struct f2fs_sb_info *sbi,
				unsigned long long mtime, unsigned int segno)
{
	struct atgc_management *am = &sbi->am;
	struct rb_root_cached *root = &am->root;
	struct rb_node **p = &root->rb_root.rb_node;
	struct rb_node *parent = NULL;
	struct victim_entry *ve;
	bool left_most = true;

	/* look up rb tree to find parent node */
	while (*p) {
		parent = *p;
		ve = rb_entry(parent, struct victim_entry, rb_node);

		if (mtime < ve->mtime) {
			p = &(*p)->rb_left;
		} else {
			p = &(*p)->rb_right;
			left_most = false;
		}
	}

	ve = __create_victim_entry(sbi, mtime, segno);

	rb_link_node(&ve->rb_node, parent, p);
	rb_insert_color_cached(&ve->rb_node, root, left_most);
}

static void add_victim_entry(struct f2fs_sb_info *sbi,
				struct victim_sel_policy *p, unsigned int segno)
{
	struct sit_info *sit_i = SIT_I(sbi);
	unsigned int secno = GET_SEC_FROM_SEG(sbi, segno);
	unsigned int start = GET_SEG_FROM_SEC(sbi, secno);
	unsigned long long mtime = 0;
	unsigned int i;

	if (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED))) {
		if (p->gc_mode == GC_AT &&
			get_valid_blocks(sbi, segno, true) == 0)
			return;
	}

	for (i = 0; i < sbi->segs_per_sec; i++)
		mtime += get_seg_entry(sbi, start + i)->mtime;
	mtime = div_u64(mtime, sbi->segs_per_sec);

	/* Handle if the system time has changed by the user */
	if (mtime < sit_i->min_mtime)
		sit_i->min_mtime = mtime;
	if (mtime > sit_i->max_mtime)
		sit_i->max_mtime = mtime;
	if (mtime < sit_i->dirty_min_mtime)
		sit_i->dirty_min_mtime = mtime;
	if (mtime > sit_i->dirty_max_mtime)
		sit_i->dirty_max_mtime = mtime;

	/* don't choose young section as candidate */
	if (sit_i->dirty_max_mtime - mtime < p->age_threshold)
		return;

	__insert_victim_entry(sbi, mtime, segno);
}

static void atgc_lookup_victim(struct f2fs_sb_info *sbi,
						struct victim_sel_policy *p)
{
	struct sit_info *sit_i = SIT_I(sbi);
	struct atgc_management *am = &sbi->am;
	struct rb_root_cached *root = &am->root;
	struct rb_node *node;
	struct victim_entry *ve;
	unsigned long long total_time;
	unsigned long long age, u, accu;
	unsigned long long max_mtime = sit_i->dirty_max_mtime;
	unsigned long long min_mtime = sit_i->dirty_min_mtime;
	unsigned int sec_blocks = CAP_BLKS_PER_SEC(sbi);
	unsigned int vblocks;
	unsigned int dirty_threshold = max(am->max_candidate_count,
					am->candidate_ratio *
					am->victim_count / 100);
	unsigned int age_weight = am->age_weight;
	unsigned int cost;
	unsigned int iter = 0;

	if (max_mtime < min_mtime)
		return;

	max_mtime += 1;
	total_time = max_mtime - min_mtime;

	accu = div64_u64(ULLONG_MAX, total_time);
	accu = min_t(unsigned long long, div_u64(accu, 100),
					DEFAULT_ACCURACY_CLASS);

	node = rb_first_cached(root);
next:
	ve = rb_entry_safe(node, struct victim_entry, rb_node);
	if (!ve)
		return;

	if (ve->mtime >= max_mtime || ve->mtime < min_mtime)
		goto skip;

	/* age = 10000 * x% * 60 */
	age = div64_u64(accu * (max_mtime - ve->mtime), total_time) *
								age_weight;

	vblocks = get_valid_blocks(sbi, ve->segno, true);
	f2fs_bug_on(sbi, !vblocks || vblocks == sec_blocks);

	/* u = 10000 * x% * 40 */
	u = div64_u64(accu * (sec_blocks - vblocks), sec_blocks) *
							(100 - age_weight);

	f2fs_bug_on(sbi, age + u >= UINT_MAX);

	cost = UINT_MAX - (age + u);
	iter++;

	if (cost < p->min_cost ||
			(cost == p->min_cost && age > p->oldest_age)) {
		p->min_cost = cost;
		p->oldest_age = age;
		p->min_segno = ve->segno;
	}
skip:
	if (iter < dirty_threshold) {
		node = rb_next(node);
		goto next;
	}
}

/*
 * select candidates around source section in range of
 * [target - dirty_threshold, target + dirty_threshold]
 */
static void atssr_lookup_victim(struct f2fs_sb_info *sbi,
						struct victim_sel_policy *p)
{
	struct sit_info *sit_i = SIT_I(sbi);
	struct atgc_management *am = &sbi->am;
	struct victim_entry *ve;
	unsigned long long age;
	unsigned long long max_mtime = sit_i->dirty_max_mtime;
	unsigned long long min_mtime = sit_i->dirty_min_mtime;
	unsigned int seg_blocks = sbi->blocks_per_seg;
	unsigned int vblocks;
	unsigned int dirty_threshold = max(am->max_candidate_count,
					am->candidate_ratio *
					am->victim_count / 100);
	unsigned int cost, iter;
	int stage = 0;

	if (max_mtime < min_mtime)
		return;
	max_mtime += 1;
next_stage:
	iter = 0;
	ve = __lookup_victim_entry(sbi, p->age);
next_node:
	if (!ve) {
		if (stage++ == 0)
			goto next_stage;
		return;
	}

	if (ve->mtime >= max_mtime || ve->mtime < min_mtime)
		goto skip_node;

	age = max_mtime - ve->mtime;

	vblocks = get_seg_entry(sbi, ve->segno)->ckpt_valid_blocks;
	f2fs_bug_on(sbi, !vblocks);

	/* rare case */
	if (vblocks == seg_blocks)
		goto skip_node;

	iter++;

	age = max_mtime - abs(p->age - age);
	cost = UINT_MAX - vblocks;

	if (cost < p->min_cost ||
			(cost == p->min_cost && age > p->oldest_age)) {
		p->min_cost = cost;
		p->oldest_age = age;
		p->min_segno = ve->segno;
	}
skip_node:
	if (iter < dirty_threshold) {
		ve = rb_entry(stage == 0 ? rb_prev(&ve->rb_node) :
					rb_next(&ve->rb_node),
					struct victim_entry, rb_node);
		goto next_node;
	}

	if (stage++ == 0)
		goto next_stage;
}

static void lookup_victim_by_age(struct f2fs_sb_info *sbi,
						struct victim_sel_policy *p)
{
	f2fs_bug_on(sbi, !f2fs_check_victim_tree(sbi, &sbi->am.root));

	if (p->gc_mode == GC_AT)
		atgc_lookup_victim(sbi, p);
	else if (p->alloc_mode == AT_SSR)
		atssr_lookup_victim(sbi, p);
	else
		f2fs_bug_on(sbi, 1);
}

static void release_victim_entry(struct f2fs_sb_info *sbi)
{
	struct atgc_management *am = &sbi->am;
	struct victim_entry *ve, *tmp;

	list_for_each_entry_safe(ve, tmp, &am->victim_list, list) {
		list_del(&ve->list);
		kmem_cache_free(victim_entry_slab, ve);
		am->victim_count--;
	}

	am->root = RB_ROOT_CACHED;

	f2fs_bug_on(sbi, am->victim_count);
	f2fs_bug_on(sbi, !list_empty(&am->victim_list));
}

static bool f2fs_pin_section(struct f2fs_sb_info *sbi, unsigned int segno)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);
	unsigned int secno = GET_SEC_FROM_SEG(sbi, segno);

	if (!dirty_i->enable_pin_section)
		return false;
	if (!test_and_set_bit(secno, dirty_i->pinned_secmap))
		dirty_i->pinned_secmap_cnt++;
	return true;
}

static bool f2fs_pinned_section_exists(struct dirty_seglist_info *dirty_i)
{
	return dirty_i->pinned_secmap_cnt;
}

static bool f2fs_section_is_pinned(struct dirty_seglist_info *dirty_i,
						unsigned int secno)
{
	return dirty_i->enable_pin_section &&
		f2fs_pinned_section_exists(dirty_i) &&
		test_bit(secno, dirty_i->pinned_secmap);
}

static void f2fs_unpin_all_sections(struct f2fs_sb_info *sbi, bool enable)
{
	unsigned int bitmap_size = f2fs_bitmap_size(MAIN_SECS(sbi));

	if (f2fs_pinned_section_exists(DIRTY_I(sbi))) {
		memset(DIRTY_I(sbi)->pinned_secmap, 0, bitmap_size);
		DIRTY_I(sbi)->pinned_secmap_cnt = 0;
	}
	DIRTY_I(sbi)->enable_pin_section = enable;
}

static int f2fs_gc_pinned_control(struct inode *inode, int gc_type,
							unsigned int segno)
{
	if (!f2fs_is_pinned_file(inode))
		return 0;
	if (gc_type != FG_GC)
		return -EBUSY;
	if (!f2fs_pin_section(F2FS_I_SB(inode), segno))
		f2fs_pin_file_control(inode, true);
	return -EAGAIN;
}

/*
 * This function is called from two paths.
 * One is garbage collection and the other is SSR segment selection.
 * When it is called during GC, it just gets a victim segment
 * and it does not remove it from dirty seglist.
 * When it is called from SSR segment selection, it finds a segment
 * which has minimum valid blocks and removes it from dirty seglist.
 */
static int get_victim_by_default(struct f2fs_sb_info *sbi,
			unsigned int *result, int gc_type, int type,
			char alloc_mode, unsigned long long age)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);
	struct sit_info *sm = SIT_I(sbi);
	struct victim_sel_policy p;
	unsigned int secno, last_victim;
	unsigned int last_segment;
	unsigned int nsearched;
	bool is_atgc;
	int ret = 0;

	mutex_lock(&dirty_i->seglist_lock);
	last_segment = MAIN_SECS(sbi) * sbi->segs_per_sec;

	p.alloc_mode = alloc_mode;
	p.age = age;
	p.age_threshold = sbi->am.age_threshold;

retry:
	select_policy(sbi, gc_type, type, &p);
	p.min_segno = NULL_SEGNO;
	p.oldest_age = 0;
	p.min_cost = get_max_cost(sbi, &p);

	is_atgc = (p.gc_mode == GC_AT || p.alloc_mode == AT_SSR);
	nsearched = 0;

	if (is_atgc)
		SIT_I(sbi)->dirty_min_mtime = ULLONG_MAX;

	if (*result != NULL_SEGNO) {
		if (!get_valid_blocks(sbi, *result, false)) {
			ret = -ENODATA;
			goto out;
		}

		if (sec_usage_check(sbi, GET_SEC_FROM_SEG(sbi, *result)))
			ret = -EBUSY;
		else
			p.min_segno = *result;
		goto out;
	}

	ret = -ENODATA;
	if (p.max_search == 0)
		goto out;

	if (__is_large_section(sbi) && p.alloc_mode == LFS) {
		if (sbi->next_victim_seg[BG_GC] != NULL_SEGNO) {
			p.min_segno = sbi->next_victim_seg[BG_GC];
			*result = p.min_segno;
			sbi->next_victim_seg[BG_GC] = NULL_SEGNO;
			goto got_result;
		}
		if (gc_type == FG_GC &&
				sbi->next_victim_seg[FG_GC] != NULL_SEGNO) {
			p.min_segno = sbi->next_victim_seg[FG_GC];
			*result = p.min_segno;
			sbi->next_victim_seg[FG_GC] = NULL_SEGNO;
			goto got_result;
		}
	}

	last_victim = sm->last_victim[p.gc_mode];
	if (p.alloc_mode == LFS && gc_type == FG_GC) {
		p.min_segno = check_bg_victims(sbi);
		if (p.min_segno != NULL_SEGNO)
			goto got_it;
	}

	while (1) {
		unsigned long cost, *dirty_bitmap;
		unsigned int unit_no, segno;

		dirty_bitmap = p.dirty_bitmap;
		unit_no = find_next_bit(dirty_bitmap,
				last_segment / p.ofs_unit,
				p.offset / p.ofs_unit);
		segno = unit_no * p.ofs_unit;
		if (segno >= last_segment) {
			if (sm->last_victim[p.gc_mode]) {
				last_segment =
					sm->last_victim[p.gc_mode];
				sm->last_victim[p.gc_mode] = 0;
				p.offset = 0;
				continue;
			}
			break;
		}

		p.offset = segno + p.ofs_unit;
		nsearched++;

#ifdef CONFIG_F2FS_CHECK_FS
		/*
		 * skip selecting the invalid segno (that is failed due to block
		 * validity check failure during GC) to avoid endless GC loop in
		 * such cases.
		 */
		if (test_bit(segno, sm->invalid_segmap))
			goto next;
#endif

		secno = GET_SEC_FROM_SEG(sbi, segno);

		if (sec_usage_check(sbi, secno))
			goto next;

		/* Don't touch checkpointed data */
		if (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED))) {
			if (p.alloc_mode == LFS) {
				/*
				 * LFS is set to find source section during GC.
				 * The victim should have no checkpointed data.
				 */
				if (get_ckpt_valid_blocks(sbi, segno, true))
					goto next;
			} else {
				/*
				 * SSR | AT_SSR are set to find target segment
				 * for writes which can be full by checkpointed
				 * and newly written blocks.
				 */
				if (!f2fs_segment_has_free_slot(sbi, segno))
					goto next;
			}
		}

		if (gc_type == BG_GC && test_bit(secno, dirty_i->victim_secmap))
			goto next;

		if (gc_type == FG_GC && f2fs_section_is_pinned(dirty_i, secno))
			goto next;

		if (is_atgc) {
			add_victim_entry(sbi, &p, segno);
			goto next;
		}

		cost = get_gc_cost(sbi, segno, &p);

		if (p.min_cost > cost) {
			p.min_segno = segno;
			p.min_cost = cost;
		}
next:
		if (nsearched >= p.max_search) {
			if (!sm->last_victim[p.gc_mode] && segno <= last_victim)
				sm->last_victim[p.gc_mode] =
					last_victim + p.ofs_unit;
			else
				sm->last_victim[p.gc_mode] = segno + p.ofs_unit;
			sm->last_victim[p.gc_mode] %=
				(MAIN_SECS(sbi) * sbi->segs_per_sec);
			break;
		}
	}

	/* get victim for GC_AT/AT_SSR */
	if (is_atgc) {
		lookup_victim_by_age(sbi, &p);
		release_victim_entry(sbi);
	}

	if (is_atgc && p.min_segno == NULL_SEGNO &&
			sm->elapsed_time < p.age_threshold) {
		p.age_threshold = 0;
		goto retry;
	}

	if (p.min_segno != NULL_SEGNO) {
got_it:
		*result = (p.min_segno / p.ofs_unit) * p.ofs_unit;
got_result:
		if (p.alloc_mode == LFS) {
			secno = GET_SEC_FROM_SEG(sbi, p.min_segno);
			if (gc_type == FG_GC)
				sbi->cur_victim_sec = secno;
			else
				set_bit(secno, dirty_i->victim_secmap);
		}
		ret = 0;

	}
out:
	if (p.min_segno != NULL_SEGNO)
		trace_f2fs_get_victim(sbi->sb, type, gc_type, &p,
				sbi->cur_victim_sec,
				prefree_segments(sbi), free_segments(sbi));
	mutex_unlock(&dirty_i->seglist_lock);

	return ret;
}

static const struct victim_selection default_v_ops = {
	.get_victim = get_victim_by_default,
};

static void init_gc_inode_list(struct gc_inode_list *gc_list, int max_refby_req_cnt)
{
	gc_list->max_refby_req_cnt = max_refby_req_cnt;
	INIT_LIST_HEAD(&gc_list->ilist);
	INIT_RADIX_TREE(&gc_list->iroot, GFP_NOFS);
}

static struct inode *find_gc_inode(struct gc_inode_list *gc_list, nid_t ino)
{
	struct inode_entry *ie;

	ie = radix_tree_lookup(&gc_list->iroot, ino);
	if (ie)
		return ie->inode;
	return NULL;
}

static struct inode_entry *find_gc_inode_entry(struct gc_inode_list *gc_list, nid_t ino)
{
	struct inode_entry *ie;

	ie = radix_tree_lookup(&gc_list->iroot, ino);
	if (ie)
		return ie;
	return NULL;
}

static struct inode_entry *add_gc_inode(struct gc_inode_list *gc_list, 
		struct inode *inode, bool from_csgc)
{
	struct inode_entry *new_ie;

	new_ie = find_gc_inode_entry(gc_list, inode->i_ino);
	if (new_ie && inode == new_ie->inode) {
		iput(inode);
		return new_ie;
	}
	// if(from_csgc)
	// 	f2fs_debug_csgc_pid("add inode(%lu) to gc_list", inode->i_ino);
	new_ie = f2fs_kmem_cache_alloc(f2fs_inode_entry_slab,
					GFP_NOFS, true, NULL);
	new_ie->inode = inode;
	new_ie->page = NULL;
	new_ie->refcnt = 0;
	new_ie->refby = kzalloc(gc_list->max_refby_req_cnt*sizeof(short), GFP_NOFS);
	new_ie->gc_rwsem_locked = 0;
	new_ie->gc_rwsem_req_by = kzalloc(gc_list->max_refby_req_cnt*sizeof(bool), GFP_NOFS);
	new_ie->rollback_refcnt = 0;

	F2FS_I_SB(inode)->gc_add_inode_cnt ++;
	// f2fs_debug_csgc("add inode(%lu) to gc_list", inode->i_ino);
	f2fs_radix_tree_insert(&gc_list->iroot, inode->i_ino, new_ie);
	list_add_tail(&new_ie->list, &gc_list->ilist);
	return new_ie;
}

static void show_gc_inode_list(struct gc_inode_list *gc_list)
{
	struct inode_entry *ie;

	printk(KERN_INFO "Dumping gc_inode_list:");
	list_for_each_entry(ie, &gc_list->ilist, list){
		if(ie->inode){
			printk(KERN_INFO "inode(%lu), page:%p, refcnt:%d, rollback_refcnt:%d",
				ie->inode->i_ino, ie->page, ie->refcnt, ie->rollback_refcnt);
		}else{
			printk(KERN_INFO "NULL inode");
		}
	}
}

static void clear_gc_inode_rollback_refcnt(struct gc_inode_list *gc_list)
{
	struct inode_entry *ie;

	list_for_each_entry(ie, &gc_list->ilist, list)
		ie->rollback_refcnt = 0;
}

// make sure the inode pages are freed and set to NULL 
// before calling this function
static void put_gc_inode(struct gc_inode_list *gc_list)
{
	struct inode_entry *ie, *next_ie;

	list_for_each_entry_safe(ie, next_ie, &gc_list->ilist, list) {
		F2FS_I_SB(ie->inode)->gc_put_inode_cnt ++;
		// f2fs_debug_csgc("put inode(%lu) from gc_list", ie->inode->i_ino);
		radix_tree_delete(&gc_list->iroot, ie->inode->i_ino);
		iput(ie->inode);

		kfree(ie->refby);
		kfree(ie->gc_rwsem_req_by);
		list_del(&ie->list);
		WARN_ON(ie->page != NULL);
		kmem_cache_free(f2fs_inode_entry_slab, ie);
	}
}

static void init_gc_dnode_list(struct gc_dnode_list *dno_list, int max_refby_req_cnt)
{
	INIT_LIST_HEAD(&dno_list->dlist);
	INIT_RADIX_TREE(&dno_list->droot, GFP_NOFS);
	dno_list->nr_dnode = 0;
	dno_list->max_refby_req_cnt = max_refby_req_cnt;
}

static struct dnode_entry *find_gc_dnode_entry(struct gc_dnode_list *dno_list, nid_t nid)
{
	struct dnode_entry *de;

	de = radix_tree_lookup(&dno_list->droot, nid);
	if(de)
		return de;
	return NULL;
}

static void add_gc_dnode(struct gc_dnode_list *dno_list, nid_t nid, nid_t ino_nid)
{
	struct dnode_entry *de = find_gc_dnode_entry(dno_list, nid);

	if(de){
		WARN_ON(de->nid != nid);
		return;
	}

	de = f2fs_kmem_cache_alloc(f2fs_dnode_entry_slab, 
				GFP_NOFS, true, NULL);
	de->nid = nid;
	de->ino_nid = ino_nid;
	de->page = NULL;
	de->refcnt = 0;
	de->refby = kzalloc(dno_list->max_refby_req_cnt*sizeof(short), GFP_NOFS);
	de->rollback_refcnt = 0;
	
	f2fs_radix_tree_insert(&dno_list->droot, nid, de);
	list_add_tail(&de->list, &dno_list->dlist);
	dno_list->nr_dnode++;
}

static void show_gc_dnode_list(struct gc_dnode_list *dno_list)
{
	struct dnode_entry *de;

	printk(KERN_INFO "Dumping gc_dnode_list:");
	list_for_each_entry(de, &dno_list->dlist, list){
		printk(KERN_INFO "nid:%u, ino_nid:%u, page:%p, refcnt:%d, rollback_refcnt:%d",
			de->nid, de->ino_nid, de->page, de->refcnt, de->rollback_refcnt);
	}
}

static void clear_gc_dnode_rollback_refcnt(struct gc_dnode_list *dno_list)
{
	struct dnode_entry *de;

	list_for_each_entry(de, &dno_list->dlist, list)
		de->rollback_refcnt = 0;
}

// make sure the dnode pages are freed and set to NULL 
// before calling this function
static void free_gc_dnode_list(struct gc_dnode_list *dno_list)
{
	struct dnode_entry *de, *next_de;

	list_for_each_entry_safe(de, next_de, &dno_list->dlist, list){
		radix_tree_delete(&dno_list->droot, de->nid);
		list_del(&de->list);
		WARN_ON(de->page != NULL);
		kfree(de->refby);
		kmem_cache_free(f2fs_dnode_entry_slab, de);
		dno_list->nr_dnode--;
	}
}

static int rb_compare_gc_folio(struct folio *folio1, struct folio *folio2)
{
	if (folio1->mapping < folio2->mapping)
		return -1;
	else if (folio1->mapping > folio2->mapping)
		return 1;
	else 
		return folio1->index - folio2->index;
}

static struct folio_entry *rb_find_gc_folio_entry(struct rb_root *root, struct folio *folio)
{
	struct rb_node *node = root->rb_node;

	while(node){
		struct folio_entry *this = container_of(node, struct folio_entry, rb_node);
		int result = rb_compare_gc_folio(folio, this->folio);

		if(result < 0)
			node = node->rb_left;
		else if(result > 0)
			node = node->rb_right;
		else
			return this;
	}
	return NULL;
}

static void rb_insert_gc_folio_entry(struct rb_root *root, struct folio_entry *new_fe)
{
	struct rb_node **new = &(root->rb_node), *parent = NULL;

	while(*new){
		struct folio_entry *this = container_of(*new, struct folio_entry, rb_node);
		int result = rb_compare_gc_folio(new_fe->folio, this->folio);

		parent = *new;
		if(result < 0)
			new = &((*new)->rb_left);
		else if(result > 0)
			new = &((*new)->rb_right);
		else
			return;	
	}

	rb_link_node(&new_fe->rb_node, parent, new);
	rb_insert_color(&new_fe->rb_node, root);	
}

static void init_gc_folio_list(struct gc_folio_list *folio_list)
{
	folio_list->folio_rbroot = RB_ROOT;
	INIT_LIST_HEAD(&folio_list->list);
	folio_list->nr_folio = 0;
}

static struct folio_entry *find_gc_folio_entry(struct gc_folio_list *folio_list, struct folio *folio)
{
	return rb_find_gc_folio_entry(&folio_list->folio_rbroot, folio);
}

static struct folio_entry *add_gc_folio(struct gc_folio_list *folio_list, struct folio *folio)
{
	struct folio_entry *new_fe = find_gc_folio_entry(folio_list, folio);

	if(new_fe){
		WARN_ON(new_fe->folio != folio);
		return new_fe;
	}

	new_fe = f2fs_kmem_cache_alloc(f2fs_folio_entry_slab, GFP_NOFS, true, NULL);
	new_fe->folio = folio;
	new_fe->refcnt = 0;
	new_fe->delta_refcnt = 0;
	new_fe->is_valid = 1;

	rb_insert_gc_folio_entry(&folio_list->folio_rbroot, new_fe);
	list_add_tail(&new_fe->list, &folio_list->list);
	folio_list->nr_folio++;
	return new_fe;
}

static void free_gc_folio_list(struct gc_folio_list *folio_list, bool from_normal_path)
{
	struct folio_entry *fe, *next_fe;

	list_for_each_entry_safe(fe, next_fe, &folio_list->list, list){
		// no need to call `rb_erase`
		list_del(&fe->list);
		if(from_normal_path)
			WARN_ON(fe->refcnt);
		kmem_cache_free(f2fs_folio_entry_slab, fe);
		folio_list->nr_folio--;
	}
}

static void init_gc_data_list(struct gc_data_list *data_list)
{
	INIT_LIST_HEAD(&data_list->list);
	data_list->nr_data = 0;
}

static struct data_entry *find_gc_data_entry(struct gc_data_list *data_list, struct page *page)
{
	struct data_entry *data_ent;

	list_for_each_entry(data_ent, &data_list->list, list){
		if(data_ent->page == page)
			return data_ent;
	}
	return NULL;
}

static struct data_entry *add_gc_data_page(struct gc_data_list *data_list, struct page *page)
{
	struct data_entry *new_data_ent;

	new_data_ent = f2fs_kmem_cache_alloc(f2fs_data_entry_slab, GFP_NOFS, true, NULL);
	new_data_ent->page = page;
	new_data_ent->fe = NULL;

	list_add_tail(&new_data_ent->list, &data_list->list);
	data_list->nr_data++;
	return new_data_ent;
}

static void del_gc_data_entry(struct gc_data_list *data_list, struct data_entry *data_ent)
{
	list_del(&data_ent->list);
	WARN_ON(data_ent->page != NULL);
	kmem_cache_free(f2fs_data_entry_slab, data_ent);
	data_list->nr_data--;
}

static void free_gc_data_list(struct gc_data_list *data_list)
{
	struct data_entry *data_ent, *next_data_ent;

	list_for_each_entry_safe(data_ent, next_data_ent, &data_list->list, list){
		list_del(&data_ent->list);
		WARN_ON(data_ent->page != NULL);
		kmem_cache_free(f2fs_data_entry_slab, data_ent);
		data_list->nr_data--;
	}
}

void f2fs_init_page_list(struct f2fs_page_list *page_list, int type)
{
	INIT_LIST_HEAD(&page_list->list);
	page_list->nr_pages = 0;
	page_list->type = type;
}

struct page *f2fs_find_page_list(struct f2fs_page_list *page_list, struct page *page)
{
	struct page_entry *pe;

	list_for_each_entry(pe, &page_list->list, list){
		if(pe->page == page)
			return pe->page;
	}
	return NULL;
}

void f2fs_add_page_list(struct f2fs_page_list *page_list, struct page *page)
{
	struct page_entry *new_pe;

	if (page == f2fs_find_page_list(page_list, page)) {
		return;
	}

	new_pe = f2fs_kmem_cache_alloc(f2fs_page_entry_slab,
					GFP_NOFS, true, NULL);
	new_pe->page = page;
	page_list->nr_pages++;

	list_add_tail(&new_pe->list, &page_list->list);
}

void f2fs_put_page_list(struct f2fs_page_list *page_list)
{
	struct page_entry *pe, *next_pe;

	list_for_each_entry_safe(pe, next_pe, &page_list->list, list) {
		list_del(&pe->list);
		kmem_cache_free(f2fs_page_entry_slab, pe);
	}
	page_list->nr_pages = 0;
}

static int check_valid_map(struct f2fs_sb_info *sbi,
				unsigned int segno, int offset)
{
	struct sit_info *sit_i = SIT_I(sbi);
	struct seg_entry *sentry;
	int ret;

	down_read(&sit_i->sentry_lock);
	sentry = get_seg_entry(sbi, segno);
	ret = f2fs_test_bit(offset, sentry->cur_valid_map);
	up_read(&sit_i->sentry_lock);
	return ret;
}

/*
 * This function compares node address got in summary with that in NAT.
 * On validity, copy that node with cold status, otherwise (invalid node)
 * ignore that.
 */
static int gc_node_segment(struct f2fs_sb_info *sbi,
		struct f2fs_summary *sum, unsigned int segno, int gc_type)
{
	struct f2fs_summary *entry;
	block_t start_addr;
	int off;
	int phase = 0;
	bool fggc = (gc_type == FG_GC);
	int submitted = 0;
	unsigned int usable_blks_in_seg = f2fs_usable_blks_in_seg(sbi, segno);

	start_addr = START_BLOCK(sbi, segno);

next_step:
	entry = sum;

	if (fggc && phase == 2)
		atomic_inc(&sbi->wb_sync_req[NODE]);

	for (off = 0; off < usable_blks_in_seg; off++, entry++) {
		nid_t nid = le32_to_cpu(entry->nid);
		struct page *node_page;
		struct node_info ni;
		int err;

		/* stop BG_GC if there is not enough free sections. */
		if (gc_type == BG_GC && has_not_enough_free_secs(sbi, 0, 0))
			return submitted;

		if (check_valid_map(sbi, segno, off) == 0)
			continue;

		if (phase == 0) {
			f2fs_ra_meta_pages(sbi, NAT_BLOCK_OFFSET(nid), 1,
							META_NAT, true);
			continue;
		}

		if (phase == 1) {
			f2fs_ra_node_page(sbi, nid);
			continue;
		}

		/* phase == 2 */
		node_page = f2fs_get_node_page(sbi, nid);
		if (IS_ERR(node_page))
			continue;

		/* block may become invalid during f2fs_get_node_page */
		if (check_valid_map(sbi, segno, off) == 0) {
			f2fs_put_page(node_page, 1);
			continue;
		}

		if (f2fs_get_node_info(sbi, nid, &ni, false)) {
			f2fs_put_page(node_page, 1);
			continue;
		}

		if (ni.blk_addr != start_addr + off) {
			f2fs_put_page(node_page, 1);
			continue;
		}

		err = f2fs_move_node_page(node_page, gc_type);
		if (!err && gc_type == FG_GC)
			submitted++;
		stat_inc_node_blk_count(sbi, 1, gc_type);
	}

	if (++phase < 3)
		goto next_step;

	if (fggc)
		atomic_dec(&sbi->wb_sync_req[NODE]);
	return submitted;
}

/*
 * Calculate start block index indicating the given node offset.
 * Be careful, caller should give this node offset only indicating direct node
 * blocks. If any node offsets, which point the other types of node blocks such
 * as indirect or double indirect node blocks, are given, it must be a caller's
 * bug.
 */
block_t f2fs_start_bidx_of_node(unsigned int node_ofs, struct inode *inode)
{
	unsigned int indirect_blks = 2 * NIDS_PER_BLOCK + 4;
	unsigned int bidx;

	if (node_ofs == 0)
		return 0;

	if (node_ofs <= 2) {
		bidx = node_ofs - 1;
	} else if (node_ofs <= indirect_blks) {
		int dec = (node_ofs - 4) / (NIDS_PER_BLOCK + 1);

		bidx = node_ofs - 2 - dec;
	} else {
		int dec = (node_ofs - indirect_blks - 3) / (NIDS_PER_BLOCK + 1);

		bidx = node_ofs - 5 - dec;
	}
	return bidx * ADDRS_PER_BLOCK(inode) + ADDRS_PER_INODE(inode);
}

static bool is_alive(struct f2fs_sb_info *sbi, struct f2fs_summary *sum,
		struct node_info *dni, block_t blkaddr, unsigned int *nofs)
{
	struct page *node_page;
	nid_t nid;
	unsigned int ofs_in_node, max_addrs, base;
	block_t source_blkaddr;

	nid = le32_to_cpu(sum->nid);
	ofs_in_node = le16_to_cpu(sum->ofs_in_node);

	node_page = f2fs_get_node_page(sbi, nid);
	if (IS_ERR(node_page))
		return false;

	if (f2fs_get_node_info(sbi, nid, dni, false)) {
		f2fs_put_page(node_page, 1);
		return false;
	}

	if (sum->version != dni->version) {
		f2fs_warn(sbi, "%s: valid data with mismatched node version.",
			  __func__);
		set_sbi_flag(sbi, SBI_NEED_FSCK);
	}

	if (f2fs_check_nid_range(sbi, dni->ino)) {
		f2fs_put_page(node_page, 1);
		return false;
	}

	if (IS_INODE(node_page)) {
		base = offset_in_addr(F2FS_INODE(node_page));
		max_addrs = DEF_ADDRS_PER_INODE;
	} else {
		base = 0;
		max_addrs = DEF_ADDRS_PER_BLOCK;
	}

	if (base + ofs_in_node >= max_addrs) {
		f2fs_err(sbi, "Inconsistent blkaddr offset: base:%u, ofs_in_node:%u, max:%u, ino:%u, nid:%u",
			base, ofs_in_node, max_addrs, dni->ino, dni->nid);
		f2fs_put_page(node_page, 1);
		return false;
	}

	*nofs = ofs_of_node(node_page);
	source_blkaddr = data_blkaddr(NULL, node_page, ofs_in_node);
	f2fs_put_page(node_page, 1);

	if (source_blkaddr != blkaddr) {
#ifdef CONFIG_F2FS_CHECK_FS
		unsigned int segno = GET_SEGNO(sbi, blkaddr);
		unsigned long offset = GET_BLKOFF_FROM_SEG0(sbi, blkaddr);

		if (unlikely(check_valid_map(sbi, segno, offset))) {
			if (!test_and_set_bit(segno, SIT_I(sbi)->invalid_segmap)) {
				f2fs_err(sbi, "mismatched blkaddr %u (source_blkaddr %u) in seg %u",
					 blkaddr, source_blkaddr, segno);
				set_sbi_flag(sbi, SBI_NEED_FSCK);
			}
		}
#endif
		return false;
	}
	return true;
}

static int ra_data_block(struct inode *inode, pgoff_t index)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct address_space *mapping = inode->i_mapping;
	struct dnode_of_data dn;
	struct page *page;
	struct extent_info ei = {0, };
	struct f2fs_io_info fio = {
		.sbi = sbi,
		.ino = inode->i_ino,
		.type = DATA,
		.temp = COLD,
		.op = REQ_OP_READ,
		.op_flags = 0,
		.encrypted_page = NULL,
		.in_list = false,
		.retry = false,
	};
	int err;

	page = f2fs_grab_cache_page(mapping, index, true);
	if (!page)
		return -ENOMEM;

	if (f2fs_lookup_read_extent_cache(inode, index, &ei)) {
		dn.data_blkaddr = ei.blk + index - ei.fofs;
		if (unlikely(!f2fs_is_valid_blkaddr(sbi, dn.data_blkaddr,
						DATA_GENERIC_ENHANCE_READ))) {
			err = -EFSCORRUPTED;
			f2fs_handle_error(sbi, ERROR_INVALID_BLKADDR);
			goto put_page;
		}
		goto got_it;
	}

	set_new_dnode(&dn, inode, NULL, NULL, 0);
	err = f2fs_get_dnode_of_data(&dn, index, LOOKUP_NODE);
	if (err)
		goto put_page;
	f2fs_put_dnode(&dn);

	if (!__is_valid_data_blkaddr(dn.data_blkaddr)) {
		err = -ENOENT;
		goto put_page;
	}
	if (unlikely(!f2fs_is_valid_blkaddr(sbi, dn.data_blkaddr,
						DATA_GENERIC_ENHANCE))) {
		err = -EFSCORRUPTED;
		f2fs_handle_error(sbi, ERROR_INVALID_BLKADDR);
		goto put_page;
	}
got_it:
	/* read page */
	fio.page = page;
	fio.new_blkaddr = fio.old_blkaddr = dn.data_blkaddr;

	/*
	 * don't cache encrypted data into meta inode until previous dirty
	 * data were writebacked to avoid racing between GC and flush.
	 */
	f2fs_wait_on_page_writeback(page, DATA, true, true);

	f2fs_wait_on_block_writeback(inode, dn.data_blkaddr);

	fio.encrypted_page = f2fs_pagecache_get_page(META_MAPPING(sbi),
					dn.data_blkaddr,
					FGP_LOCK | FGP_CREAT, GFP_NOFS);
	if (!fio.encrypted_page) {
		err = -ENOMEM;
		goto put_page;
	}

	err = f2fs_submit_page_bio(&fio);
	if (err)
		goto put_encrypted_page;
	f2fs_put_page(fio.encrypted_page, 0);
	f2fs_put_page(page, 1);

	f2fs_update_iostat(sbi, inode, FS_DATA_READ_IO, F2FS_BLKSIZE);
	f2fs_update_iostat(sbi, NULL, FS_GDATA_READ_IO, F2FS_BLKSIZE);

	return 0;
put_encrypted_page:
	f2fs_put_page(fio.encrypted_page, 1);
put_page:
	f2fs_put_page(page, 1);
	return err;
}

/*
 * Move data block via META_MAPPING while keeping locked data page.
 * This can be used to move blocks, aka LBAs, directly on disk.
 */
static int move_data_block(struct inode *inode, block_t bidx,
				int gc_type, unsigned int segno, int off)
{
	struct f2fs_io_info fio = {
		.sbi = F2FS_I_SB(inode),
		.ino = inode->i_ino,
		.type = DATA,
		.temp = COLD,
		.op = REQ_OP_READ,
		.op_flags = 0,
		.encrypted_page = NULL,
		.in_list = false,
		.retry = false,
	};
	struct dnode_of_data dn;
	struct f2fs_summary sum;
	struct node_info ni;
	struct page *page, *mpage;
	block_t newaddr;
	int err = 0;
	bool lfs_mode = f2fs_lfs_mode(fio.sbi);
	int type = fio.sbi->am.atgc_enabled && (gc_type == BG_GC) &&
				(fio.sbi->gc_mode != GC_URGENT_HIGH) ?
				CURSEG_ALL_DATA_ATGC : CURSEG_COLD_DATA;

	/* do not read out */
	page = f2fs_grab_cache_page(inode->i_mapping, bidx, false);
	if (!page)
		return -ENOMEM;

	if (!check_valid_map(F2FS_I_SB(inode), segno, off)) {
		err = -ENOENT;
		goto out;
	}

	err = f2fs_gc_pinned_control(inode, gc_type, segno);
	if (err)
		goto out;

	set_new_dnode(&dn, inode, NULL, NULL, 0);
	err = f2fs_get_dnode_of_data(&dn, bidx, LOOKUP_NODE);
	if (err)
		goto out;

	if (unlikely(dn.data_blkaddr == NULL_ADDR)) {
		ClearPageUptodate(page);
		err = -ENOENT;
		goto put_out;
	}

	/*
	 * don't cache encrypted data into meta inode until previous dirty
	 * data were writebacked to avoid racing between GC and flush.
	 */
	f2fs_wait_on_page_writeback(page, DATA, true, true);

	f2fs_wait_on_block_writeback(inode, dn.data_blkaddr);

	err = f2fs_get_node_info(fio.sbi, dn.nid, &ni, false);
	if (err)
		goto put_out;

	/* read page */
	fio.page = page;
	fio.new_blkaddr = fio.old_blkaddr = dn.data_blkaddr;

	if (lfs_mode)
		f2fs_down_write(&fio.sbi->io_order_lock);

	mpage = f2fs_grab_cache_page(META_MAPPING(fio.sbi),
					fio.old_blkaddr, false);
	if (!mpage) {
		err = -ENOMEM;
		goto up_out;
	}

	fio.encrypted_page = mpage;

	/* read source block in mpage */
	if (!PageUptodate(mpage)) {
		err = f2fs_submit_page_bio(&fio);
		if (err) {
			f2fs_put_page(mpage, 1);
			goto up_out;
		}

		f2fs_update_iostat(fio.sbi, inode, FS_DATA_READ_IO,
							F2FS_BLKSIZE);
		f2fs_update_iostat(fio.sbi, NULL, FS_GDATA_READ_IO,
							F2FS_BLKSIZE);

		lock_page(mpage);
		if (unlikely(mpage->mapping != META_MAPPING(fio.sbi) ||
						!PageUptodate(mpage))) {
			err = -EIO;
			f2fs_put_page(mpage, 1);
			goto up_out;
		}
	}

	set_summary(&sum, dn.nid, dn.ofs_in_node, ni.version);

	/* allocate block address */
	f2fs_allocate_data_block(fio.sbi, NULL, fio.old_blkaddr, &newaddr,
				&sum, type, NULL);

	fio.encrypted_page = f2fs_pagecache_get_page(META_MAPPING(fio.sbi),
				newaddr, FGP_LOCK | FGP_CREAT, GFP_NOFS);
	if (!fio.encrypted_page) {
		err = -ENOMEM;
		f2fs_put_page(mpage, 1);
		goto recover_block;
	}

	/* write target block */
	f2fs_wait_on_page_writeback(fio.encrypted_page, DATA, true, true);
	memcpy(page_address(fio.encrypted_page),
				page_address(mpage), PAGE_SIZE);
	f2fs_put_page(mpage, 1);
	invalidate_mapping_pages(META_MAPPING(fio.sbi),
				fio.old_blkaddr, fio.old_blkaddr);
	f2fs_invalidate_compress_page(fio.sbi, fio.old_blkaddr);

	set_page_dirty(fio.encrypted_page);
	if (clear_page_dirty_for_io(fio.encrypted_page))
		dec_page_count(fio.sbi, F2FS_DIRTY_META);

	set_page_writeback(fio.encrypted_page);
	ClearPageError(page);

	fio.op = REQ_OP_WRITE;
	fio.op_flags = REQ_SYNC;
	fio.new_blkaddr = newaddr;
	f2fs_submit_page_write(&fio);
	if (fio.retry) {
		err = -EAGAIN;
		if (PageWriteback(fio.encrypted_page))
			end_page_writeback(fio.encrypted_page);
		goto put_page_out;
	}

	f2fs_update_iostat(fio.sbi, NULL, FS_GC_DATA_IO, F2FS_BLKSIZE);

	f2fs_update_data_blkaddr(&dn, newaddr);
	set_inode_flag(inode, FI_APPEND_WRITE);
	if (page->index == 0)
		set_inode_flag(inode, FI_FIRST_BLOCK_WRITTEN);
put_page_out:
	f2fs_put_page(fio.encrypted_page, 1);
recover_block:
	if (err)
		f2fs_do_replace_block(fio.sbi, &sum, newaddr, fio.old_blkaddr,
							true, true, true);
up_out:
	if (lfs_mode)
		f2fs_up_write(&fio.sbi->io_order_lock);
put_out:
	f2fs_put_dnode(&dn);
out:
	f2fs_put_page(page, 1);
	return err;
}

static int move_data_page(struct inode *inode, block_t bidx, int gc_type,
							unsigned int segno, int off)
{
	struct page *page;
	int err = 0;
#ifdef CONFIG_F2FS_ORI_GC_DEBUG_ADD_PAGE_PERF
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	unsigned long long t1, t2;
	t1 = ktime_get_ns();
#endif
	page = f2fs_get_lock_data_page(inode, bidx, true);
	if (IS_ERR(page))
		return PTR_ERR(page);
#ifdef CONFIG_F2FS_ORI_GC_DEBUG_ADD_PAGE_PERF
	t2 = ktime_get_ns();
	sbi->csgc_dpage_cached_count ++;
	sbi->csgc_get_dpage_time_ns += (t2 - t1);
#endif

	if (!check_valid_map(F2FS_I_SB(inode), segno, off)) {
		err = -ENOENT;
		goto out;
	}

	err = f2fs_gc_pinned_control(inode, gc_type, segno);
	if (err)
		goto out;

	if (gc_type == BG_GC) {
		if (PageWriteback(page)) {
			err = -EAGAIN;
			goto out;
		}
		set_page_dirty(page);
		set_page_private_gcing(page);
	} else {
		// struct fio is used to store parameters for f2fs io
		struct f2fs_io_info fio = {
			.sbi = F2FS_I_SB(inode),
			.ino = inode->i_ino,
			.type = DATA,
			.temp = COLD,
			.op = REQ_OP_WRITE,
			.op_flags = REQ_SYNC | REQ_GCIO, // mark with REQ_GCIO
			.old_blkaddr = NULL_ADDR,
			.page = page,
			.encrypted_page = NULL,
			.need_lock = LOCK_REQ,
			.io_type = FS_GC_DATA_IO,
		};
		bool is_dirty = PageDirty(page);

retry:
		f2fs_wait_on_page_writeback(page, DATA, true, true);

		set_page_dirty(page);
		if (clear_page_dirty_for_io(page)) {
			inode_dec_dirty_pages(inode);
			f2fs_remove_dirty_inode(inode);
		}

		set_page_private_gcing(page);

		// write data page(IOs are submitted to f2fs, but the `struct fio` may 
		// cache the submitted IOs and try to merged them, the merged IOs will 
		// be submitted by a `f2fs_submit_merged_writes` in `gc_data_segment`
		err = f2fs_do_write_data_page(&fio);
		if (err) {
			clear_page_private_gcing(page);
			if (err == -ENOMEM) {
				memalloc_retry_wait(GFP_NOFS);
				goto retry;
			}
			if (is_dirty)
				set_page_dirty(page);
		}
	}
out:
	f2fs_put_page(page, 1);
	return err;
}

/*
 * This function tries to get parent node of victim data block, and identifies
 * data block validity. If the block is valid, copy that with cold status and
 * modify parent node.
 * If the parent node is not valid or the data block address is different,
 * the victim data block is ignored.
 */
static int gc_data_segment(struct f2fs_sb_info *sbi, struct f2fs_summary *sum,
		struct gc_inode_list *gc_list, unsigned int segno, int gc_type,
		bool force_migrate)
{
	struct super_block *sb = sbi->sb;
	struct f2fs_summary *entry;
	block_t start_addr;
	int off;
	int phase = 0;
	int submitted = 0;
	unsigned int usable_blks_in_seg = f2fs_usable_blks_in_seg(sbi, segno);

	start_addr = START_BLOCK(sbi, segno);

next_step:
	entry = sum;

	for (off = 0; off < usable_blks_in_seg; off++, entry++) {
		struct page *data_page;
		struct inode *inode;
		struct node_info dni; /* dnode info for the data */
		unsigned int ofs_in_node, nofs;
		block_t start_bidx;
		nid_t nid = le32_to_cpu(entry->nid);
		unsigned long long t1, t2;

		/*
		 * stop BG_GC if there is not enough free sections.
		 * Or, stop GC if the segment becomes fully valid caused by
		 * race condition along with SSR block allocation.
		 */
		if ((gc_type == BG_GC && has_not_enough_free_secs(sbi, 0, 0)) ||
			(!force_migrate && get_valid_blocks(sbi, segno, true) ==
							CAP_BLKS_PER_SEC(sbi)))
			return submitted;

		if (check_valid_map(sbi, segno, off) == 0)
			continue;

		if (phase == 0) {
			f2fs_ra_meta_pages(sbi, NAT_BLOCK_OFFSET(nid), 1,
							META_NAT, true);
			continue;
		}

		if (phase == 1) {
			f2fs_ra_node_page(sbi, nid);
			continue;
		}

		/* Get an inode by ino with checking validity */
		if (!is_alive(sbi, entry, &dni, start_addr + off, &nofs))
			continue;

		if (phase == 2) {
			f2fs_ra_node_page(sbi, dni.ino);
			continue;
		}

		ofs_in_node = le16_to_cpu(entry->ofs_in_node);

		if (phase == 3) {
			int err;

			inode = f2fs_iget(sb, dni.ino);
			if (IS_ERR(inode) || is_bad_inode(inode) ||
					special_file(inode->i_mode))
				continue;

			err = f2fs_gc_pinned_control(inode, gc_type, segno);
			if (err == -EAGAIN) {
				iput(inode);
				return submitted;
			}

			if (!f2fs_down_write_trylock(
				&F2FS_I(inode)->i_gc_rwsem[WRITE])) {
				iput(inode);
				sbi->skipped_gc_rwsem++;
				continue;
			}

			start_bidx = f2fs_start_bidx_of_node(nofs, inode) +
								ofs_in_node;

			if (f2fs_post_read_required(inode)) {
				int err = ra_data_block(inode, start_bidx);

				f2fs_up_write(&F2FS_I(inode)->i_gc_rwsem[WRITE]);
				if (err) {
					iput(inode);
					continue;
				}
				add_gc_inode(gc_list, inode, false);
				continue;
			}
#ifdef CONFIG_F2FS_ORI_GC_DEBUG_ADD_PAGE_PERF
			t1 = ktime_get_ns();
#endif
			// readahead data pages, REQ_GCIO is for GC IO stat
			data_page = f2fs_get_read_data_page(inode,
						start_bidx, REQ_RAHEAD | REQ_GCIO, true);
#ifdef CONFIG_F2FS_ORI_GC_DEBUG_ADD_PAGE_PERF
			t2 = ktime_get_ns();
			sbi->csgc_dpage_hole_count ++;
			sbi->csgc_grab_dpage_time_ns += (t2 - t1);
#endif
			f2fs_up_write(&F2FS_I(inode)->i_gc_rwsem[WRITE]);
			if (IS_ERR(data_page)) {
				iput(inode);
				continue;
			}

			f2fs_put_page(data_page, 0);
			add_gc_inode(gc_list, inode, false);
			continue;
		}

		/* phase 4 */
		inode = find_gc_inode(gc_list, dni.ino);
		if (inode) {
			struct f2fs_inode_info *fi = F2FS_I(inode);
			bool locked = false;
			int err;

			if (S_ISREG(inode->i_mode)) {
				if (!f2fs_down_write_trylock(&fi->i_gc_rwsem[READ])) {
					sbi->skipped_gc_rwsem++;
					continue;
				}
				if (!f2fs_down_write_trylock(
						&fi->i_gc_rwsem[WRITE])) {
					sbi->skipped_gc_rwsem++;
					f2fs_up_write(&fi->i_gc_rwsem[READ]);
					continue;
				}
				locked = true;

				/* wait for all inflight aio data */
				inode_dio_wait(inode);
			}

			start_bidx = f2fs_start_bidx_of_node(nofs, inode)
								+ ofs_in_node;
			if (f2fs_post_read_required(inode))
				err = move_data_block(inode, start_bidx,
							gc_type, segno, off);
			else // write data page to destination address
				err = move_data_page(inode, start_bidx, gc_type,
								segno, off);

			if (!err && (gc_type == FG_GC ||
					f2fs_post_read_required(inode)))
				submitted++;

			if (locked) {
				f2fs_up_write(&fi->i_gc_rwsem[WRITE]);
				f2fs_up_write(&fi->i_gc_rwsem[READ]);
			}

			stat_inc_data_blk_count(sbi, 1, gc_type);
		}
	}

	if (++phase < 5)
		goto next_step;

	return submitted;
}

static int __get_victim(struct f2fs_sb_info *sbi, unsigned int *victim,
			int gc_type)
{
	struct sit_info *sit_i = SIT_I(sbi);
	int ret;

	down_write(&sit_i->sentry_lock);
	ret = DIRTY_I(sbi)->v_ops->get_victim(sbi, victim, gc_type,
					      NO_CHECK_TYPE, LFS, 0);
	up_write(&sit_i->sentry_lock);
	return ret;
}

static int do_garbage_collect(struct f2fs_sb_info *sbi,
				unsigned int start_segno,
				struct gc_inode_list *gc_list, int gc_type,
				bool force_migrate)
{
	struct page *sum_page;
	struct f2fs_summary_block *sum;
	struct blk_plug plug;
	unsigned int segno = start_segno;
	unsigned int end_segno = start_segno + sbi->segs_per_sec;
	int seg_freed = 0, migrated = 0;
	unsigned char type = IS_DATASEG(get_seg_entry(sbi, segno)->type) ?
						SUM_TYPE_DATA : SUM_TYPE_NODE;
	int submitted = 0;
	unsigned long long t1, t2;

	t1 = ktime_get_ns();

	if (__is_large_section(sbi))
		end_segno = rounddown(end_segno, sbi->segs_per_sec);

	/*
	 * zone-capacity can be less than zone-size in zoned devices,
	 * resulting in less than expected usable segments in the zone,
	 * calculate the end segno in the zone which can be garbage collected
	 */
	if (f2fs_sb_has_blkzoned(sbi))
		end_segno -= sbi->segs_per_sec -
					f2fs_usable_segs_in_sec(sbi, segno);

	sanity_check_seg_type(sbi, get_seg_entry(sbi, segno)->type);

	f2fs_debug_ori_gc("f2fs_gc ra summary page, segno=%u  start:%u, nr_pages:%u",
			segno, GET_SUM_BLOCK(sbi, segno), end_segno - segno);
	/* readahead multi ssa blocks those have contiguous address */
	if (__is_large_section(sbi))
		f2fs_ra_meta_pages(sbi, GET_SUM_BLOCK(sbi, segno),
					end_segno - segno, META_SSA, true);

	/* reference all summary page */
	while (segno < end_segno) {
		f2fs_debug_ori_gc("f2fs_gc get sum page, segno=%u", segno);
		sum_page = f2fs_get_sum_page(sbi, segno++);
		if (IS_ERR(sum_page)) {
			int err = PTR_ERR(sum_page);

			end_segno = segno - 1;
			for (segno = start_segno; segno < end_segno; segno++) {
				sum_page = find_get_page(META_MAPPING(sbi),
						GET_SUM_BLOCK(sbi, segno));
				f2fs_put_page(sum_page, 0);
				f2fs_put_page(sum_page, 0);
			}
			return err;
		}
		unlock_page(sum_page);
	}

	f2fs_debug_ori_gc("f2fs_gc blk_start_plug");
	blk_start_plug(&plug);

	for (segno = start_segno; segno < end_segno; segno++) {

		f2fs_debug_ori_gc("do_garbage_collect: migrating segment %u, start block:%u", 
				segno, START_BLOCK(sbi, segno));
		f2fs_debug_ori_gc("curseg cold data, start block: %u", 
				START_BLOCK(sbi, CURSEG_I(sbi, CURSEG_COLD_DATA)->segno));
		__dump_sit_entry(get_seg_entry(sbi, segno), false);
		
		/* find segment summary of victim */
		sum_page = find_get_page(META_MAPPING(sbi),
					GET_SUM_BLOCK(sbi, segno));
		f2fs_put_page(sum_page, 0);

		if (get_valid_blocks(sbi, segno, false) == 0)
			goto freed;
		if (gc_type == BG_GC && __is_large_section(sbi) &&
				migrated >= sbi->migration_granularity)
			goto skip;
		if (!PageUptodate(sum_page) || unlikely(f2fs_cp_error(sbi)))
			goto skip;

		sum = page_address(sum_page);
		if (type != GET_SUM_TYPE((&sum->footer))) {
			f2fs_err(sbi, "Inconsistent segment (%u) type [%d, %d] in SSA and SIT",
				 segno, type, GET_SUM_TYPE((&sum->footer)));
			set_sbi_flag(sbi, SBI_NEED_FSCK);
			f2fs_stop_checkpoint(sbi, false,
				STOP_CP_REASON_CORRUPTED_SUMMARY);
			goto skip;
		}

		/*
		 * this is to avoid deadlock:
		 * - lock_page(sum_page)         - f2fs_replace_block
		 *  - check_valid_map()            - down_write(sentry_lock)
		 *   - down_read(sentry_lock)     - change_curseg()
		 *                                  - lock_page(sum_page)
		 */
		// data migration here
		if (type == SUM_TYPE_NODE)
			submitted += gc_node_segment(sbi, sum->entries, segno,
								gc_type);
		else
			submitted += gc_data_segment(sbi, sum->entries, gc_list,
							segno, gc_type,
							force_migrate);

		stat_inc_seg_count(sbi, type, gc_type);
		sbi->gc_reclaimed_segs[sbi->gc_mode]++;
		migrated++;

freed:
		if (gc_type == FG_GC &&
				get_valid_blocks(sbi, segno, false) == 0)
			seg_freed++;

		if (__is_large_section(sbi))
			sbi->next_victim_seg[gc_type] =
				(segno + 1 < end_segno) ? segno + 1 : NULL_SEGNO;
skip:
		// if(submitted==0){
		// 	__dump_summary(sum);
		// }
		f2fs_put_page(sum_page, 0);
	}

	// submit the megred fio write requests to bio layer
	if (submitted)
		f2fs_submit_merged_write(sbi,
				(type == SUM_TYPE_NODE) ? NODE : DATA);

	blk_finish_plug(&plug);
	t2 = ktime_get_ns();
	// for xin: cumulate t2-t1 to total GC time
	printk(KERN_INFO "f2fs_gc blk_finish_plug, submitted:%u, time: %llu us", 
			submitted, (t2 - t1) / 1000);
	sbi->origc_blks_migrated += submitted;
	sbi->origc_seg_freed += migrated;
	sbi->origc_total_latency_ns += t2 - t1;

	stat_inc_call_count(sbi->stat_info);

	return seg_freed;
}

static void dump_dirty_sit_pack(struct dirty_sit_pack *dsp)
{
    struct dsp_entry *dsp_ent;
    int i;
    f2fs_debug_csgc("Dirty SIT pack, nr_dirty_se = %u:", dsp->nr_dirty_se);
    for(i = 0; i < dsp->nr_dirty_se; i++){
        dsp_ent = &dsp->entry[i];
        f2fs_debug_csgc("segno = %u", dsp_ent->segno);
        __dump_sit_entry_raw(&dsp_ent->se);
    }
}

static void dump_curseg_pack(struct curseg_pack *csp)
{
    f2fs_debug_csgc("Curseg pack:");
    f2fs_debug_csgc("alloc_type = %d, seg_type = %d, segno = %u, next_blkoff = %u, inited = %d", 
            csp->alloc_type, csp->seg_type, csp->segno, csp->next_blkoff, csp->inited);
    __dump_summary(&csp->sum_blk);
}

static void dump_pseg_pack(struct pseg_pack *psp, unsigned int nr_pseg)
{
    f2fs_debug_csgc("Pre-allocated segment pack, nr_summaries%u:", psp->nr_summaries);
	for(int i = 0 ; i < nr_pseg; i++){
		f2fs_debug_csgc("seg_type = %d, segno = %u, start_blkoff = %u, end_blkoff = %u, sum_len = %u",
				psp->sum_info[i].seg_type, psp->sum_info[i].segno, 
				psp->sum_info[i].start_blkoff, psp->sum_info[i].end_blkoff, psp->sum_info[i].sum_len);
	}
}

static void dump_dirty_node_pack(struct dirty_node_pack *dnp)
{
    struct dnp_entry *dnp_ent;
    unsigned int offset = 0;
    int i, j;
    f2fs_debug_csgc("Dirty node pack, nr_dirty_node = %u:", dnp->nr_dirty_node);
	if(dnp->nr_dirty_node > 512){
		printk(KERN_INFO "Invalild nr_dirty_node: %u\n", dnp->nr_dirty_node);
		return;
	}
    for(i = 0; i < dnp->nr_dirty_node; i++){
        dnp_ent = (struct dnp_entry *)(dnp->dnp_entries + offset);
        offset += get_dnp_entry_size(dnp_ent);
        f2fs_debug_csgc("-----<nid = %u, nr_ext = %u>-----", dnp_ent->nid, dnp_ent->nr_ext);
        for(j = 0; j < dnp_ent->nr_ext; j++){
            f2fs_debug_csgc("ext[%d]: ofs_in_node = %d, new_addr = %u, len = %u", 
                    j, dnp_ent->exts[j].ofs_in_node, dnp_ent->exts[j].new_addr, dnp_ent->exts[j].len);
        }
    }

}

static void dump_csgc_debug_info(struct csgc_package *package)
{
	struct offset_info *offs = &package->header.offs;
	if(offs->data_size_d2h > offs->debug_start){
		__dump_summary((struct f2fs_summary_block *) (package->data + offs->debug_start));
	}
}

static void dump_csgc_package_h2d(struct csgc_package *package)
{
    struct csgc_header *header = &package->header;
    void *base_addr = package->data;
	struct node_info *ni;
	struct f2fs_sit_entry *sentry;
	struct pre_alloc_seg_info *pi;

	f2fs_debug_csgc("capacity: %u, npages: %u", header->capacity, header->npages);
    f2fs_debug_csgc("pages: %p, pages_recv: %p", header->pages, header->pages_recv);
    f2fs_debug_csgc("segno: %u, status: %d", header->segno, header->status[0]);
    f2fs_debug_csgc("prealloc_curseg_segno: %u, nr_pre_alloc: %u", 
					header->prealloc_curseg_segno, header->nr_pre_alloc);
    f2fs_debug_csgc("nr_node_info: %u, meta_sent_from_host: %s", 
					header->nr_node_info, header->meta_sent_from_host ? "true" : "false");
    f2fs_debug_csgc("print_offset: %u, print_size: %u", 
					header->print_offset, header->print_size);

	// Assuming meta_sent_from_host indicates usage of `offs` in union
	f2fs_debug_csgc("offs: nat_start: %u, sit_start_h2d: %u, prealloc_start: %u, data_size_h2d: %u",
					header->offs.nat_start, header->offs.sit_start_h2d, 
					header->offs.prealloc_start, header->offs.data_size_h2d);
	
	ni = (struct node_info *)(base_addr + header->offs.nat_start);
	for(int i = 0; i < header->nr_node_info; i++){
		f2fs_debug_csgc("node_info[%d]: nid = %u, ino = %u, blk_addr = %u, version = %hhx, flag = %hhx", 
				i, ni[i].nid, ni[i].ino, ni[i].blk_addr, ni[i].version, ni[i].flag);
	}

	sentry = (struct f2fs_sit_entry *)(base_addr + header->offs.sit_start_h2d);
	__dump_sit_entry_raw(sentry);

	pi = (struct pre_alloc_seg_info *)(base_addr + header->offs.prealloc_start);
	for(int i = 0; i < header->nr_pre_alloc; i++, pi++){
		f2fs_debug_csgc("PSEG info: segno=%u, seg_type=%u, is_curseg=%d, start_off=%u, len=%u\n",
			pi->segno, pi->seg_type, pi->is_curseg, pi->start_off, pi->len);
	}
}

static void dump_csgc_package(struct csgc_package *package) 
{
    struct csgc_header *header = &package->header;
    void *base_addr = package->data;
    struct dirty_sit_pack *dsp;
    struct dirty_node_pack *dnp;

    f2fs_debug_csgc("package_pointer = %016llx",(unsigned long long) package);
	f2fs_debug_csgc("CSGC package header:");
    f2fs_debug_csgc("capacity = %u, npages = %u, pages_pointer = %016llx, segno = %u, status = %d", 
            header->capacity, header->npages, (unsigned long long)header->pages, header->segno, header->status[0]);
    f2fs_debug_csgc("data_pointer = %016llx, sit_start = %u, curseg_start = %u, dnode_start = %u, data_size = %u", 
            (unsigned long long)package->data, header->offs.sit_start, header->offs.dirty_sum_start, 
			header->offs.dnode_start, header->offs.data_size_d2h);
    
    // dsp = (struct dirty_sit_pack *)(base_addr + header->offs.sit_start);
    // dump_dirty_sit_pack(dsp);

    // if(package->header.nr_pre_alloc){
		// struct pseg_pack *psp = (struct pseg_pack *)(base_addr + header->offs.dirty_sum_start);
		// dump_pseg_pack(psp, header->nr_pre_alloc);
	// }else{
	// 	struct curseg_pack *csp = (struct curseg_pack *)(base_addr + header->offs.curseg_start);
	// 	dump_curseg_pack(csp);
	// }

    // dnp = (struct dirty_node_pack *)(base_addr + header->offs.dnode_start);
    // dump_dirty_node_pack(dnp);

}	

static void show_csgc_pack_page_ptr(struct csgc_info *csi)
{
	struct csgc_package *package = csi->csgc_pkg;
	printk(KERN_INFO "pkg: %p, pages:%p(pa:%p), pages_recv:%p(pa:%p)", 
		package, package->header.pages, page_address(package->header.pages), 
		package->header.pages_recv, page_address(package->header.pages_recv));
	
	package = csi->csgc_pkg_recv;
	printk(KERN_INFO "pkg_recv: %p, pages:%p(pa:%p), pages_recv:%p(pa:%p)", 
		package, package->header.pages, page_address(package->header.pages), 
		package->header.pages_recv, page_address(package->header.pages_recv));
}

static void init_csgc_pack(struct csgc_package *package, 
			unsigned int segno, unsigned int head_segno, 
			unsigned int max_nr_cpus, bool send_meta_from_host)
{
	struct csgc_header *pkg_header = &package->header;

	pkg_header = &package->header;

	pkg_header->segno = segno;
	pkg_header->head_segno = head_segno;

	pkg_header->nr_pre_alloc = 0;
	pkg_header->nr_node_info = 0;
	pkg_header->meta_sent_from_host = send_meta_from_host;
	pkg_header->max_nr_cpus = max_nr_cpus;

	pkg_header->print_offset = 0;
	pkg_header->print_size = 0;
	memset(pkg_header->status, 0, sizeof(pkg_header->status));
	
	memset(&pkg_header->offs, 0, sizeof(struct offset_info));
}

static void set_csgc_segno(struct csgc_package *package, unsigned int segno)
{
	package->header.segno = segno;
}

static void free_csgc_pack(struct csgc_package *package)
{
	int order = get_order(package->header.capacity);
	struct page *page = package->header.pages;
	struct page *page_recv = package->header.pages_recv;
	__free_pages(page, order);
	__free_pages(page_recv, order);
}

static int check_cs_status(struct csgc_header *pkg_header)
{
	for(int i = 0; i < pkg_header->max_nr_cpus; i++){
		if(pkg_header->status[i] != CSGC_SUCCESS){
			return pkg_header->status[i];
		}
	}
	return CSGC_SUCCESS;
}

static void check_err_node_info(struct f2fs_csgc_context *csgc_ctx, struct csgc_package *package)
{
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct csgc_header *header = &package->header;
	struct csgc_error_info *err_info = &header->err_info;
	struct node_info ni;
	struct inode *inode;
	struct page *node_page, *sum_page;
	struct inode_entry *ie;
	struct dnode_entry *de;
	struct f2fs_summary_block *sum;

	//print the fields of struct csgc_error_info
	printk(KERN_INFO "src_segno:%u, dst_segno:%u, src_blkaddr:%u, dst_blkaddr:%u", 
			err_info->src_segno, err_info->dst_segno, err_info->src_blkaddr, err_info->dst_blkaddr);
	printk(KERN_INFO "dno:%u, ino:%u, ofs_in_node:%u", 
			err_info->dno, err_info->ino, err_info->ofs_in_node);
	printk(KERN_INFO "CSGC failed: block address inconsistency in dnode");

	sum_page = f2fs_get_sum_page(sbi, header->segno);
	if(IS_ERR(sum_page)){
		printk(KERN_INFO "sum page is err: %ld", PTR_ERR(sum_page));
		return;
	}
	printk(KERN_INFO "Summary page of segno %u, PageDirty(%d), PageUpToDate(%d)", 
			header->segno, PageDirty(sum_page), PageUptodate(sum_page));
	sum = (struct f2fs_summary_block *) page_address(sum_page);
	printk(KERN_INFO "Summary entry of the block %u, offset in segment:%u", 
			err_info->src_blkaddr, err_info->src_blkaddr % 512);
	__dump_sum_entry(page_address(sum_page), err_info->src_blkaddr % 512, 1);
	
	de = find_gc_dnode_entry(&csgc_ctx->gc_dlist, err_info->dno);
	if(!de){
		printk(KERN_INFO "dnode entry not found, nid = %u", err_info->dno);
		return;
	}
	printk(KERN_INFO "dnode entry: nid:%u, ino:%u, refcnt:%u, rollback_refcnt:%u",
			de->nid, de->ino_nid, de->refcnt, de->rollback_refcnt);
	node_page = de->page;
	if(IS_ERR(node_page)){
		printk(KERN_INFO "dnode page is err: %ld", PTR_ERR(node_page));
		return;
	}

	ie = find_gc_inode_entry(&csgc_ctx->gc_ilist, err_info->ino);
	if(!ie){
		printk(KERN_INFO "inode entry not found, nid = %u", err_info->ino);
		return;
	}
	printk(KERN_INFO "inode entry: nid: %u, refcnt:%u, rollback_refcnt:%u",
			err_info->ino, ie->refcnt, ie->rollback_refcnt);
	inode = ie->inode;
	if(IS_ERR(inode)){
		printk(KERN_INFO "inode is err: %ld", PTR_ERR(inode));
		return;
	}
	f2fs_get_node_info(sbi, err_info->dno, &ni, false);

	printk(KERN_INFO "segno:%u, blkaddr:%u, dno:%u, ino:%u/%u, ofs_in_node:%u",
			header->segno, err_info->src_blkaddr, err_info->dno, err_info->ino, 
			F2FS_NODE(node_page)->footer.ino, err_info->ofs_in_node);
	printk(KERN_INFO "dnode page: PageUpToDate(%d) PageDirty(%d) PageWriteback(%d)",
			PageUptodate(node_page), PageDirty(node_page), PageWriteback(node_page));
	printk(KERN_INFO "dnode info: nid:%u, ino:%u, blkaddr:%u, version:%u, flag:%u",
			ni.nid, ni.ino, ni.blk_addr, ni.version, ni.flag);
	f2fs_dump_node(inode, node_page, err_info->ofs_in_node, 20, 0);

	printk(KERN_INFO "Check dnode page again after clear uptodate");
	ClearPageUptodate(node_page);
	f2fs_put_page(node_page, 1);
	node_page = f2fs_get_node_page(sbi, err_info->dno);
	if(IS_ERR(node_page)){
		printk(KERN_INFO "dnode page is err: %ld", PTR_ERR(node_page));
		return;
	}
	f2fs_dump_node(inode, node_page, err_info->ofs_in_node, 20, 0);
}

static void check_err_seg_info(struct f2fs_sb_info *sbi, struct csgc_package *package)
{
	struct csgc_header *header = &package->header;
	struct csgc_error_info *err_info = &header->err_info;
	f2fs_check_segmap_info(sbi, true);
	printk(KERN_INFO "Check csgc seg info of source seg, segno=%u",
		err_info->src_segno);
	__dump_sit_entry(get_seg_entry(sbi, err_info->src_segno), 1);
	printk(KERN_INFO "Check csgc seg info of destination seg, segno=%u",
		err_info->dst_segno);
	__dump_sit_entry(get_seg_entry(sbi, err_info->dst_segno), 1);
}

static void check_err_info(struct csgc_info *csi, struct csgc_package *package)
{
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct csgc_header *header = &package->header;
	int status = check_cs_status(header);


	switch (-status) {
	case CSGC_NOMEM:
		printk(KERN_INFO "CSGC failed: not enought memory space for meta data");
		break;
	case CSGC_INCONSISTENT:
	case CSGC_FAILREAD:
		check_err_node_info(csgc_ctx, package);
		break;
	case CSGC_WRONG_SIT:
	case CSGC_NO_FREE_SEG:
		check_err_seg_info(sbi, package);
		break;
	default:
		f2fs_debug_csgc("CSGC failed: unknown error ");
	}
}

static void check_sum_page(struct f2fs_sb_info *sbi, unsigned int segno)
{
	struct page *sum_page;
	sum_page = f2fs_get_sum_page(sbi, segno);
	if(!IS_ERR(sum_page)){
		__dump_summary((struct f2fs_summary_block *) page_address(sum_page));
		f2fs_put_page(sum_page, 1);
	}
}

static void set_dnode_ext_info(struct dnode_ext_info *dn, 
				struct gc_inode_list *gc_list, nid_t ino_nid,
				nid_t dno_nid, unsigned int ofs)
{
	dn->inode = find_gc_inode(gc_list, ino_nid);
	dn->nid = dno_nid;
	dn->ofs_of_node = ofs;
}

static void set_dnode_ext_page(struct f2fs_sb_info *sbi, struct dnode_ext_info *dn, 
		struct gc_inode_list *gc_ilist, struct gc_dnode_list *gc_dlist)
{
	struct f2fs_node *dno;
	nid_t ino_nid = dn->inode->i_ino, dno_nid = dn->nid;
	struct inode_entry *ie;
	struct dnode_entry *de;

	ie = find_gc_inode_entry(gc_ilist, ino_nid);
	de = find_gc_dnode_entry(gc_dlist, dno_nid);
	dn->ie = ie;
	dn->de = de;
	dn->inode_page = ie->page;
	if(ino_nid==dno_nid)
		dn->node_page = dn->inode_page;
	else{
		dn->node_page = de->page;
	}
	if(dn->node_page){
		dno = (struct f2fs_node *) page_address(dn->node_page);
		if(ino_nid != ino_of_node(dn->node_page))
			printk(KERN_INFO "inode number inconsistent: %u(cs), %u(host mem)\n", 
				ino_nid, ino_of_node(dn->node_page));
		if(dn->ofs_of_node != ofs_of_node(dn->node_page))
			printk(KERN_INFO "ofs_of_node inconsistent: %u(cs), %u(host mem)\n", 
				dn->ofs_of_node, ofs_of_node(dn->node_page));
	}
}

// update dnodes changed by csgc to sync with device
static int update_csgc_dnodes(struct f2fs_sb_info *sbi, 
				struct csgc_package *package,
				struct gc_inode_list *gc_ilist,
				struct gc_dnode_list *gc_dlist,
				unsigned int req_idx)
{
	int ret = 0;
	unsigned int offset = 0;
	struct dirty_node_pack *dnp;
	struct dnp_entry *dnp_ent;
	struct dnode_ext_info dn;
	unsigned int nr_dnode, nr_ext;
	
	// show_gc_dnode_list(gc_dlist);
	// show_gc_inode_list(gc_ilist);
	dnp = (struct dirty_node_pack *)(package->data + package->header.offs.dnode_start);
	nr_dnode = dnp->nr_dirty_node;
	for(int i = 0; i < nr_dnode; i++){
		dnp_ent = (struct dnp_entry *) (dnp->dnp_entries + offset);
		offset += get_dnp_entry_size(dnp_ent);
		nr_ext = dnp_ent->nr_ext;
		if(!nr_ext)
			continue;

		set_dnode_ext_info(&dn, gc_ilist, dnp_ent->ino_nid, 
				dnp_ent->nid, dnp_ent->ofs_of_node);
		if(dn.inode == NULL){
			ret = -CSGC_INCONSISTENT;
			printk(KERN_ERR "inode(%u) not found in csgc inode list\n", dnp_ent->ino_nid);
#ifdef CONFIG_F2FS_CSGC_DEBUG
			dump_dirty_node_pack(dnp);
			printk(KERN_INFO "summary page of segno = %u in host(before add gc inode):", package->header.segno);
			__dump_summary((struct f2fs_summary_block *) sbi->csgc_private);
			printk(KERN_INFO "summary page of segno = %u in host(now):", package->header.segno);
			check_sum_page(sbi, package->header.segno);
			printk(KERN_INFO "summary page of segno = %u in device:", package->header.segno);
			dump_csgc_debug_info(package);
#endif
			goto out;
		}

		set_dnode_ext_page(sbi, &dn, gc_ilist, gc_dlist);
		f2fs_update_csgc_dnode(sbi, &dn, dnp_ent, req_idx);
	}

out:
	return ret;
}

static void print_cs_outputs(struct csgc_package *package)
{
	char *buf;
	struct csgc_header *header = &package->header;
	unsigned int size = header->print_size;
	unsigned int offset_l = 0, offset_h = 0;
	int log_line_max = 900;  // check LOG_LINE_MAX
	char tmp;

	f2fs_debug_csgc("Ready to dump cs outputs, size = %u", size);
	if(size == 0 || size > 16384)
	{	
		printk(KERN_INFO "Invalid size");
		return;
	}
	
	buf = package->data + header->print_offset;
	if(buf[size - 1] != '\0')
		buf[size - 1] = '\0';
	while(offset_l < size - 1){
		offset_h = min(offset_l + log_line_max , size - 1);
		while(buf[offset_h]!='\n' && offset_h > offset_l)
			offset_h--;
		if(offset_h == offset_l)
			offset_h = min(offset_l + log_line_max , size - 1);
		else
		 	offset_h += 1; // can't be greater than size - 1, since buf[size - 1]=='\0'
		
		tmp = buf[offset_h];
		buf[offset_h] = '\0';
		printk("%s", buf + offset_l);
		buf[offset_h] = tmp;

		offset_l = offset_h;
	}
}

static void put_gc_data_pages(struct csgc_info *csi);

// update seg info(SIT entries and curseg) and dnode info to sync with device
int f2fs_update_csgc_meta(struct csgc_info *csi)
{
	int ret;
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct csgc_package *package = (struct csgc_package *) page_address(csi->csgc_pkg->header.pages_recv);
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct csgc_header *pkg_header = &package->header;
	int status = check_cs_status(pkg_header);

#ifdef CONFIG_F2FS_CSGC_DEBUG_PACK
	dump_csgc_package(package);
#endif
	if(status){
		ret = status;
		f2fs_printk(sbi, "CSGC wrong status: %d %d %d | #C=%u\n", pkg_header->status[0], 
				pkg_header->status[1], pkg_header->status[2], pkg_header->max_nr_cpus);
		check_err_info(csi, package);
		// print_cs_outputs(package);
		goto out;
	}
	
	// print_cs_outputs(package);
	f2fs_debug_csgc("Ready to update csgc seg info");
	if(pkg_header->nr_pre_alloc)
		ret = f2fs_update_prealloc_seg_summary(sbi, package);
	else
		ret = f2fs_update_csgc_seg_info(sbi, package);
	if(ret)
		goto out;

	ret = update_csgc_dnodes(sbi, package, gc_ilist, gc_dlist, 
			csi->segno - csgc_ctx->start_segno);

out:
	if(status || ret)
		print_cs_outputs(package);
	csgc_unlock_op(csgc_ctx);
	put_gc_data_pages(csi);
	csi->stat.update_meta_time = ktime_get_ns();
	return ret;
}

static int request_csgc(struct f2fs_sb_info *sbi, struct csgc_info *csi)
{
	struct csgc_package *package = csi->csgc_pkg;
	struct csgc_header *pkg_header = &package->header;
	struct f2fs_io_info fio = {
		.sbi = sbi,
		.op = REQ_OP_WRITE,					// CS GC request is wrapped in nvme write command.
		.op_flags = REQ_CSGC | REQ_SYNC,	// REQ_CSGC results in CS bit set in control field 
											// of nvme write command
		.old_blkaddr = NULL_ADDR,
		.new_blkaddr = MAIN_BLKADDR(sbi), 	// A dummy addr to fill in nvme write command.
											// With CS bit set, nvme write command will not
											// perform write to this addr.
		.encrypted_page = NULL,
		.need_lock = LOCK_REQ, // TODO: consider lock later
	};
	
	fio.page = pkg_header->pages;

	return f2fs_submit_csgc_bio(&fio, csi, 0);
}

static int wait_csgc_result(struct f2fs_sb_info *sbi, struct csgc_info *csi)
{
	struct csgc_package *package = csi->csgc_pkg;
	int sync = csi->csgc_ctx->sync;
	struct csgc_header *pkg_header = &package->header;
	struct f2fs_io_info fio = {
		.sbi = sbi,
		.op = REQ_OP_READ,
		.op_flags = REQ_CSGC | REQ_SYNC,
		.old_blkaddr = NULL_ADDR,
		.new_blkaddr = MAIN_BLKADDR(sbi),
		.encrypted_page = NULL,
		.need_lock = LOCK_REQ,
	};

	fio.page = pkg_header->pages_recv;

	return f2fs_submit_csgc_bio(&fio, csi, sync);
}

static int sync_fs_before_csgc(struct f2fs_sb_info *sbi, struct f2fs_csgc_context *csgc_ctx)
{
	int ret = 0;
	unsigned long long t1, t2;
	struct cp_control cpc;

	// TODO: avoid write checkpoint, send needed data with the request
	t1 = ktime_get_ns();
	cpc.reason = __get_cp_reason(sbi);

	if(!is_sbi_flag_set(sbi, SBI_IS_DIRTY)){
		f2fs_printk(sbi, "sbi already up-to-date, ckpt_ver = %llx, "
				"set sbi dirty to force a ckpt again",
				cur_cp_version(F2FS_CKPT(sbi)));
		set_sbi_flag(sbi, SBI_IS_DIRTY);
	}
	ret = f2fs_write_checkpoint(sbi, &cpc);
	if(ret){
		f2fs_printk(sbi, "csgc fail to write checkpoint, ret = %d", ret);
		return ret;
	}
	f2fs_printk(sbi, "successfully write ckpt, ckpt_ver = %llx", cur_cp_version(F2FS_CKPT(sbi)));

	t2 = ktime_get_ns();
	f2fs_printk(sbi, "write checkpoint before gc takes %llu us", (t2-t1)/1000);

	if(csgc_ctx){
		csgc_ctx->start_time = t1;
		csgc_ctx->sync_fs_time = t2;
	}

	return ret;
}

static inline void dump_meta_before_csgc(struct f2fs_sb_info *sbi, unsigned int segno)
{
	struct page *sum_page;

	__dump_sit_entry(get_seg_entry(sbi, segno), true);
	sum_page = f2fs_get_sum_page(sbi, segno);
	if(!IS_ERR(sum_page)){
		__dump_summary((struct f2fs_summary_block *) page_address(sum_page));
		f2fs_put_page(sum_page, 1);
	}
}

static int pack_node_info(struct f2fs_sb_info *sbi, struct csgc_package *package,
				struct gc_dnode_list *dno_list, unsigned int req_idx)
{
	struct csgc_header *header = &package->header;
	unsigned int start_offset = header->offs.nat_start;
	unsigned int *end_offset = &header->offs.sit_start_h2d;
	unsigned int *nr_node_info = &header->nr_node_info;
	struct node_info ni;
	struct node_info *packed_ni = (struct node_info *)(package->data + start_offset);
	struct dnode_entry *de;
	int i = 0;
	int ret = 0;

	if(list_empty(&dno_list->dlist)){
		printk(KERN_INFO "No dnode in the list");
		ret = -ENOENT;
		return ret;
	}
	list_for_each_entry(de, &dno_list->dlist, list){
		if(!de->refby[req_idx])
			continue;
		ret = f2fs_get_node_info(sbi, de->nid, &ni, false);
		if(ret){
			printk(KERN_INFO "Fail to get node info of nid = %u", de->nid);
			return ret;
		}
		packed_ni[i] = ni;
		f2fs_debug_csgc("packed node info: nid = %u, ino = %u, blk_addr = %u, version = %u, flag = %u", 
			ni.nid, ni.ino, ni.blk_addr, ni.version, ni.flag);
		i++;
	}
	*nr_node_info = i;
	*end_offset = start_offset + (*nr_node_info) * sizeof(struct node_info);

	return ret;
}

static void pack_sit_entry(struct f2fs_sb_info *sbi, struct csgc_package *package, 
				unsigned int segno)
{
	struct seg_entry *se = get_seg_entry(sbi, segno);
	struct f2fs_sit_entry *rs = (struct f2fs_sit_entry *)(package->data + \
					package->header.offs.sit_start_h2d);
	unsigned short raw_vblocks = (se->type << SIT_VBLOCKS_SHIFT) |
					se->valid_blocks;

	rs->vblocks = cpu_to_le16(raw_vblocks);
	memcpy(rs->valid_map, se->cur_valid_map, SIT_VBLOCK_MAP_SIZE);
	rs->mtime = cpu_to_le64(se->mtime);

	package->header.offs.prealloc_start = package->header.offs.sit_start_h2d + sizeof(*rs);
}

// get dnodes and inodes involved in CSGC
static int get_gc_node_list(struct f2fs_sb_info *sbi, unsigned int segno, 
				struct gc_inode_list *gc_list, struct gc_dnode_list *dno_list,
				unsigned int req_idx)
{
	struct page *sum_page;
	struct f2fs_summary_block *sum_block;
	struct f2fs_summary *sum_entry;
	nid_t dnode_nid, ino_nid;
	struct node_info ni;
	struct inode *inode;
	struct inode_entry *ie;
	int i;
	int err = 0;

	sum_page = find_get_page(META_MAPPING(sbi),
					GET_SUM_BLOCK(sbi, segno));
	f2fs_put_page(sum_page, 0);
	sum_block = page_address(sum_page);

	for(i = 0; i < sbi->blocks_per_seg; i++){
		if(check_valid_map(sbi, segno, i) == 0)
			continue;
		
		sum_entry = sum_block->entries + i;
		dnode_nid = le32_to_cpu(sum_entry->nid);
		err = f2fs_get_node_info(sbi, dnode_nid, &ni, false);
		if(err){
			printk(KERN_INFO "Fail to get node info of nid = %u, sum entry offset = %d", 
						dnode_nid, i);
			__dump_summary(sum_block);
			goto out;
		}

		ino_nid = ni.ino;
		inode = f2fs_iget(sbi->sb, ino_nid);
		if(IS_ERR(inode)){
			err = PTR_ERR(inode);
			printk(KERN_INFO "Fail to get inode of nid = %u, err = %ld, blkoff = %d", 
					ino_nid, PTR_ERR(inode), i);
			if(err == -ENOENT){	// maybe the inode was truncated by others
				err = 0;
				continue;
			}
			goto out;
		}
		add_gc_dnode(dno_list, dnode_nid, ni.ino);
		ie = add_gc_inode(gc_list, inode, true);
		ie->gc_rwsem_req_by[req_idx] = true;

	}
out:
	if(err){
		printk(KERN_INFO "Fail to get gc node list, dumping summary");
		__dump_summary(sum_block);
	}
	clear_gc_inode_rollback_refcnt(gc_list);
	clear_gc_dnode_rollback_refcnt(dno_list);
	return err;
}

static struct page *get_or_grab_data_page(struct inode *inode, block_t index)
{
	struct page *page;
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
	unsigned long long t1, t2;

	t1 = ktime_get_ns();
#endif
	page = f2fs_grab_cache_page(inode->i_mapping, index, 1);
	unlock_page(page);
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
	t2 = ktime_get_ns();
#endif
	if(PageUptodate(page)){
		sbi->csgc_dpage_cached_count ++;
		if(PageDirty(page))
			sbi->csgc_dpage_dirty_count ++;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		sbi->csgc_get_dpage_time_ns += t2 - t1;
#endif
	}else{
		sbi->csgc_dpage_hole_count ++;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		sbi->csgc_grab_dpage_time_ns += t2 - t1;
#endif
	}

	return page;
}

static void put_gc_folio_entry(struct gc_folio_list *gc_flist, struct folio_entry *fe)
{
	fe->refcnt --;
	WARN_ON(fe->refcnt < 0);
	if(fe->refcnt == 0){
		// TODO: maybe delete from rb_tree
		folio_unlock(fe->folio);
		fe->is_valid = 0;
	}
}

static int get_lock_gc_data_pages(struct csgc_info *csi)
{
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	unsigned int segno = csi->segno;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct gc_folio_list *gc_flist = &csgc_ctx->gc_flist;
	struct gc_data_list *gc_datalist = &csi->gc_data_list;
	struct inode_entry *ie;
	struct dnode_entry *de;
	struct folio_entry *fe;
	struct data_entry *data_ent;
	struct page *sum_page, *node_page, *data_page;
	struct folio *data_folio;
	struct f2fs_summary_block *sum_block;
	struct f2fs_summary *sum_entry;
	nid_t dnode_nid, ino_nid;
	unsigned int nofs, ofs_in_node;
	block_t bidx;
	struct off2folio{
		unsigned int ofs;
		struct data_entry *data_ent;
	} *map;
	int ret = 0, i, vblocks = 0;
	bool should_put = false;
	unsigned long long t1, t2;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
	unsigned long long t1_1 = 0, t1_2 = 0, t1_3 = 0, t1_4 = 0;
	unsigned long long a, b, c, d, e;
#endif
	t2 = t1 = ktime_get_ns();
	vblocks = get_valid_blocks(sbi, segno, false);
	printk(KERN_INFO "get lock gc data pages, segno = %u, valid blocks = %d", segno, vblocks);
	map = kmalloc_array(vblocks, sizeof(struct off2folio), GFP_NOFS);
	vblocks = 0;

	sum_page = find_get_page(META_MAPPING(sbi),
					GET_SUM_BLOCK(sbi,segno));
	f2fs_put_page(sum_page, 0);
	sum_block = page_address(sum_page);

	for(i = 0; i < sbi->blocks_per_seg; i++){
		if(check_valid_map(sbi, segno, i) == 0)
			continue;
		sum_entry = sum_block->entries + i;
		dnode_nid = le32_to_cpu(sum_entry->nid);
		ofs_in_node = le16_to_cpu(sum_entry->ofs_in_node);

#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		a = ktime_get_ns();
#endif
		de = find_gc_dnode_entry(gc_dlist, dnode_nid);
		if(!de){
			// maybe during truncation
			printk(KERN_INFO "dnode(%u) not found in csgc dnode list, blkoff=%d", 
					dnode_nid, i);
			continue;
		}
		ino_nid = de->ino_nid;
		ie = find_gc_inode_entry(gc_ilist, ino_nid);

#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		b = ktime_get_ns();
#endif
		if(de->page)
			node_page = de->page;
		else{
			// if current inode also dnode in this request, and is only inode in previous request
			// then without this if, dead lock happens, since it tries to get the inode page 
			// locked in the previous request 
			if(dnode_nid==ino_nid && ie->page)
				node_page = ie->page;
			else{
				// f2fs_debug_csgc("[%d]lock dnode page(nid=%u)", i, dnode_nid);
				node_page = f2fs_get_node_page(sbi, de->nid);
				should_put = true;
			}
		}
		nofs = ofs_of_node(node_page);
		bidx = f2fs_start_bidx_of_node(nofs, ie->inode) + ofs_in_node;
		if(should_put)
			f2fs_put_page(node_page, 1);
		should_put = false;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		c = ktime_get_ns();
#endif
		// printk(KERN_INFO "get or grab data page(off=%u, bidx=%u) of inode(%u), dnode(%u)", 
		// 		i, bidx, ino_nid, dnode_nid);
		data_page = get_or_grab_data_page(ie->inode, bidx);
		// printk(KERN_INFO "got page, inode(%u), bidx(%u), page=%p, folio=%p, folio_nr_pages=%ld",
		// 		ino_nid, bidx, data_page, page_folio(data_page), 
		// 		folio_nr_pages(page_folio(data_page)));
		if(IS_ERR(data_page)){
			ret = PTR_ERR(data_page);
			printk(KERN_INFO "Fail to get data page(idx=%u) of inode(%u), err = %d", 
						bidx, ino_nid, ret);
			goto out;
		}

#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		d = ktime_get_ns();
#endif
		data_folio = page_folio(data_page);
		data_ent = add_gc_data_page(gc_datalist, data_page);
		fe = add_gc_folio(gc_flist, data_folio);
		fe->delta_refcnt ++;
		data_ent->fe = fe;

		map[vblocks].ofs = i;
		map[vblocks].data_ent = data_ent;
		vblocks++;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
		e = ktime_get_ns();
		t1_1 += b - a; t1_2 += c - b; t1_3 += d - c; t1_4 += e - d;
#endif
	}
	t2 = ktime_get_ns(); f2fs_debug_csgc("add folio takes %llu us", (t2-t1)/1000); t1 = t2;
#ifdef CONFIG_F2FS_CSGC_DEBUG_ADD_PAGE_PERF
	f2fs_debug_csgc("breakdown: %llu, %llu, %llu, %llu", t1_1/1000, t1_2/1000, t1_3/1000, t1_4/1000);
#endif

	// follow the folio lock order 
	for(struct rb_node *n = rb_first(&gc_flist->folio_rbroot); n; n = rb_next(n)){
		fe = rb_entry(n, struct folio_entry, rb_node);
		if(fe->refcnt == 0 && fe->is_valid)
			folio_lock(fe->folio);
		fe->refcnt += fe->delta_refcnt;
		fe->delta_refcnt = 0;
	}
	t2 = ktime_get_ns(); f2fs_debug_csgc("lock folio takes %llu us", (t2-t1)/1000); t1 = t2;

	for(i = 0; i < vblocks; i++){
		if(check_valid_map(sbi, segno, map[i].ofs))
			continue;
		// someone move the page for us during our waiting for the folio lock
		data_ent = map[i].data_ent;
		f2fs_debug_csgc("someone moves the page for us, offset = %d", map[i].ofs);
		f2fs_put_page(data_ent->page, 0);
		data_ent->page = NULL;
		put_gc_folio_entry(gc_flist, data_ent->fe);
		del_gc_data_entry(gc_datalist, data_ent);
	}
	t2 = ktime_get_ns(); f2fs_debug_csgc("check folio takes %llu us", (t2-t1)/1000); t1 = t2;

out:
	kfree(map);
	return ret;
}

static int check_gc_data_validness(struct csgc_info *csi)
{
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	unsigned int segno = csi->segno;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct inode_entry *ie;
	struct dnode_entry *de;
	struct page *sum_page;
	struct f2fs_summary_block *sum_block;
	struct f2fs_summary *sum_entry;
	nid_t dnode_nid, ino_nid;
	unsigned int ofs_in_node;
	int ret = 0, i;

	sum_page = find_get_page(META_MAPPING(sbi),
					GET_SUM_BLOCK(sbi,segno));
	f2fs_put_page(sum_page, 0);
	sum_block = page_address(sum_page);

	printk(KERN_INFO "Checking CSGC data validness...");
	for(i = 0; i < sbi->blocks_per_seg; i++){
		if(check_valid_map(sbi, segno, i) == 0)
			continue;
		sum_entry = sum_block->entries + i;
		dnode_nid = le32_to_cpu(sum_entry->nid);
		ofs_in_node = le16_to_cpu(sum_entry->ofs_in_node);

		
		de = find_gc_dnode_entry(gc_dlist, dnode_nid);
		if(!de){
			printk(KERN_INFO "dnode(%u) not found in csgc dnode list, blkoff=%d", dnode_nid, i);
			ret = -ENOENT;
			goto out;
		}
		ino_nid = de->ino_nid;
		ie = find_gc_inode_entry(gc_ilist, ino_nid);
		if(!ie){
			printk(KERN_INFO "dnode(%u) not found in csgc dnode list, blkoff=%d", dnode_nid, i);
			ret = -ENOENT;
			goto out;
		}
	}

out:
	return ret;
}

static void put_gc_data_pages(struct csgc_info *csi)
{
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	struct gc_folio_list *gc_flist = &csgc_ctx->gc_flist;
	struct gc_data_list *gc_datalist = &csi->gc_data_list;
	struct data_entry *data_ent;

	list_for_each_entry(data_ent, &gc_datalist->list, list){
		if(!data_ent->page)
			continue;
		// printk(KERN_INFO "put data page(bidx=%lu) of inode(%lu)", 
		// 		data_ent->page->index, data_ent->page->mapping->host->i_ino);
		f2fs_put_page(data_ent->page, 0);
		data_ent->page = NULL;
		put_gc_folio_entry(gc_flist, data_ent->fe);
	}
}

// should lock data pages first before calling this function
// lock order: data page -> inode page -> dnode page
static int get_lock_gc_node_pages(struct f2fs_csgc_context *csgc_ctx, unsigned int segno)
{
	struct page *sum_page;
	struct f2fs_summary_block *sum_block;
	struct f2fs_summary *sum_entry;
	nid_t dnode_nid, ino_nid;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct inode_entry *ie;
	struct dnode_entry *de;
	int req_idx = segno - csgc_ctx->start_segno;
	int ret = 0, i;

	printk(KERN_INFO "get lock gc node pages, segno = %u", segno);
	
	sum_page = find_get_page(META_MAPPING(sbi),
					GET_SUM_BLOCK(sbi,segno));
	f2fs_put_page(sum_page, 0);
	sum_block = page_address(sum_page);

	for(i = 0; i < sbi->blocks_per_seg; i++){
		if(check_valid_map(sbi, segno, i) == 0)
			continue;
		
		sum_entry = sum_block->entries + i;
		dnode_nid = le32_to_cpu(sum_entry->nid);

		de = find_gc_dnode_entry(gc_dlist, dnode_nid);
		ino_nid = de->ino_nid;
		ie = find_gc_inode_entry(gc_ilist, ino_nid);

		// inode page already locked
		if(ie->refcnt > 0){
			f2fs_bug_on(sbi, !ie->page);
		}else{ // get lock inode page
			// f2fs_debug_csgc("Get lock inode page of inode(%u)", ino_nid);
			ie->page = f2fs_get_node_page(sbi, ino_nid);
			f2fs_debug_csgc("Get lock inode page of inode(%u), PageUpToDate(%d),"
			" PageDirty(%d), PageWriteback(%d)", ino_nid, PageUptodate(ie->page), 
			PageDirty(ie->page), PageWriteback(ie->page));
			
			if(IS_ERR(ie->page)){
				ret = PTR_ERR(ie->page);
				ie->page = NULL;
				printk(KERN_INFO "Fail to get node page of inode(%u), err = %d", 
							ino_nid, ret);
				goto out;
			}
			f2fs_wait_on_page_writeback(ie->page, NODE, true, true);
		}
		inc_gc_inode_ref(ie, req_idx);

		// dnode page already locked
		if(de->refcnt > 0){
			f2fs_bug_on(sbi, !de->page);
		}else{ // get lock dnode page
			if(de->nid == de->ino_nid){
				ie = find_gc_inode_entry(gc_ilist, de->ino_nid);
				if(!ie){
					printk(KERN_INFO "inode(%u) not found in gc_ilist", de->ino_nid);
					ret = -ENOENT;
					goto out;
				}
				de->page = ie->page;
			}else{
				// f2fs_debug_csgc("Get lock dnode page of dnode(%u)", de->nid);
				de->page = f2fs_get_node_page(sbi, de->nid);
				f2fs_debug_csgc("Get lock dnode page of dnode(%u), PageUpToDate(%d),"
				" PageDirty(%d), PageWriteback(%d)", de->nid, PageUptodate(de->page), 
				PageDirty(de->page), PageWriteback(de->page));
				if(IS_ERR(de->page)){
					ret = PTR_ERR(de->page);
					de->page = NULL;
					printk(KERN_INFO "Fail to get node page of dnode(%u), err = %d", 
								de->nid, ret);
					goto out;
				}
				f2fs_wait_on_page_writeback(de->page, NODE, true, true);
			}
		}
		inc_gc_dnode_ref(de, req_idx);
	}
	// show_gc_dnode_list(gc_dlist);
	// show_gc_inode_list(gc_ilist);

out:
	return ret;
}

static void rollback_gc_node_pages(struct f2fs_csgc_context *csgc_ctx, unsigned int req_idx)
{
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct inode_entry *ie;
	struct dnode_entry *de;

	list_for_each_entry(ie, &gc_ilist->ilist, list){
		if(!ie->page)
			continue;
		dec_gc_inode_ref(ie, ie->rollback_refcnt, req_idx);
		if(ie->refcnt == 0){
			f2fs_put_page(ie->page, 1);
			ie->page = NULL;
		}
	}

	list_for_each_entry(de, &gc_dlist->dlist, list){
		if(!de->page)
			continue;
		dec_gc_dnode_ref(de, de->rollback_refcnt, req_idx);
		if(de->refcnt == 0){
			if(de->ino_nid != de->nid)
				f2fs_put_page(de->page, 1);
			else
				WARN_ON(ie->refcnt != 0);
			de->page = NULL;
		}
	}
}

static void put_gc_node_pages(struct f2fs_csgc_context *csgc_ctx)
{
	struct gc_inode_list *gc_ilist = &csgc_ctx->gc_ilist;
	struct gc_dnode_list *gc_dlist = &csgc_ctx->gc_dlist;
	struct inode_entry *ie;
	struct dnode_entry *de;

	list_for_each_entry(ie, &gc_ilist->ilist, list){
		if(ie->page)
			f2fs_debug_csgc("inode(%lu) page is not released", ie->inode->i_ino);
		f2fs_put_page(ie->page, 1);
		ie->page = NULL;
	}

	list_for_each_entry(de, &gc_dlist->dlist, list){
		if(de->page)
			f2fs_debug_csgc("dnode(%u) page is not released", de->nid);
		if(de->nid != de->ino_nid)
			f2fs_put_page(de->page, 1);
		de->page = NULL;
	}
}

// don't use this function. It will cause deadlock in the following case:
// background writeback thread(writeback some page and triggers gc):
//		locked `&sbi->gc_lock`(In `f2fs_balance_fs`)	
//				--> trying to lock `&inode->i_rwsem`(in `do_garbage_collect_cs`)
// foreground user write thread(write some file with the inode above and triggers gc):
// 		locked `&inode->i_rwsem`(in `f2fs_file_write_iter`)
//				--> trying to lock `&sbi->gc_lock`(in `f2fs_balance_fs`)
static void lock_gc_inodes(struct f2fs_sb_info *sbi, struct gc_inode_list *gc_list)
{
	struct inode_entry *ie;
	struct f2fs_inode_info *fi;
	list_for_each_entry(ie, &gc_list->ilist, list){
		if(S_ISREG(ie->inode->i_mode)){
			fi = F2FS_I(ie->inode);
			// TODO: lock data pages to be migrated instead of locking the inode
			inode_lock(ie->inode);
			f2fs_debug_csgc_pid("locked i_rwsem of inode(%lu)", ie->inode->i_ino);
			f2fs_down_write(&fi->i_gc_rwsem[READ]);
			f2fs_down_write(&fi->i_gc_rwsem[WRITE]);
			inode_dio_wait(ie->inode);
			f2fs_debug_csgc_pid("locked i_gc_rwsem of inode(%lu)", ie->inode->i_ino);
		}
	}
}

static int try_lock_gc_inodes(struct f2fs_sb_info *sbi, 
		struct gc_inode_list *gc_list, unsigned int req_idx, 
		struct list_head **last_locked)
{
	struct inode_entry *ie;
	struct f2fs_inode_info *fi;

	*last_locked = &gc_list->ilist;
	f2fs_debug_csgc("try lock inodes");
	list_for_each_entry(ie, &gc_list->ilist, list){
		if(S_ISREG(ie->inode->i_mode)){
			// skip inodes that are not referred by this request
			if(!ie->gc_rwsem_req_by[req_idx])
				continue;
			
			fi = F2FS_I(ie->inode);
			// if(!inode_trylock(ie->inode)){
			// 	printk(KERN_INFO "trylock failed for inode(%lu) i_rwsem", 
			// 			ie->inode->i_ino);
			// 	return 0;
			// }
			if(ie->gc_rwsem_locked == 0){
				if(!f2fs_down_write_trylock(&fi->i_gc_rwsem[READ])){
					// inode_unlock(ie->inode);
					printk(KERN_INFO "trylock failed for inode(%lu) i_gc_rwsem[READ]",
							ie->inode->i_ino);
					return 0;
				}
				if(!f2fs_down_write_trylock(&fi->i_gc_rwsem[WRITE])){
					f2fs_up_write(&fi->i_gc_rwsem[READ]);
					// inode_unlock(ie->inode);
					printk(KERN_INFO "trylock failed for inode(%lu) i_gc_rwsem[WRITE]",
							ie->inode->i_ino);
					return 0;
				}
				inode_dio_wait(ie->inode);
				f2fs_debug_csgc_pid("try lock inode %lu, succeed", ie->inode->i_ino);
			}else{
				f2fs_debug_csgc_pid("try lock inode %lu, locked by previous requests", 
						ie->inode->i_ino);
			}
			ie->gc_rwsem_locked++;
		}
		*last_locked = &ie->list;
	}
	return 1;
}

// unlock inodes in reverse order starting from `start_pos`
static void unlock_gc_inodes(struct f2fs_sb_info *sbi, 
		struct gc_inode_list *gc_list, unsigned int req_idx, 
		struct list_head *start_pos)
{
	struct inode_entry *ie;
	struct f2fs_inode_info *fi;

	f2fs_debug_csgc("unlock inodes");
	if(!start_pos){
		printk(KERN_INFO "unlock_gc_inodes: start_pos is NULL");
		return;
	}
	
	if(list_is_head(start_pos, &gc_list->ilist))
		return;
	
	ie = list_entry(start_pos, typeof(*ie), list);

	list_for_each_entry_from_reverse(ie, &gc_list->ilist, list){
		if(S_ISREG(ie->inode->i_mode)){
			if(!ie->gc_rwsem_req_by[req_idx])
				continue;
			f2fs_bug_on(sbi, ie->gc_rwsem_locked == 0);
			ie->gc_rwsem_req_by[req_idx] = false;
			ie->gc_rwsem_locked --;
			if(ie->gc_rwsem_locked > 0)
				continue;
			fi = F2FS_I(ie->inode);
			f2fs_up_write(&fi->i_gc_rwsem[WRITE]);
			f2fs_up_write(&fi->i_gc_rwsem[READ]);
			// inode_unlock(ie->inode);
			f2fs_debug_csgc_pid("unlocked gc_rwsem of inode(%lu)", ie->inode->i_ino);

		}
	}
}

static void init_csgc_ctx(struct f2fs_sb_info *sbi, 
		struct f2fs_csgc_context *csgc_ctx, int start_segno, 
		int seg_cnt, int pkg_sz_in_page_order, 
		int sync, int send_meta_from_host)
{
	csgc_ctx->sbi = sbi;
	csgc_ctx->max_nr_cpus = f2fs_get_nr_cs_cores();
	csgc_ctx->package_size_in_page_order = pkg_sz_in_page_order;
	csgc_ctx->sync = sync;
	csgc_ctx->send_meta_from_host = send_meta_from_host;

	csgc_ctx->op_locked = 0;

	init_gc_inode_list(&csgc_ctx->gc_ilist, sbi->segs_per_sec);
	init_gc_dnode_list(&csgc_ctx->gc_dlist, sbi->segs_per_sec);
	init_gc_folio_list(&csgc_ctx->gc_flist);

	sbi->nocare_list_size = 0;
	for(int i = 0; i < sbi->segs_per_sec; i++)
		sbi->pseg_sum_nocare_list[i] = NULL_SEGNO;
	
	csgc_ctx->start_segno = start_segno;
	csgc_ctx->head_segno = start_segno;
	csgc_ctx->seg_cnt = seg_cnt;
	csgc_ctx->req_submitted = 0;
	csgc_ctx->req_completed = 0;

	csgc_ctx->submitted = kmalloc_array(seg_cnt, sizeof(struct completion), GFP_NOFS);
	csgc_ctx->completed = kmalloc_array(seg_cnt, sizeof(struct completion), GFP_NOFS);
	for(int i = 0; i < seg_cnt; i++){
		init_completion(&csgc_ctx->submitted[i]);
		init_completion(&csgc_ctx->completed[i]);
		complete(&csgc_ctx->submitted[i]);
		complete(&csgc_ctx->completed[i]);
		// the completion status will be reset before the request is submitted
	}
}

static void free_csgc_ctx(struct f2fs_csgc_context *csgc_ctx, bool from_normal_path)
{
	csgc_ctx->end_time = ktime_get_ns();
	csgc_ctx->sbi->csgc_total_latency_ns += csgc_ctx->end_time - csgc_ctx->start_time;
	
	f2fs_debug_csgc("free csgc context");
	f2fs_bug_on(csgc_ctx->sbi, csgc_ctx->op_locked!=0);
	kfree(csgc_ctx->completed);
	kfree(csgc_ctx->submitted);
	free_gc_folio_list(&csgc_ctx->gc_flist, from_normal_path);
	put_gc_node_pages(csgc_ctx);
	free_gc_dnode_list(&csgc_ctx->gc_dlist);
	put_gc_inode(&csgc_ctx->gc_ilist);
	kfree(csgc_ctx);
}

static struct csgc_package *create_csgc_package(gfp_t gfp_flags, int pg_order)
{
	struct csgc_package *pkg;
	struct page *page, *page_recv;

	page = alloc_pages(gfp_flags, pg_order);
	if(!page)
		return NULL;
	page_recv = alloc_pages(gfp_flags, pg_order);
	if(!page_recv){
		__free_pages(page, pg_order);
		return NULL;
	}

	pkg = (struct csgc_package *) page_address(page);
	pkg->header.capacity = PAGE_SIZE << pg_order;
	pkg->header.npages = 1 << pg_order;
	pkg->header.pages = page;
	pkg->header.pages_recv = page_recv;

	return pkg;
}

static void destroy_csgc_package(struct csgc_package *package)
{
	int order = get_order(package->header.capacity);
	struct page *page = package->header.pages;
	struct page *page_recv = package->header.pages_recv;
	__free_pages(page, order);
	__free_pages(page_recv, order);
}

static void destroy_csgc_info(struct csgc_info *csi)
{
	destroy_csgc_package(csi->csgc_pkg);
	kfree(csi->private);
	kfree(csi);
}

void f2fs_destroy_csgc_info_pool(struct f2fs_sb_info *sbi)
{
	struct csgc_info *csi, *tmp;

	list_for_each_entry_safe(csi, tmp, &sbi->free_csi_list, csi_pool_list){
		list_del(&csi->csi_pool_list);
		destroy_csgc_info(csi);
	}
	list_for_each_entry_safe(csi, tmp, &sbi->busy_csi_list, csi_pool_list){
		list_del(&csi->csi_pool_list);
		destroy_csgc_info(csi);
	}
}

int f2fs_create_csgc_info_pool(struct f2fs_sb_info *sbi, 
		unsigned int pool_size, unsigned int cs_pkg_size_in_page_order)
{
	int ret = 0;
	struct csgc_info *csi;

	sbi->csi_pool_size = pool_size;
	sbi->cs_pkg_size_in_page_order = cs_pkg_size_in_page_order;
	spin_lock_init(&sbi->csi_list_lock);
	INIT_LIST_HEAD(&sbi->free_csi_list);
	INIT_LIST_HEAD(&sbi->busy_csi_list);

	csi = kmalloc_array(pool_size, sizeof(struct csgc_info), GFP_NOFS);
	if(!csi)
		return -ENOMEM;

	for(int i = 0; i < pool_size; i++, csi++){
		csi->csgc_pkg = create_csgc_package(GFP_NOFS, cs_pkg_size_in_page_order);
		if(!csi->csgc_pkg){
			ret = -ENOMEM;
			goto out;
		}
		csi->csgc_pkg_recv = page_address(csi->csgc_pkg->header.pages_recv);
		csi->private = kmalloc(F2FS_BLKSIZE, GFP_NOFS);
		list_add_tail(&csi->csi_pool_list, &sbi->free_csi_list);
	}

out:
	return ret;;
}

static struct csgc_info *alloc_csgc_info(struct f2fs_sb_info *sbi)
{
	struct csgc_info *csi;

	spin_lock(&sbi->csi_list_lock);
	if(list_empty(&sbi->free_csi_list)){
		spin_unlock(&sbi->csi_list_lock);
		return NULL;
	}
	csi = list_first_entry(&sbi->free_csi_list, struct csgc_info, csi_pool_list);
	list_del(&csi->csi_pool_list);
	list_add_tail(&csi->csi_pool_list, &sbi->busy_csi_list);
	spin_unlock(&sbi->csi_list_lock);

	return csi;
}

static void dealloc_csgc_info(struct f2fs_sb_info *sbi, struct csgc_info *csi)
{
	spin_lock(&sbi->csi_list_lock);
	list_del(&csi->csi_pool_list);
	list_add_tail(&csi->csi_pool_list, &sbi->free_csi_list);
	spin_unlock(&sbi->csi_list_lock);
}

static void f2fs_pre_csgc_work(struct work_struct *work);

#define CS_SEQ_ID_BITS 5
#define CS_SEQ_ID_MASK ((1 << CS_SEQ_ID_BITS) - 1)

static void inc_cs_seq_id(struct f2fs_sb_info *sbi)
{
	sbi->cs_seq_id = (sbi->cs_seq_id + 1) & CS_SEQ_ID_MASK;
}

static int init_csgc_info(struct csgc_info *csi, 
		struct f2fs_csgc_context *csgc_ctx, unsigned int segno)
{
	csi->csgc_ctx = csgc_ctx;

	INIT_WORK(&csi->work_pre, f2fs_pre_csgc_work);
	csi->ret_pre_work = 0;
	INIT_WORK(&csi->work_post, f2fs_post_csgc_work);
	csi->ret_post_work = 0;

	init_gc_data_list(&csi->gc_data_list);

	csi->segno = segno;
	csi->bio_trigger = NULL;
	init_completion(&csi->trigger_done);
	csi->bio_fetch_result = NULL;
	init_completion(&csi->fetch_result_done);
	csi->sum_page = NULL;
	// space for csgc_pkg and csgc_pkg_recv is allocated when creating csgc_info pool
	init_csgc_pack(csi->csgc_pkg, segno, 
			segno - csgc_ctx->start_segno, 
			csgc_ctx->max_nr_cpus, 
			csgc_ctx->send_meta_from_host);
	
	memset(&csi->stat, 0, sizeof(struct gc_time_stat));
	csi->stat.start_time = csgc_ctx->start_time;
	csi->stat.sync_fs_time = csgc_ctx->sync_fs_time;
	csi->stat.start_gc_time = ktime_get_ns();

	csi->cs_bi_private.cs_seq_id = csgc_ctx->sbi->cs_seq_id;
	csi->cs_bi_private.is_cs_head = (segno == csgc_ctx->head_segno);
	csi->cs_bi_private.csi = csi;
	inc_cs_seq_id(csgc_ctx->sbi);
	printk(KERN_INFO "package capacity: %u, npages: %u, cs_seq_id:%d, is_cs_head: %d", 
			csi->csgc_pkg->header.capacity, csi->csgc_pkg->header.npages, 
			csi->cs_bi_private.cs_seq_id, csi->cs_bi_private.is_cs_head);

	csi->submited = &csgc_ctx->submitted[segno - csgc_ctx->start_segno];
	csi->completed = &csgc_ctx->completed[segno - csgc_ctx->start_segno];
	reinit_completion(csi->submited);
	reinit_completion(csi->completed);
	
	return 0;
}

static void free_csgc_info(struct csgc_info *csi)
{
	put_gc_data_pages(csi);
	free_gc_data_list(&csi->gc_data_list);
	dealloc_csgc_info(csi->csgc_ctx->sbi, csi);
}

static void show_gc_stat(struct gc_time_stat *stat)
{
	// printk(KERN_INFO "start_time: 		%llu", stat->start_time);
	// printk(KERN_INFO "sync_fs_time: 	%llu", stat->sync_fs_time);
	// printk(KERN_INFO "start_gc_time: 	%llu", stat->start_gc_time);
	// printk(KERN_INFO "get_sum_time: 	%llu", stat->get_sum_time);
	// printk(KERN_INFO "get_node_time: 	%llu", stat->get_node_time);
	// printk(KERN_INFO "lock_inode_time: %llu", stat->lock_inode_time);
	// printk(KERN_INFO "pre_alloc_time: 	%llu", stat->pre_alloc_time);
	// printk(KERN_INFO "cs_time: 		%llu", stat->cs_time);
	// printk(KERN_INFO "enq_wq_time: 	%llu", stat->enq_wq_time);
	// printk(KERN_INFO "update_meta_time:%llu", stat->update_meta_time);
	// printk(KERN_INFO "end_time: 		%llu", stat->end_time);
	// printk(KERN_INFO "total_time: 		%llu", stat->total_time);
	
	printk(KERN_INFO "sync_fs_time = %llu us, get_sum_time = %llu us, "
			"get_node_time = %llu us, lock_inode_time = %llu us, "
			"get_lock_dpage_time = %llu us, get_lock_npage_time = %llu us, "
			"pre_alloc_time = %llu us, cs_time = %llu us, "
			"enqueue_wq_time = %llu us, update_meta_time = %llu us, "
			"end_time = %llu us, total_time = %llu us", 
			stat_time_diff_us(stat, start_time, sync_fs_time),
			stat_time_diff_us(stat, start_gc_time, get_sum_time),
			stat_time_diff_us(stat, get_sum_time, get_node_time),
			stat_time_diff_us(stat, get_node_time, lock_inode_time),
			stat_time_diff_us(stat, lock_inode_time, get_lock_dpage_time),
			stat_time_diff_us(stat, get_lock_dpage_time, get_lock_npage_time),
			stat_time_diff_us(stat, get_lock_npage_time, pre_alloc_time),
			stat_time_diff_us(stat, pre_alloc_time, cs_time),
			stat_time_diff_us(stat, cs_time, enq_wq_time),
			stat_time_diff_us(stat, enq_wq_time, update_meta_time),
			stat_time_diff_us(stat, update_meta_time, end_time),
			stat_time_diff_us(stat, start_gc_time, end_time));
	printk(KERN_INFO "csgc takes %llu us", stat->total_time/1000);
}

void f2fs_finish_csgc_segment(struct csgc_info *csi)
{
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	struct completion *completed = csi->completed;

	unlock_gc_inodes(sbi, &csgc_ctx->gc_ilist, 
			csi->segno - csgc_ctx->start_segno, csi->ilist_pos);
	f2fs_put_page(csi->sum_page, 0);

	csi->stat.end_time = ktime_get_ns();
	csi->stat.total_time = csi->stat.end_time - csi->stat.start_time;

	show_gc_stat(&csi->stat);

	free_csgc_info(csi);

	complete(completed);
	csgc_ctx->req_completed++;
	f2fs_debug_csgc("csgc request completed, %d/%d", 
			csgc_ctx->req_completed, csgc_ctx->seg_cnt);
	if(csgc_ctx->req_completed == csgc_ctx->seg_cnt)
		free_csgc_ctx(csgc_ctx, 1);
}

// pre-process and the offload of the CSGC request,
// include identification, pre-allocation, and necessary locking
// when `f2fs_pre_csgc_work` completes:
//	if csgc_ctx->sync is set, the csgc request is guaranteed to be 
//		submitted to CSD and completed by CSD
// 	else, the csgc request is only guaranteed to be submitted, 
static void f2fs_pre_csgc_work(struct work_struct *work)
{
	struct csgc_info *csi = container_of(work, struct csgc_info, work_pre);
	struct f2fs_csgc_context *csgc_ctx = csi->csgc_ctx;
	struct f2fs_sb_info *sbi = csgc_ctx->sbi;
	unsigned int segno = csi->segno;
	unsigned int req_idx = segno - csgc_ctx->start_segno;
	unsigned int n = 0;
	long ret = 0;

	// reference summary page of target segment
	f2fs_debug_csgc("get sum page of segno = %u, addr = %u", segno, GET_SUM_BLOCK(sbi, segno));
	csi->sum_page = f2fs_get_sum_page(sbi, segno);
	if(IS_ERR(csi->sum_page)){
		ret = PTR_ERR(csi->sum_page);
		printk(KERN_INFO "Fail to get summary page, ret = %ld", ret);
		goto out_err;
	}
	unlock_page(csi->sum_page);
	memcpy(csi->private, page_address(csi->sum_page), sizeof(struct f2fs_summary_block));
	csi->stat.get_sum_time = ktime_get_ns();

	// get gc inode/dnode list
	f2fs_debug_csgc("get gc inode list");
	ret = get_gc_node_list(sbi, segno, &csgc_ctx->gc_ilist, 
			&csgc_ctx->gc_dlist, req_idx);
	if(ret){
		printk(KERN_INFO "Fail to get gc inode list, ret = %ld", ret);
		goto put_sum_page;
	}
	csi->stat.get_node_time = ktime_get_ns();

	if(!try_lock_gc_inodes(sbi, &csgc_ctx->gc_ilist, req_idx, &csi->ilist_pos)){
		ret = -EAGAIN;
		f2fs_debug_csgc("try lock gc inodes failed, skip");
		sbi->skipped_gc_rwsem++;
		goto unlock_all;
	}
	csi->stat.lock_inode_time = ktime_get_ns();

	ret = get_lock_gc_data_pages(csi);
	if(ret){
		printk(KERN_INFO "Fail to get lock gc data pages, ret = %ld", ret);
		goto put_data_pages;
	}
	csi->stat.get_lock_dpage_time = ktime_get_ns();

	// dead lock due to page->lock and cp_rwsem
	if(!csgc_trylock_op(csgc_ctx)){
		f2fs_debug_csgc("try lock op failed, skip");
		ret = -EAGAIN;
		sbi->skipped_gc_rwsem++;
		goto put_data_pages;
	}

	ret = get_lock_gc_node_pages(csgc_ctx, segno);
	if(ret){
		printk(KERN_INFO "Fail to get lock gc node pages, ret = %ld", ret);
		goto put_node_pages;
	}

	n = get_valid_blocks(sbi, csi->segno, false);
	printk("After lock, valid blocks count = %u", n);
	if(n == 0){
		printk(KERN_INFO "already freed, segno = %u", segno);
		if(segno == csgc_ctx->head_segno)
			csgc_ctx->head_segno++;
		goto put_node_pages;
	}
	
	ret = check_gc_data_validness(csi);
	if(ret){
		printk(KERN_INFO "Fail to check gc data validness, ret = %ld", ret);
		goto put_node_pages;
	}
	csi->stat.get_lock_npage_time = ktime_get_ns();

	ret = pack_node_info(sbi, csi->csgc_pkg, &csgc_ctx->gc_dlist, req_idx);
	if(ret)
		goto put_node_pages;
	pack_sit_entry(sbi, csi->csgc_pkg, segno);
	// TODO!: modify dnodes in host, send dnode info to device, 
	//			then update them in device.
	ret = f2fs_csgc_preallocate(sbi, csi, CURSEG_COLD_DATA);
	if(ret){
		f2fs_printk(sbi, "Fail to preallocate blocks, ret = %ld\n", ret);
		goto put_node_pages;
	}

	// dump_csgc_package_h2d(csi->csgc_pkg);
	// __dump_summary(page_address(sum_page));
	csi->stat.pre_alloc_time = ktime_get_ns();

	ret = request_csgc(sbi, csi);
	if(ret){
		f2fs_printk(sbi, "Fail to request csgc, ret = %ld\n", ret);
		goto put_node_pages;
	}

	ret = wait_csgc_result(sbi, csi);
	if(ret){
		f2fs_printk(sbi, "Fail to get csgc result, ret = %ld\n", ret);
		goto put_node_pages;
	}
	f2fs_debug_csgc_pid("wait_csgc_result returns");

	sbi->csgc_blks_migrated += n;
	sbi->csgc_seg_freed ++;
	// sbi->csgc_bytes_read += n * F2FS_BLKSIZE;
	// sbi->csgc_bytes_written += n * F2FS_BLKSIZE;

	csi->ret_pre_work = ret;
	csgc_ctx->req_submitted++;
	if(!csgc_ctx->sync)
		complete(csi->submited);
	return;

put_node_pages:
	rollback_gc_node_pages(csgc_ctx, req_idx);
	csgc_unlock_op(csgc_ctx);
put_data_pages:
	put_gc_data_pages(csi);
unlock_all:
	unlock_gc_inodes(sbi, &csgc_ctx->gc_ilist, req_idx, csi->ilist_pos);
put_sum_page:
	f2fs_put_page(csi->sum_page, 0);
out_err:
	csi->ret_pre_work = ret;
	if(!csgc_ctx->sync){
		complete(csi->submited);
		complete(csi->completed);
	}
	return;
}

static int gc_data_segment_cs(struct f2fs_sb_info *sbi, 
			struct f2fs_csgc_context *csgc_ctx, 
			unsigned int segno, int *seg_freed)
{
	struct csgc_info *csi;
	long ret = 0;
	unsigned long long skipped = sbi->skipped_gc_rwsem;
	int try_cnt = 0;

	printk(KERN_INFO "Ready to do csgc for segment %u, valid_blocks=%u\n", 
				segno, get_valid_blocks(sbi, segno, false));
	if(get_valid_blocks(sbi, segno, false) == 0){
		printk(KERN_INFO "already freed, segno = %u", segno);
		(*seg_freed)++;
		csgc_ctx->req_completed++;
		if(segno == csgc_ctx->head_segno)
			csgc_ctx->head_segno++;
		if(csgc_ctx->seg_cnt == csgc_ctx->req_completed)
			free_csgc_ctx(csgc_ctx, 1);
		return ret;
	}

	csi = alloc_csgc_info(sbi);
	if(!csi)
		return -ENOMEM;
	ret = init_csgc_info(csi, csgc_ctx, segno);
	if(ret){
		kfree(csi);
		return -ENOMEM;
	}

retry:
	try_cnt ++;
	if(csgc_ctx->sync){
		f2fs_pre_csgc_work(&csi->work_pre);
	}else{
		// enqueue execution of `f2fs_pre_csgc_work`
		queue_work(sbi->csgc_offloader_wq, &csi->work_pre);
		// wait until request is submitted to CSD
		// wait_for_completion(csi->submited);
		wait_for_completion_interruptible(csi->submited);
	}
	ret = csi->ret_pre_work;
	if(ret == -EAGAIN && try_cnt < CSGC_TRY_LOCK_OP_MAX_CNT){
		f2fs_debug_csgc("retry csgc request, already tried %d times", try_cnt);
		reinit_completion(csi->submited);
		goto retry;
	}
	if(try_cnt == CSGC_TRY_LOCK_OP_MAX_CNT){
		sbi->skipped_gc_rwsem ++;
	}

	if(sbi->skipped_gc_rwsem > skipped){
		printk(KERN_INFO "skipped_gc_rwsem increased, %llu -> %llu", 
				skipped, sbi->skipped_gc_rwsem);
		goto free_csi;
	}

	if(ret)
		goto free_csi;
	
	sbi->gc_reclaimed_segs[sbi->gc_mode]++;
	(*seg_freed)++;

	return ret;

free_csi:
	free_csgc_info(csi);
	return ret;
}

static int do_garbage_collect_cs(struct f2fs_sb_info *sbi, 
			unsigned int start_segno, int *seg_freed)
{
	struct f2fs_csgc_context *csgc_ctx;
	unsigned long long skipped = sbi->skipped_gc_rwsem;
	unsigned int segno = start_segno;
	unsigned int end_segno = start_segno + sbi->segs_per_sec;
	int sync = f2fs_get_csgc_sync();
	long ret = 0;

	if (__is_large_section(sbi))
		end_segno = rounddown(end_segno, sbi->segs_per_sec);
	
	*seg_freed = 0;
	csgc_ctx = kmalloc(sizeof(struct f2fs_csgc_context), GFP_NOFS);
	if(!csgc_ctx)
		return -ENOMEM;
	init_csgc_ctx(sbi, csgc_ctx, start_segno, 
		end_segno - start_segno, 3,
		sync, 1);

	sync_fs_before_csgc(sbi, csgc_ctx);

	/* readahead multi ssa blocks those have contiguous address */
	if (__is_large_section(sbi))
		f2fs_ra_meta_pages(sbi, GET_SUM_BLOCK(sbi, segno),
					end_segno - segno, META_SSA, true);

	for (segno = start_segno; segno < end_segno; segno++) {
		ret = gc_data_segment_cs(sbi, csgc_ctx, segno, seg_freed);
		if(ret)
			goto free_ctx;

		if(sbi->skipped_gc_rwsem > skipped)
			goto free_ctx;

		if(sync && sbi->gc_add_inode_cnt != sbi->gc_put_inode_cnt){
			printk("Failed at %s:%d, gc_add_inode_cnt = %lu, gc_put_inode_cnt = %lu", 
					__func__, __LINE__, sbi->gc_add_inode_cnt, sbi->gc_put_inode_cnt);
			f2fs_bug_on(sbi, 1);
		}
	}
	return ret;


free_ctx:
	// wait for unfinished GC requests in async multi-seg offloading
	if(!sync){
		for(int i = 0; i < csgc_ctx->seg_cnt; i++){
			if(completion_done(&csgc_ctx->completed[i]))
				continue;
			wait_for_completion(&csgc_ctx->completed[i]);
		}
	}
	free_csgc_ctx(csgc_ctx, 0);
	if(sbi->gc_add_inode_cnt != sbi->gc_put_inode_cnt){
		printk("Failed at %s:%d, gc_add_inode_cnt = %lu, gc_put_inode_cnt = %lu", 
				__func__, __LINE__, sbi->gc_add_inode_cnt, sbi->gc_put_inode_cnt);
		f2fs_bug_on(sbi, 1);
	}
	return ret;
}

static inline bool __should_csgc(struct f2fs_sb_info *sbi)
{
	return sbi->should_csgc && sbi->csgc_called < f2fs_get_csgc_max_count();
}

int f2fs_gc(struct f2fs_sb_info *sbi, struct f2fs_gc_control *gc_control)
{
	int gc_type = gc_control->init_gc_type;
	unsigned int segno = gc_control->victim_segno;
	int sec_freed = 0, seg_freed = 0, total_freed = 0;
	int ret = 0;
	struct cp_control cpc;
	struct gc_inode_list gc_list = {	// used only for original gc
		.ilist = LIST_HEAD_INIT(gc_list.ilist),
		.iroot = RADIX_TREE_INIT(gc_list.iroot, GFP_NOFS),
	};
	unsigned int skipped_round = 0, round = 0;
	unsigned int upper_secs;
	bool csgc_flag;

	trace_f2fs_gc_begin(sbi->sb, gc_type, gc_control->no_bg_gc,
				gc_control->nr_free_secs,
				get_pages(sbi, F2FS_DIRTY_NODES),
				get_pages(sbi, F2FS_DIRTY_DENTS),
				get_pages(sbi, F2FS_DIRTY_IMETA),
				free_sections(sbi),
				free_segments(sbi),
				reserved_segments(sbi),
				prefree_segments(sbi));

	cpc.reason = __get_cp_reason(sbi);
	// if(!sbi->csgc_called){
	// 	sbi->csgc_called++;
	// 	f2fs_csgc(sbi);
	// }
gc_more:
	csgc_flag = false;
	sbi->skipped_gc_rwsem = 0;
	if (unlikely(!(sbi->sb->s_flags & SB_ACTIVE))) {
		ret = -EINVAL;
		goto stop;
	}
	if (unlikely(f2fs_cp_error(sbi))) {
		ret = -EIO;
		goto stop;
	}

	if (gc_type == BG_GC && has_not_enough_free_secs(sbi, 0, 0)) {
		/*
		 * For example, if there are many prefree_segments below given
		 * threshold, we can make them free by checkpoint. Then, we
		 * secure free segments which doesn't need fggc any more.
		 */
		if (prefree_segments(sbi)) {
			ret = f2fs_write_checkpoint(sbi, &cpc);
			f2fs_debug_csgc("gc finished ckpt to free prefree segments");
			if (ret)
				goto stop;
		}
		if (has_not_enough_free_secs(sbi, 0, 0))
			gc_type = FG_GC;
	}

	/* f2fs_balance_fs doesn't need to do BG_GC in critical path. */
	if (gc_type == BG_GC && gc_control->no_bg_gc) {
		ret = -EINVAL;
		goto stop;
	}
retry:
	ret = __get_victim(sbi, &segno, gc_type);
	if (ret) {
		/* allow to search victim from sections has pinned data */
		if (ret == -ENODATA && gc_type == FG_GC &&
				f2fs_pinned_section_exists(DIRTY_I(sbi))) {
			f2fs_unpin_all_sections(sbi, false);
			goto retry;
		}
		goto stop;
	}

	// if(sbi->csgc_called < f2fs_get_csgc_max_count()){
	// 	test_cs(sbi);
	// 	sbi->csgc_called ++;
	// }
	
	if(__should_csgc(sbi) && gc_type == FG_GC && IS_DATASEG(get_seg_entry(sbi, segno)->type)){
		csgc_flag = true;
		ret = do_garbage_collect_cs(sbi, segno, &seg_freed);
		f2fs_debug_csgc("gc add inode cnt: %lu, gc put inode cnt: %lu", 
				sbi->gc_add_inode_cnt, sbi->gc_put_inode_cnt);
		sbi->csgc_called++;
		printk(KERN_INFO "csgc called %d times, %u segs freed in this call (%u/%u|skipped/round)\n", 
				sbi->csgc_called, seg_freed, skipped_round, round);
		if(ret && ret != -EAGAIN){
			f2fs_printk(sbi, "Fail to do csgc, ret = %d\n", ret);
			sbi->should_csgc = false;
			csgc_flag = false;
			f2fs_set_csgc_status(-ret);
			set_sbi_flag(sbi, SBI_NEED_FSCK);
			goto stop;
		}
		if(is_sbi_flag_set(sbi, SBI_NEED_FSCK)){
			f2fs_printk(sbi, "something went wrong during csgc, need fsck\n");
			sbi->should_csgc = false;
			csgc_flag = false;
			f2fs_set_csgc_status(-1);
			goto stop;
		}
		// if(sbi->csgc_called >= 20)
		// 	sbi->should_csgc = false;
	}else{
		seg_freed = do_garbage_collect(sbi, segno, &gc_list, gc_type,
					gc_control->should_migrate_blocks);
		sbi->origc_called ++;
	}

	total_freed += seg_freed;

	if (seg_freed == f2fs_usable_segs_in_sec(sbi, segno))
		sec_freed++;

	if (gc_type == FG_GC)
		sbi->cur_victim_sec = NULL_SEGNO;

	if (gc_control->init_gc_type == FG_GC ||
	    !has_not_enough_free_secs(sbi,
				(gc_type == FG_GC) ? sec_freed : 0, 0)) {
		if(gc_type == FG_GC && csgc_flag){
			if (sbi->skipped_gc_rwsem)
				skipped_round++;
			round++;
			if (skipped_round > MAX_SKIP_CSGC_COUNT &&
					skipped_round * 2 >= round) {
				ret = f2fs_write_checkpoint(sbi, &cpc);
				goto stop;
			}
		}
		if (gc_type == FG_GC && sec_freed < gc_control->nr_free_secs)
			goto go_gc_more;
		goto stop;
	}

	/* FG_GC stops GC by skip_count */
	if (gc_type == FG_GC) {
		int max_skip_count = csgc_flag ? MAX_SKIP_CSGC_COUNT : MAX_SKIP_GC_COUNT;

		if (sbi->skipped_gc_rwsem)
			skipped_round++;
		round++;
		if (skipped_round > max_skip_count &&
				skipped_round * 2 >= round) {
			printk(KERN_INFO "Skip GC for %d times out of %d rounds", 
					skipped_round, round);
			if(csgc_flag){
				sbi->csgc_skip_cnt ++;
				// if(sbi->csgc_skip_cnt >= 20){
				// 	sbi->should_csgc = false;
				// 	f2fs_set_csgc_status(-1);
				// 	printk(KERN_INFO "stop CSGC because of irregular skip");
				// }
			}
			ret = f2fs_write_checkpoint(sbi, &cpc);
			goto stop;
		}
	}

	__get_secs_required(sbi, NULL, &upper_secs, NULL);

	/*
	 * Write checkpoint to reclaim prefree segments.
	 * We need more three extra sections for writer's data/node/dentry.
	 */
	if (free_sections(sbi) <= upper_secs + NR_GC_CHECKPOINT_SECS &&
				prefree_segments(sbi)) {
		ret = f2fs_write_checkpoint(sbi, &cpc);
		if (ret)
			goto stop;
	}
go_gc_more:
	segno = NULL_SEGNO;
	goto gc_more;

stop:
	SIT_I(sbi)->last_victim[ALLOC_NEXT] = 0;
	SIT_I(sbi)->last_victim[FLUSH_DEVICE] = gc_control->victim_segno;

	if (gc_type == FG_GC)
		f2fs_unpin_all_sections(sbi, true);

	trace_f2fs_gc_end(sbi->sb, ret, total_freed, sec_freed,
				get_pages(sbi, F2FS_DIRTY_NODES),
				get_pages(sbi, F2FS_DIRTY_DENTS),
				get_pages(sbi, F2FS_DIRTY_IMETA),
				free_sections(sbi),
				free_segments(sbi),
				reserved_segments(sbi),
				prefree_segments(sbi));

	f2fs_up_write(&sbi->gc_lock);

	put_gc_inode(&gc_list);

	if (gc_control->err_gc_skipped && !ret)
		ret = sec_freed ? 0 : -EAGAIN;
	return ret;
}

int __init f2fs_create_garbage_collection_cache(void)
{
	victim_entry_slab = f2fs_kmem_cache_create("f2fs_victim_entry",
					sizeof(struct victim_entry));
	if (!victim_entry_slab)
		return -ENOMEM;
	return 0;
}

void f2fs_destroy_garbage_collection_cache(void)
{
	kmem_cache_destroy(victim_entry_slab);
}

static void init_atgc_management(struct f2fs_sb_info *sbi)
{
	struct atgc_management *am = &sbi->am;

	if (test_opt(sbi, ATGC) &&
		SIT_I(sbi)->elapsed_time >= DEF_GC_THREAD_AGE_THRESHOLD)
		am->atgc_enabled = true;

	am->root = RB_ROOT_CACHED;
	INIT_LIST_HEAD(&am->victim_list);
	am->victim_count = 0;

	am->candidate_ratio = DEF_GC_THREAD_CANDIDATE_RATIO;
	am->max_candidate_count = DEF_GC_THREAD_MAX_CANDIDATE_COUNT;
	am->age_weight = DEF_GC_THREAD_AGE_WEIGHT;
	am->age_threshold = DEF_GC_THREAD_AGE_THRESHOLD;
}

void f2fs_build_gc_manager(struct f2fs_sb_info *sbi)
{
	DIRTY_I(sbi)->v_ops = &default_v_ops;

	sbi->gc_pin_file_threshold = DEF_GC_FAILED_PINNED_FILES;

	/* give warm/cold data area from slower device */
	if (f2fs_is_multi_device(sbi) && !__is_large_section(sbi))
		SIT_I(sbi)->last_victim[ALLOC_NEXT] =
				GET_SEGNO(sbi, FDEV(0).end_blk) + 1;

	init_atgc_management(sbi);
}

static int free_segment_range(struct f2fs_sb_info *sbi,
				unsigned int secs, bool gc_only)
{
	unsigned int segno, next_inuse, start, end;
	struct cp_control cpc = { CP_RESIZE, 0, 0, 0 };
	int gc_mode, gc_type;
	int err = 0;
	int type;

	/* Force block allocation for GC */
	MAIN_SECS(sbi) -= secs;
	start = MAIN_SECS(sbi) * sbi->segs_per_sec;
	end = MAIN_SEGS(sbi) - 1;

	mutex_lock(&DIRTY_I(sbi)->seglist_lock);
	for (gc_mode = 0; gc_mode < MAX_GC_POLICY; gc_mode++)
		if (SIT_I(sbi)->last_victim[gc_mode] >= start)
			SIT_I(sbi)->last_victim[gc_mode] = 0;

	for (gc_type = BG_GC; gc_type <= FG_GC; gc_type++)
		if (sbi->next_victim_seg[gc_type] >= start)
			sbi->next_victim_seg[gc_type] = NULL_SEGNO;
	mutex_unlock(&DIRTY_I(sbi)->seglist_lock);

	/* Move out cursegs from the target range */
	for (type = CURSEG_HOT_DATA; type < NR_CURSEG_PERSIST_TYPE; type++)
		f2fs_allocate_segment_for_resize(sbi, type, start, end);

	/* do GC to move out valid blocks in the range */
	for (segno = start; segno <= end; segno += sbi->segs_per_sec) {
		struct gc_inode_list gc_list = {
			.ilist = LIST_HEAD_INIT(gc_list.ilist),
			.iroot = RADIX_TREE_INIT(gc_list.iroot, GFP_NOFS),
		};

		do_garbage_collect(sbi, segno, &gc_list, FG_GC, true);
		put_gc_inode(&gc_list);

		if (!gc_only && get_valid_blocks(sbi, segno, true)) {
			err = -EAGAIN;
			goto out;
		}
		if (fatal_signal_pending(current)) {
			err = -ERESTARTSYS;
			goto out;
		}
	}
	if (gc_only)
		goto out;

	err = f2fs_write_checkpoint(sbi, &cpc);
	if (err)
		goto out;

	next_inuse = find_next_inuse(FREE_I(sbi), end + 1, start);
	if (next_inuse <= end) {
		f2fs_err(sbi, "segno %u should be free but still inuse!",
			 next_inuse);
		f2fs_bug_on(sbi, 1);
	}
out:
	MAIN_SECS(sbi) += secs;
	return err;
}

static void update_sb_metadata(struct f2fs_sb_info *sbi, int secs)
{
	struct f2fs_super_block *raw_sb = F2FS_RAW_SUPER(sbi);
	int section_count;
	int segment_count;
	int segment_count_main;
	long long block_count;
	int segs = secs * sbi->segs_per_sec;

	f2fs_down_write(&sbi->sb_lock);

	section_count = le32_to_cpu(raw_sb->section_count);
	segment_count = le32_to_cpu(raw_sb->segment_count);
	segment_count_main = le32_to_cpu(raw_sb->segment_count_main);
	block_count = le64_to_cpu(raw_sb->block_count);

	raw_sb->section_count = cpu_to_le32(section_count + secs);
	raw_sb->segment_count = cpu_to_le32(segment_count + segs);
	raw_sb->segment_count_main = cpu_to_le32(segment_count_main + segs);
	raw_sb->block_count = cpu_to_le64(block_count +
					(long long)segs * sbi->blocks_per_seg);
	if (f2fs_is_multi_device(sbi)) {
		int last_dev = sbi->s_ndevs - 1;
		int dev_segs =
			le32_to_cpu(raw_sb->devs[last_dev].total_segments);

		raw_sb->devs[last_dev].total_segments =
						cpu_to_le32(dev_segs + segs);
	}

	f2fs_up_write(&sbi->sb_lock);
}

static void update_fs_metadata(struct f2fs_sb_info *sbi, int secs)
{
	int segs = secs * sbi->segs_per_sec;
	long long blks = (long long)segs * sbi->blocks_per_seg;
	long long user_block_count =
				le64_to_cpu(F2FS_CKPT(sbi)->user_block_count);

	SM_I(sbi)->segment_count = (int)SM_I(sbi)->segment_count + segs;
	MAIN_SEGS(sbi) = (int)MAIN_SEGS(sbi) + segs;
	MAIN_SECS(sbi) += secs;
	FREE_I(sbi)->free_sections = (int)FREE_I(sbi)->free_sections + secs;
	FREE_I(sbi)->free_segments = (int)FREE_I(sbi)->free_segments + segs;
	F2FS_CKPT(sbi)->user_block_count = cpu_to_le64(user_block_count + blks);

	if (f2fs_is_multi_device(sbi)) {
		int last_dev = sbi->s_ndevs - 1;

		FDEV(last_dev).total_segments =
				(int)FDEV(last_dev).total_segments + segs;
		FDEV(last_dev).end_blk =
				(long long)FDEV(last_dev).end_blk + blks;
#ifdef CONFIG_BLK_DEV_ZONED
		FDEV(last_dev).nr_blkz = (int)FDEV(last_dev).nr_blkz +
					(int)(blks >> sbi->log_blocks_per_blkz);
#endif
	}
}

int f2fs_resize_fs(struct file *filp, __u64 block_count)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(file_inode(filp));
	__u64 old_block_count, shrunk_blocks;
	struct cp_control cpc = { CP_RESIZE, 0, 0, 0 };
	unsigned int secs;
	int err = 0;
	__u32 rem;

	old_block_count = le64_to_cpu(F2FS_RAW_SUPER(sbi)->block_count);
	if (block_count > old_block_count)
		return -EINVAL;

	if (f2fs_is_multi_device(sbi)) {
		int last_dev = sbi->s_ndevs - 1;
		__u64 last_segs = FDEV(last_dev).total_segments;

		if (block_count + last_segs * sbi->blocks_per_seg <=
								old_block_count)
			return -EINVAL;
	}

	/* new fs size should align to section size */
	div_u64_rem(block_count, BLKS_PER_SEC(sbi), &rem);
	if (rem)
		return -EINVAL;

	if (block_count == old_block_count)
		return 0;

	if (is_sbi_flag_set(sbi, SBI_NEED_FSCK)) {
		f2fs_err(sbi, "Should run fsck to repair first.");
		return -EFSCORRUPTED;
	}

	if (test_opt(sbi, DISABLE_CHECKPOINT)) {
		f2fs_err(sbi, "Checkpoint should be enabled.");
		return -EINVAL;
	}

	err = mnt_want_write_file(filp);
	if (err)
		return err;

	shrunk_blocks = old_block_count - block_count;
	secs = div_u64(shrunk_blocks, BLKS_PER_SEC(sbi));

	/* stop other GC */
	if (!f2fs_down_write_trylock(&sbi->gc_lock)) {
		err = -EAGAIN;
		goto out_drop_write;
	}

	/* stop CP to protect MAIN_SEC in free_segment_range */
	f2fs_lock_op(sbi);

	spin_lock(&sbi->stat_lock);
	if (shrunk_blocks + valid_user_blocks(sbi) +
		sbi->current_reserved_blocks + sbi->unusable_block_count +
		F2FS_OPTION(sbi).root_reserved_blocks > sbi->user_block_count)
		err = -ENOSPC;
	spin_unlock(&sbi->stat_lock);

	if (err)
		goto out_unlock;

	err = free_segment_range(sbi, secs, true);

out_unlock:
	f2fs_unlock_op(sbi);
	f2fs_up_write(&sbi->gc_lock);
out_drop_write:
	mnt_drop_write_file(filp);
	if (err)
		return err;

	err = freeze_super(sbi->sb);
	if (err)
		return err;

	if (f2fs_readonly(sbi->sb)) {
		thaw_super(sbi->sb);
		return -EROFS;
	}

	f2fs_down_write(&sbi->gc_lock);
	f2fs_down_write(&sbi->cp_global_sem);

	spin_lock(&sbi->stat_lock);
	if (shrunk_blocks + valid_user_blocks(sbi) +
		sbi->current_reserved_blocks + sbi->unusable_block_count +
		F2FS_OPTION(sbi).root_reserved_blocks > sbi->user_block_count)
		err = -ENOSPC;
	else
		sbi->user_block_count -= shrunk_blocks;
	spin_unlock(&sbi->stat_lock);
	if (err)
		goto out_err;

	set_sbi_flag(sbi, SBI_IS_RESIZEFS);
	err = free_segment_range(sbi, secs, false);
	if (err)
		goto recover_out;

	update_sb_metadata(sbi, -secs);

	err = f2fs_commit_super(sbi, false);
	if (err) {
		update_sb_metadata(sbi, secs);
		goto recover_out;
	}

	update_fs_metadata(sbi, -secs);
	clear_sbi_flag(sbi, SBI_IS_RESIZEFS);
	set_sbi_flag(sbi, SBI_IS_DIRTY);

	err = f2fs_write_checkpoint(sbi, &cpc);
	if (err) {
		update_fs_metadata(sbi, secs);
		update_sb_metadata(sbi, secs);
		f2fs_commit_super(sbi, false);
	}
recover_out:
	clear_sbi_flag(sbi, SBI_IS_RESIZEFS);
	if (err) {
		set_sbi_flag(sbi, SBI_NEED_FSCK);
		f2fs_err(sbi, "resize_fs failed, should run fsck to repair!");

		spin_lock(&sbi->stat_lock);
		sbi->user_block_count += shrunk_blocks;
		spin_unlock(&sbi->stat_lock);
	}
out_err:
	f2fs_up_write(&sbi->cp_global_sem);
	f2fs_up_write(&sbi->gc_lock);
	thaw_super(sbi->sb);
	return err;
}
