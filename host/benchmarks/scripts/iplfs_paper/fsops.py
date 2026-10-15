"""Narrow privileged file operations, callable only on the explicitly mounted test device."""

import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess


def mounted_root(root):
    """Refuse file operations unless the expected F2FS namespace is mounted here."""
    root = Path(root)
    info = json.loads(subprocess.check_output(['findmnt', '-J', '-M', str(root),
                                              '-o', 'SOURCE,FSTYPE,TARGET'], text=True))['filesystems']
    if len(info) != 1 or info[0]['source'] != '/dev/nvme0n1' or info[0]['fstype'] != 'f2fs':
        raise ValueError('Not the explicitly mounted experimental namespace')
    if root.is_symlink() or root.resolve() != root:
        raise ValueError('Redirected test mountpoint')
    return root


def normalize(root, job, count):
    """Convert a just-created file_writer pool from one-based to fio's zero-based names."""
    folder = root / f'pipeline.{job}'
    for index in range(1, count + 1):
        source, target = folder / f'file.{index}', folder / f'file.{index - 1}'
        if not source.is_file() or source.is_symlink() or target.exists():
            raise ValueError(f'Unexpected pool layout: {source} -> {target}')
        source.rename(target)
    for index in range(count):
        if (folder / f'file.{index}').stat().st_size != 2 * 1024 ** 2:
            raise ValueError('Small-file prefill length mismatch')


def verify(root):
    """Check durable write/readback, overwrite and hole-punch behavior without deleting files."""
    path = root / 'iplfs-smoke-integrity.bin'
    expected = bytes(range(256)) * 4096
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        if os.pwrite(fd, expected, 0) != len(expected):
            raise ValueError('Short integrity write')
        os.fsync(fd)
        if os.pread(fd, len(expected), 0) != expected:
            raise ValueError('Integrity readback mismatch')
        replacement = (b'IPLFS-OVERWRITE!!' * 256)[:4096]
        if os.pwrite(fd, replacement, 4096) != 4096:
            raise ValueError('Short overwrite')
        os.fsync(fd)
        if os.pread(fd, 4096, 4096) != replacement:
            raise ValueError('Overwrite readback mismatch')
        libc = ctypes.CDLL(None, use_errno=True)
        libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong]
        if libc.fallocate(fd, 3, 8192, 4096):
            raise OSError(ctypes.get_errno(), 'Hole punching failed')
        os.fsync(fd)
        if os.pread(fd, 4096, 8192) != b'\0' * 4096:
            raise ValueError('Hole readback mismatch')
    finally:
        os.close(fd)
    print('Integrity write/readback, overwrite and hole-punch checks passed')


def main():
    """Restrict privileged operations to explicit, bounded experiment actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['normalize', 'verify'])
    parser.add_argument('mountpoint')
    parser.add_argument('--job', type=int, default=0)
    parser.add_argument('--count', type=int, default=1646)
    args = parser.parse_args()
    if not 0 <= args.job < 16 or not 1 <= args.count <= 1646:
        parser.error('Invalid pool bounds')
    root = mounted_root(args.mountpoint)
    (normalize(root, args.job, args.count) if args.operation == 'normalize' else verify(root))


if __name__ == '__main__':
    main()
