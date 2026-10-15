#!/usr/bin/env python3
"""Archive-first OpenSSD source synchronization, for manual execution on server31."""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import uuid

COMMIT = '463e8b0b13ad345ed99c2176b1f81ad34d3c986a'
PROJECTS = {'ftl': ['ftl', 'shared'],
            'cs_leader': ['cs', 'shared', 'shared_cs'],
            'cs_worker1': ['cs_worker1', 'shared', 'shared_cs'],
            'cs_worker2': ['cs_worker2', 'shared', 'io_worker', 'emu']}


def digest(path):
    """Hash synchronization inputs and recorded ELF files incrementally."""
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


def git(repo, *args):
    """Read repository identity without changing branches, fetching, or resetting."""
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def file_manifest(root):
    """Reject symlinks and record every source file before installing an overlay."""
    values = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink in source tree: {path}')
        if path.is_file():
            values[str(path.relative_to(root))] = digest(path)
    return values


def plan(repo, workspace):
    """Validate all source and destination directories before moving any file."""
    repo, workspace = Path(repo).resolve(), Path(workspace).resolve()
    if git(repo, 'rev-parse', 'HEAD') != COMMIT:
        raise ValueError(f'Expected firmware commit {COMMIT}')
    if git(repo, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Tracked firmware source changes must be preserved and resolved first')
    result = {}
    for project, layers in PROJECTS.items():
        destination = workspace / project / 'src'
        if not destination.is_dir() or destination.is_symlink() or destination.resolve() != destination:
            raise ValueError(f'Missing or redirected destination: {destination}')
        # Unknown links in the old workspace must not redirect a later copy outside the workspace.
        old_files = file_manifest(destination)
        inputs = {}
        for layer in layers:
            source = repo / 'src' / layer
            if not source.is_dir() or source.is_symlink():
                raise ValueError(f'Missing source directory: {source}')
            input_files = file_manifest(source)
            tracked = {str(Path(p).relative_to(Path('src') / layer))
                       for p in git(repo, 'ls-files', '--', 'src/' + layer).splitlines()}
            if set(input_files) != tracked:
                raise ValueError(f'Unexpected untracked or missing inputs in {source}; archive them manually first')
            for relative, value in input_files.items():
                inputs[relative] = {'source': str(source / relative), 'sha256': value}
        if not inputs:
            raise ValueError(f'No sources for {project}')
        result[project] = {'destination': str(destination), 'old_files': old_files, 'inputs': inputs}
    return result


def synchronize(repo, workspace, archive_root, manifest):
    """Stage complete trees, then archive old trees and install without deleting anything."""
    identifier = dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    archive = Path(archive_root).resolve() / identifier
    workspace = Path(workspace).resolve()
    if archive == workspace or workspace in archive.parents:
        raise ValueError('Archive root must be outside the Vitis workspace')
    archive.mkdir(parents=True, exist_ok=False)
    record = {'schema': 1, 'firmware_commit': COMMIT, 'recorded_at': dt.datetime.now().astimezone().isoformat(),
              'repo': str(Path(repo).resolve()), 'workspace': str(workspace),
              'archive': str(archive), 'projects': manifest, 'state': 'staging'}
    (archive / 'plan.json').write_text(json.dumps(record, indent=2) + '\n')
    for project, entry in manifest.items():
        stage = workspace / project / ('src.pending.' + identifier)
        stage.mkdir(exist_ok=False)
        old = Path(entry['destination'])
        # Preserve non-code project assets; archive obsolete C/H and managed subdirectories.
        for child in old.iterdir():
            if child.suffix in ('.c', '.h') or child.name in ('nvme', 'inverval-mapping'):
                continue
            target = stage / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        for relative, value in entry['inputs'].items():
            source, target = Path(value['source']), stage / relative
            if digest(source) != value['sha256']:
                raise ValueError(f'Source changed during synchronization: {source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                displaced = archive / 'staged-assets' / project / relative
                displaced.parent.mkdir(parents=True, exist_ok=True)
                target.rename(displaced)
            shutil.copy2(source, target)
            os.utime(target, None)
        entry['stage'] = str(stage)
    for project, entry in manifest.items():
        old = Path(entry['destination'])
        if file_manifest(old) != entry['old_files']:
            raise ValueError(f'Workspace changed during preparation: {old}')
    installed = []
    try:
        for project, entry in manifest.items():
            old = Path(entry['destination'])
            backup = archive / project / 'src'
            backup.parent.mkdir(parents=True)
            shutil.move(str(old), str(backup))
            Path(entry['stage']).rename(old)
            installed.append(project)
    except Exception as exc:
        # Do not retry or overwrite an ambiguous installation; the journal identifies every move.
        (archive / 'interrupted.json').write_text(json.dumps({'installed': installed, 'error': str(exc)}, indent=2))
        raise
    record.update(state='synchronized-not-built', installed=installed)
    target = archive / 'sync-receipt.json'
    target.write_text(json.dumps(record, indent=2) + '\n')
    return target


def startup_receipt(sync_receipt, elf, uart_log):
    """Record an operator's startup attestation without claiming device hash readback."""
    receipt = json.loads(Path(sync_receipt).read_text())
    workspace = Path(receipt['workspace'])
    if receipt.get('state') != 'synchronized-not-built' or receipt['firmware_commit'] != COMMIT:
        raise ValueError('Invalid synchronization receipt')
    supplied = dict(pair.split('=', 1) for pair in elf)
    if set(supplied) != set(PROJECTS):
        raise ValueError('Supply exactly four --elf PROJECT=PATH arguments')
    files = {}
    for project, path in supplied.items():
        path = Path(path).resolve()
        if not path.is_file() or path.stat().st_mtime_ns < Path(sync_receipt).stat().st_mtime_ns:
            raise ValueError(f'ELF missing or older than synchronization: {path}')
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise ValueError(f'Not an ELF executable: {path}')
        for relative, value in receipt['projects'][project]['inputs'].items():
            if digest(workspace / project / 'src' / relative) != value['sha256']:
                raise ValueError('Workspace inputs changed after synchronization')
        files[project] = {'path': str(path), 'sha256': digest(path), 'size': path.stat().st_size}
    uart = Path(uart_log).resolve()
    if not uart.is_file():
        raise ValueError('An existing UART capture file is required')
    receipt.update(state='operator-confirmed-started', elfs=files, uart_log=str(uart),
                   started_at=dt.datetime.now().astimezone().isoformat(),
                   runtime_identity='Operator attestation plus ELF/input hashes; no device hash readback')
    target = Path(sync_receipt).parent / ('startup-receipt-' + uuid.uuid4().hex[:8] + '.json')
    target.write_text(json.dumps(receipt, indent=2) + '\n')
    return target


def main():
    """Default to a read-only plan; mutations require an explicit manual mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='/home/xin/work-xie/openssd-csgc-withjin/openssd-csgc')
    parser.add_argument('--workspace', default='/home/xin/vitis_workspaces/xie_csgc_withjin')
    parser.add_argument('--archive-root', default='/home/xin/openssd-source-archives')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-device-offline', action='store_true')
    parser.add_argument('--record-startup', metavar='SYNC_RECEIPT')
    parser.add_argument('--confirm-four-elfs-started', action='store_true')
    parser.add_argument('--elf', action='append', default=[])
    parser.add_argument('--uart-log')
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] != 'ddst-31':
        parser.error('This manual deployment command may run only on ddst-31')
    if args.record_startup:
        if not args.confirm_four_elfs_started or not args.uart_log or args.apply:
            parser.error('Startup recording requires explicit confirmation, UART log, and no --apply')
        print(startup_receipt(args.record_startup, args.elf, args.uart_log))
        return
    manifest = plan(args.repo, args.workspace)
    if not args.apply:
        print(json.dumps({project: {'destination': entry['destination'], 'files': len(entry['inputs'])}
                          for project, entry in manifest.items()}, indent=2))
        print('Read-only plan. No file or runtime state was changed.')
        return
    if not args.confirm_device_offline:
        parser.error('--apply requires --confirm-device-offline after unmount and shutdown of SSD I/O')
    print(synchronize(args.repo, args.workspace, args.archive_root, manifest))
    print('Sources synchronized. Rebuild and start all four applications manually in Vitis.')


if __name__ == '__main__':
    main()
