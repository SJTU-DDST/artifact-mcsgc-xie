/*
 * Program Description:
 * --------------------
 * This C program writes files concurrently using multiple threads, tailored for two distinct modes of operation:
 * 1. "collaborate" mode: All threads collaborate to sequentially write to the same file, with each thread responsible for a specific partition.
 * 2. "independent" mode: Each thread independently writes multiple files, distributed evenly across threads.
 *
 * Additionally, the program supports preallocation of file space using `posix_fallocate` to ensure disk space is allocated before writing begins.
 * It provides performance statistics by calculating and displaying the time taken and bandwidth achieved for the file-writing operations.
 *
 * Usage:
 * ------
 * ./file_writer <directory> <filename_prefix> <num_files> <total_size> <num_threads> <buffer_size> <write_mode> <use_fallocate>
 *
 * Input Parameters:
 * -----------------
 * 1. directory        - The output directory where files will be created and written.
 * 2. filename_prefix  - Prefix for filenames, with numerical suffixes indicating the sequence or thread ID.
 * 3. num_files        - Total number of files to be created and written.
 * 4. total_size       - Combined total size for all files (supports "G", "M", "K" suffixes for size specification).
 * 5. num_threads      - Number of threads that will perform the file-writing tasks.
 * 6. buffer_size      - Size of each write operation's buffer (supports "G", "M", "K" suffixes).
 * 7. write_mode       - Mode of operation: "collaborate" for collaborative file writing or "independent" for independent file writing.
 * 8. use_fallocate    - Whether to preallocate space for files: "yes" to enable, "no" to disable.
 *
 * Detailed Behavior:
 * ------------------
 * - "collaborate" mode:
 *   Each thread writes to a partition of a single file in sequence, ensuring that no two threads write to the same portion simultaneously.
 *
 * - "independent" mode:
 *   Each thread writes several files independently, with file assignments distributed in a round-robin fashion across threads.
 *   For example, with 10 files and 3 threads:
 *     - Thread 0 writes files 0, 3, 6, 9
 *     - Thread 1 writes files 1, 4, 7
 *     - Thread 2 writes files 2, 5, 8
 *
 * Output:
 * -------
 * The program outputs detailed statistics for each file in "collaborate" mode, and cumulative statistics for all files in "independent" mode,
 * including the total writing time, total data size, and overall bandwidth.
 *
 * Example Command:
 * ----------------
 * ./file_writer /mnt/openssd_f2fs/data testfile 10 32G 3 1M independent yes
 *
 * This command configures 3 threads to independently write a total of 10 files to the specified directory,
 * with each file preallocated to a proportionate size, using a 1 MB buffer for each write operation.
 *
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>
#include <pthread.h>
#include <errno.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>

#define BLOCKSIZE 4096
#define ALIGNMENT 4096  // Alignment for O_DIRECT
#define ALIGN_UP(size, align) (((size) + (align) - 1) & ~((align) - 1))
#define ALIGN_DOWN(size, align) ((size) & ~((align) - 1))
#define BLK2BYTE(blk) ((blk) * BLOCKSIZE)
#define BYTE2BLK(byte) ((byte) / BLOCKSIZE)


typedef struct {
    char *directory;
    char *filename_prefix;
    size_t file_size;
    size_t buffer_size;
    int file_id;
    int thread_id;
    int num_threads;
    int total_file_num;
    int use_fallocate;
} thread_data_t;

pthread_barrier_t barrier;  // Global barrier variable for collaborate mode

void parse_size(const char *str, size_t *size) {
    char suffix = str[strlen(str) - 1];
    *size = atoll(str);
    if (suffix == 'G' || suffix == 'g') *size *= 1024 * 1024 * 1024;
    else if (suffix == 'M' || suffix == 'm') *size *= 1024 * 1024;
    else if (suffix == 'K' || suffix == 'k') *size *= 1024;
}

void *write_collaborate(void *arg) {
    thread_data_t *data = (thread_data_t *)arg;
    char file_path[512];
    sprintf(file_path, "%s/%s%d", data->directory, data->filename_prefix, data->file_id);

    if (data->thread_id == 0) {
        int fd = open(file_path, O_WRONLY | O_CREAT | O_DIRECT, 0644);
        if (fd < 0) {
            perror("Failed to open file");
            return NULL;
        }

        if (data->use_fallocate && posix_fallocate(fd, 0, data->file_size) != 0) {
            perror("Failed to allocate file space");
            close(fd);
            return NULL;
        }
        close(fd);
    }

    pthread_barrier_wait(&barrier);

    int fd = open(file_path, O_WRONLY | O_DIRECT, 0644);
    if (fd < 0) {
        perror("Failed to open file");
        return NULL;
    }

    char *buffer;
    if (posix_memalign((void **)&buffer, ALIGNMENT, data->buffer_size)) {
        perror("Buffer allocation failed");
        close(fd);
        return NULL;
    }
    memset(buffer, 'A', data->buffer_size);

    size_t file_blocks = data->file_size / BLOCKSIZE;
    size_t remainder = file_blocks % data->num_threads;
    size_t partition_blocks = file_blocks / data->num_threads + (data->thread_id < remainder);
    size_t start_blk_offset = data->thread_id < remainder ? data->thread_id * partition_blocks :
                              remainder * (partition_blocks + 1) + (data->thread_id - remainder) * partition_blocks;
    size_t remaining = partition_blocks;
    size_t buf_block_size = data->buffer_size / BLOCKSIZE;

    if (lseek(fd, BLK2BYTE(start_blk_offset), SEEK_SET) == -1) {
        perror("Seek failed");
        free(buffer);
        close(fd);
        return NULL;
    }

    while (remaining > 0) {
        size_t to_write = (remaining > buf_block_size) ? buf_block_size : remaining;
        ssize_t written = write(fd, buffer, BLK2BYTE(to_write));
        if (written < 0) {
            perror("Write failed");
            free(buffer);
            close(fd);
            return NULL;
        }
        remaining -= BYTE2BLK(written);
    }

    free(buffer);
    close(fd);
    return NULL;
}

void *write_independent(void *arg) {
    thread_data_t *data = (thread_data_t *)arg;
    for (int file_index = data->thread_id; file_index < data->total_file_num; file_index += data->num_threads) {
        char file_path[512];
        sprintf(file_path, "%s/%s%d", data->directory, data->filename_prefix, file_index + 1);

        printf("worker %d writing file: %s, size: %zu \n",data->thread_id, file_path, data->file_size);
        int fd = open(file_path, O_WRONLY | O_CREAT | O_DIRECT, 0644);
        if (fd < 0) {
            perror("Failed to open file");
            continue;
        }

        if (data->use_fallocate && posix_fallocate(fd, 0, data->file_size) != 0) {
            perror("Failed to allocate file space");
            close(fd);
            continue;
        }

        char *buffer;
        if (posix_memalign((void **)&buffer, ALIGNMENT, data->buffer_size)) {
            perror("Buffer allocation failed");
            close(fd);
            continue;
        }
        memset(buffer, 'A', data->buffer_size);

        size_t total_size = data->file_size;
        while (total_size > 0) {
            size_t to_write = (total_size > data->buffer_size) ? data->buffer_size : total_size;
            ssize_t written = write(fd, buffer, to_write);
            if (written < 0) {
                perror("Write failed");
                break;
            }
            total_size -= written;
        }

        free(buffer);
        close(fd);
    }

    return NULL;
}

int main(int argc, char *argv[]) {
    if (argc != 9) {
        fprintf(stderr, "Usage: %s <directory> <filename_prefix> <num_files> <total_size> <num_threads> <buffer_size> <write_mode> <use_fallocate>\n", argv[0]);
        return EXIT_FAILURE;
    }

    char *directory = argv[1];
    char *filename_prefix = argv[2];
    int num_files = atoi(argv[3]);
    size_t total_size;
    parse_size(argv[4], &total_size);
    int num_threads = atoi(argv[5]);
    size_t buffer_size;
    parse_size(argv[6], &buffer_size);
    buffer_size = ALIGN_UP(buffer_size, ALIGNMENT);
    char *write_mode = argv[7];
    int use_fallocate = (strcmp(argv[8], "yes") == 0);

    size_t file_size = ALIGN_DOWN(total_size / num_files, ALIGNMENT) ; // Size per file
    pthread_t threads[num_threads];
    thread_data_t thread_data[num_threads];

    // print args in one line
    printf("[filewriter arguments] directory: %s, filename_prefix: %s, num_files: %d, total_size: %zu, num_threads: %d, buffer_size: %zu, write_mode: %s, use_fallocate: %d\n",
           directory, filename_prefix, num_files, total_size, num_threads, buffer_size, write_mode, use_fallocate);

    
    if (strcmp(write_mode, "collaborate") == 0) {
        pthread_barrier_init(&barrier, NULL, num_threads);
        for (int file_id = 1; file_id <= num_files; file_id++) {
            // Start timing
            struct timespec start, end;
            clock_gettime(CLOCK_MONOTONIC, &start);

            for (int i = 0; i < num_threads; i++) {
                thread_data[i].directory = directory;
                thread_data[i].filename_prefix = filename_prefix;
                thread_data[i].file_size = file_size;
                thread_data[i].buffer_size = buffer_size;
                thread_data[i].file_id = file_id;
                thread_data[i].thread_id = i;
                thread_data[i].num_threads = num_threads;
                thread_data[i].use_fallocate = use_fallocate;

                int rc = pthread_create(&threads[i], NULL, write_collaborate, &thread_data[i]);
                if (rc) {
                    fprintf(stderr, "Error: unable to create thread %d, %s\n", i, strerror(rc));
                    return EXIT_FAILURE;
                }
            }

            // Wait for all threads to finish writing their parts
            for (int i = 0; i < num_threads; i++) {
                pthread_join(threads[i], NULL);
            }

            // End timing
            clock_gettime(CLOCK_MONOTONIC, &end);
            double elapsed_time = (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9;
            double bandwidth = file_size / elapsed_time / (1024 * 1024); // MB/s

            printf("File %d: Size = %zu bytes, Time = %.3f seconds, Bandwidth = %.2f MB/s\n",
                   file_id, file_size, elapsed_time, bandwidth);
        }
    } else {
        struct timespec start, end;
        clock_gettime(CLOCK_MONOTONIC, &start);
        for (int i = 0; i < num_threads; i++) {
            thread_data[i].directory = directory;
            thread_data[i].filename_prefix = filename_prefix;
            thread_data[i].file_size = file_size; // Size per file
            thread_data[i].buffer_size = buffer_size;
            thread_data[i].thread_id = i;
            thread_data[i].num_threads = num_threads;
            thread_data[i].total_file_num = num_files; // Total number of files
            thread_data[i].use_fallocate = use_fallocate;

            pthread_create(&threads[i], NULL, write_independent, &thread_data[i]);
        }

        for (int i = 0; i < num_threads; i++) {
            pthread_join(threads[i], NULL);
        }
        clock_gettime(CLOCK_MONOTONIC, &end);
        double elapsed_time = (end.tv_sec - start.tv_sec) + (end.tv_nsec - start.tv_nsec) / 1e9;
        double total_size_written = file_size * num_files;
        double bandwidth = total_size_written / elapsed_time / (1024 * 1024); // MB/s

        printf("All Files: Total Size = %.0f bytes, Time = %.3f seconds, Bandwidth = %.2f MB/s\n",
               total_size_written, elapsed_time, bandwidth);
    }


    if (strcmp(write_mode, "collaborate") == 0) {
        pthread_barrier_destroy(&barrier);
    }

    return EXIT_SUCCESS;
}
