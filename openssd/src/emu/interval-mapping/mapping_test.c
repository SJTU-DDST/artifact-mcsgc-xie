/*
 * mapping_test.c
 *
 *  Created on: 2021. 5. 3.
 *      Author: Minsu Jang (nobleminsu@gmail.com)
 */

#include <assert.h>

#include "map_segment.h"
#include "xtime_l.h"

extern struct linear_allocator ssd_allocator;

// alex::Alex<unsigned int, unsigned int> testHotmap;
int testCurMaxFtableIdx = -1;
// FTable testHotFTables[FTABLE_DEFAULT_TABLE_NUM];

void test_fhm() {
    xil_printf("Starting fhm test...\n");
    xil_printf("fhm test ok.\n");
}
void test_hotmap() {}
void test_mapseg() {
    nsleep(1000000);
    xil_printf("Starting mapseg test...\n");

    uintptr_t base, ptr2;
    unsigned long long  startTime, mapTime;


    startTime = get_time_ns();

    mapseg_init();
    for (unsigned int msb = 0; msb < 8; msb++){ // 0~3, tree 0, 4~7, tree; interval 128M LBAs, 512GB, 32 zone nodes
        xil_printf("msb = %u\n", msb);
        linear_allocator_get_mem_usage(&ssd_allocator, true);
        for (unsigned int i = 0; i < 10 * 16 * (1 << 16); i++) {    // 10M LBAs, 40GB storage, 3 zone nodes
            //        xil_printf("setting %d\n", i);
            int isSetSuccess = mapseg_set_mapping((msb << 27) + i, i + 1, false);
            //        xil_printf("getting %d\n", i);
            unsigned int out = mapseg_get_mapping((msb << 27) + i);
            if (out != i + 1){
                xil_printf("set fail %d, out is %d\n", isSetSuccess, out);
                ASSERT(0);
            }
        }
    }
    
    mapTime = get_time_ns();
    xil_printf("test finished\n");
    linear_allocator_get_mem_usage(&ssd_allocator, true);

    char outText[128];
    sprintf(outText, "mapseg test took %llu sec\n",
            (mapTime - startTime) / 1000000000);
    xil_printf("%s", outText);

    //    FTable* ftable = ftable_create_table(
    //        0, testHotFTables, &testCurMaxFtableIdx,
    //        FTABLE_DEFAULT_TABLE_NUM);
    //    ftable_insert(ftable, 0, 1, NULL);
    //    assert(ftable_get(ftable, 0) == 1);

    //    int i;
    //    for (i = 0; i < FTABLE_DEFAULT_CAPACITY * 10; i += 10000) {
    //        ftable_insert(ftable, i, i + 100, NULL);
    //        assert(ftable_get(ftable, i) == i + 100);
    //    }
    //    xil_printf("ftable test ok.\n");
}
