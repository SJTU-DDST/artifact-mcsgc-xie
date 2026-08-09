#!/bin/bash

workloads=("fio:rw16t26336file")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=1

# Use the same partitioned 2 MiB file pool as the strongest completed pipeline
# experiment. The total prefill is approximately 86% of the 64 GB device.
export should_prefill=1
export smallfile_layout=partitioned
export smallfile_jobs=16
export smallfile_files_per_job=1646
export smallfile_size_mb=2
export smallfile_prefill_threads=8

# Formal mode avoids every custom Host/SSD measurement interface and relies on
# fio JSON as the primary result. One fixed precondition round is used for all
# modes instead of stopping dynamically when a custom GC counter changes.
formal_performance_only=1
require_pipeline_stats=0
fio_gc_precondition=1
fio_gc_precondition_size_per_job="1G"
fio_gc_precondition_max_rounds=1
