#!/usr/bin/env python3
"""Compose a keyless QEMU candidate from tagged Q2 OS Release assets."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile

from build import LOCK, ROOT, WORK, run, sha
from fork_checkout import verify
import sdk_platform
import sdk_release

RELEASE_FILES = (sdk_release.ARCHIVE, sdk_release.MANIFEST, sdk_release.REPORT,
                 sdk_release.CONFIG, 'SHA256SUMS', sdk_platform.ARCHIVE,
                 sdk_platform.RECORD, 'buildroot-legal-info.tar.gz')


def release_platform(bundle, commit):
    record = sdk_platform.verify(bundle, bundle, commit)
    sums = ''.join(f'{sha(sdk_release.regular(bundle, name))}  {name}\n' for name in RELEASE_FILES)
    if sdk_release.regular(bundle, 'RELEASE-SHA256SUMS').read_text() != sums:
        raise ValueError('release checksum list differs from the verified assets')
    return record


def archive_names(archive):
    with tarfile.open(archive) as content:
        members = content.getmembers()
    files = {}
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or '..' in path.parts or path in files
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
            raise ValueError('unsafe or ambiguous SDK archive member')
        files[path] = member
    for path, member in files.items():
        for parent in path.parents:
            if parent in files and not files[parent].isdir():
                raise ValueError('SDK archive member descends through a non-directory')
        if member.islnk():
            target = PurePosixPath(member.linkname)
            if target not in files or not files[target].isfile():
                raise ValueError('SDK archive hard link must target a bundled regular file')
    return files


def unpack(archive, output):
    archive_names(archive)
    run(['tar', '-xzf', archive, '--no-same-owner', '-C', output])


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


def build_containers():
    run([sys.executable, ROOT / 'os/build.py', 'container'])
    qemu = LOCK['qemu_installed']
    repo = qemu['url'].removeprefix('https://github.com/').removesuffix('.git')
    url = 'https://raw.githubusercontent.com/' + repo + '/' + qemu['commit'] + '/tests/q2/Containerfile'
    with tempfile.TemporaryDirectory(dir=WORK, prefix='kernel-container-') as directory:
        recipe = Path(directory) / 'Containerfile'
        run(['curl', '--fail', '--location', '--retry', '3', url, '-o', recipe])
        if sha(recipe) != qemu['containerfile_sha256']:
            raise ValueError('public QEMU kernel container recipe digest differs')
        run(['podman', 'build', '-t', 'q2-qemu-build', '-f', recipe, directory])


def compose(args):
    commit = subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()
    verify(ROOT, commit)
    assets = args.assets.resolve(strict=True)
    record = release_platform(assets, commit)
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK, prefix='ci-platform-') as directory:
        platform = Path(directory) / 'platform'
        target = restore_platform(assets, platform)
        build_containers()
        for image in ('q2-os-build', 'q2-qemu-build'):
            record[image] = subprocess.check_output(
                ['podman', 'image', 'inspect', image, '--format', '{{.Id}}'], text=True).strip()
        host = platform / 'host'
        run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
             '-v', f'{ROOT}:/work', 'q2-os-build',
             '/work/' + str((host / 'relocate-sdk.sh').relative_to(ROOT))])
        src = WORK / ('release-linux-' + LOCK['linux_production']['commit'][:12])
        run([sys.executable, ROOT / 'os/install/prepare.py', '--dest', src])
        run([sys.executable, ROOT / 'os/install/build.py', '--qemu-fixture',
             '--kernel-source', src, '--buildroot-target', target,
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
