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
# Copyright 2008 Sun Microsystems, Inc.  All rights reserved.
# Use is subject to license terms.
#



# set $dir=/mnt/openssd_f2fs
# set $runtime=300
set $dir=/home/xin/ssd/mnt
set $runtime=300
set $nfiles=54000
set $meandirwidth=40
set $filesize=cvar(type=cvar-uniform,parameters=lower:524288;upper:1572864) # min:512KiB max:1536KiB
set $nthreads=4
set $iosize=1m
set $meanappendsize=32k
set $meanwritesize=128k

define fileset name=bigfileset,path=$dir,size=$filesize,entries=$nfiles,dirwidth=$meandirwidth,prealloc=80

define process name=filereader,instances=1
{
  thread name=filereaderthread,memsize=100m,instances=$nthreads
  {
    flowop createfile name=createfile1,filesetname=bigfileset,fd=1
    flowop writewholefile name=wrtfile1,srcfd=1,fd=1,iosize=$iosize
    flowop closefile name=closefile1,fd=1

    flowop openfile name=openfile2,filesetname=bigfileset,fd=1
    flowop write name=writefile2,iosize=$meanwritesize,fd=1,random
#    flowop appendfilerand name=appendfilerand1,iosize=$meanappendsize,fd=1
    flowop closefile name=closefile2,fd=1

    # flowop openfile name=openfile3,filesetname=bigfileset,fd=1
    # flowop write name=writefile3,iosize=$meanwritesize,fd=1,random
    # flowop closefile name=closefile3,fd=1

    flowop openfile name=openfile4,filesetname=bigfileset,fd=1
    flowop readwholefile name=readfile1,fd=1,iosize=$iosize
    flowop closefile name=closefile4,fd=1

    flowop deletefile name=deletefile1,filesetname=bigfileset
    flowop statfile name=statfile1,filesetname=bigfileset
  }
}

echo  "File-server Version 3.0 personality successfully loaded"

psrun -5 300