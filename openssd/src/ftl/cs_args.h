#include "config.h"
#include "queue.h"

enum {
    CS_ARGS_FREE = 0,
    CS_ARGS_RX,
    CS_ARGS_TX,
};

struct cs_args_req {
    int type;
    unsigned int cmd_slot_tag;
    unsigned int qid;
    unsigned int cid;
    unsigned int nlb; /* 1-based */
    unsigned int cs_seq_id;
    int cs_slot_id;
    unsigned int dma_tail;
    unsigned int dma_overflow_cnt;
    QTAILQ_ENTRY(cs_args_req) qent;
};

void init_cs_args();
void transfer_cs_args(unsigned int cmd_slot_tag, unsigned int qid, unsigned int cid,
                      unsigned int nlb_0, unsigned int cs_seq_id, int type);
void queue_cs_args_req(unsigned int cmd_slot_tag, unsigned int qid, unsigned int cid,
                       unsigned int nlb_0, unsigned int cs_seq_id, int type);
void execute_queued_cs_args_reqs();
void check_done_cs_args_reqs();
// int get_cs_status();
