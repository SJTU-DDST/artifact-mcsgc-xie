#!/bin/bash

workloads=("fio:randwrite")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=0

# Reproduce the historical single-big-file workload while using the quiet
# formal measurement path. The precondition performs one fixed 4 GiB write per
# fio job so every mode receives the same 16 GiB warm-up traffic.
formal_performance_only=1
require_pipeline_stats=0
fio_gc_precondition=1
fio_gc_precondition_size_per_job="4G"
fio_gc_precondition_max_rounds=1
