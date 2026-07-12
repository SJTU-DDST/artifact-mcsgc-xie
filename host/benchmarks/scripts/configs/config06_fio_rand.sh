#!/bin/bash

workloads=("fio:randwrite")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=0

# Enter foreground GC before starting the measured fio workload.
fio_gc_precondition=1
fio_gc_precondition_size_per_job="4G"
fio_gc_precondition_max_rounds=4
