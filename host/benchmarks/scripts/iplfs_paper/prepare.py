"""Build private IPLFS artifacts; never install tools, modules, or boot entries."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess

from common import ROOT, SUPPORT, checked, default_config, load_json, now, sha, stamp, write_json


def source_copy(source, destination, kernel=False):
    """Copy source inputs while omitting stale generated products from the old artifact."""
    source = Path(source)

    def omitted(directory, names):
        """Exclude generated files without deleting or modifying the original tree."""
        rel = Path(directory).relative_to(source)
        skip = set()
        for name in names:
            item = Path(directory) / name
            path = rel / name
            if name in {'.git', '.deps', '.libs', 'autom4te.cache', 'TODELETE', '__pycache__'}:
                skip.add(name)
            elif name.endswith(('.o', '.lo', '.la', '.a', '.ko', '.mod', '.mod.c', '.cmd', '.d', '~')):
                skip.add(name)
            elif kernel and (str(path).startswith(('include/config', 'include/generated',
                                                   'arch/x86/include/generated')) or
                             name in {'.config', '.config.old', 'Module.symvers', 'modules.order',
                                      'modules.builtin', 'modules.builtin.modinfo', 'vmlinux',
                                      'System.map', 'bzImage'}):
                skip.add(name)
            elif not kernel and name in {'Makefile', 'config.status', 'config.log', 'config.h',
                                          'stamp-h1', 'libtool'}:
                skip.add(name)
            elif not item.is_symlink() and item.is_file():
                with item.open('rb') as stream:
                    if stream.read(4) == b'\x7fELF':
                        skip.add(name)
        return skip

    shutil.copytree(source, destination, symlinks=True, ignore=omitted)


def build_command(args, directory, log, env=None):
    """Append build output and preserve failed builds for inspection."""
    with log.open('a') as stream:
        stream.write(f'\n{now()} {args!r}\n')
        stream.flush()
        result = subprocess.run([str(x) for x in args], cwd=directory, env=env,
                                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream)
    if result.returncode:
        raise RuntimeError(f'Build failed ({result.returncode}); see {log}')


def build_tools(jobs):
    """Build dedicated mkfs/fsck and file_writer, and freeze other installed executables."""
    destination = SUPPORT / 'builds' / ('tools-' + stamp())
    destination.mkdir(parents=True)
    source = destination / 'f2fs-tools'
    source_copy(ROOT / 'host/src/f2fs-tools-iplfs', source)
    log = destination / 'build.log'
    build_command(['bash', 'configure', '--disable-shared', '--enable-static',
                   '--prefix=' + str(destination / 'unused-install-prefix')], source, log)
    build_command(['make', f'-j{jobs}'], source, log)
    binary = destination / 'bin'
    binary.mkdir()
    sources = {'mkfs': source / 'mkfs/mkfs.f2fs', 'fsck': source / 'fsck/fsck.f2fs',
               'nvme': Path('/home/xin/artifact-csgc/host/src/nvme-cli/nvme')}
    for name in ('fio', 'filebench', 'cgexec', 'cgcreate', 'cgset', 'python2', 'java'):
        executable = shutil.which(name)
        if not executable:
            raise RuntimeError(f'Required executable missing: {name}')
        sources[name] = Path(executable).resolve()
    manifest = {'built_at': now(), 'artifact_commit': checked(['git', 'rev-parse', 'HEAD'], cwd=ROOT),
                'source_patch': checked(['git', 'diff', '--', 'host/src/f2fs-tools-iplfs'], cwd=ROOT),
                'read_only_fsck': True, 'binaries': {}}
    for name, path in sources.items():
        # Java depends on its installation layout; pin it in place instead of relocating it.
        target = path if name == 'java' else binary / (path.name if name in ('mkfs', 'fsck') else name)
        if target != path:
            shutil.copy2(path, target)
        target.chmod(target.stat().st_mode | 0o111) if target != path else None
        manifest['binaries'][name] = {'path': str(target), 'sha256': sha(target), 'source': str(path)}
    writer = binary / 'file_writer'
    build_command(['gcc', '-o', writer, ROOT / 'host/benchmarks/file_writer/file_writer.c',
                   '-lpthread'], destination, log)
    manifest['binaries']['file_writer'] = {'path': str(writer), 'sha256': sha(writer)}
    dsm = binary / 'iplfs_dsm_page'
    dsm_source = Path(__file__).with_name('iplfs_dsm_page.c')
    build_command(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', dsm_source, '-o', dsm], destination, log)
    manifest['binaries']['dsm_page'] = {'path': str(dsm), 'sha256': sha(dsm),
                                       'source_sha256': sha(dsm_source)}
    manifest['build_log'] = str(log)
    write_json(destination / 'manifest.json', manifest)
    write_json(SUPPORT / 'tools.json', manifest)
    print(f'Private tools: {destination}', flush=True)


def build_kernel(jobs):
    """Build a separately named kernel and modules without touching the installed kernel."""
    destination = SUPPORT / 'builds' / ('kernel-' + stamp())
    destination.mkdir(parents=True)
    source = destination / 'source'
    source_copy(ROOT / 'host/src/linux-iplfs', source, kernel=True)
    output = destination / 'output'
    output.mkdir()
    shutil.copy2(ROOT / 'host/src/linux-iplfs/.config', output / '.config')
    log = destination / 'build.log'
    env = dict(os.environ, KBUILD_BUILD_USER='xin', KBUILD_BUILD_HOST='iplfs-paper',
               KBUILD_BUILD_TIMESTAMP=now())
    args = ['make', 'O=' + str(output), 'LOCALVERSION=-iplfs-paper-20261016']
    build_command(args + ['olddefconfig'], source, log, env)
    build_command(args + [f'-j{jobs}', 'bzImage', 'modules'], source, log, env)
    artifacts = {name: str(output / path) for name, path in {
        'kernel': 'arch/x86/boot/bzImage', 'module': 'fs/f2fs/f2fs.ko',
        'config': '.config', 'system_map': 'System.map', 'symbols': 'Module.symvers'}.items()}
    manifest = {'built_at': now(), 'release': '5.11.0-iplfs-paper-20261016',
                'artifact_commit': checked(['git', 'rev-parse', 'HEAD'], cwd=ROOT),
                'source_tree': checked(['git', 'rev-parse', 'HEAD:host/src/linux-iplfs'], cwd=ROOT),
                'source': str(source), 'output': str(output), 'build_log': str(log),
                'compiler': checked(['gcc', '--version']).splitlines()[0],
                'artifacts': {k: {'path': v, 'sha256': sha(v)} for k, v in artifacts.items()},
                'installed': False}
    write_json(destination / 'manifest.json', manifest)
    write_json(SUPPORT / 'kernel-build.json', manifest)
    print(f'Kernel built, NOT installed: {destination}', flush=True)


def main():
    """Expose explicit build modes, with no runtime experiment operations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['tools', 'kernel', 'audit-installed'])
    parser.add_argument('--jobs', type=int, default=16)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error('jobs must be 1..32')
    if args.target == 'audit-installed':
        audit_installed()
    else:
        (build_tools if args.target == 'tools' else build_kernel)(args.jobs)


def audit_installed():
    """Pin the existing kernel only when its config and module source checksum match the fresh build."""
    built = load_json(SUPPORT / 'kernel-build.json')
    config_path = SUPPORT / 'config.json'
    config = load_json(config_path) if config_path.exists() else default_config()
    original = ROOT / 'host/src/linux-iplfs/.config'
    installed = Path('/boot/config-5.11.0-iplfs')
    if sha(original) != sha(installed):
        raise RuntimeError('Installed IPLFS kernel configuration differs from the source artifact')
    module = '/lib/modules/5.11.0-iplfs/kernel/fs/f2fs/f2fs.ko'
    srcversion = checked(['modinfo', '-F', 'srcversion', module])
    if srcversion != checked(['modinfo', '-F', 'srcversion', built['artifacts']['module']['path']]):
        raise RuntimeError('Installed F2FS does not match the independently built source checksum')
    config.update(kernel_release='5.11.0-iplfs', kernel_image='/boot/vmlinuz-5.11.0-iplfs',
                  module=module, module_srcversion=srcversion,
                  kernel_sha256=sha('/boot/vmlinuz-5.11.0-iplfs'), module_sha256=sha(module))
    write_json(config_path, config)
    write_json(SUPPORT / 'installed-kernel-audit.json', {
        'audited_at': now(), 'installed_config_sha256': sha(installed),
        'source_config_sha256': sha(original), 'module_srcversion': srcversion,
        'installed_kernel_sha256': config['kernel_sha256'], 'installed_module_sha256': config['module_sha256'],
        'fresh_build': str(SUPPORT / 'kernel-build.json'), 'system_changed': False,
        'boundary': 'Config and F2FS source checksum match; old whole-kernel ELF build provenance is unavailable.'})
    print('Existing IPLFS kernel pinned in local configuration; no system file changed.')


if __name__ == '__main__':
    main()
