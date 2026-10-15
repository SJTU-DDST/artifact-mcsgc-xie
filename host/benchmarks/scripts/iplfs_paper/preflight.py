"""Read-only readiness checks; no device commands, mounts, or configuration writes."""

import os
from pathlib import Path
import re
import shlex
import stat

from analysis import kernel_errors
from common import FIRMWARE, ROOT, boot_id, capture, checked, load_json, now, read, sha


def uart_read(config, offset=None):
    """Read an existing UART capture over SSH without changing server31 files or state."""
    path = config.get('uart_log')
    host = config.get('uart_host')
    if not path or not host or not re.fullmatch(r'[a-zA-Z0-9_.@:-]+', host):
        raise ValueError('Configure the UART host and an absolute existing capture path')
    if not path.startswith('/') or '\n' in path:
        raise ValueError('Invalid UART path')
    quoted = shlex.quote(path)
    prefix = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
              '-o', 'StrictHostKeyChecking=yes', host]
    info = checked(prefix + [f"stat -Lc '%i %s' -- {quoted}"], timeout=15).split()
    inode, size = map(int, info)
    if offset is None:
        return {'inode': inode, 'size': size}
    if inode != offset['inode'] or size < offset['size']:
        raise ValueError('UART log was rotated or truncated; configuration readback is ambiguous')
    count = size - offset['size']
    if count > 8 * 1024 * 1024:
        raise ValueError('Unexpectedly large UART readback window')
    output = checked(prefix + [f'dd if={quoted} iflag=skip_bytes,count_bytes '
                               f'skip={offset["size"]} count={count} status=none'], timeout=15)
    return output


def parse_uart_config(text):
    """Decode the last complete firmware configuration block emitted by Core3."""
    blocks = re.findall(r'=+SSD CONFIG=+(.*?)(?:\n=+)', text, re.S)
    if not blocks:
        raise ValueError('No complete SSD CONFIG UART block')
    values = {key: int(value) for key, value in re.findall(r'(\w+):\s*(\d+)', blocks[-1])}
    expected = {'l2p_mapping_type': 3, 'nand_latency_emu_enabled': 0, 'dsm_enabled': 1}
    if any(values.get(key) != value for key, value in expected.items()):
        raise ValueError(f'SSD configuration readback mismatch: {values}')
    return values


def device_state(config):
    """Read sysfs and mount tables, refusing partitions, holders, and other mounted users."""
    device = Path(config['device'])
    if str(device) != '/dev/nvme0n1' or device.is_symlink() or not stat.S_ISBLK(device.stat().st_mode):
        raise ValueError('Expected the explicit /dev/nvme0n1 block namespace')
    sys = Path('/sys/class/block') / device.name
    model = read(sys / 'device/model').strip()
    serial = read(sys / 'device/serial').strip()
    capacity = int(read(sys / 'size')) * 512
    if (model, serial, capacity) != (config['model'], config['serial'], config['physical_bytes']):
        raise ValueError(f'Device identity/capacity mismatch: {model}, {serial}, {capacity}')
    partitions = [p.name for p in sys.glob(device.name + 'p*')]
    if partitions or list((sys / 'holders').iterdir()):
        raise ValueError('Namespace has partitions or active block holders')
    major_minor = read(sys / 'dev').strip()
    if any(line.split()[2] == major_minor for line in read('/proc/self/mountinfo').splitlines()):
        raise ValueError('Target namespace is mounted')
    if str(device) in read('/proc/swaps'):
        raise ValueError('Target namespace is in swap use')
    busy = capture(['sudo', '-n', 'fuser', str(device)], timeout=10)
    if busy.returncode != 1 or busy.stdout.strip():
        raise ValueError(f'Device is in use or fuser could not establish idleness: {busy.stdout} {busy.stderr}')
    mountpoint = Path(config['mountpoint'])
    if not mountpoint.is_dir() or mountpoint.is_symlink():
        raise ValueError('Mountpoint must already exist as a real directory')
    if any(mountpoint.iterdir()):
        raise ValueError('Mountpoint is not empty; existing files will not be hidden')
    return {'model': model, 'serial': serial, 'physical_bytes': capacity,
            'sysfs_device': str((sys / 'device').resolve()), 'major_minor': major_minor}


def tool_manifest(config):
    """Verify all frozen executables against the locally prepared manifest."""
    data = load_json(config['tools_manifest'])
    if data.get('read_only_fsck') is not True:
        raise ValueError('A dedicated read-only IPLFS fsck build is required')
    required = {'mkfs', 'fsck', 'fio', 'filebench', 'nvme', 'cgexec', 'cgcreate', 'cgset',
                'file_writer', 'python2', 'java', 'dsm_page'}
    if not required <= set(data['binaries']):
        raise ValueError('Incomplete tool manifest')
    for name, item in data['binaries'].items():
        if not os.access(item['path'], os.X_OK) or sha(item['path']) != item['sha256']:
            raise ValueError(f'Executable changed or missing: {name}')
    return data


def preflight(config, online_uart=False):
    """Return all readiness blockers rather than changing the environment to hide them."""
    result = {'checked_at': now(), 'boot_id': boot_id(), 'kernel': os.uname().release,
              'read_only': True, 'checks': {}, 'blockers': []}

    def check(name, function):
        """Keep independent checks running after one readiness failure."""
        try:
            result['checks'][name] = function()
        except Exception as exc:
            result['blockers'].append(f'{name}: {exc}')

    def kernel():
        """Check installed and loaded kernel artifacts, not just a branch label."""
        if os.uname().release != config['kernel_release']:
            raise ValueError(f'Boot {config["kernel_release"]}; current kernel is {os.uname().release}')
        for prefix, key in [('kernel', 'kernel_image'), ('module', 'module')]:
            expected = config.get(prefix + '_sha256')
            if not expected or sha(config[key]) != expected:
                raise ValueError(f'Missing or mismatched {prefix} SHA256')
        if checked(['modinfo', '-F', 'srcversion', config['module']]) != config['module_srcversion']:
            raise ValueError('Installed module srcversion mismatch')
        loaded = Path('/sys/module/f2fs/srcversion')
        if loaded.exists() and read(loaded).strip() != config['module_srcversion']:
            raise ValueError('A different F2FS module is loaded')
        return {'image_sha256': config['kernel_sha256'], 'module_sha256': config['module_sha256'],
                'loaded': loaded.exists()}

    def sysctl():
        """Require previously approved settings without writing any sysctl."""
        current = {'panic': int(read('/proc/sys/kernel/panic')),
                   'aslr': int(read('/proc/sys/kernel/randomize_va_space'))}
        if current != {'panic': config['required_panic'], 'aslr': config['required_aslr']}:
            raise ValueError(f'System settings need user approval: current {current}; expected panic=0, ASLR=0')
        if config['required_panic'] != 0 or config['required_aslr'] != 0:
            raise ValueError('The frozen matrix requires panic=0 and ASLR=0')
        return current

    def firmware():
        """Require an operator startup receipt, explicitly retaining its proof boundary."""
        if config['firmware_commit'] != FIRMWARE or not config.get('firmware_receipt'):
            raise ValueError('No pinned original-firmware startup receipt')
        receipt = load_json(config['firmware_receipt'])
        if receipt.get('firmware_commit') != FIRMWARE or receipt.get('state') != 'operator-confirmed-started':
            raise ValueError('Wrong firmware receipt')
        if set(receipt.get('elfs', {})) != {'ftl', 'cs_leader', 'cs_worker1', 'cs_worker2'}:
            raise ValueError('Four ELF hashes are required')
        if config.get('uart_log') != receipt['uart_log']:
            raise ValueError('UART path differs from startup receipt')
        return {'commit': FIRMWARE, 'receipt_sha256': sha(config['firmware_receipt']),
                'boundary': receipt['runtime_identity']}

    def resources():
        """Check cgroup capabilities and absence of competing benchmark processes."""
        if 'memory' not in read('/sys/fs/cgroup/cgroup.controllers').split():
            raise ValueError('A cgroup-v2 memory controller is required')
        processes = checked(['ps', '-eo', 'pid=,comm=,stat='])
        active = [line for line in processes.splitlines()
                  if re.search(r'\s(fio|filebench|mysqld|mkfs\.f2fs|fsck\.f2fs)\s', line)]
        if active:
            raise ValueError('Competing processes: ' + '; '.join(active))
        if any(line.split()[-1].startswith('D') for line in processes.splitlines() if line.split()):
            raise ValueError('Uninterruptible tasks exist; inspect before starting')
        group = Path('/sys/fs/cgroup') / config['cgroup']
        if group.exists() and read(group / 'cgroup.procs').strip():
            raise ValueError('The dedicated benchmark cgroup is busy')
        return {'memory_controller': True, 'dedicated_cgroup': str(group)}

    def database():
        """Verify the historical DB path and scope without starting the service."""
        seed = Path(config['mysql_seed']) / 'ycsb_db/usertable.ibd'
        checked(['sudo', '-n', 'test', '-f', str(seed)])
        defaults = checked(['sudo', '-n', 'my_print_defaults', 'mysqld'])
        paths = re.findall(r'^--datadir=(.+)$', defaults, re.M)
        if not paths or paths[-1].rstrip('/') != config['mysql_datadir']:
            raise ValueError(f'MySQL datadir must already be {config["mysql_datadir"]}; do not rewrite it automatically')
        scope = checked(['systemctl', 'show', config['mysql_service'], '-p', 'MemoryMax', '--value'])
        if scope != 'infinity':
            raise ValueError('MySQL MemoryMax differs from the measured historical unlimited scope')
        jdbc = ROOT / 'host/benchmarks/ycsb-0.17.0/jdbc-binding/conf/db.properties'
        if not jdbc.exists():
            raise ValueError('Missing historical JDBC configuration')
        return {'seed': str(seed), 'datadir': paths[-1], 'memory_scope': config['mysql_scope']}

    def logs():
        """Read existing kernel messages without clearing or triggering any log output."""
        messages = checked(['sudo', '-n', 'dmesg', '--color=never'], timeout=15)
        errors = kernel_errors(messages)
        if errors:
            raise ValueError('Current boot already has kernel errors: ' + '; '.join(errors[-4:]))
        return {'failure_signatures': 0}

    check('sudo', lambda: checked(['sudo', '-n', 'true']))
    check('device', lambda: device_state(config))
    check('kernel', kernel)
    check('tools', lambda: {name: {'path': value['path'], 'sha256': value['sha256']}
                           for name, value in tool_manifest(config)['binaries'].items()})
    check('sysctl', sysctl)
    check('firmware', firmware)
    check('resources', resources)
    check('database', database)
    check('kernel_log', logs)
    if online_uart:
        check('uart', lambda: uart_read(config))
    elif not config.get('uart_log'):
        result['blockers'].append('uart: existing server31 UART capture path is not configured')
    result['ready'] = not result['blockers']
    return result
