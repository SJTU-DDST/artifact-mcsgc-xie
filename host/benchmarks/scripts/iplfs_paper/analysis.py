"""Validate raw benchmark output and publish figure-ready IPLFS data."""

import csv
import json
from pathlib import Path
import re
import statistics

from common import MIB, archive, load_json, now, read, write_json


def fio_metrics(text, case):
    """Extract fio's final JSON and reject errors or incomplete fixed-size workloads."""
    decoder = json.JSONDecoder()
    data = None
    for match in re.finditer(r'\{', text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start():])
            if isinstance(candidate, dict) and 'jobs' in candidate:
                data = candidate
                break
        except ValueError:
            pass
    if not data or not data['jobs']:
        raise ValueError('Missing fio JSON jobs')
    jobs = data['jobs']
    if any(int(job.get('error', -1)) != 0 for job in jobs):
        raise ValueError('fio reported a failed job')
    writes = [job['write'] for job in jobs]
    byte_count = sum(w.get('io_bytes', w.get('io_kbytes', 0) * 1024) for w in writes)
    runtime = max(w['runtime'] for w in writes) / 1000
    if byte_count <= 0 or runtime <= 0:
        raise ValueError('Empty fio measurement')
    if case['timed'] and runtime < case['runtime_s'] - 1:
        raise ValueError('fio ended before the requested time')
    if not case['timed'] and byte_count != case['size_per_job'] * case['jobs']:
        raise ValueError('fio byte count differs from the fixed workload')
    metrics = {'throughput': byte_count / runtime / MIB, 'unit': 'MiB/s',
               'runtime_s': runtime, 'application_write_bytes': byte_count,
               'iops': sum(w.get('iops', 0) for w in writes), 'fio_jobs': jobs}
    for field in ('slat_ns', 'clat_ns', 'lat_ns'):
        weighted = [(w.get(field, {}).get('mean'), w.get('total_ios', 0)) for w in writes]
        valid = [(a, b) for a, b in weighted if a is not None and b]
        metrics[field + '_mean'] = sum(a * b for a, b in valid) / sum(b for _, b in valid) if valid else None
    # Group reporting provides a pooled percentile; never average independent job percentiles here.
    metrics['fio_clat_p99_us'] = None
    if len(writes) == 1:
        for field, scale in [('clat_ns', .001), ('clat_us', 1), ('clat_ms', 1000)]:
            percentile = writes[0].get(field, {}).get('percentile', {})
            found = [value for key, value in percentile.items() if float(key) == 99]
            if found:
                metrics['fio_clat_p99_us'] = found[0] * scale
                break
    return metrics


def ycsb_metrics(text, case):
    """Validate final YCSB operation counts without double-counting workload F's RMW."""
    fields = {}
    for line in text.splitlines():
        match = re.match(r'^\[([^]]+)\],\s*([^,]+),\s*([0-9.eE+-]+)\s*$', line)
        if match:
            group, key, value = match.groups()
            fields.setdefault(group, {})[key] = float(value)
    overall = fields.get('OVERALL', {})
    if not overall.get('Throughput(ops/sec)', 0) > 0:
        raise ValueError('Missing YCSB final throughput')
    for group, values in fields.items():
        if any(k.startswith('Return=') and k != 'Return=OK' and v != 0 for k, v in values.items()):
            raise ValueError(f'YCSB error status in {group}')
        if 'Operations' in values and 'Return=OK' in values and values['Operations'] != values['Return=OK']:
            raise ValueError(f'YCSB unsuccessful operations in {group}')
    total = fields.get('READ', {}).get('Operations', 0)
    if case['workload'] == 'workloada':
        total += fields.get('UPDATE', {}).get('Operations', 0)
    if total != case['operations']:
        raise ValueError('Incomplete YCSB operation count')
    metrics = {'throughput': overall['Throughput(ops/sec)'], 'unit': 'ops/s',
               'runtime_s': overall['RunTime(ms)'] / 1000, 'ycsb': fields}
    for group, prefix in [('READ', 'read'), ('UPDATE', 'update'), ('READ-MODIFY-WRITE', 'rmw')]:
        metrics[prefix + '_mean_us'] = fields.get(group, {}).get('AverageLatency(us)')
        metrics[prefix + '_p99_us'] = fields.get(group, {}).get('99thPercentileLatency(us)')
    return metrics


def filebench_metrics(text, case):
    """Calculate interval-weighted throughput, preserving real timestamped samples."""
    if re.search(r'(?i)(fatal|aborting|failed to|out of memory|no space left)', text):
        raise ValueError('Filebench error text')
    durations = re.findall(r'Run took\s+(\d+)\s+seconds', text)
    if not durations or int(durations[-1]) != case['runtime_s']:
        raise ValueError('Filebench did not complete its requested runtime')
    matches = re.findall(r'([0-9.]+):\s+IO Summary:\s+(\d+) ops\s+([0-9.]+) ops/s', text)
    if not matches:
        raise ValueError('Missing Filebench IO Summary')
    samples = [{'log_timestamp_s': float(t), 'ops': int(n), 'ops_s': float(rate),
                'duration_s': int(n) / float(rate)} for t, n, rate in matches if float(rate) > 0]
    if not samples:
        raise ValueError('No Filebench progress')
    periodic = case['family'] == 'timeline'
    if not periodic:
        samples = samples[-1:]
    total_time = sum(s['duration_s'] for s in samples)
    if abs(total_time - case['runtime_s']) > max(2, case['runtime_s'] * .02):
        raise ValueError('Filebench interval coverage does not match requested runtime')
    elapsed = 0
    for sample in samples:
        sample['start_s'] = elapsed
        elapsed += sample['duration_s']
        sample['end_s'] = elapsed
    return {'throughput': sum(s['ops'] for s in samples) / total_time, 'unit': 'ops/s',
            'runtime_s': total_time, 'intervals': samples}


def waf_metrics(text, require_nonzero=True):
    """Read integer device counters; never derive physical WAF from application bytes."""
    clean = text.replace('\x00', '')
    values = {k: int(v) for k, v in re.findall(r'\b([a-z_]+)\s*[:=]\s*<?(\d+)>?', clean)}
    required = ('host_normal_write_bytes', 'nand_write_bytes', 'host_gc_write_bytes', 'nand_gc_write_bytes')
    if any(key not in values for key in required):
        raise ValueError('Device statistics are missing required WAF counters')
    denominator = values['host_normal_write_bytes']
    if require_nonzero and denominator <= 0:
        raise ValueError('Device WAF denominator is zero')
    return {'physical_waf': values['nand_write_bytes'] / denominator if denominator else None,
            'device_counters': values}


def kernel_errors(text):
    """Select concrete kernel failure signatures without matching routine GC words."""
    pattern = re.compile(r'BUG:|Oops:|Kernel panic|general protection fault|KASAN:|'
                         r'kernel BUG|EUCLEAN|SIT.*(?:mismatch|inconsisten)|'
                         r'blocked for more than|I/O error|nvme.*(?:timeout|reset controller)|'
                         r'F2FS-fs.*(?:corrupt|error|inconsisten)|PCIe Bus Error.*severity=Fatal', re.I)
    return [line for line in text.splitlines() if pattern.search(line)]


def csv_file(path, rows):
    """Write a new CSV with deterministic columns and archive any earlier analysis."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    archive(path)
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fields, delimiter='\t' if path.suffix == '.tsv' else ',')
        writer.writeheader()
        writer.writerows(rows)


def analyze(batch):
    """Analyze validated cases only and list failures separately without discarding low results."""
    batch = Path(batch)
    schedule = load_json(batch / 'schedule.json')
    output = batch / 'analysis'
    rows, failures, timeline = [], [], []
    for case in schedule:
        folder = batch / 'runs' / case['id']
        validation = folder / 'validation.json'
        if not validation.exists():
            failures.append({'id': case['id'], 'status': 'missing'})
            continue
        value = load_json(validation)
        if value.get('status') != 'passed':
            failures.append({'id': case['id'], 'status': value.get('status'), 'reason': value.get('error')})
            continue
        metric = value['metrics']
        row = {k: case[k] for k in ('id', 'point', 'family', 'kind', 'repeat', 'ratio', 'dist', 's', 'smoke')}
        row.update({k: metric.get(k) for k in ('throughput', 'unit', 'runtime_s', 'physical_waf',
                                              'slat_ns_mean', 'clat_ns_mean', 'lat_ns_mean', 'fio_clat_p99_us',
                                              'read_mean_us', 'read_p99_us', 'update_mean_us', 'update_p99_us',
                                              'rmw_mean_us', 'rmw_p99_us')})
        row.update(metric.get('device_counters', {}))
        row['fs_gc_migration_us'] = 'N/A'
        row['started_at'] = value.get('started_at')
        row['finished_at'] = value.get('finished_at')
        row['elapsed_s'] = value.get('elapsed_s')
        row['source'] = str(validation)
        rows.append(row)
        for sample in metric.get('intervals', []):
            timeline.append(dict(id=case['id'], repeat=case['repeat'], point=case['point'], **sample))
    aggregate = []
    for point in dict.fromkeys(row['point'] for row in rows):
        samples = [row for row in rows if row['point'] == point]
        item = {k: samples[0][k] for k in ('point', 'family', 'kind', 'ratio', 'dist', 's', 'unit', 'smoke')}
        item['n'] = len(samples)
        for field in ('throughput', 'physical_waf', 'fio_clat_p99_us', 'read_mean_us', 'read_p99_us',
                      'update_mean_us', 'update_p99_us', 'rmw_mean_us', 'rmw_p99_us'):
            values = [r[field] for r in samples if r.get(field) is not None]
            for statistic, function in [('mean', statistics.mean), ('median', statistics.median),
                                        ('min', min), ('max', max)]:
                item[f'{field}_{statistic}'] = function(values) if values else None
            item[f'{field}_stdev'] = statistics.stdev(values) if len(values) > 1 else None
        aggregate.append(item)
    csv_file(output / 'per-run.csv', rows)
    csv_file(batch / 'case-results.tsv', rows)
    csv_file(output / 'aggregate.csv', aggregate)
    csv_file(output / 'failures.csv', failures)
    csv_file(output / 'fileserver-intervals.csv', timeline)
    for family in ('headline', 'utilization', 'skewness', 'timeline', 'gcheavy'):
        csv_file(output / 'figure-inputs' / (family + '.csv'), [x for x in rows if x['family'] == family])
    result = {'analyzed_at': now(), 'expected': len(schedule), 'passed': len(rows),
              'failures_or_missing': failures, 'complete_data': len(rows) == len(schedule)}
    write_json(output / 'summary.json', result)
    template = read(Path(__file__).with_name('report-template-zh.md'))
    report = [template.replace('{{batch}}', str(batch)).replace('{{passed}}', str(len(rows)))
              .replace('{{expected}}', str(len(schedule)))]
    for row in aggregate:
        deviation = row['throughput_stdev']
        waf = row['physical_waf_mean']
        report.append(f"| {row['point']} | {row['n']} | {row['throughput_mean']:.3f} | "
                      f"{format(deviation, '.3f') if deviation is not None else 'N/A'} | {row['unit']} | "
                      f"{format(waf, '.3f') if waf is not None else 'N/A'} |")
    target = output / 'integration-report-zh.md'
    archive(target)
    target.write_text('\n'.join(report) + '\n')
    return result
