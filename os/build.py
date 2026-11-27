#!/usr/bin/env python3
"""Build source-only Q2 emulator images. No device/deployment operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from fork_checkout import checkout

ROOT = Path(__file__).resolve().parent.parent
LOCK = json.loads((ROOT / 'os/sources.json').read_text())
WORK = ROOT / 'build/os'
BR = WORK / ('buildroot-' + LOCK['buildroot']['version'])

def run(args, **kwargs):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def kernel_patch_hashes():
    return {path.name: sha(path) for path in sorted((ROOT / 'os/patches/kernel').glob('*.patch'))}

def package_patch_identity():
    directory = ROOT / 'os/patches/packages'
    return {str(path.relative_to(directory)): sha(path)
            for path in sorted(directory.glob('*/*.patch'))}

def prepare_package_patches(output, make):
    """Invalidate cached packages before a changed patch selection is built.

    Buildroot's .stamp_patched does not depend on patch contents. The receipt
    records the selection only after successful make; it is not an ELF proof.
    """
    expected = package_patch_identity()
    receipt = output / '.q2-package-patch-selection.json'
    previous = json.loads(receipt.read_text()) if receipt.is_file() else {}
    config = output / '.config'
    if expected and config.exists() and not any(line in config.read_text().splitlines() for line in (
            'BR2_GLOBAL_PATCH_DIR="$(BR2_EXTERNAL_Q2_PATH)/patches/packages"',
            'BR2_GLOBAL_PATCH_DIR="/work/os/patches/packages"')):
        raise RuntimeError('configure this output before building changed package patches')
    if previous != expected:
        packages = sorted({name.split('/')[0] for name in set(previous) | set(expected)})
        if any(not name.replace('-', '').isalnum() for name in packages):
            raise RuntimeError('invalid package patch selection')
        # Keep the old selection until every clean succeeds. In particular,
        # removing the last patch must still retry a failed clean next time.
        # Interrupted patches may leave source without .stamp_patched.
        for package in packages:
            if any((output / 'build').glob(package + '-*')):
                container(make + [package + '-dirclean'])
        receipt.unlink(missing_ok=True)
    return receipt, expected

def record_package_patches(receipt, expected):
    temp = receipt.with_suffix('.tmp')
    temp.write_text(json.dumps(expected, sort_keys=True, indent=2) + '\n')
    temp.replace(receipt)

def fetch(item):
    dest = WORK / 'downloads' / item['url'].rsplit('/', 1)[1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        temp = dest.with_suffix(dest.suffix + '.download')
        run(['curl', '--fail', '--location', '--retry', '3', item['url'], '-o', temp])
        if sha(temp) != item['sha256']:
            raise RuntimeError('download hash mismatch: ' + dest.name)
        temp.rename(dest)
    if sha(dest) != item['sha256']:
        raise RuntimeError('cached source hash mismatch: ' + dest.name)
    return dest

def container(args, network='none', extra=()):
    run(['podman', 'run', '--rm', '--network', network,
         '--security-opt', 'label=disable', '-v', f'{ROOT}:/work', '-w', '/work',
         '-e', f'SOURCE_DATE_EPOCH={LOCK["source_date_epoch"]}',
         '-e', 'FORCE_UNSAFE_CONFIGURE=1', *extra, 'q2-os-build', *args])

def prepare():
    checkout(BR, LOCK['buildroot']['fork_commit'], 'Q2_BUILDROOT_REPO', LOCK['buildroot']['fork_url'])

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'container', 'configure', 'build', 'sdk'])
    p.add_argument('--output', default='out', help='isolated build directory name')
    p.add_argument('targets', nargs='*')
    a = p.parse_args()
    if not a.output.replace('-', '').replace('_', '').isalnum():
        p.error('output must be a simple name')
    WORK.mkdir(parents=True, exist_ok=True)
    if a.action == 'container':
        run(['podman', 'build', '-t', 'q2-os-build', '-f', 'os/Containerfile', 'os'])
        return
    prepare()
    if a.action == 'prepare':
        return
    make = ['make', '-C', '/work/' + str(BR.relative_to(ROOT)),
            'O=/work/build/os/' + a.output, 'BR2_EXTERNAL=/work/os',
            'BR2_DL_DIR=/work/build/os/downloads/packages', 'BR2_JLEVEL=2']
    if a.action == 'configure':
        container(make + ['q2_defconfig'])
    else:
        if not (WORK / a.output / '.config').exists():
            container(make + ['q2_defconfig'])
        targets = a.targets or (['sdk'] if a.action == 'sdk' else [])
        receipt, selection = prepare_package_patches(WORK / a.output, make)
        container(make + targets, network='host')
        record_package_patches(receipt, selection)

if __name__ == '__main__':
    main()
