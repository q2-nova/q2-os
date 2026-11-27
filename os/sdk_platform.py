#!/usr/bin/env python3
"""Freeze or verify the userspace target built beside a qualified SDK."""
import argparse
import json
from pathlib import Path
import subprocess

from build import sha
import sdk_release

ARCHIVE = 'target.tar.gz'
RECORD = 'platform.json'


def identity(sdk, target_archive):
    return {
        'schema': 1, 'os_commit': sdk['os_commit'], 'buildroot_commit': sdk['buildroot_commit'],
        'sdk_sha256': sdk['archive_sha256'], 'configuration_sha256': sdk['configuration_sha256'],
        'target_sha256': sha(target_archive), 'target_bytes': target_archive.stat().st_size,
    }


def verify(platform, sdk_bundle, commit):
    sdk = sdk_release.verify(sdk_bundle, commit)
    archive = sdk_release.regular(platform, ARCHIVE)
    record = sdk_release.read_json(sdk_release.regular(platform, RECORD))
    if record != identity(sdk, archive):
        raise ValueError('platform target must match the exact qualified SDK')
    return record


def freeze(target, sdk_bundle, commit, output):
    sdk = sdk_release.verify(sdk_bundle, commit)
    if target.is_symlink() or not target.is_dir() or not (target / 'usr/lib/libq2platform.so').is_file():
        raise ValueError('completed Q2 Buildroot target directory required')
    output.mkdir(parents=True)
    archive = output / ARCHIVE
    subprocess.run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner',
                    '-C', target, '-czf', archive, '.'], check=True)
    (output / RECORD).write_text(json.dumps(identity(sdk, archive), indent=2) + '\n')
    return verify(output, sdk_bundle, commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'verify'))
    parser.add_argument('--sdk-bundle', type=Path, required=True)
    parser.add_argument('--os-commit', required=True)
    parser.add_argument('--platform', type=Path, required=True)
    parser.add_argument('--target', type=Path)
    args = parser.parse_args()
    if args.action == 'freeze' and args.target is None:
        parser.error('freezing requires a completed --target')
    publish = (freeze(args.target, args.sdk_bundle, args.os_commit, args.platform)
               if args.action == 'freeze' else verify(args.platform, args.sdk_bundle, args.os_commit))
    print(json.dumps(publish, indent=2))


if __name__ == '__main__':
    main()
