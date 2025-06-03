#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#
#
# Copyright 2007 Sun Microsystems, Inc.  All rights reserved.
# Use is subject to license terms.
#

# set $dir=/mnt/openssd_f2fs
set $dir=__DATA_PATH_PLACEHOLDER__
set $runtime=__RUNTIME_PLACEHOLDER__

# set $nfiles=432000
# set $filesize=cvar(type=cvar-gamma,parameters=mean:1048576;gamma:1.5)
# set $meandirwidth=100000
# set $meanappendsize=64k

set $nfiles=54000
set $filesize=cvar(type=cvar-uniform,parameters=lower:524288;upper:1572864) # min:512KiB max:1536KiB
set $meandirwidth=10000
set $meanwritesize=128k

# set $nfiles=80000
# # set $filesize=cvar(type=cvar-gamma,parameters=mean:131072;gamma:1.5) # 128KB*80000 = 10GB
# set $filesize=cvar(type=cvar-uniform,parameters=lower:65536;upper:196608) # min:64KiB max:192KiB
# set $meandirwidth=10000
# set $meanappendsize=72k

# set $nfiles=864000
# set $filesize=cvar(type=cvar-gamma,parameters=mean:65536;gamma:1.5)
# set $meandirwidth=10000
# set $meanappendsize=40k

set $nthreads=4
set $iosize=1m
# set $meanwritesize=64k

define fileset name=bigfileset,path=$dir,size=$filesize,entries=$nfiles,dirwidth=$meandirwidth,prealloc=80

define process name=filereader,instances=1
{
  thread name=filereaderthread,memsize=100m,instances=$nthreads
  {
    flowop deletefile name=deletefile1,filesetname=bigfileset
    flowop createfile name=createfile2,filesetname=bigfileset,fd=1
    flowop writewholefile name=writewholefile1,srcfd=1,fd=1,iosize=$iosize
    # flowop appendfilerand name=appendfilerand2,iosize=$meanappendsize,fd=1
    flowop fsync name=fsyncfile2,fd=1
    flowop closefile name=closefile2,fd=1
    flowop openfile name=openfile3,filesetname=bigfileset,fd=1
    flowop readwholefile name=readfile3,fd=1,iosize=$iosize
    flowop write name=writefile2,iosize=$meanwritesize,fd=1,random
    # flowop appendfilerand name=appendfilerand3,iosize=$meanappendsize,fd=1
    flowop fsync name=fsyncfile3,fd=1
    flowop closefile name=closefile3,fd=1
    # flowop openfile name=openfile4,filesetname=bigfileset,fd=1
    # flowop readwholefile name=readfile4,fd=1,iosize=$iosize
    # flowop closefile name=closefile4,fd=1
  }
}

echo  "Varmail Version 3.0 personality successfully loaded"

run $runtime
# psrun -5 300
