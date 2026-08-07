#!/bin/bash

workloads=("fio:rw16t52kfile")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=1

# The measured workload must overwrite the pre-created inode pool.
export should_prefill=1
fio_gc_precondition=0
