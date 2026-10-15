#define _GNU_SOURCE
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/nvme_ioctl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

struct dsm_range {
    uint32_t attributes;
    uint32_t blocks;
    uint64_t start_lba;
};

_Static_assert(sizeof(struct dsm_range) == 16, "DSM wire range must be 16 bytes");

/* Parse unsigned decimal arguments without accepting truncation or negative values. */
static uint64_t number(const char *text)
{
    char *end;
    unsigned long long value;

    if (!text[0] || text[0] == '-') {
        fprintf(stderr, "Invalid unsigned argument\n");
        exit(2);
    }
    errno = 0;
    value = strtoull(text, &end, 10);
    if (errno || *end) {
        fprintf(stderr, "Invalid unsigned argument\n");
        exit(2);
    }
    return value;
}

/* Submit one DSM range in a complete aligned page for the pinned firmware's DMA contract. */
int main(int argc, char **argv)
{
    struct nvme_passthru_cmd command = {0};
    struct stat info;
    struct dsm_range *range;
    void *page;
    uint64_t start, blocks;
    int dry, fd, nsid, result;

    dry = argc == 4 && !strcmp(argv[1], "--dry-run");
    if (!dry && (argc != 5 || strcmp(argv[1], "--execute") ||
                 strcmp(argv[2], "/dev/nvme0n1"))) {
        fprintf(stderr, "Usage: %s --dry-run SLBA BLOCKS | --execute /dev/nvme0n1 SLBA BLOCKS\n", argv[0]);
        return 2;
    }
    start = number(argv[dry ? 2 : 3]);
    blocks = number(argv[dry ? 3 : 4]);
    if (!blocks || blocks > UINT32_MAX || UINT64_MAX - start < blocks)
        return 2;
    if (posix_memalign(&page, 4096, 4096))
        return 2;
    memset(page, 0, 4096);
    range = page;
    range->blocks = htole32((uint32_t)blocks);
    range->start_lba = htole64(start);
    command.opcode = 0x09;
    command.addr = (uint64_t)(uintptr_t)page;
    command.data_len = 4096;
    command.cdw10 = 0;
    command.cdw11 = 1U << 2;
    command.timeout_ms = 10000;
    if (dry) {
        printf("{\"data_len\":4096,\"ranges\":1,\"address_alignment\":%u,"
               "\"start_lba\":%" PRIu64 ",\"blocks\":%" PRIu64 "}\n",
               (unsigned)((uintptr_t)page % 4096), start, blocks);
        free(page);
        return 0;
    }
    fd = open(argv[2], O_RDWR | O_NOFOLLOW);
    if (fd < 0 || fstat(fd, &info) || !S_ISBLK(info.st_mode)) {
        perror("Expected namespace block device");
        if (fd >= 0)
            close(fd);
        free(page);
        return 2;
    }
    nsid = ioctl(fd, NVME_IOCTL_ID);
    if (nsid <= 0) {
        perror("NVMe namespace ID");
        close(fd);
        free(page);
        return 2;
    }
    command.nsid = (uint32_t)nsid;
    result = ioctl(fd, NVME_IOCTL_IO_CMD, &command);
    if (result)
        fprintf(stderr, "DSM failed: status=%d errno=%d\n", result, errno);
    else
        printf("Aligned-page DSM completed\n");
    close(fd);
    free(page);
    return result ? 1 : 0;
}
