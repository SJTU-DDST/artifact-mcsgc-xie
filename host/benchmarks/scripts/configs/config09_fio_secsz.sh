#!/bin/bash

workloads=("fio:randwrite")
random_distributions=("random")
prefill_ratios=("0.86")
segs_per_sec_list=("1" "2" "4" "8" "16")
fio_timebased=0