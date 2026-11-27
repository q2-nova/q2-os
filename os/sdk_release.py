#!/usr/bin/env python3
"""Freeze and admit a tested SDK bundle from package outputs."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from build import LOCK, ROOT, sha
from test_sdk import ARCHIVE

MANIFEST = 'sdk-manifest.json'
REPORT = 'sdk-test.json'
CONFIG = 'buildroot.config'
FILES = (ARCHIVE, REPORT, CONFIG, MANIFEST)
REQUIRED_CONFIG = {
    'BR2_mipsel=y', 'BR2_mips_xburst2_q2=y', 'BR2_MIPS_FP32_MODE_64=y',
    'BR2_TOOLCHAIN_BUILDROOT_GLIBC=y', 'BR2_TIME_BITS_64=y', 'BR2_PACKAGE_Q2_PLATFORM=y',
}


def regular(directory, name):
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.resolve() != path.absolute():
        raise ValueError('SDK bundle requires a regular ' + name)
    return path


def read_json(path):
    with path.open() as source:
        return json.load(source)


def validate_test(receipt, archive_hash):
    elf = receipt.get('elf', {})
    if (receipt.get('passed') is not True or receipt.get('sysroot_is_relocated') is not True
            or receipt.get('sdk_sha256') != archive_hash
            or elf.get('fp_abi') != 6 or elf.get('stack_flags') != 6
            or elf.get('interpreter') != '/lib/ld-linux-mipsn8.so.1'
            or receipt.get('platform_touchscreen_symbols') != ['q2_touchscreen_open', 'q2_touchscreen_state']):
        raise ValueError('matching relocated time64 MIPS SDK qualification required')


def identity(commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('full OS commit required')
    return dict(schema=1, os_commit=commit,
                buildroot_commit=LOCK['buildroot']['fork_commit'],
                defconfig_sha256=sha(ROOT / 'os/configs/q2_defconfig'))


def checksum_text(directory):
    return ''.join(f'{sha(regular(directory, name))}  {name}\n' for name in FILES)


def freeze(archive, report, config, container, output):
    commit = subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()
    if subprocess.check_output(['git', '-C', ROOT, 'status', '--porcelain'], text=True):
        raise ValueError('SDK qualification must use a clean OS checkout')
    if not re.fullmatch('sha256:[0-9a-f]{64}', container):
        raise ValueError('exact OCI image identity required')
    archive_hash = sha(archive)
    validate_test(read_json(report), archive_hash)
    manifest = dict(identity(commit), container_image=container,
                    archive_bytes=archive.stat().st_size, archive_sha256=archive_hash,
                    configuration_sha256=sha(config), qualification_sha256=sha(report))
    output.mkdir(parents=True)
    for source, name in ((archive, ARCHIVE), (report, REPORT), (config, CONFIG)):
        shutil.copyfile(source, output / name)
    (output / MANIFEST).write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'SHA256SUMS').write_text(checksum_text(output))
    return verify(output, commit)


def verify(bundle, commit, expected_archive=None):
    record = read_json(regular(bundle, MANIFEST))
    for key, expected in identity(commit).items():
        if record.get(key) != expected:
            raise ValueError(f'SDK bundle {key} differs from qualification')
    if not re.fullmatch('sha256:[0-9a-f]{64}', record.get('container_image', '')):
        raise ValueError('SDK bundle requires an exact OCI image identity')
    archive = regular(bundle, ARCHIVE)
    archive_hash = sha(archive)
    if (archive_hash != record.get('archive_sha256') or archive.stat().st_size != record.get('archive_bytes')
            or (expected_archive is not None and archive_hash != expected_archive)
            or sha(regular(bundle, REPORT)) != record.get('qualification_sha256')
            or sha(regular(bundle, CONFIG)) != record.get('configuration_sha256')
            or regular(bundle, 'SHA256SUMS').read_text() != checksum_text(bundle)):
        raise ValueError('SDK bundle bytes differ from qualification')
    validate_test(read_json(regular(bundle, REPORT)), archive_hash)
    if not REQUIRED_CONFIG <= set(regular(bundle, CONFIG).read_text().splitlines()):
        raise ValueError('SDK bundle lacks the required Q2 configuration')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    record = sub.add_parser('freeze')
    for name in ('archive', 'report', 'config', 'out'):
        record.add_argument('--' + name, type=Path, required=True)
    record.add_argument('--container', required=True)
    check = sub.add_parser('verify')
    check.add_argument('--bundle', type=Path, required=True)
    check.add_argument('--os-commit', required=True)
    check.add_argument('--expected-archive')
    args = parser.parse_args()
    try:
        result = (freeze(args.archive, args.report, args.config, args.container, args.out)
                  if args.action == 'freeze'
                  else verify(args.bundle, args.os_commit, args.expected_archive))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
