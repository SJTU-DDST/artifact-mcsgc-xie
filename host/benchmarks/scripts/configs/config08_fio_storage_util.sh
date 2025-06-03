#!/bin/bash

workloads=("fio:randwrite")
random_distributions=("random")
prefill_ratios=("0.6" "0.7" "0.8" "0.9" "0.95")
segs_per_sec_list=("8")
fio_timebased=0