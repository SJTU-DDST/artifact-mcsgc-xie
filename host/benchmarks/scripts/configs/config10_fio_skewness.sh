#!/bin/bash

workloads=("fio:randwrite")
random_distributions=("random" "zipf:0.3" "zipf:0.7" "zipf:0.9" "zipf:1.1")
prefill_ratios=("0.86")
segs_per_sec_list=("8")
fio_timebased=1