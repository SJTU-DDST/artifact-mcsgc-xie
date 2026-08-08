#!/bin/bash

workloads=("fio:rw16t26336file")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=1

# Build 16 disjoint pools. The aggregate prefill is 26,336 * 2 MiB,
# matching 86% of the current 64 GB test device within 2 MiB.
export should_prefill=1
export smallfile_layout=partitioned
export smallfile_jobs=16
export smallfile_files_per_job=1646
export smallfile_size_mb=2
export smallfile_prefill_threads=8

# The CSGC wrapper defaults this to 1; ORI explicitly sets it to 0 because
# ordinary GC does not emit CSGC_PIPELINE_STAT records.
: "${require_pipeline_stats:=1}"
export require_pipeline_stats

# Each round writes 1 GiB per fio job across the complete file pool.
fio_gc_precondition=1
fio_gc_precondition_size_per_job="1G"
fio_gc_precondition_max_rounds=4
