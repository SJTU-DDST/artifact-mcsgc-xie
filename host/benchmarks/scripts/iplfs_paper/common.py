"""Shared, device-independent helpers for the IPLFS paper experiment suite."""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / 'host/benchmarks/scripts'
SUPPORT = ROOT / 'iplfs-paper-preparation'
SOURCE = '0271b907ec00ed643fd139403b726817c9fe8c32'
FIRMWARE = '463e8b0b13ad345ed99c2176b1f81ad34d3c986a'
MIB = 1024 ** 2
GIB = 1024 ** 3


def now():
    """Return a timezone-explicit timestamp in the experiment's reporting zone."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()


def stamp():
    """Generate a collision-resistant identifier without relying on wall time alone."""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]


def sha(path):
    """Hash a file incrementally, including large kernel and ELF artifacts."""
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    """Read a small text input while tolerating kernel-log replacement bytes."""
    return Path(path).read_text(errors='replace')


def capture(args, timeout=30, cwd=None):
    """Run an explicit argument vector without a shell or interactive input."""
    return subprocess.run([str(x) for x in args], cwd=cwd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors='replace', timeout=timeout, check=False)


def checked(args, timeout=30, cwd=None):
    """Return command output or fail with the command's diagnostic text."""
    p = capture(args, timeout, cwd)
    if p.returncode:
        raise RuntimeError(f'{args[0]} exited {p.returncode}: {p.stderr[-3000:]}')
    return p.stdout.strip()


def archive(path):
    """Preserve an existing file beside its owner before publishing a replacement."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        target = path.parent / 'TODELETE' / (path.name + '.' + stamp())
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        return target
    return None


def write_json(path, value):
    """Publish JSON atomically, preserving every previous version instead of deleting it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.pending.' + stamp())
    with pending.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    archive(path)
    pending.rename(path)


def load_json(path):
    """Load structured configuration without executing shell assignments."""
    return json.loads(Path(path).read_text())


def boot_id():
    """Identify the current kernel boot for provenance and restart checks."""
    return read('/proc/sys/kernel/random/boot_id').strip()


def process_identity(pid=None):
    """Read process start ticks safely even when its command contains spaces."""
    pid = pid or os.getpid()
    stat = read(f'/proc/{pid}/stat').rsplit(')', 1)[1].split()
    return {'pid': pid, 'start_ticks': int(stat[19]), 'boot_id': boot_id()}


def process_alive(identity):
    """Reject reused PIDs and identities left over from a previous boot."""
    try:
        return process_identity(identity['pid']) == identity
    except (FileNotFoundError, ProcessLookupError):
        return False


def default_config():
    """Provide explicit pending deployment values rather than guessing runtime identity."""
    return {
        'schema': 1, 'device': '/dev/nvme0n1', 'serial': 'SSDD515T',
        'model': 'DaisyPlus OpenSSD', 'physical_bytes': 64223813632,
        'mountpoint': '/mnt/openssd_f2fs', 'kernel_release': '5.11.0-iplfs',
        'kernel_image': '/boot/vmlinuz-5.11.0-iplfs',
        'module': '/lib/modules/5.11.0-iplfs/kernel/fs/f2fs/f2fs.ko',
        'kernel_sha256': None, 'module_sha256': None, 'module_srcversion': None,
        'tools_manifest': str(SUPPORT / 'tools.json'),
        'firmware_receipt': None, 'uart_host': '192.168.98.31', 'uart_log': None,
        'firmware_commit': FIRMWARE, 'required_aslr': 0, 'required_panic': 0,
        'memory_bytes': 8 * GIB, 'cgroup': 'iplfs_paper',
        'mysql_datadir': '/mnt/openssd_f2fs/mysql',
        'mysql_seed': '/var/lib/mysql', 'mysql_service': 'mysql',
        'mysql_scope': 'historical-systemd-unlimited-client-unlimited',
        'mysql_user': 'ycsb_user', 'mysql_password': '1111',
        'result_root': str(SUPPORT / 'batches'), 'expected_cases': 63,
        'timeouts': {'command': 60, 'prefill': 3600, 'benchmark': 7200,
                     'sync': 300, 'unmount': 180, 'fsck': 600, 'mysql': 120},
    }


def schedule(smoke=False):
    """Build the fixed 63-run matrix, placing Filebench after lower-risk cases."""
    base = []
    for i, util in enumerate(('0.6', '0.7', '0.8', '0.9', '0.95'), 1):
        base.append(dict(point=f'U{i:02}', family='utilization', kind='fio', ratio=util))
    for i, dist in enumerate(('random', 'zipf:0.3', 'zipf:0.7', 'zipf:0.9', 'zipf:1.1'), 1):
        base.append(dict(point=f'Z{i:02}', family='skewness', kind='fio', dist=dist, timed=True))
    base += [dict(point='P05', family='headline', kind='fio'),
             dict(point='P06', family='headline', kind='fio', dist='zipf:1.1', timed=True),
             dict(point='P03', family='headline', kind='ycsb', workload='workloada'),
             dict(point='P04', family='headline', kind='ycsb', workload='workloadf', ratio='0.8')]
    gc = [dict(point='G01', family='gcheavy', kind='fio', warmup=True),
          dict(point='G02', family='gcheavy', kind='smallfile', warmup=True, timed=True)]
    fb = [dict(point='P01', family='headline', kind='filebench', workload='fileserver_4t_60G_1M_54k'),
          dict(point='T01', family='timeline', kind='filebench', workload='fileserver_4t_60G_1M_54k_period'),
          dict(point='P02', family='headline', kind='filebench', workload='varmail_4t_60G_1M_54k')]
    rows = []
    groups = [(base, 3), (gc, 6), (fb, 3)]
    if smoke:
        groups = [([dict(point='S00', family='smoke', kind='verify'),
                    base[0], base[12], gc[0], gc[1], fb[0], fb[2], fb[1]], 1)]
    for points, repeats in groups:
        for rep in range(1, repeats + 1):
            for item in points:
                row = dict(ratio='0.86', dist='random', timed=False, warmup=False,
                           runtime_s=300, s=1, repeat=rep, smoke=smoke, operations=2000000,
                           jobs=4, size_per_job=20 * GIB, files_per_job=1646)
                row.update(item)
                if row['kind'] == 'smallfile':
                    row.update(jobs=16, size_per_job=3292 * MIB)
                if smoke:
                    row.update(runtime_s=10, operations=2000, ratio='0.02',
                               size_per_job=16 * MIB, files_per_job=8)
                row['id'] = f"{len(rows) + 1:03}-{row['point']}-r{rep}"
                rows.append(row)
    assert len(rows) == (8 if smoke else 63)
    return rows
