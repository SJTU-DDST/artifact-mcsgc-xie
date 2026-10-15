"""Single-entry IPLFS evaluation matrix with explicit destructive runtime modes."""

import argparse
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import traceback

from analysis import (analyze, csv_file, filebench_metrics, fio_metrics, kernel_errors,
                      waf_metrics, ycsb_metrics)
from common import (FIRMWARE, GIB, MIB, ROOT, SCRIPTS, SUPPORT, boot_id, capture, checked,
                    default_config, load_json, now, process_alive, process_identity, read,
                    schedule, sha, stamp, write_json)
from preflight import device_state, parse_uart_config, preflight, tool_manifest, uart_read

HERE = Path(__file__).resolve().parent
WORKLOADS = ROOT / 'host/benchmarks/myworkloads'


def config_fingerprint(config):
    """Bind smoke approval to configuration and every runnable harness input."""
    import hashlib
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode())
    inputs = list(HERE.glob('*.py')) + [HERE / 'report-template-zh.md', SCRIPTS / 'run_taco_iplfs_matrix.sh']
    inputs += list(HERE.glob('*.c'))
    inputs += sorted(p for p in WORKLOADS.rglob('*') if p.is_file())
    inputs += [Path(config['tools_manifest'])]
    if config.get('firmware_receipt'):
        inputs.append(Path(config['firmware_receipt']))
    for path in sorted(inputs):
        digest.update(str(path).encode())
        digest.update(sha(path).encode())
    return digest.hexdigest()


def status(batch):
    """Distinguish active transitions, complete matrices and interrupted runners."""
    batch = Path(batch)
    state = load_json(batch / 'state.json')
    cases = load_json(batch / 'schedule.json')
    passed = []
    durations = {}
    for case in cases:
        path = batch / 'runs' / case['id'] / 'validation.json'
        if path.exists() and load_json(path).get('status') == 'passed':
            passed.append(case['id'])
            value = load_json(path)
            if value.get('elapsed_s', 0) > 0:
                durations.setdefault(case['point'], []).append(value['elapsed_s'])
    done = batch / 'completed.json'
    complete = (done.exists() and load_json(done).get('status') == 'completed'
                and load_json(done).get('passed_ids') == [c['id'] for c in cases]
                and len(passed) == len(cases) and state.get('status') == 'completed')
    alive = process_alive(state['runner'])
    missing_estimates = sorted({c['point'] for c in cases if c['id'] not in passed and c['point'] not in durations})
    remaining = None if missing_estimates else sum(statistics.median(durations[c['point']])
                     for c in cases if c['id'] not in passed)
    return {'batch': str(batch), 'status': 'completed' if complete else
            ('running' if alive and state['status'] == 'running' else
             ('failed' if state['status'] == 'failed' else 'interrupted')),
            'passed': len(passed), 'expected': len(cases), 'current_case': state.get('case'),
            'phase': state.get('phase'), 'last_update': state.get('updated_at'),
            'runner_alive': alive, 'error': state.get('error'),
            'remaining_s_from_completed_same_point_runs': remaining,
            'points_without_duration_evidence': missing_estimates}


class Runner:
    """Own a uniquely identified matrix and stop immediately on any invalid case."""

    def __init__(self, config, smoke=False, smoke_batch=None, resume=None):
        """Validate all prerequisites before allocating a new, separate result batch."""
        self.config = config
        self.smoke = smoke
        self.cases = schedule(smoke)
        self.tools = tool_manifest(config)['binaries']
        self.fingerprint = config_fingerprint(config)
        self.previous = None
        if resume:
            previous = Path(resume).resolve()
            old = load_json(previous / 'provenance.json')
            if old['fingerprint'] != self.fingerprint or load_json(previous / 'schedule.json') != self.cases:
                raise ValueError('Resume source/configuration/schedule differs; start a new full batch')
            if status(previous)['runner_alive']:
                raise ValueError('The previous runner is still alive')
            self.previous = previous
        if not smoke:
            if not smoke_batch:
                raise ValueError('Formal runs require --smoke-batch from this configuration and boot')
            result = status(smoke_batch)
            smoke_provenance = load_json(Path(smoke_batch) / 'provenance.json')
            if result['status'] != 'completed' or not smoke_provenance['smoke']:
                raise ValueError('The designated smoke batch did not complete')
            if smoke_provenance['fingerprint'] != self.fingerprint or smoke_provenance['boot_id'] != boot_id():
                raise ValueError('Configuration or boot changed after smoke; repeat smoke first')
        self.batch = Path(config['result_root']) / (('smoke-' if smoke else 'formal-') + stamp())
        self.batch.mkdir(parents=True, exist_ok=False)
        self.current = self.batch
        self.phase_number = 0
        self.state = {'status': 'running', 'runner': process_identity(), 'started_at': now(),
                      'case': None, 'phase': 'initializing', 'passed_ids': [], 'expected': len(self.cases)}
        self.update()
        write_json(self.batch / 'schedule.json', self.cases)
        csv_file(self.batch / 'schedule.tsv', self.cases)
        write_json(self.batch / 'config.json', config)
        patch = checked(['git', 'diff', '--binary'], cwd=ROOT)
        (self.batch / 'source.patch').write_text(patch)
        source = self.batch / 'source-snapshot'
        shutil.copytree(HERE, source / 'iplfs_paper', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(WORKLOADS, source / 'workloads')
        shutil.copy2(SCRIPTS / 'run_taco_iplfs_matrix.sh', source)
        shutil.copy2(config['tools_manifest'], source / 'tools.json')
        shutil.copy2(config['firmware_receipt'], source / 'firmware-startup.json')
        self.provenance = {'schema': 1, 'operator': os.environ.get('SUDO_USER', os.environ.get('USER')),
                           'outer_script': str(SCRIPTS / 'run_taco_iplfs_matrix.sh'),
                           'started_at': now(), 'boot_id': boot_id(), 'kernel': os.uname().release,
                           'artifact_commit': checked(['git', 'rev-parse', 'HEAD'], cwd=ROOT),
                           'artifact_branch': checked(['git', 'branch', '--show-current'], cwd=ROOT),
                           'fingerprint': self.fingerprint, 'smoke': smoke,
                           'previous_batch': str(self.previous) if self.previous else None,
                           'system': checked(['uname', '-a']), 'kernel_config_sha256':
                           sha('/boot/config-' + os.uname().release)}
        write_json(self.batch / 'provenance.json', self.provenance)
        print('Batch directory: ' + str(self.batch), flush=True)

    def update(self, **values):
        """Publish progress atomically while preserving earlier states for postmortem analysis."""
        self.state.update(values, updated_at=now())
        write_json(self.batch / 'state.json', self.state)

    def tool(self, name):
        """Resolve a previously hash-verified private executable."""
        return self.tools[name]['path']

    def phase(self, name, command, timeout=None, root=False, environment=None):
        """Run a bounded phase, preserving its command, output, identity and exit status."""
        self.phase_number += 1
        output = self.current / f'{self.phase_number:03}-{name}.log'
        timeout = timeout or self.config['timeouts']['command']
        actual = [str(x) for x in command]
        if root:
            actual = ['sudo', '-n', 'timeout', '--signal=TERM', '--kill-after=10s', str(timeout)] + actual
        else:
            actual = ['timeout', '--signal=TERM', '--kill-after=10s', str(timeout)] + actual
        self.update(phase=name, phase_log=str(output), phase_started_at=now())
        start = time.monotonic()
        event = {'name': name, 'started_at': now(), 'command': actual, 'log': str(output)}
        print(f'{event["started_at"]} {self.state["case"] or "batch"} {name}', flush=True)
        with output.open('xb') as stream:
            proc = subprocess.Popen(actual, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=subprocess.STDOUT, start_new_session=True, env=environment)
            event['pid'] = proc.pid
            self.update(child=process_identity(proc.pid))
            try:
                code = proc.wait(timeout=timeout + 20)
            except KeyboardInterrupt:
                identity = self.state['child']
                if process_alive(identity):
                    if root:
                        capture(['sudo', '-n', 'kill', '-TERM', '--', '-' + str(proc.pid)], timeout=5)
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                raise
            except subprocess.TimeoutExpired:
                event.update(error='Command did not exit after timeout; preserve kernel state',
                             elapsed_s=time.monotonic() - start)
                with (self.current / 'phases.jsonl').open('a') as journal:
                    journal.write(json.dumps(event) + '\n')
                raise RuntimeError(event['error'])
        event.update(finished_at=now(), elapsed_s=time.monotonic() - start, returncode=code)
        with (self.current / 'phases.jsonl').open('a') as journal:
            journal.write(json.dumps(event) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
        self.update(child=None)
        if code:
            raise RuntimeError(f'{name} exited {code}; see {output}')
        return output

    def admin_config(self):
        """Reset to Interval Mapping and verify its values from fresh UART readback."""
        offset = uart_read(self.config)
        self.phase('ssd-set-config', [self.tool('nvme'), 'ssd-admin', self.config['device'],
                                      '-o', '1', '--l2p', '3', '--nand', '0', '--dsm', '1'], root=True)
        self.phase('ssd-get-config', [self.tool('nvme'), 'ssd-admin', self.config['device'], '-o', '0'], root=True)
        last = ''
        for _ in range(10):
            last = uart_read(self.config, offset)
            try:
                parsed = parse_uart_config(last)
                (self.current / f'device-config-uart-{self.phase_number:03}.log').write_text(last)
                write_json(self.current / f'device-config-{self.phase_number:03}.json', parsed)
                return
            except ValueError:
                time.sleep(1)
        (self.current / 'device-config-uart-failed.log').write_text(last)
        raise RuntimeError('Could not verify L2P=3, NAND latency=0, DSM=1 from fresh UART output')

    def reset_stats(self):
        """Reset after prefill/warmup and verify a zero normal-write counter."""
        self.phase('reset-statistics', [self.tool('nvme'), 'ssd-admin', self.config['device'], '-o', '2'], root=True)
        path = self.device_statistics('statistics-reset-readback')
        values = waf_metrics(read(path), require_nonzero=False)['device_counters']
        if values['host_normal_write_bytes'] != 0 or values['nand_write_bytes'] != 0:
            raise RuntimeError('Statistics did not reset, or unexpected writes reached the device')

    def device_statistics(self, name):
        """Read the existing firmware log without enabling new instrumentation."""
        return self.phase(name, [self.tool('nvme'), 'read', self.config['device'],
                                 '-s', '123', '-c', '1', '-z', '4096', '-t', '-L'], root=True)

    def limited(self, name, command, timeout):
        """Run fio/Filebench with measured evidence of the historical 8-GiB memory cap."""
        evidence = self.current / (name + '-cgroup.json')
        args = [self.tool('cgexec'), '-g', 'memory:' + self.config['cgroup'],
                sys.executable, '-B', HERE / 'exec_in_cgroup.py', evidence,
                self.config['cgroup'], str(self.config['memory_bytes']), *command]
        return self.phase(name, args, timeout=timeout, root=True)

    def prefill(self, case):
        """Create the exact physical-byte working set, with localized small-file pools."""
        mnt = self.config['mountpoint']
        timeout = self.config['timeouts']['prefill']
        if case['kind'] == 'smallfile':
            for job in range(16):
                folder = Path(mnt) / f'pipeline.{job}'
                self.phase(f'pool-{job:02}-mkdir', ['mkdir', str(folder)], root=True)
                count = case['files_per_job']
                self.phase(f'pool-{job:02}-prefill', [self.tool('file_writer'), folder, 'file.',
                           str(count), str(count * 2 * MIB), '8', '1M', 'independent', 'no'], timeout, root=True)
                self.phase(f'pool-{job:02}-normalize', [sys.executable, '-B', HERE / 'fsops.py',
                           'normalize', mnt, '--job', str(job), '--count', str(count)], root=True)
            return case['files_per_job'] * 2 * MIB * 16
        files = 16 if case['kind'] == 'ycsb' else 1
        requested = int(Decimal(str(self.config['physical_bytes'])) * Decimal(case['ratio']))
        actual = requested // files // 4096 * 4096 * files
        self.phase('prefill', [self.tool('file_writer'), mnt, 'testbigfile', str(files),
                   str(actual), str(files), '1M', 'independent', 'no'], timeout, root=True)
        lengths = self.phase('prefill-lengths', ['stat', '-c', '%n %s',
                             *[str(Path(mnt) / f'testbigfile{i}') for i in range(1, files + 1)]], root=True)
        sizes = [int(line.rsplit(' ', 1)[1]) for line in read(lengths).splitlines()]
        if sizes != [actual // files] * files:
            raise RuntimeError('Prefill file sizes differ from the physical-capacity working set')
        write_json(self.current / 'prefill.json', {'requested_bytes': requested, 'actual_bytes': actual,
                   'physical_capacity': self.config['physical_bytes'], 'files': files})
        return actual

    def fio(self, case, byte_count, warmup=False):
        """Preserve main-paper and GC-heavy semantics without changing stop conditions."""
        small = case['kind'] == 'smallfile'
        effective = dict(case)
        if warmup:
            effective.update(timed=False, size_per_job=(16 * MIB if case['smoke'] else
                                                       (GIB if small else 4 * GIB)))
        mnt = self.config['mountpoint']
        path = self.current / ('warmup.fio' if warmup else 'effective-workload.fio')
        if small:
            contents = ('[global]\nioengine=libaio\nthread=1\ngroup_reporting=1\n'
                        f'filename_format={mnt}/pipeline.$jobnum/file.$filenum\n'
                        f'filesize=2M\nnrfiles={case["files_per_job"]}\n'
                        f'size={case["files_per_job"] * 2}M\nopenfiles=128\n'
                        'allow_file_create=0\noverwrite=1\n'
                        '[pipeline_partitioned_randwrite]\nrw=randwrite\ndirect=0\n'
                        'bs=4k\niodepth=16\nnumjobs=16\nfile_service_type=random:64\n'
                        'random_distribution=random\nrandrepeat=1\nrandseed=20260807\n')
            args = [self.tool('fio'), str(path)]
            if warmup or case['smoke']:
                args += ['--io_size=' + str(effective['size_per_job'])]
        else:
            contents = read(WORKLOADS / 'fio/randwrite.fio')
            args = [self.tool('fio'), str(path), '--directory=' + mnt, '--alloc-size=16m',
                    '--filesize=' + str(byte_count), '--size=' + str(effective['size_per_job']),
                    '--numjobs=4', '--random_distribution=' + case['dist']]
            if case['warmup']:
                args += ['--allow_file_create=0', '--overwrite=1']
        args += ['--time_based=' + str(int(effective['timed'])), '--output-format=normal,json']
        if effective['timed']:
            args += ['--runtime=' + str(effective['runtime_s'])]
        if warmup:
            args += ['--end_fsync=1']
        path.write_text(contents)
        log = self.limited('warmup' if warmup else 'benchmark', args, self.config['timeouts']['benchmark'])
        metric = fio_metrics(read(log), effective)
        write_json(self.current / ('warmup-derived.json' if warmup else 'benchmark-derived.json'), metric)
        return metric

    def filebench(self, case):
        """Render only path, runtime and smoke-size parameters; use literal psrun duration."""
        text = read(WORKLOADS / 'filebench' / (case['workload'] + '.f'))
        text = text.replace('__DATA_PATH_PLACEHOLDER__', self.config['mountpoint'])
        text = text.replace('__RUNTIME_PLACEHOLDER__', str(case['runtime_s']))
        text = text.replace('psrun -5 $runtime', f'psrun -5 {case["runtime_s"]}')
        if case['smoke']:
            text = text.replace('set $nfiles=54000', 'set $nfiles=256')
        if '__' in text or (case['family'] == 'timeline' and f'psrun -5 {case["runtime_s"]}' not in text):
            raise ValueError('Unresolved Filebench workload')
        path = self.current / 'effective-workload.f'
        path.write_text(text)
        log = self.limited('benchmark', [self.tool('filebench'), '-f', str(path)], self.config['timeouts']['benchmark'])
        return filebench_metrics(read(log), case)

    def ycsb(self, case):
        """Run the historical database workload and record the actual unlimited memory scope."""
        self.phase('database-copy', ['cp', '-a', self.config['mysql_seed'], self.config['mysql_datadir']],
                   self.config['timeouts']['prefill'], root=True)
        service = self.config['mysql_service']
        self.phase('mysql-start', ['systemctl', 'start', service], self.config['timeouts']['mysql'], root=True)
        self.phase('mysql-ping', ['mysqladmin', 'ping'], root=True)
        self.phase('mysql-scope', ['systemctl', 'show', service, '-p', 'MemoryMax', '-p', 'ControlGroup'])
        (self.current / 'ycsb-client-cgroup.log').write_text(read('/proc/self/cgroup'))
        base = ROOT / 'host/benchmarks/ycsb-0.17.0'
        source = WORKLOADS / 'ycsb' / case['workload']
        shutil.copy2(source, self.current / 'effective-workload.properties')
        properties = {'db.driver': 'com.mysql.jdbc.Driver',
                      'db.url': 'jdbc:mysql://localhost:3306/ycsb_db',
                      'db.user': self.config['mysql_user'], 'db.passwd': self.config['mysql_password'],
                      'recordcount': 1000000, 'fieldcount': 16, 'fieldlength': 64, 'minfieldlength': 16,
                      'operationcount': case['operations'], 'readallfields': 'true', 'writeallfields': 'false'}
        args = [self.tool('python2'), str(base / 'bin/ycsb'), 'run', 'jdbc', '-P', str(source),
                '-P', str(base / 'jdbc-binding/conf/db.properties')]
        for key, value in properties.items():
            args += ['-p', f'{key}={value}']
        args += ['-threads', '36', '-s']
        env = dict(os.environ, JAVA_HOME=str(Path(self.tool('java')).resolve().parent.parent))
        log = self.phase('benchmark', args, self.config['timeouts']['benchmark'], environment=env)
        metrics = ycsb_metrics(read(log), case)
        self.phase('mysql-stop', ['systemctl', 'stop', service], self.config['timeouts']['mysql'], root=True)
        return metrics

    def integrity(self):
        """Exercise IPLFS high logical addresses before mkfs, with explicit readback and DSM."""
        self.reset_stats()
        nvme, device = self.tool('nvme'), self.config['device']
        logical_block = int(read('/sys/class/block/nvme0n1/queue/logical_block_size'))
        if logical_block not in (512, 4096):
            raise ValueError('Unexpected namespace block size')
        lba = self.config['physical_bytes'] // logical_block + 8192
        blocks = 4096 // logical_block
        for index in range(2):
            data = self.current / f'high-lba-write-{index}.bin'
            data.write_bytes(bytes([(index * 173 + i) % 256 for i in range(4096)]))
            returned = self.current / f'high-lba-read-{index}.bin'
            self.phase('high-lba-write', [nvme, 'write', device, '-s', str(lba), '-c', str(blocks - 1),
                       '-z', '4096', '-d', data], root=True)
            self.phase('high-lba-read', [nvme, 'read', device, '-s', str(lba), '-c', str(blocks - 1),
                       '-z', '4096', '-d', returned], root=True)
            if sha(data) != sha(returned):
                raise RuntimeError('High-LBA data mismatch')
        self.phase('high-lba-discard', [self.tool('dsm_page'), '--execute', device,
                                      str(lba), str(blocks)], root=True)
        counters = waf_metrics(read(self.device_statistics('high-lba-statistics')))['device_counters']
        if counters.get('nand_page_dsm_cnt', 0) <= 0:
            raise RuntimeError('High-LBA discard did not advance the device DSM counter')
        write_json(self.current / 'high-lba.json', {'lba': lba, 'block_bytes': logical_block,
                                                  'write_readback_overwrite': 'passed', 'dsm_counters': counters})
        self.admin_config()

    def one_case(self, case):
        """Run one independently initialized case, then require teardown and readonly fsck."""
        self.current = self.batch / 'runs' / case['id']
        self.current.mkdir(parents=True, exist_ok=False)
        self.phase_number = 0
        self.update(case=case['id'], phase='pre-case')
        start = time.monotonic()
        started_at = now()
        write_json(self.current / 'case.json', case)
        try:
            if config_fingerprint(self.config) != self.fingerprint:
                raise RuntimeError('Harness, configuration or tools changed during this batch')
            device_state(self.config)
            boot_start = float(read('/proc/uptime').split()[0])
            before = self.phase('kernel-before', ['dmesg', '--color=never'], root=True)
            if kernel_errors(read(before)):
                raise RuntimeError('Kernel errors predate this case; refusing another reset')
            self.admin_config()
            if case['kind'] == 'verify':
                self.integrity()
            device, mnt = self.config['device'], self.config['mountpoint']
            self.phase('mkfs', [self.tool('mkfs'), '-f', '-s', '1', device], 300, root=True)
            self.phase('mount', ['mount', '-t', 'f2fs', '-o',
                       'mode=lfs,background_gc=off,fsync_mode=strict,discard', device, mnt], root=True)
            self.phase('mount-evidence', ['findmnt', '-J', '-M', mnt, '-o', 'SOURCE,TARGET,FSTYPE,OPTIONS'])
            if case['kind'] == 'verify':
                self.phase('integrity', [sys.executable, '-B', HERE / 'fsops.py', 'verify', mnt], root=True)
                self.phase('discard', ['fstrim', '-v', mnt], 300, root=True)
                metrics = {'throughput': 0, 'unit': 'correctness-only', 'runtime_s': time.monotonic() - start}
            else:
                byte_count = self.prefill(case) if case['kind'] != 'filebench' else 0
                if case['warmup']:
                    self.phase('prefill-sync', ['sync', '-f', mnt], self.config['timeouts']['sync'], root=True)
                if case['warmup']:
                    self.fio(case, byte_count, warmup=True)
                    self.phase('warmup-sync', ['sync', '-f', mnt], self.config['timeouts']['sync'], root=True)
                self.reset_stats()
                write_json(self.current / 'measurement-start.json', {'time': now(), 'monotonic_s': time.monotonic()})
                if case['kind'] in ('fio', 'smallfile'):
                    metrics = self.fio(case, byte_count)
                elif case['kind'] == 'filebench':
                    metrics = self.filebench(case)
                else:
                    metrics = self.ycsb(case)
                write_json(self.current / 'measurement-end.json', {'time': now(), 'monotonic_s': time.monotonic()})
            self.phase('final-sync', ['sync', '-f', mnt], self.config['timeouts']['sync'], root=True)
            self.phase('unmount', ['umount', mnt], self.config['timeouts']['unmount'], root=True)
            statistics = self.device_statistics('device-stat-after')
            if case['kind'] != 'verify':
                metrics.update(waf_metrics(read(statistics)))
                counters = metrics['device_counters']
                if counters['host_gc_write_bytes'] or counters.get('nand_cs_write_bytes', 0):
                    raise RuntimeError('IPLFS unexpectedly generated Host-GC or CSGC write traffic')
            fsck = self.phase('readonly-fsck', [self.tool('fsck'), '-n', device],
                              self.config['timeouts']['fsck'], root=True)
            fsck_text = read(fsck)
            if 'Done.' not in fsck_text or re.search(r'\[Fail\]|ASSERT|Error:|corrupt', fsck_text, re.I):
                raise RuntimeError('Readonly fsck did not report a clean complete check')
            after = self.phase('kernel-after', ['dmesg', '--color=never'], root=True)
            lines = []
            for line in read(after).splitlines():
                timestamp = re.match(r'^\[\s*([0-9.]+)\]', line)
                if timestamp and float(timestamp[1]) >= boot_start:
                    lines.append(line)
            fresh = '\n'.join(lines)
            (self.current / 'kernel-new.log').write_text(fresh + '\n')
            errors = kernel_errors(fresh)
            if errors:
                raise RuntimeError('Kernel errors: ' + '; '.join(errors[-4:]))
            device_state(self.config)
            validation = {'status': 'passed', 'started_at': started_at, 'finished_at': now(), 'elapsed_s': time.monotonic() - start,
                          'fsck': 'read-only-clean', 'kernel_errors': [], 'metrics': metrics}
            write_json(self.current / 'validation.json', validation)
            return validation
        except BaseException as exc:
            write_json(self.current / 'validation.json', {'status': 'failed', 'error': str(exc),
                       'started_at': started_at, 'finished_at': now(), 'elapsed_s': time.monotonic() - start,
                       'traceback': traceback.format_exc(), 'last_phase': self.state['phase']})
            # Do not force unmount, restart, repair, or format again after an ambiguous failure.
            for name, command in [('kernel-failure', ['sudo', '-n', 'dmesg', '--color=never']),
                                  ('process-failure', ['ps', '-eo', 'pid,ppid,stat,wchan:32,comm,args']),
                                  ('mount-failure', ['findmnt', '-J'])]:
                try:
                    output = capture(command, timeout=10)
                    (self.current / (name + '.log')).write_text(output.stdout + output.stderr)
                except Exception:
                    pass
            raise

    def run(self):
        """Execute all cases and publish completion only after every required check passes."""
        try:
            readiness = preflight(self.config, online_uart=True)
            write_json(self.batch / 'preflight.json', readiness)
            if not readiness['ready']:
                raise RuntimeError('Readiness changed before runtime setup')
            self.phase('load-module', ['modprobe', 'f2fs'], root=True)
            if read('/sys/module/f2fs/srcversion').strip() != self.config['module_srcversion']:
                raise RuntimeError('Loaded module identity differs from the configured binary')
            group = Path('/sys/fs/cgroup') / self.config['cgroup']
            if not group.exists():
                self.phase('create-cgroup', [self.tool('cgcreate'), '-g', 'memory:' + self.config['cgroup']], root=True)
            self.phase('configure-cgroup', [self.tool('cgset'), '-r',
                       'memory.max=' + str(self.config['memory_bytes']), self.config['cgroup']], root=True)
            for case in self.cases:
                old = self.previous / 'runs' / case['id'] if self.previous else None
                if old and (old / 'validation.json').exists() and load_json(old / 'validation.json')['status'] == 'passed':
                    destination = self.batch / 'runs' / case['id']
                    shutil.copytree(old, destination)
                    write_json(destination / 'reused-from.json', {'source': str(old)})
                else:
                    self.one_case(case)
                self.state['passed_ids'].append(case['id'])
                self.update(phase='between-cases')
                analyze(self.batch)
            if len(self.state['passed_ids']) != len(self.cases):
                raise RuntimeError('Incomplete matrix')
            self.update(status='completed', phase='completed', finished_at=now())
            write_json(self.batch / 'completed.json', dict(self.state))
            self.provenance['finished_at'] = now()
            write_json(self.batch / 'provenance.json', self.provenance)
            analyze(self.batch)
            print('Completed: ' + str(self.batch), flush=True)
        except BaseException as exc:
            self.update(status='failed', error=str(exc), finished_at=now())
            analyze(self.batch)
            raise


def main():
    """Keep planning and preflight side-effect free; require explicit runtime authorization."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--init-config', metavar='PATH')
    modes.add_argument('--bind-firmware', metavar='STARTUP_RECEIPT', help='Bind an existing local receipt; no server31 access')
    modes.add_argument('--status', metavar='BATCH')
    modes.add_argument('--analyze', metavar='BATCH')
    modes.add_argument('--smoke', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--all', action='store_true', help='Run smoke first, then the 63 formal cases')
    parser.add_argument('--config', default=str(SUPPORT / 'config.json'))
    parser.add_argument('--online-uart', action='store_true', help='Permit read-only SSH to the configured UART log')
    parser.add_argument('--allow-destructive', metavar='DEVICE')
    parser.add_argument('--smoke-batch')
    parser.add_argument('--resume', metavar='FAILED_BATCH')
    args = parser.parse_args()
    if args.init_config:
        target = Path(args.init_config)
        if target.exists():
            parser.error('Refusing to overwrite an existing configuration')
        write_json(target, default_config())
        print(f'Pending deployment configuration written: {target}')
        return
    if args.dry_run:
        print(json.dumps({'formal_cases': schedule(), 'smoke_cases': schedule(True),
                          'warning': 'Planning only: no experiment, formatting, mounting, or device command'}, indent=2))
        return
    if args.bind_firmware:
        receipt_path = Path(args.bind_firmware).resolve()
        receipt = load_json(receipt_path)
        if receipt.get('firmware_commit') != FIRMWARE or receipt.get('state') != 'operator-confirmed-started':
            parser.error('Expected a pinned operator-confirmed startup receipt')
        if set(receipt.get('elfs', {})) != {'ftl', 'cs_leader', 'cs_worker1', 'cs_worker2'}:
            parser.error('Expected exactly four ELF records')
        config = load_json(args.config)
        config.update(firmware_receipt=str(receipt_path), uart_log=receipt['uart_log'])
        write_json(args.config, config)
        print('Local firmware receipt bound; no experiment or remote operation was performed.')
        return
    if args.status or args.analyze:
        print(json.dumps(status(args.status) if args.status else analyze(args.analyze), indent=2))
        return
    if not any((args.preflight, args.smoke, args.run, args.all)):
        parser.print_help()
        return
    config = load_json(args.config)
    readiness = preflight(config, online_uart=args.online_uart or args.smoke or args.run or args.all)
    if args.preflight:
        print(json.dumps(readiness, indent=2))
        raise SystemExit(0 if readiness['ready'] else 2)
    if args.allow_destructive != '/dev/nvme0n1' or config['device'] != args.allow_destructive:
        parser.error('Runtime requires --allow-destructive /dev/nvme0n1; no interactive prompt will follow')
    if not readiness['ready']:
        print(json.dumps(readiness, indent=2))
        raise SystemExit('Preflight failed; no experiment was started')
    if args.resume and not args.run:
        parser.error('--resume is valid only with --run and a matching smoke batch')
    SUPPORT.mkdir(parents=True, exist_ok=True)
    with (SUPPORT / 'matrix.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another IPLFS matrix owns the experiment lock')
        # Recheck device idleness after taking the lock.
        device_state(config)
        smoke_batch = args.smoke_batch
        if args.smoke or args.all:
            smoke = Runner(config, smoke=True)
            smoke.run()
            smoke_batch = str(smoke.batch)
        if args.run or args.all:
            Runner(config, smoke_batch=smoke_batch, resume=args.resume).run()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
