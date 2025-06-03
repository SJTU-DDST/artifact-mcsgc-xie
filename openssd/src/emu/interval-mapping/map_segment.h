/*
 * wchunk.h
 *
 *  Created on: 2021. 7. 16.
 *      Author: Minsu Jang (nobleminsu@gmail.com)
 */

#ifndef SRC_MAPPING_MAPSEG_MAP_SEGMENT_H_
#define SRC_MAPPING_MAPSEG_MAP_SEGMENT_H_

#include "../ftl.h"
// #include "../../address_translation.h"
// #include "../../alex/alex.h"
// #include "../../alex/openssd_allocator.h"
//#include "../functional/functional_mapping.h"

struct unit_allocator{
    size_t unit_size;
    void * allocator;
};

static inline void init_unit_allocator(struct unit_allocator *uallocator, 
        size_t unit_size, void *base_allocator){
    uallocator->unit_size = unit_size;
    uallocator->allocator = base_allocator;
}

static inline void *uallocate(struct unit_allocator *uallocator, size_t size)
{
    if(size * uallocator->unit_size >= 1024)
        nsleep(10*1000); // 10us
    else
        nsleep(5*1000); // 5us
    return linear_malloc((struct linear_allocator *)(uallocator->allocator), size * uallocator->unit_size, 4); 
}

static inline void udeallocate(struct unit_allocator *uallocator, void *ptr)
{
    nsleep(2*1000); // 2us
    // TODO: implement deallocate
}

#define VSA_FAIL (~0U) // INVALID_PPA
#define VSA_NONE (~0U) // UNMAPPED_PPA

#define MAPSEG_USE_CACHE 0
#define MAPSEG_CACHE_USE_LAST_SLOT 1

#define MAPSEG_LENGTH_DIGIT 12  // 4K
#define MAPSEG_BUCKET_DIGIT 8   // 256
#define MAPSEG_MAP_SEGMENT_SIZE (1 << MAPSEG_LENGTH_DIGIT)  // 4K, one mapseg handles at most 4K LBAs, i.e. 16MB, mapping is done in 4KB granularity
#define MAPSEG_BUCKET_SIZE (1 << MAPSEG_BUCKET_DIGIT)       // 256
#define MAPSEG_CACHE_SIZE 20
#define MAPSEG_START_ADDR_MASK (~(MAPSEG_MAP_SEGMENT_SIZE - 1))
#define MAPSEG_BUCKET_INDEX_MASK \
    ((MAPSEG_BUCKET_SIZE - 1) << MAPSEG_LENGTH_DIGIT)
#define MAPSEG_BUCKET_INDEX(lsa) \
    ((lsa & MAPSEG_BUCKET_INDEX_MASK) >> MAPSEG_LENGTH_DIGIT)

#define MAPSEG_VALID_BIT_INDEX(index) (index >> 3)
#define MAPSEG_VALID_BIT_SELECTOR(index, bitsInSlice) \
    (bitsInSlice << (28 - ((index & 0x7) << 2)))
#define MAPSEG_FULL_BITS_IN_SLICE 0xF

#define MAPSEG_ERASE_LIST_LENGTH (1024)

#define MAPSEG_INITIAL_BUFFER_SIZE 1

extern struct unit_allocator rangeDirAllocator;
typedef struct range_directory {
    uint16_t *bufferIdxs;  // size = mappingSize/unitRangeSize,
                               // use this to find mapping in range buffer.
} RangeDirectory;

static inline void mapseg_init_range_directory(RangeDirectory *rangeDir,
                                        unsigned int dirSize) {
    rangeDir->bufferIdxs = (uint16_t *) uallocate(&rangeDirAllocator, dirSize);
}

typedef struct _LOGICAL_SLICE_ENTRY {
	unsigned int virtualSliceAddr;
} LOGICAL_SLICE_ENTRY, *P_LOGICAL_SLICE_ENTRY;

extern struct unit_allocator rangeBufferAllocator;
typedef struct range_buffer {
    /*struct header {
        unsigned int startLsa : 16;
    };
    struct header header;*/
    LOGICAL_SLICE_ENTRY *entries;  // size = variable
} RangeBuffer;

static inline void mapseg_init_range_buffer(RangeBuffer *rangeBuffer,
                                     unsigned int startLsa,
                                     unsigned int bufferSize) {
   rangeBuffer->entries = (LOGICAL_SLICE_ENTRY *) uallocate(&rangeBufferAllocator, bufferSize);
   /* set header */
   ((rangeBuffer->entries)[0]).virtualSliceAddr = startLsa;
   
   /* set region directory */
}

struct zone_node;
extern struct unit_allocator mapSegmentBufferAllocator;
typedef struct map_segment {
    // metadata
    unsigned int startLsa;
    unsigned int mappingSize : 16;      // number of LBAs in the map segment
    unsigned int unitRangeSize : 16;    // number of LBAs in a range (region)
    int numOfValidMaps : 16;  // number of valid bits, used for efficient
                              // decision of erase
    int numOfWrittenMaps : 16;  // number of valid written Mappings, used for checking
			      // map segment had been fully written. 
    unsigned int validBits[MAPSEG_VALID_BIT_INDEX(MAPSEG_MAP_SEGMENT_SIZE)]; // validity bitmap
    uint32_t sz;  /* size of map_segment*/
    RangeDirectory rangeDir;
    RangeBuffer *rangeBuffer;

    uint8_t compact_cnt;

    struct zone_node *parent;
} MapSegment, *MapSegment_p;



static inline LOGICAL_SLICE_ENTRY *mapseg_fetch_entry_in_map_segment(
    MapSegment *pMapSegment, unsigned int logicalSliceAddr, uint16_t* diridx,
	uint32_t* bufidx) {
    LOGICAL_SLICE_ENTRY *entry, region_sLBA;
    uint16_t dirIdx =
        (logicalSliceAddr - pMapSegment->startLsa) / pMapSegment->unitRangeSize;
    uint16_t bufferIdx = ((pMapSegment->rangeDir).bufferIdxs)[dirIdx];
    if (diridx != NULL)
    	*diridx = dirIdx;
    if (bufidx != NULL)
    	*bufidx = bufferIdx;

    entry = &((pMapSegment->rangeBuffer->entries)[bufferIdx]);
    region_sLBA = *entry;
    entry += logicalSliceAddr - region_sLBA.virtualSliceAddr + 1;
    *bufidx = bufferIdx + logicalSliceAddr - region_sLBA.virtualSliceAddr + 1;
//    if (diridx != NULL)
//    	xil_printf("%s: inputLsa: %x headerLsa: %x \n", __func__, logicalSliceAddr,
//    			region_sLBA.virtualSliceAddr);
    //unsigned int indexInMapSegment = logicalSliceAddr - rangeStartLsa;

    // xil_printf("entry fetched %p = %p\n", logicalSliceAddr,
    //            &pMapSegment->rangeBuffer->entries[indexInMapSegment]);
    //return &pMapSegment->rangeBuffer->entries[indexInMapSegment];
    return entry;
}

void mapseg_init();
void mapseg_init_map_segment(MapSegment *mapSegment_p, unsigned int startLsa);
unsigned int mapseg_get_mapping(unsigned int logicalSliceAddr);
int mapseg_set_mapping(unsigned int logicalSliceAddr,
                       unsigned int virtualSliceAddr, bool last_discard);
int mapseg_remove(unsigned int logicalSliceAddr, bool last_discard);

void mapseg_deallocate(MapSegment_p wchunk_p, unsigned int chunkStartAddr);
int mapseg_is_empty(MapSegment_p wchunk_p, unsigned int indexInChunk);
int mapseg_is_valid(MapSegment_p wchunk_p, unsigned int indexInChunk);
void mapseg_mark_valid(MapSegment_p wchunk_p, unsigned int indexInChunk,
                       int length, unsigned int wchunkStartAddr, int isValid,
                       int bitsInSlice, uint64_t parent_addr, bool last_discard);
int mapseg_mark_valid_partial(unsigned int logicalSliceAddr, int isValid,
                              int start, int end, bool include_last_discard);

#endif /* SRC_MAPPING_MAPSEG_MAP_SEGMENT_H_ */
