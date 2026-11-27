#!/usr/bin/env python3
"""Compose a keyless QEMU candidate from tagged Q2 OS Release assets."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from archives import unpack
from build import ROOT, WORK, run, sha
from fork_checkout import verify
from install import kernel_base
import sdk_platform
import sdk_release

RELEASE_FILES = (sdk_release.ARCHIVE, sdk_release.MANIFEST, sdk_release.REPORT,
                 sdk_release.CONFIG, 'SHA256SUMS', sdk_platform.ARCHIVE,
                 sdk_platform.RECORD, 'buildroot-legal-info.tar.gz', *kernel_base.FILES)


def release_platform(bundle, commit):
    record = sdk_platform.verify(bundle, bundle, commit)
    sums = ''.join(f'{sha(sdk_release.regular(bundle, name))}  {name}\n' for name in RELEASE_FILES)
    if sdk_release.regular(bundle, 'RELEASE-SHA256SUMS').read_text() != sums:
        raise ValueError('release checksum list differs from the verified assets')
    return record, kernel_base.verify_base(bundle, record)


def restore_platform(bundle, platform):
    platform.mkdir(parents=True)
    unpack(bundle / sdk_release.ARCHIVE, platform)
    (platform / sdk_release.ARCHIVE.removesuffix('.tar.gz')).rename(platform / 'host')
    target = platform / 'target'
    target.mkdir()
    unpack(bundle / sdk_platform.ARCHIVE, target)
    if not (target / 'usr/lib/libq2platform.so').is_file():
        raise ValueError('Q2 Buildroot target platform bridge is missing')
    return target


def compose(args):
    commit = subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()
    verify(ROOT, commit)
    assets = args.assets.resolve(strict=True)
    record, kernel = release_platform(assets, commit)
    record['kernel_source_commit'] = kernel['linux']['commit']
    record['kernel_container'] = kernel['kernel_container']
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK, prefix='ci-platform-') as directory:
        platform = Path(directory) / 'platform'
        target = restore_platform(assets, platform)
        run([sys.executable, ROOT / 'os/build.py', 'container'])
        record['q2-os-build'] = subprocess.check_output(
            ['podman', 'image', 'inspect', 'q2-os-build', '--format', '{{.Id}}'], text=True).strip()
        host = platform / 'host'
        run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
             '-v', f'{ROOT}:/work', 'q2-os-build',
             '/work/' + str((host / 'relocate-sdk.sh').relative_to(ROOT))])
        run([sys.executable, ROOT / 'os/install/build.py', '--qemu-fixture',
             '--kernel-base', assets, '--buildroot-target', target,
             '--player-source', args.player_source.resolve(), '--app', args.app.resolve(),
             '--tsrsd', args.tsrsd.resolve(), '--tsrs-source', args.tsrs_source.resolve(),
             '--release-tag', args.release_tag, '--output', args.output.resolve(),
             *(['--qemu-user', args.qemu_user.resolve()] if args.qemu_user else [])])
    release = args.output / 'os-release-inputs.json'
    with release.open('x') as handle:
        json.dump(record, handle, indent=2)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('assets', 'player-source', 'app', 'tsrsd', 'tsrs-source', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--release-tag', required=True)
    parser.add_argument('--qemu-user', type=Path)
    args = parser.parse_args()
    try:
        compose(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
