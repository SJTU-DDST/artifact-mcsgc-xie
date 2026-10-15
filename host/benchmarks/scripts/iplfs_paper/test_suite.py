"""Synthetic, non-device regression tests for the experiment harness and safe synchronization."""

import collections
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import analysis
import fsops
from common import MIB, ROOT, SCRIPTS, SUPPORT, default_config, load_json, process_identity, schedule, sha, stamp, write_json
import matrix
import preflight

TEST_ROOT = SUPPORT / 'test-runs' / stamp()
TEST_ROOT.mkdir(parents=True)
spec = importlib.util.spec_from_file_location('safe_sync', SCRIPTS / 'sync_iplfs_openssd_safe.py')
safe_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safe_sync)


class HarnessTests(unittest.TestCase):
    """Test experiment semantics, failure detection and preservation using synthetic data only."""

    def folder(self, name):
        """Allocate persistent test fixtures; never delete user files or temporary test evidence."""
        folder = TEST_ROOT / name
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def test_matrix_contract(self):
        """Require all 63 formal cases and the declared workload parameters."""
        rows = schedule()
        self.assertEqual(len(rows), 63)
        counts = collections.Counter(row['family'] for row in rows)
        self.assertEqual(counts, dict(utilization=15, skewness=15, headline=18, timeline=3, gcheavy=12))
        self.assertEqual(len({row['id'] for row in rows}), 63)
        self.assertTrue(all(row['s'] == 1 for row in rows))
        self.assertTrue(all(row['kind'] == 'filebench' for row in rows[-9:]))
        self.assertEqual([row['dist'] for row in rows if row['family'] == 'skewness' and row['repeat'] == 1],
                         ['random', 'zipf:0.3', 'zipf:0.7', 'zipf:0.9', 'zipf:1.1'])
        self.assertEqual(sum(row['point'] == 'G01' for row in rows), 6)
        self.assertEqual(sum(row['point'] == 'G02' for row in rows), 6)
        self.assertEqual(len(schedule(True)), 8)

    def test_fio_errors_and_length(self):
        """Reject incomplete fixed work and errors even when throughput is present."""
        case = dict(timed=False, size_per_job=MIB, jobs=4, runtime_s=300)
        data = {'jobs': [{'error': 0, 'write': {'io_bytes': 4 * MIB, 'runtime': 2000, 'iops': 512}}]}
        self.assertEqual(analysis.fio_metrics(json.dumps(data), case)['throughput'], 2)
        data['jobs'][0]['error'] = 5
        with self.assertRaises(ValueError):
            analysis.fio_metrics(json.dumps(data), case)
        data['jobs'][0]['error'] = 0
        data['jobs'][0]['write']['io_bytes'] = MIB
        with self.assertRaises(ValueError):
            analysis.fio_metrics(json.dumps(data), case)
        case['timed'] = True
        with self.assertRaises(ValueError):
            analysis.fio_metrics(json.dumps(data), case)

    def test_ycsb_rmw_counts(self):
        """Count workload F's logical operations once, while checking its nested update statuses."""
        text = ('[OVERALL], Throughput(ops/sec), 100\n[OVERALL], RunTime(ms), 1000\n'
                '[READ], Operations, 100\n[READ], Return=OK, 100\n'
                '[UPDATE], Operations, 80\n[UPDATE], Return=OK, 80\n'
                '[READ-MODIFY-WRITE], Operations, 80\n')
        self.assertEqual(analysis.ycsb_metrics(text, dict(workload='workloadf', operations=100))['throughput'], 100)
        with self.assertRaises(ValueError):
            analysis.ycsb_metrics(text + '[UPDATE], Return=ERROR, 1\n', dict(workload='workloadf', operations=100))

    def test_filebench_weighted_timeline(self):
        """Use all intervals and reject the historical accidental 60-second run."""
        text = ('11.0: IO Summary: 100 ops 20.0 ops/s\n'
                '16.0: IO Summary: 200 ops 40.0 ops/s\n16: Run took 10 seconds...\n')
        metric = analysis.filebench_metrics(text, dict(runtime_s=10, family='timeline'))
        self.assertEqual(metric['throughput'], 30)
        self.assertEqual(metric['intervals'][-1]['end_s'], 10)
        with self.assertRaises(ValueError):
            analysis.filebench_metrics(text, dict(runtime_s=300, family='timeline'))

    def test_waf_boundaries(self):
        """Decode NUL-padded raw counters and reject missing/zero denominators."""
        text = ('<OpenSSD>: host_normal_write_bytes: 100\n<OpenSSD>: nand_write_bytes: 250\n'
                'host_gc_write_bytes: 0\nnand_gc_write_bytes: 150\n\0\0')
        self.assertEqual(analysis.waf_metrics(text)['physical_waf'], 2.5)
        with self.assertRaises(ValueError):
            analysis.waf_metrics(text.replace('write_bytes: 100', 'write_bytes: 0'))
        with self.assertRaises(ValueError):
            analysis.waf_metrics('physical WAF: 2500')

    def test_uart_readback(self):
        """Use the last complete UART block and refuse a wrong mapping mode."""
        text = ('============SSD CONFIG============\nl2p_mapping_type: 3\n'
                'nand_latency_emu_enabled: 0\nmain_area_lba: 42\ndsm_enabled: 1\n'
                '==================================\n')
        self.assertEqual(preflight.parse_uart_config(text)['l2p_mapping_type'], 3)
        with self.assertRaises(ValueError):
            preflight.parse_uart_config(text.replace('type: 3', 'type: 2'))

    def test_integrity_helper_on_private_fixture(self):
        """Check helper byte ranges on a new regular file, without an IPLFS mount or experiment."""
        folder = self.folder('integrity-helper')
        with patch('sys.stdout', new_callable=io.StringIO):
            fsops.verify(folder)
        self.assertEqual((folder / 'iplfs-smoke-integrity.bin').stat().st_size, MIB)

    def test_generated_workload_semantics(self):
        """Check commands and workload files without invoking fio or Filebench."""
        runner = matrix.Runner.__new__(matrix.Runner)
        runner.current = self.folder('generated-workloads')
        runner.config = default_config()
        runner.tools = {name: {'path': '/synthetic/' + name} for name in ('fio', 'filebench')}
        dummy = runner.current / 'dummy.log'
        dummy.write_text('synthetic')
        small = next(row for row in schedule() if row['point'] == 'G02')
        with patch.object(runner, 'limited', return_value=dummy) as launch, \
                patch.object(matrix, 'fio_metrics', return_value={'throughput': 1}):
            runner.fio(small, 26336 * 2 * MIB, warmup=True)
            arguments = launch.call_args.args[1]
            self.assertIn('--io_size=1073741824', arguments)
            self.assertIn('--time_based=0', arguments)
            self.assertIn('--end_fsync=1', arguments)
            self.assertIn('randseed=20260807', (runner.current / 'warmup.fio').read_text())
            runner.fio(small, 26336 * 2 * MIB)
            self.assertIn('--runtime=300', launch.call_args.args[1])
            self.assertNotIn('--end_fsync=1', launch.call_args.args[1])
        timeline = next(row for row in schedule() if row['point'] == 'T01')
        with patch.object(runner, 'limited', return_value=dummy), \
                patch.object(matrix, 'filebench_metrics', return_value={'throughput': 1}):
            runner.filebench(timeline)
            self.assertIn('psrun -5 300', (runner.current / 'effective-workload.f').read_text())
            self.assertNotIn('psrun -5 $runtime', (runner.current / 'effective-workload.f').read_text())

    def test_status_between_cases(self):
        """A live parent between cases is not a completed matrix; stale identities cannot pass."""
        folder = self.folder('status')
        rows = schedule()[:2]
        write_json(folder / 'schedule.json', rows)
        state = dict(status='running', runner=process_identity(), phase='between-cases')
        write_json(folder / 'state.json', state)
        write_json(folder / 'runs' / rows[0]['id'] / 'validation.json', {'status': 'passed'})
        result = matrix.status(folder)
        self.assertEqual((result['status'], result['passed'], result['expected']), ('running', 1, 2))
        write_json(folder / 'completed.json', {'status': 'completed', 'passed_ids': [row['id'] for row in rows]})
        self.assertNotEqual(matrix.status(folder)['status'], 'completed')
        state['runner']['start_ticks'] += 1
        write_json(folder / 'state.json', state)
        self.assertEqual(matrix.status(folder)['status'], 'interrupted')

    def test_archive_preserves_state(self):
        """Keep the old contents when a state file is replaced."""
        folder = self.folder('archive')
        path = folder / 'state.json'
        write_json(path, {'version': 1})
        write_json(path, {'version': 2})
        older = list((folder / 'TODELETE').iterdir())
        self.assertEqual(len(older), 1)
        self.assertEqual(load_json(older[0]), {'version': 1})
        self.assertEqual(load_json(path), {'version': 2})

    def test_dry_run_no_subprocess(self):
        """A dry run must never reach any command or experiment operation."""
        with patch.object(sys, 'argv', ['matrix.py', '--dry-run']), patch('sys.stdout', new_callable=io.StringIO) as output:
            with patch('subprocess.run', side_effect=AssertionError('No process allowed')), \
                    patch('subprocess.Popen', side_effect=AssertionError('No process allowed')):
                matrix.main()
        self.assertEqual(len(json.loads(output.getvalue())['formal_cases']), 63)

    def test_analysis_keeps_low_samples_and_failures(self):
        """Compute sample dispersion without silently dropping failed or slow runs."""
        folder = self.folder('analysis')
        rows = [dict(schedule()[0], id=f'case-{i}', repeat=i) for i in range(3)]
        write_json(folder / 'schedule.json', rows)
        for row, value in zip(rows[:2], [1.0, 3.0]):
            write_json(folder / 'runs' / row['id'] / 'validation.json',
                       {'status': 'passed', 'metrics': dict(throughput=value, unit='MiB/s', runtime_s=300, physical_waf=1.5)})
        write_json(folder / 'runs' / rows[2]['id'] / 'validation.json', {'status': 'failed', 'error': 'synthetic'})
        result = analysis.analyze(folder)
        self.assertEqual(result['passed'], 2)
        self.assertFalse(result['complete_data'])
        self.assertIn('2.0', (folder / 'analysis/aggregate.csv').read_text())

    def test_runtime_failure_stops_next_case(self):
        """Inject a failed first case and prove the runner never initializes the next one."""
        runner = matrix.Runner.__new__(matrix.Runner)
        runner.batch = self.folder('failure-stop')
        runner.cases = schedule()[:2]
        runner.config = default_config()
        runner.config['module_srcversion'] = 'synthetic'
        runner.tools = {name: {'path': '/synthetic/' + name} for name in ('cgcreate', 'cgset')}
        runner.state = {'passed_ids': []}
        runner.previous = None
        visited = []

        def fail_case(case):
            """Simulate a kernel failure before a second format can occur."""
            visited.append(case['id'])
            raise RuntimeError('synthetic failure')

        with patch.object(runner, 'phase'), patch.object(runner, 'update'), \
                patch.object(runner, 'one_case', side_effect=fail_case), \
                patch.object(matrix, 'preflight', return_value={'ready': True}), \
                patch.object(matrix, 'read', return_value='synthetic'), patch.object(matrix, 'analyze'):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                runner.run()
        self.assertEqual(visited, [runner.cases[0]['id']])

    def test_case_order_and_clean_finish(self):
        """Simulate a complete case to verify measurement, unmount, statistics and fsck ordering."""
        runner = matrix.Runner.__new__(matrix.Runner)
        runner.batch = self.folder('complete-case')
        runner.config = default_config()
        runner.fingerprint = 'synthetic'
        runner.state = {'passed_ids': []}
        runner.tools = {'mkfs': {'path': '/synthetic/mkfs'}, 'fsck': {'path': '/synthetic/fsck'},
                        'nvme': {'path': '/synthetic/nvme'}}
        calls = []

        def phase(name, command, *args, **kwargs):
            """Return synthetic phase logs without launching a process or touching a device."""
            calls.append((name, command))
            path = runner.current / (name + '.log')
            text = ''
            if name == 'readonly-fsck':
                text = 'Done.\n'
            if name == 'device-stat-after':
                text = 'host_normal_write_bytes: 100\nnand_write_bytes: 150\nhost_gc_write_bytes: 0\nnand_gc_write_bytes: 50\n'
            path.write_text(text)
            return path

        with patch.object(matrix, 'config_fingerprint', return_value='synthetic'), \
                patch.object(matrix, 'device_state'), patch.object(runner, 'admin_config'), \
                patch.object(runner, 'prefill', return_value=100), patch.object(runner, 'reset_stats'), \
                patch.object(runner, 'fio', return_value={'throughput': 1, 'unit': 'MiB/s'}), \
                patch.object(runner, 'phase', side_effect=phase):
            result = runner.one_case(schedule()[0])
        self.assertEqual(result['status'], 'passed')
        self.assertIn('started_at', result)
        names = [name for name, _ in calls]
        self.assertLess(names.index('unmount'), names.index('device-stat-after'))
        self.assertLess(names.index('device-stat-after'), names.index('readonly-fsck'))
        self.assertNotIn('prefill-sync', names)
        self.assertIn('-n', dict(calls)['readonly-fsck'])

    def test_fsck_readonly_invalid_image(self):
        """Exercise the private checker on a regular invalid fixture, never a block device."""
        folder = self.folder('fsck')
        image = folder / 'invalid-image.bin'
        image.write_bytes(b'\0' * (2 * MIB))
        before = sha(image)
        fsck = load_json(SUPPORT / 'tools.json')['binaries']['fsck']['path']
        for index, flags in enumerate([['-n'], ['-n', '-f'], ['-n', '-a'], ['-n', '-p', '1']]):
            result = subprocess.run([fsck, *flags, str(image)], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            (folder / f'case-{index}.log').write_bytes(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'Do you want', result.stdout)
            self.assertEqual(sha(image), before)
        trace = folder / 'readonly-syscalls.log'
        subprocess.run(['strace', '-e', 'trace=openat,fsync', '-o', str(trace), fsck, '-n', str(image)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        accesses = [line for line in trace.read_text().splitlines() if str(image) in line]
        self.assertTrue(accesses)
        self.assertTrue(all('O_RDONLY' in line and 'O_RDWR' not in line for line in accesses))

    def test_dsm_page_dry_run(self):
        """Validate wire length/alignment without opening any namespace or issuing an ioctl."""
        helper = load_json(SUPPORT / 'tools.json')['binaries']['dsm_page']['path']
        result = subprocess.run([helper, '--dry-run', '16000000', '1'],
                                stdout=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual(data, dict(data_len=4096, ranges=1, address_alignment=0, start_lba=16000000, blocks=1))
        result = subprocess.run([helper, '--execute', '/dev/INVALID', '16000000', '1'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 2)

    def test_safe_sync_archives_every_old_file(self):
        """Test archive-first synchronization entirely inside persistent local fixtures."""
        folder = self.folder('safe-sync')
        repo, workspace = folder / 'repo', folder / 'workspace'
        manifest = {}
        for project in safe_sync.PROJECTS:
            old = workspace / project / 'src'
            old.mkdir(parents=True)
            (old / 'obsolete.c').write_text('old code\n')
            (old / 'lscript.ld').write_text('linker asset\n')
            source = repo / project / 'replacement.c'
            source.parent.mkdir(parents=True)
            source.write_text('new code\n')
            manifest[project] = {'destination': str(old), 'old_files': safe_sync.file_manifest(old),
                                 'inputs': {'replacement.c': {'source': str(source), 'sha256': sha(source)}}}
        receipt = safe_sync.synchronize(repo, workspace, folder / 'archives', manifest)
        data = load_json(receipt)
        for project in safe_sync.PROJECTS:
            self.assertEqual((Path(data['archive']) / project / 'src/obsolete.c').read_text(), 'old code\n')
            self.assertFalse((workspace / project / 'src/obsolete.c').exists())
            self.assertEqual((workspace / project / 'src/lscript.ld').read_text(), 'linker asset\n')
            self.assertEqual((workspace / project / 'src/replacement.c').read_text(), 'new code\n')

    def test_safe_sync_rejects_redirected_sources(self):
        """A source symlink must not escape the synchronization input tree."""
        folder = self.folder('sync-symlink')
        (folder / 'escape').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            safe_sync.file_manifest(folder)


if __name__ == '__main__':
    with (TEST_ROOT / 'unittest.log').open('x') as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HarnessTests))
    print((TEST_ROOT / 'unittest.log').read_text())
    print(f'All fixtures retained at {TEST_ROOT}')
    raise SystemExit(0 if result.wasSuccessful() else 1)
