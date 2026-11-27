#!/usr/bin/env python3
"""Build and admit a tagged production kernel, keyless rescue and matching modules."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

OS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OS))
sys.path.insert(0, str(OS / 'hardware'))
from archives import unpack
from build import LOCK, ROOT, WORK, run, sha
from fork_checkout import verify
from hardware.package import uimage
from ssh_provision import InstalledSsh
import sdk_platform
import sdk_release
from install.kernel_build import build_kernel, build_kernel_container, copy, prepare_rescue

COPIED = ('xImage', 'rescue.cpio', 'shanling-q2.dtb', 'linux.config')
MODULES = 'kernel-modules.tar.gz'
MANIFEST = 'kernel-base.json'
FILES = (*COPIED, MODULES, MANIFEST)


def source_commit():
    return subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()


def identities(directory):
    result = {}
    for name in (*COPIED, MODULES):
        artifact = sdk_release.regular(directory, name)
        result[name] = {'sha256': sha(artifact), 'bytes': artifact.stat().st_size}
    return result


def module_hashes(directory):
    return {path.relative_to(directory).as_posix(): sha(path)
            for path in sorted(directory.rglob('*.ko'))}


def verify_base(bundle, platform):
    identity = sdk_release.read_json(sdk_release.regular(bundle, MANIFEST))
    if (identity.get('schema') != 1 or identity.get('platform') != platform
            or identity.get('linux') != LOCK['linux_production']
            or identity.get('diagnostic_ssh') != {'profile': 'disabled'}
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', identity.get('kernel_container', ''))
            or not re.fullmatch(r'[a-zA-Z0-9.+_-]+', identity.get('kernel_release', ''))
            or identity.get('artifacts') != identities(bundle) or not identity.get('modules')):
        raise ValueError('prebuilt kernel does not match the pinned keyless OS platform')
    uimage((bundle / 'xImage').read_bytes())
    return identity


def install_base(bundle, out, root, target, commit):
    platform = sdk_platform.verify(bundle, bundle, commit)
    identity = verify_base(bundle, platform)
    # The supplied target must yield the same keyless rescue inputs.
    check = out / 'rescue-input-check.cpio'
    prepare_rescue(target, out / 'rescue-input-check', check, InstalledSsh(None, None, None, None))
    if sha(check) != identity['artifacts']['rescue.cpio']['sha256']:
        raise ValueError('Buildroot target differs from the prebuilt rescue inputs')
    for name in COPIED:
        copy(bundle / name, out / name)
    modules = root / 'lib/modules'
    if modules.is_symlink() or not modules.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError('installed modules require a regular directory')
    modules.mkdir(parents=True, exist_ok=True)
    if any(modules.iterdir()):
        raise ValueError('Buildroot target already supplies kernel modules')
    unpack(bundle / MODULES, modules)
    release = identity['kernel_release']
    if set(modules.iterdir()) != {modules / release} or module_hashes(modules / release) != identity['modules']:
        raise ValueError('installed modules differ from the built kernel closure')
    return release, modules / release, out / 'linux.config'


def produce(bundle, platform_dir, output):
    commit = source_commit()
    verify(ROOT, commit)
    platform = sdk_platform.verify(platform_dir, bundle, commit)
    # Build from the exact qualified platform, including the rescue executables.
    target_archive = platform_dir / sdk_platform.ARCHIVE
    if not output.is_relative_to(ROOT / 'build'):
        raise ValueError('use an isolated build/ output')
    output.mkdir(parents=True)
    target = output / 'target'
    target.mkdir()
    unpack(target_archive, target)
    linux = LOCK['linux_production']
    source = WORK / ('release-linux-' + linux['commit'][:12])
    run([sys.executable, OS / 'install/prepare.py', '--dest', source])
    container = build_kernel_container()
    ssh = InstalledSsh(None, None, None, None)
    prepare_rescue(target, output / 'rescue', output / 'rescue.cpio', ssh)
    release, modules, config = build_kernel(source, output, output / 'module-stage')
    copy(config, output / 'linux.config')
    run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner',
         '-C', modules.parent, '-czf', output / MODULES, release])
    record = dict(schema=1, platform=platform, linux=linux, kernel_container=container,
                  kernel_release=release, diagnostic_ssh=ssh.receipt,
                  artifacts=identities(output), modules=module_hashes(modules))
    (output / MANIFEST).write_text(json.dumps(record, indent=2) + '\n')
    return verify_base(output, platform)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('sdk-bundle', 'platform', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    opts = parser.parse_args()
    print(json.dumps(produce(opts.sdk_bundle, opts.platform, opts.output.resolve()), indent=2))


if __name__ == '__main__':
    main()
