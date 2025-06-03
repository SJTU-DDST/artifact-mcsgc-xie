#ifndef __F2FS_PROBE_H
#define __F2FS_PROBE_H

#include "assert.h"
#include "utils.h"
#include "xil_printf.h"

void f2fs_read_super();
int f2fs_probe(int is_ready, unsigned int *main_blkaddr);

#endif