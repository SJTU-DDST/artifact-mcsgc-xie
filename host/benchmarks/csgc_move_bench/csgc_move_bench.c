#define _GNU_SOURCE

#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <linux/fs.h>
#include <linux/nvme_ioctl.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <time.h>
#include <unistd.h>

#define F2FS_SUPER_MAGIC 0xF2F52010U
#define F2FS_SUPER_OFFSET 1024U
#define F2FS_BLOCK_SIZE 4096U

#define CSGC_SLOT_SIZE (32U * 1024U)
#define CSGC_SLOT_BLOCKS (CSGC_SLOT_SIZE / F2FS_BLOCK_SIZE)
#define CSGC_PRINT_BUFFER_OFFSET (16U * 1024U)
#define CSGC_MAX_MOVES 512U
#define CSGC_MAX_RANGES 2U
#define CSGC_MAX_SEQUENCE_IDS 32U
#define CSGC_MAX_WORKERS 3U

#define CSGC_MOVE_PLAN_MAGIC 0x43534732U
#define CSGC_MOVE_PLAN_VERSION 2U
#define CSGC_REQUEST_TYPE_MOVE_PLAN 1U
#define CSGC_FAILED_INDEX_NONE UINT32_MAX

#define NVME_OPCODE_WRITE 0x01U
#define NVME_OPCODE_READ 0x02U
#define NVME_CONTROL_CSGC (1U << 0)
#define NVME_CONTROL_CSGC_SEQUENCE_SHIFT 3U

struct csgc_offset_info {
	uint32_t nat_start;
	uint32_t sit_start_h2d;
	uint32_t prealloc_start;
	uint32_t data_size_h2d;
	uint32_t sit_start;
	uint32_t dirty_sum_start;
	uint32_t dnode_start;
	uint32_t debug_start;
	uint32_t data_size_d2h;
};

struct csgc_header_wire {
	uint32_t capacity;
	uint32_t npages;
	uint64_t pages;
	uint64_t pages_recv;
	uint32_t segno;
	uint32_t head_segno;
	int32_t status[CSGC_MAX_WORKERS];
	uint32_t prealloc_curseg_segno;
	uint32_t nr_pre_alloc;
	uint32_t nr_node_info;
	uint8_t meta_sent_from_host;
	uint8_t protocol_version;
	uint8_t request_type;
	uint8_t protocol_flags;
	uint32_t max_nr_cpus;
	uint32_t print_offset;
	uint32_t print_size;
	union {
		struct csgc_offset_info offs;
		uint8_t raw[40];
	};
};

struct csgc_move_desc {
	uint32_t src_blkaddr;
	uint32_t dst_blkaddr;
};

struct csgc_move_range {
	uint32_t segno;
	uint16_t start_off;
	uint16_t len;
};

struct csgc_move_plan {
	uint32_t magic;
	uint16_t version;
	uint16_t flags;
	uint64_t request_id;
	uint32_t victim_segno;
	uint32_t nr_moves;
	uint32_t desc_offset;
	uint32_t desc_bytes;
	uint32_t prealloc_offset;
	uint32_t nr_prealloc;
	uint32_t result_offset;
	uint32_t result_size;
};

struct csgc_move_result {
	uint32_t magic;
	uint16_t version;
	uint16_t flags;
	uint64_t request_id;
	int32_t status;
	uint32_t submitted_moves;
	uint32_t completed_moves;
	uint32_t failed_index;
};

/* Only the fixed prefix through main_blkaddr is needed by this benchmark. */
struct f2fs_super_prefix {
	uint32_t magic;
	uint16_t major_ver;
	uint16_t minor_ver;
	uint32_t log_sectorsize;
	uint32_t log_sectors_per_block;
	uint32_t log_blocksize;
	uint32_t log_blocks_per_seg;
	uint32_t segs_per_sec;
	uint32_t secs_per_zone;
	uint32_t checksum_offset;
	uint64_t block_count;
	uint32_t section_count;
	uint32_t segment_count;
	uint32_t segment_count_ckpt;
	uint32_t segment_count_sit;
	uint32_t segment_count_nat;
	uint32_t segment_count_ssa;
	uint32_t segment_count_main;
	uint32_t segment0_blkaddr;
	uint32_t cp_blkaddr;
	uint32_t sit_blkaddr;
	uint32_t nat_blkaddr;
	uint32_t ssa_blkaddr;
	uint32_t main_blkaddr;
} __attribute__((packed));

_Static_assert(sizeof(struct csgc_header_wire) == 112,
		"CSGC header ABI changed");
_Static_assert(offsetof(struct csgc_header_wire, protocol_version) == 57,
		"CSGC protocol version offset changed");
_Static_assert(offsetof(struct csgc_header_wire, max_nr_cpus) == 60,
		"CSGC max CPU offset changed");
_Static_assert(offsetof(struct csgc_header_wire, offs) == 72,
		"CSGC offset union changed");
_Static_assert(sizeof(struct csgc_offset_info) == 36,
		"CSGC offset info ABI changed");
_Static_assert(offsetof(struct csgc_offset_info, data_size_d2h) == 32,
		"CSGC D2H size offset changed");
_Static_assert(offsetof(struct csgc_header_wire, offs) +
	       offsetof(struct csgc_offset_info, data_size_d2h) == 104,
		"CSGC header D2H size offset changed");
_Static_assert(sizeof(struct csgc_move_desc) == 8,
		"Move descriptor ABI changed");
_Static_assert(sizeof(struct csgc_move_range) == 8,
		"Move range ABI changed");
_Static_assert(sizeof(struct csgc_move_plan) == 48,
		"Move Plan ABI changed");
_Static_assert(sizeof(struct csgc_move_result) == 32,
		"Move result ABI changed");
_Static_assert(sizeof(struct f2fs_super_prefix) == 96,
		"F2FS superblock prefix changed");
_Static_assert(offsetof(struct f2fs_super_prefix, main_blkaddr) == 92,
		"F2FS main block address offset changed");

struct geometry {
	uint32_t main_blkaddr;
	uint32_t segment_count_main;
	uint32_t blocks_per_seg;
};

struct config {
	const char *device;
	uint32_t queue_depth;
	uint32_t moves;
	uint32_t warmup_seconds;
	uint32_t runtime_seconds;
	uint32_t timeout_ms;
	uint32_t pool_size;
	uint32_t source_seg;
	uint32_t destination_seg;
	bool source_seg_set;
	bool destination_seg_set;
	bool dry_run;
	bool self_test;
};

struct latency_vector {
	uint64_t *values;
	size_t count;
	size_t capacity;
};

struct shared_state {
	struct config cfg;
	struct geometry geo;
	atomic_bool started;
	atomic_bool stop;
	uint64_t start_ns;
	uint64_t measurement_start_ns;
	uint64_t deadline_ns;
};

struct worker_state {
	struct shared_state *shared;
	uint32_t worker_id;
	uint32_t sequence_id;
	uint32_t nsid;
	int fd;
	void *package;
	uint64_t completed_requests;
	uint64_t completed_moves;
	int error_code;
	struct latency_vector latencies;
};

static volatile sig_atomic_t interrupted;

static uint64_t monotonic_ns(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC_RAW, &ts) != 0) {
		perror("clock_gettime");
		exit(EXIT_FAILURE);
	}
	return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

static uint32_t align_u32(uint32_t value, uint32_t alignment)
{
	return (value + alignment - 1U) & ~(alignment - 1U);
}

static void handle_signal(int signal_number)
{
	(void)signal_number;
	interrupted = 1;
}

static void usage(FILE *stream, const char *program)
{
	fprintf(stream,
		"Usage:\n"
		"  %s --device DEVICE [options]\n"
		"  %s --self-test\n\n"
		"Options:\n"
		"  --queue-depth N       Concurrent CSGC requests, 1..32 (default 1)\n"
		"  --moves N             Blocks per Move Plan, 1..512 (default 512)\n"
		"  --warmup SEC          Unmeasured warmup time (default 1)\n"
		"  --runtime SEC         Measured runtime in seconds (default 10)\n"
		"  --timeout-ms MS       Timeout for each NVMe command (default 120000)\n"
		"  --pool-size N         Distinct source/destination segment pairs\n"
		"  --source-seg N        First source segment, relative to main area\n"
		"  --destination-seg N   First destination segment, relative to main area\n"
		"  --dry-run             Validate geometry and print the address plan only\n"
		"  --self-test           Validate wire layouts and package construction\n"
		"  --help                Show this help\n\n"
		"The namespace must be unmounted. This benchmark overwrites blocks in the\n"
		"F2FS main area and the filesystem must be recreated afterwards.\n",
		program, program);
}

static int parse_u32(const char *text, uint32_t *value)
{
	char *end = NULL;
	unsigned long long parsed;

	errno = 0;
	parsed = strtoull(text, &end, 0);
	if (errno || end == text || *end != '\0' || parsed > UINT32_MAX)
		return -1;
	*value = (uint32_t)parsed;
	return 0;
}

static int parse_options(int argc, char **argv, struct config *cfg)
{
	enum {
		OPT_QUEUE_DEPTH = 1000,
		OPT_MOVES,
		OPT_WARMUP,
		OPT_RUNTIME,
		OPT_TIMEOUT,
		OPT_POOL_SIZE,
		OPT_SOURCE_SEG,
		OPT_DESTINATION_SEG,
		OPT_DRY_RUN,
		OPT_SELF_TEST,
	};
	static const struct option options[] = {
		{ "device", required_argument, NULL, 'd' },
		{ "queue-depth", required_argument, NULL, OPT_QUEUE_DEPTH },
		{ "moves", required_argument, NULL, OPT_MOVES },
		{ "warmup", required_argument, NULL, OPT_WARMUP },
		{ "runtime", required_argument, NULL, OPT_RUNTIME },
		{ "timeout-ms", required_argument, NULL, OPT_TIMEOUT },
		{ "pool-size", required_argument, NULL, OPT_POOL_SIZE },
		{ "source-seg", required_argument, NULL, OPT_SOURCE_SEG },
		{ "destination-seg", required_argument, NULL, OPT_DESTINATION_SEG },
		{ "dry-run", no_argument, NULL, OPT_DRY_RUN },
		{ "self-test", no_argument, NULL, OPT_SELF_TEST },
		{ "help", no_argument, NULL, 'h' },
		{ NULL, 0, NULL, 0 },
	};
	int option;

	memset(cfg, 0, sizeof(*cfg));
	cfg->queue_depth = 1;
	cfg->moves = CSGC_MAX_MOVES;
	cfg->warmup_seconds = 1;
	cfg->runtime_seconds = 10;
	cfg->timeout_ms = 120000;

	while ((option = getopt_long(argc, argv, "d:h", options, NULL)) != -1) {
		uint32_t parsed;

		switch (option) {
		case 'd':
			cfg->device = optarg;
			break;
		case OPT_QUEUE_DEPTH:
		case OPT_MOVES:
		case OPT_WARMUP:
		case OPT_RUNTIME:
		case OPT_TIMEOUT:
		case OPT_POOL_SIZE:
		case OPT_SOURCE_SEG:
		case OPT_DESTINATION_SEG:
			if (parse_u32(optarg, &parsed) != 0) {
				fprintf(stderr, "Invalid numeric argument: %s\n", optarg);
				return -1;
			}
			if (option == OPT_QUEUE_DEPTH)
				cfg->queue_depth = parsed;
			else if (option == OPT_MOVES)
				cfg->moves = parsed;
			else if (option == OPT_WARMUP)
				cfg->warmup_seconds = parsed;
			else if (option == OPT_RUNTIME)
				cfg->runtime_seconds = parsed;
			else if (option == OPT_TIMEOUT)
				cfg->timeout_ms = parsed;
			else if (option == OPT_POOL_SIZE)
				cfg->pool_size = parsed;
			else if (option == OPT_SOURCE_SEG) {
				cfg->source_seg = parsed;
				cfg->source_seg_set = true;
			} else {
				cfg->destination_seg = parsed;
				cfg->destination_seg_set = true;
			}
			break;
		case OPT_DRY_RUN:
			cfg->dry_run = true;
			break;
		case OPT_SELF_TEST:
			cfg->self_test = true;
			break;
		case 'h':
			usage(stdout, argv[0]);
			exit(EXIT_SUCCESS);
		default:
			return -1;
		}
	}

	if (optind != argc) {
		fprintf(stderr, "Unexpected positional argument: %s\n", argv[optind]);
		return -1;
	}
	if (cfg->self_test)
		return 0;
	if (!cfg->device) {
		fprintf(stderr, "--device is required\n");
		return -1;
	}
	if (!cfg->queue_depth || cfg->queue_depth > CSGC_MAX_SEQUENCE_IDS) {
		fprintf(stderr, "--queue-depth must be in 1..%u\n",
			CSGC_MAX_SEQUENCE_IDS);
		return -1;
	}
	if (!cfg->moves || cfg->moves > CSGC_MAX_MOVES) {
		fprintf(stderr, "--moves must be in 1..%u\n", CSGC_MAX_MOVES);
		return -1;
	}
	if (!cfg->runtime_seconds || !cfg->timeout_ms) {
		fprintf(stderr, "--runtime and --timeout-ms must be positive\n");
		return -1;
	}
	return 0;
}

static bool device_is_mounted(const struct stat *device_stat)
{
	FILE *mountinfo;
	char line[8192];
	unsigned int device_major = major(device_stat->st_rdev);
	unsigned int device_minor = minor(device_stat->st_rdev);
	bool mounted = false;

	mountinfo = fopen("/proc/self/mountinfo", "r");
	if (!mountinfo)
		return true;
	while (fgets(line, sizeof(line), mountinfo)) {
		unsigned int mounted_major;
		unsigned int mounted_minor;

		if (sscanf(line, "%*u %*u %u:%u", &mounted_major,
			   &mounted_minor) == 2 &&
		    mounted_major == device_major && mounted_minor == device_minor) {
			mounted = true;
			break;
		}
	}
	fclose(mountinfo);
	return mounted;
}

/* Read either F2FS superblock copy and extract the geometry used on device. */
static int read_geometry(int fd, struct geometry *geo)
{
	static const off_t copies[] = { 0, F2FS_BLOCK_SIZE };
	uint8_t block[F2FS_BLOCK_SIZE];
	struct f2fs_super_prefix super;
	uint32_t log_blocks_per_seg;
	uint32_t log_blocksize;

	for (size_t i = 0; i < sizeof(copies) / sizeof(copies[0]); i++) {
		ssize_t bytes = pread(fd, block, sizeof(block), copies[i]);

		if (bytes != (ssize_t)sizeof(block))
			continue;
		memcpy(&super, block + F2FS_SUPER_OFFSET, sizeof(super));
		if (le32toh(super.magic) == F2FS_SUPER_MAGIC)
			goto found;
	}
	fprintf(stderr, "No valid F2FS superblock was found on the namespace\n");
	return -1;

found:
	log_blocksize = le32toh(super.log_blocksize);
	log_blocks_per_seg = le32toh(super.log_blocks_per_seg);
	if (log_blocksize != 12 || log_blocks_per_seg >= 32) {
		fprintf(stderr,
			"Unsupported F2FS geometry: log_blocksize=%u log_blocks_per_seg=%u\n",
			log_blocksize, log_blocks_per_seg);
		return -1;
	}
	geo->main_blkaddr = le32toh(super.main_blkaddr);
	geo->segment_count_main = le32toh(super.segment_count_main);
	geo->blocks_per_seg = 1U << log_blocks_per_seg;
	if (!geo->main_blkaddr || !geo->segment_count_main ||
	    !geo->blocks_per_seg || geo->blocks_per_seg > CSGC_MAX_MOVES) {
		fprintf(stderr,
			"Invalid F2FS main area: start=%u segments=%u blocks_per_seg=%u\n",
			geo->main_blkaddr, geo->segment_count_main,
			geo->blocks_per_seg);
		return -1;
	}
	return 0;
}

static bool ranges_overlap(uint32_t first_a, uint32_t length_a,
			   uint32_t first_b, uint32_t length_b)
{
	uint64_t end_a = (uint64_t)first_a + length_a;
	uint64_t end_b = (uint64_t)first_b + length_b;

	return first_a < end_b && first_b < end_a;
}

/* Select separated address pools unless the caller provides explicit starts. */
static int finalize_address_plan(struct config *cfg, const struct geometry *geo)
{
	uint32_t default_pool = cfg->queue_depth * 4U;

	if (default_pool < 64U)
		default_pool = 64U;
	if (!cfg->pool_size)
		cfg->pool_size = default_pool;
	if (cfg->pool_size < cfg->queue_depth) {
		fprintf(stderr, "--pool-size must be at least --queue-depth\n");
		return -1;
	}
	if (cfg->pool_size % cfg->queue_depth) {
		fprintf(stderr, "--pool-size must be a multiple of --queue-depth\n");
		return -1;
	}
	if (!cfg->source_seg_set)
		cfg->source_seg = geo->segment_count_main / 4U;
	if (!cfg->destination_seg_set)
		cfg->destination_seg = (geo->segment_count_main * 3U) / 4U;

	if ((uint64_t)cfg->source_seg + cfg->pool_size > geo->segment_count_main ||
	    (uint64_t)cfg->destination_seg + cfg->pool_size >
			geo->segment_count_main) {
		fprintf(stderr,
			"Address pool exceeds the F2FS main area: src=%u dst=%u pool=%u segments=%u\n",
			cfg->source_seg, cfg->destination_seg, cfg->pool_size,
			geo->segment_count_main);
		return -1;
	}
	if (ranges_overlap(cfg->source_seg, cfg->pool_size,
			   cfg->destination_seg, cfg->pool_size)) {
		fprintf(stderr, "Source and destination segment pools overlap\n");
		return -1;
	}
	if (cfg->moves > geo->blocks_per_seg) {
		fprintf(stderr, "--moves=%u exceeds blocks_per_seg=%u\n",
			cfg->moves, geo->blocks_per_seg);
		return -1;
	}
	return 0;
}

/* Build one complete 32 KiB Move Plan package for an existing address pair. */
static int build_move_plan(void *buffer, const struct shared_state *shared,
			   uint32_t pair_index, uint64_t request_id)
{
	const struct config *cfg = &shared->cfg;
	const struct geometry *geo = &shared->geo;
	struct csgc_header_wire *header = buffer;
	uint8_t *data = (uint8_t *)buffer + sizeof(*header);
	struct csgc_move_plan *plan = (struct csgc_move_plan *)data;
	uint32_t desc_offset = align_u32(sizeof(*plan), sizeof(uint64_t));
	uint32_t desc_bytes = cfg->moves * sizeof(struct csgc_move_desc);
	uint32_t range_offset = align_u32(desc_offset + desc_bytes,
					  sizeof(uint64_t));
	struct csgc_move_desc *descs =
		(struct csgc_move_desc *)(data + desc_offset);
	struct csgc_move_range *range =
		(struct csgc_move_range *)(data + range_offset);
	uint32_t source_seg = cfg->source_seg + pair_index;
	uint32_t destination_seg = cfg->destination_seg + pair_index;
	uint64_t source_start = (uint64_t)geo->main_blkaddr +
		(uint64_t)source_seg * geo->blocks_per_seg;
	uint64_t destination_start = (uint64_t)geo->main_blkaddr +
		(uint64_t)destination_seg * geo->blocks_per_seg;

	if (range_offset + sizeof(*range) >
	    CSGC_PRINT_BUFFER_OFFSET - sizeof(*header) ||
	    source_start + cfg->moves > UINT32_MAX + 1ULL ||
	    destination_start + cfg->moves > UINT32_MAX + 1ULL)
		return -1;

	memset(buffer, 0, CSGC_SLOT_SIZE);
	header->capacity = htole32(CSGC_SLOT_SIZE);
	header->npages = htole32(CSGC_SLOT_BLOCKS);
	header->segno = htole32(source_seg);
	header->head_segno = htole32(source_seg);
	header->prealloc_curseg_segno = htole32(destination_seg);
	header->protocol_version = CSGC_MOVE_PLAN_VERSION;
	header->request_type = CSGC_REQUEST_TYPE_MOVE_PLAN;
	header->max_nr_cpus = htole32(1);
	header->print_offset = htole32(CSGC_PRINT_BUFFER_OFFSET - sizeof(*header));
	header->offs.prealloc_start = htole32(range_offset);
	header->offs.data_size_h2d = htole32(range_offset + sizeof(*range));

	plan->magic = htole32(CSGC_MOVE_PLAN_MAGIC);
	plan->version = htole16(CSGC_MOVE_PLAN_VERSION);
	plan->request_id = htole64(request_id);
	plan->victim_segno = htole32(source_seg);
	plan->nr_moves = htole32(cfg->moves);
	plan->desc_offset = htole32(desc_offset);
	plan->desc_bytes = htole32(desc_bytes);
	plan->prealloc_offset = htole32(range_offset);
	plan->nr_prealloc = htole32(1);
	plan->result_size = htole32(sizeof(struct csgc_move_result));

	range->segno = htole32(destination_seg);
	range->len = htole16(cfg->moves);
	for (uint32_t i = 0; i < cfg->moves; i++) {
		descs[i].src_blkaddr = htole32((uint32_t)source_start + i);
		descs[i].dst_blkaddr = htole32((uint32_t)destination_start + i);
	}
	return 0;
}

static int submit_csgc_io(int fd, uint32_t nsid, uint8_t opcode,
			  uint32_t sequence_id, void *buffer,
			  uint32_t timeout_ms)
{
	struct nvme_passthru_cmd command;
	uint32_t control = NVME_CONTROL_CSGC |
		(sequence_id << NVME_CONTROL_CSGC_SEQUENCE_SHIFT);
	int result;

	memset(&command, 0, sizeof(command));
	command.opcode = opcode;
	command.nsid = nsid;
	command.addr = (uintptr_t)buffer;
	command.data_len = CSGC_SLOT_SIZE;
	command.cdw12 = (CSGC_SLOT_BLOCKS - 1U) | (control << 16);
	command.timeout_ms = timeout_ms;

	result = ioctl(fd, NVME_IOCTL_IO_CMD, &command);
	if (result < 0)
		return -errno;
	if (result > 0)
		return -EIO;
	return 0;
}

static int validate_result(const void *buffer, uint64_t request_id,
			   uint32_t expected_moves)
{
	const struct csgc_header_wire *header = buffer;
	const struct csgc_move_result *result =
		(const struct csgc_move_result *)((const uint8_t *)buffer +
					      sizeof(*header));

	if (header->protocol_version != CSGC_MOVE_PLAN_VERSION ||
	    header->request_type != CSGC_REQUEST_TYPE_MOVE_PLAN ||
	    le32toh(header->offs.data_size_d2h) != sizeof(*result) ||
	    le32toh(result->magic) != CSGC_MOVE_PLAN_MAGIC ||
	    le16toh(result->version) != CSGC_MOVE_PLAN_VERSION ||
	    le64toh(result->request_id) != request_id ||
	    le16toh(result->flags) != 0 ||
	    (int32_t)le32toh((uint32_t)result->status) != 0 ||
	    le32toh(result->submitted_moves) != expected_moves ||
	    le32toh(result->completed_moves) != expected_moves ||
	    le32toh(result->failed_index) != CSGC_FAILED_INDEX_NONE)
		return -EPROTO;
	return 0;
}

static int latency_append(struct latency_vector *vector, uint64_t value)
{
	if (vector->count == vector->capacity) {
		size_t new_capacity = vector->capacity ? vector->capacity * 2U : 4096U;
		uint64_t *new_values;

		if (new_capacity < vector->capacity)
			return -1;
		new_values = realloc(vector->values,
				     new_capacity * sizeof(*new_values));
		if (!new_values)
			return -1;
		vector->values = new_values;
		vector->capacity = new_capacity;
	}
	vector->values[vector->count++] = value;
	return 0;
}

/* Keep one CSGC request outstanding per thread and refill it on completion. */
static void *worker_main(void *argument)
{
	struct worker_state *worker = argument;
	struct shared_state *shared = worker->shared;
	uint64_t pair_iteration = 0;
	uint64_t request_serial = 1;

	while (!atomic_load_explicit(&shared->started, memory_order_acquire) &&
	       !atomic_load_explicit(&shared->stop, memory_order_relaxed))
		sched_yield();
	while (!atomic_load_explicit(&shared->stop, memory_order_relaxed) &&
	       !interrupted && monotonic_ns() < shared->deadline_ns) {
		uint32_t pairs_per_worker = shared->cfg.pool_size /
			shared->cfg.queue_depth;
		uint32_t pair = worker->worker_id +
			(uint32_t)(pair_iteration++ % pairs_per_worker) *
			shared->cfg.queue_depth;
		uint64_t request_id = ((uint64_t)(worker->worker_id + 1U) << 56) |
			request_serial++;
		uint64_t start_ns;
		uint64_t end_ns;
		int error;

		if (build_move_plan(worker->package, shared,
				    pair % shared->cfg.pool_size, request_id) != 0) {
			worker->error_code = EINVAL;
			atomic_store(&shared->stop, true);
			break;
		}

		start_ns = monotonic_ns();
		error = submit_csgc_io(worker->fd, worker->nsid,
				       NVME_OPCODE_WRITE, worker->sequence_id,
				       worker->package, shared->cfg.timeout_ms);
		if (!error)
			error = submit_csgc_io(worker->fd, worker->nsid,
					       NVME_OPCODE_READ, worker->sequence_id,
					       worker->package,
					       shared->cfg.timeout_ms);
		end_ns = monotonic_ns();
		if (!error)
			error = validate_result(worker->package, request_id,
						shared->cfg.moves);
		if (error) {
			worker->error_code = -error;
			fprintf(stderr,
				"Worker %u sequence %u failed request %" PRIu64 ": %s (%d)\n",
				worker->worker_id, worker->sequence_id, request_id,
				error == -EPROTO ? "invalid Move Plan result" :
				strerror(-error), error);
			atomic_store(&shared->stop, true);
			break;
		}
		if (start_ns >= shared->measurement_start_ns) {
			if (latency_append(&worker->latencies,
					   end_ns - start_ns) != 0) {
				worker->error_code = ENOMEM;
				atomic_store(&shared->stop, true);
				break;
			}
			worker->completed_requests++;
			worker->completed_moves += shared->cfg.moves;
		}
	}
	return NULL;
}

static int compare_u64(const void *left, const void *right)
{
	uint64_t a = *(const uint64_t *)left;
	uint64_t b = *(const uint64_t *)right;

	return (a > b) - (a < b);
}

static uint64_t percentile(const uint64_t *values, size_t count,
			   uint32_t numerator, uint32_t denominator)
{
	size_t index;

	if (!count)
		return 0;
	index = (count * numerator + denominator - 1U) / denominator;
	if (index)
		index--;
	if (index >= count)
		index = count - 1U;
	return values[index];
}

static int run_self_test(void)
{
	struct shared_state shared;
	uint8_t *buffer;
	struct csgc_header_wire *header;
	struct csgc_move_plan *plan;
	struct csgc_move_desc *descs;
	struct csgc_move_result result;
	uint32_t desc_offset;

	memset(&shared, 0, sizeof(shared));
	shared.cfg.queue_depth = 4;
	shared.cfg.moves = CSGC_MAX_MOVES;
	shared.cfg.pool_size = 64;
	shared.cfg.source_seg = 1024;
	shared.cfg.destination_seg = 3072;
	shared.geo.main_blkaddr = 4096;
	shared.geo.segment_count_main = 8192;
	shared.geo.blocks_per_seg = CSGC_MAX_MOVES;

	buffer = aligned_alloc(F2FS_BLOCK_SIZE, CSGC_SLOT_SIZE);
	if (!buffer)
		return EXIT_FAILURE;
	if (build_move_plan(buffer, &shared, 7, 12345) != 0) {
		free(buffer);
		return EXIT_FAILURE;
	}
	header = (struct csgc_header_wire *)buffer;
	plan = (struct csgc_move_plan *)(buffer + sizeof(*header));
	desc_offset = le32toh(plan->desc_offset);
	descs = (struct csgc_move_desc *)((uint8_t *)plan + desc_offset);
	if (le32toh(header->capacity) != CSGC_SLOT_SIZE ||
	    le32toh(header->segno) != 1031 ||
	    le64toh(plan->request_id) != 12345 ||
	    le32toh(plan->nr_moves) != CSGC_MAX_MOVES ||
	    le32toh(descs[0].src_blkaddr) !=
		shared.geo.main_blkaddr + 1031U * CSGC_MAX_MOVES ||
	    le32toh(descs[CSGC_MAX_MOVES - 1U].dst_blkaddr) !=
		shared.geo.main_blkaddr + 3079U * CSGC_MAX_MOVES +
		CSGC_MAX_MOVES - 1U) {
		fprintf(stderr, "Move Plan package self-test failed\n");
		free(buffer);
		return EXIT_FAILURE;
	}
	memset(&result, 0, sizeof(result));
	result.magic = htole32(CSGC_MOVE_PLAN_MAGIC);
	result.version = htole16(CSGC_MOVE_PLAN_VERSION);
	result.request_id = htole64(12345);
	result.submitted_moves = htole32(CSGC_MAX_MOVES);
	result.completed_moves = htole32(CSGC_MAX_MOVES);
	result.failed_index = htole32(CSGC_FAILED_INDEX_NONE);
	header->offs.data_size_d2h = htole32(sizeof(result));
	memcpy(buffer + sizeof(*header), &result, sizeof(result));
	if (validate_result(buffer, 12345, CSGC_MAX_MOVES) != 0) {
		fprintf(stderr, "Move Plan result self-test failed\n");
		free(buffer);
		return EXIT_FAILURE;
	}
	free(buffer);
	printf("CSGC_MOVE_BENCH_SELF_TEST status=pass header=%zu plan=%zu result=%zu slot=%u\n",
	       sizeof(struct csgc_header_wire), sizeof(struct csgc_move_plan),
	       sizeof(struct csgc_move_result), CSGC_SLOT_SIZE);
	return EXIT_SUCCESS;
}

int main(int argc, char **argv)
{
	struct config cfg;
	struct shared_state shared;
	struct worker_state *workers = NULL;
	pthread_t *threads = NULL;
	struct stat device_stat;
	struct sigaction action;
	uint64_t total_requests = 0;
	uint64_t total_moves = 0;
	uint64_t total_latency_ns = 0;
	uint64_t *all_latencies = NULL;
	uint64_t end_ns;
	size_t latency_offset = 0;
	uint32_t threads_created = 0;
	bool threads_joined = false;
	int geometry_fd = -1;
	int logical_block_size = 0;
	int exit_code = EXIT_FAILURE;

	if (parse_options(argc, argv, &cfg) != 0) {
		usage(stderr, argv[0]);
		return EXIT_FAILURE;
	}
	if (cfg.self_test)
		return run_self_test();

	geometry_fd = open(cfg.device, O_RDWR | O_CLOEXEC);
	if (geometry_fd < 0) {
		perror("open device");
		goto out;
	}
	if (fstat(geometry_fd, &device_stat) != 0 ||
	    !S_ISBLK(device_stat.st_mode)) {
		fprintf(stderr, "%s is not a block device\n", cfg.device);
		goto out;
	}
	if (device_is_mounted(&device_stat)) {
		fprintf(stderr, "%s is mounted; refusing destructive benchmark\n",
			cfg.device);
		goto out;
	}
	if (ioctl(geometry_fd, BLKSSZGET, &logical_block_size) != 0) {
		perror("BLKSSZGET");
		goto out;
	}
	if (logical_block_size != F2FS_BLOCK_SIZE) {
		fprintf(stderr, "NVMe logical block size must be %u, got %d\n",
			F2FS_BLOCK_SIZE, logical_block_size);
		goto out;
	}

	memset(&shared, 0, sizeof(shared));
	shared.cfg = cfg;
	if (read_geometry(geometry_fd, &shared.geo) != 0 ||
	    finalize_address_plan(&shared.cfg, &shared.geo) != 0)
		goto out;
	cfg = shared.cfg;
	printf("CSGC_MOVE_BENCH_CONFIG device=%s qd=%u moves=%u warmup_s=%u runtime_s=%u timeout_ms=%u "
	       "main_blkaddr=%u main_segments=%u blocks_per_seg=%u pool=%u src_seg=%u dst_seg=%u\n",
	       cfg.device, cfg.queue_depth, cfg.moves, cfg.warmup_seconds,
	       cfg.runtime_seconds, cfg.timeout_ms, shared.geo.main_blkaddr,
	       shared.geo.segment_count_main, shared.geo.blocks_per_seg,
	       cfg.pool_size, cfg.source_seg, cfg.destination_seg);
	if (cfg.dry_run) {
		printf("CSGC_MOVE_BENCH_DRY_RUN status=pass\n");
		exit_code = EXIT_SUCCESS;
		goto out;
	}

	workers = calloc(cfg.queue_depth, sizeof(*workers));
	threads = calloc(cfg.queue_depth, sizeof(*threads));
	if (!workers || !threads) {
		perror("allocate workers");
		goto out;
	}
	atomic_init(&shared.stop, false);
	atomic_init(&shared.started, false);

	for (uint32_t i = 0; i < cfg.queue_depth; i++)
		workers[i].fd = -1;
	for (uint32_t i = 0; i < cfg.queue_depth; i++) {
		int nsid;

		workers[i].shared = &shared;
		workers[i].worker_id = i;
		workers[i].sequence_id = i;
		workers[i].fd = open(cfg.device, O_RDWR | O_CLOEXEC);
		if (workers[i].fd < 0) {
			perror("open worker device");
			goto out;
		}
		nsid = ioctl(workers[i].fd, NVME_IOCTL_ID);
		if (nsid <= 0) {
			perror("NVME_IOCTL_ID");
			goto out;
		}
		workers[i].nsid = (uint32_t)nsid;
		if (posix_memalign(&workers[i].package, F2FS_BLOCK_SIZE,
				   CSGC_SLOT_SIZE) != 0) {
			fprintf(stderr, "Failed to allocate aligned CSGC package\n");
			goto out;
		}
	}
	for (uint32_t i = 0; i < cfg.queue_depth; i++) {
		if (pthread_create(&threads[i], NULL, worker_main, &workers[i]) != 0) {
			fprintf(stderr, "Failed to create worker %u\n", i);
			atomic_store(&shared.stop, true);
			atomic_store_explicit(&shared.started, true,
					      memory_order_release);
			for (uint32_t j = 0; j < threads_created; j++)
				pthread_join(threads[j], NULL);
			threads_joined = true;
			goto out;
		}
		threads_created++;
	}

	memset(&action, 0, sizeof(action));
	action.sa_handler = handle_signal;
	sigemptyset(&action.sa_mask);
	sigaction(SIGINT, &action, NULL);
	sigaction(SIGTERM, &action, NULL);

	shared.start_ns = monotonic_ns();
	shared.measurement_start_ns = shared.start_ns +
		(uint64_t)cfg.warmup_seconds * 1000000000ULL;
	shared.deadline_ns = shared.measurement_start_ns +
		(uint64_t)cfg.runtime_seconds * 1000000000ULL;
	atomic_store_explicit(&shared.started, true, memory_order_release);
	for (uint32_t i = 0; i < cfg.queue_depth; i++)
		pthread_join(threads[i], NULL);
	threads_joined = true;
	end_ns = monotonic_ns();

	for (uint32_t i = 0; i < cfg.queue_depth; i++) {
		total_requests += workers[i].completed_requests;
		total_moves += workers[i].completed_moves;
		if (workers[i].error_code)
			atomic_store(&shared.stop, true);
	}
	if (total_requests) {
		all_latencies = malloc(total_requests * sizeof(*all_latencies));
		if (!all_latencies) {
			perror("allocate aggregate latencies");
			goto stop_workers;
		}
	}
	for (uint32_t i = 0; i < cfg.queue_depth; i++) {
		if (workers[i].latencies.count)
			memcpy(all_latencies + latency_offset,
			       workers[i].latencies.values,
			       workers[i].latencies.count * sizeof(*all_latencies));
		latency_offset += workers[i].latencies.count;
	}
	qsort(all_latencies, total_requests, sizeof(*all_latencies), compare_u64);
	for (size_t i = 0; i < total_requests; i++)
		total_latency_ns += all_latencies[i];

	if (!total_requests || atomic_load(&shared.stop) || interrupted) {
		fprintf(stderr,
			"Benchmark did not complete cleanly: requests=%" PRIu64 " interrupted=%d\n",
			total_requests, interrupted != 0);
		goto stop_workers;
	}

	{
		double elapsed_seconds =
			(double)(end_ns - shared.measurement_start_ns) / 1e9;
		double logical_mib = (double)total_moves * F2FS_BLOCK_SIZE /
			(1024.0 * 1024.0);
		double request_rate = (double)total_requests / elapsed_seconds;
		double average_us = (double)total_latency_ns /
			(double)total_requests / 1000.0;
		double logical_mib_s = logical_mib / elapsed_seconds;

		printf("CSGC_MOVE_BENCH_RESULT requests=%" PRIu64
		       " moves=%" PRIu64 " logical_bytes=%" PRIu64
		       " elapsed_s=%.6f requests_s=%.3f logical_mib_s=%.3f "
		       "estimated_dma_mib_s=%.3f errors=0\n",
		       total_requests, total_moves,
		       total_moves * (uint64_t)F2FS_BLOCK_SIZE, elapsed_seconds,
		       request_rate, logical_mib_s, logical_mib_s * 2.0);
		printf("CSGC_MOVE_BENCH_LATENCY_US avg=%.3f min=%.3f p50=%.3f "
		       "p95=%.3f p99=%.3f max=%.3f\n",
		       average_us, all_latencies[0] / 1000.0,
		       percentile(all_latencies, total_requests, 50, 100) / 1000.0,
		       percentile(all_latencies, total_requests, 95, 100) / 1000.0,
		       percentile(all_latencies, total_requests, 99, 100) / 1000.0,
		       all_latencies[total_requests - 1U] / 1000.0);
		printf("CSGC_MOVE_BENCH_CSV,%u,%u,%u,%u,%u,%" PRIu64 ",%" PRIu64
		       ",%.6f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f\n",
		       cfg.queue_depth, cfg.moves, cfg.pool_size,
		       cfg.warmup_seconds, cfg.runtime_seconds,
		       total_requests, total_moves, elapsed_seconds,
		       request_rate, logical_mib_s,
		       average_us,
		       percentile(all_latencies, total_requests, 50, 100) / 1000.0,
		       percentile(all_latencies, total_requests, 95, 100) / 1000.0,
		       percentile(all_latencies, total_requests, 99, 100) / 1000.0,
		       all_latencies[total_requests - 1U] / 1000.0);
	}
	exit_code = EXIT_SUCCESS;

stop_workers:
	atomic_store(&shared.stop, true);
out:
	if (!threads_joined && threads_created) {
		atomic_store(&shared.stop, true);
		atomic_store_explicit(&shared.started, true, memory_order_release);
		for (uint32_t i = 0; i < threads_created; i++)
			pthread_join(threads[i], NULL);
	}
	if (workers) {
		for (uint32_t i = 0; i < cfg.queue_depth; i++) {
			if (workers[i].fd >= 0)
				close(workers[i].fd);
			free(workers[i].package);
			free(workers[i].latencies.values);
		}
	}
	if (geometry_fd >= 0)
		close(geometry_fd);
	free(all_latencies);
	free(threads);
	free(workers);
	return exit_code;
}
