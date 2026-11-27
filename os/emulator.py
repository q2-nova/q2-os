#!/usr/bin/env python3
"""Prepare pinned emulator/kernel sources and build isolated host artifacts."""
import argparse
from datetime import datetime, timezone
import fcntl
import tempfile
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import uuid
from build import ROOT, WORK, LOCK, fetch, run, sha, kernel_patch_hashes

from compatibility import PROFILES, input_digest, kernel_input_digest, kernel_patches
from fork_checkout import checkout, verify

QSRC = WORK / ('qemu-src-' + LOCK['qemu_commit'])
QEMU_OUT = WORK / 'qemu-out'
QEMU_BINARY = 'qemu-system-mipsel'
QEMU_CONFIGURE_ARGS = (
    '--target-list=mipsel-softmmu', '--disable-docs', '--disable-werror',
    '--enable-slirp', '--enable-fdt=system', '--disable-gtk', '--disable-sdl',
    '--disable-vnc', '--disable-opengl',
)


def kernel_source(profile='os-kernel', work_label=None):
    patches = kernel_patches(profile)
    local_patches = kernel_patch_hashes() if profile == 'os-kernel' else {}
    source_id = kernel_input_digest(profile, patches, local_patches)
    work_label = work_label or profile
    prepared = WORK / f"linux-{LOCK['kernel']['version']}-{work_label}-{source_id[:12]}"
    stamp = prepared / '.q2-prepared'
    if stamp.exists():
        if stamp.read_text().strip() != source_id:
            raise RuntimeError('kernel source preparation stamp mismatch')
        return prepared, source_id, local_patches
    if prepared.exists():
        raise RuntimeError('incomplete kernel source tree; move it aside before retrying: ' + str(prepared))
    archive = fetch(LOCK['kernel'])
    # Publish only a completely patched tree. Later patches may alter files
    # introduced by earlier patches, so reverse-applying each patch is unsafe.
    with tempfile.TemporaryDirectory(prefix='kernel-prepare-', dir=WORK) as staging:
        with tarfile.open(archive) as t:
            t.extractall(staging, filter='data')
        source = Path(staging) / ('linux-' + LOCK['kernel']['version'])
        run(['git', '-C', source, 'init', '-q'])
        for patch in patches:
            run(['git', '-C', source, 'apply', patch])
        for name in local_patches:
            run(['git', '-C', source, 'apply', ROOT / 'os/patches/kernel' / name])
        (source / '.q2-prepared').write_text(source_id + '\n')
        try:
            source.rename(prepared)
        except OSError:
            # Another profile may have published the same complete source
            # while this process prepared its own temporary tree.
            if not stamp.exists() or stamp.read_text().strip() != source_id:
                raise
    return prepared, source_id, local_patches


def engine(args, network='none'):
    run(['podman', 'run', '--rm', '--network', network, '--security-opt', 'label=disable',
         '-v', f'{ROOT}:/work', '-w', '/work',
         '-e', f'SOURCE_DATE_EPOCH={LOCK["source_date_epoch"]}',
         '-e', 'KBUILD_BUILD_TIMESTAMP=' + datetime.fromtimestamp(LOCK['source_date_epoch'], timezone.utc).strftime('%a %b %d %T UTC %Y'),
         '-e', 'KBUILD_BUILD_VERSION=1',
         '-e', 'KBUILD_BUILD_USER=q2', '-e', 'KBUILD_BUILD_HOST=builder',
         LOCK['kernel_build_container'], *args])

def qemu_identity():
    return {
        'source_commit': LOCK['qemu_commit'],
        'upstream_commit': LOCK['qemu_upstream_commit'],
        'origin_commit': PROFILES['qemu']['origin_commit'],
        'container': LOCK['kernel_build_container'],
        'configure': list(QEMU_CONFIGURE_ARGS),
        'emulation_only': True,
    }


def qemu_artifact():
    binary = QEMU_OUT / QEMU_BINARY
    receipt = json.loads((QEMU_OUT / 'artifact.json').read_text())
    identity = qemu_identity()
    if (any(receipt.get(key) != value for key, value in identity.items())
            or receipt.get('source_inputs_sha256') != input_digest(identity)
            or sha(binary) != receipt.get('sha256')):
        raise RuntimeError('stale or modified QEMU artifact: ' + str(QEMU_OUT))
    return binary, receipt


def engine_path(path):
    return '/work/' + str(path.resolve().relative_to(ROOT))


def run_qemu_control(stem, kernel):
    kernel_path = engine_path(kernel)
    kernel_digest = sha(kernel)
    prefix = {'dpu': 'dpu-test-', 'watchdog': 'wd-test-'}[stem]
    output = WORK / 'tests' / (prefix + uuid.uuid4().hex[:10])
    test_run = os.environ.get('Q2_OS_ACCEPTANCE_ID')
    with (WORK / '.qemu-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        binary, artifact = qemu_artifact()
        verify(QSRC, LOCK['qemu_commit'])
        command = ['python3', engine_path(QSRC / 'tests/q2' / (stem + '.py')),
                   '--qemu', engine_path(binary), '--kernel', kernel_path,
                   '--out', engine_path(output)]
        if test_run is not None:
            command += ['--test-run', test_run]
        engine(command)
        result_path = output / 'result.json'
        result = json.loads(result_path.read_text())
        if (result.get('pass') is not True or result.get('test_run') != test_run
                or result.get('qemu_sha256') != artifact['sha256']
                or result.get('kernel_sha256') != kernel_digest):
            raise RuntimeError('QEMU control did not admit its result: ' + str(result_path))
    print(result_path)


def preserve_qemu_artifact(out):
    manifest = out / 'artifact.json'
    if not manifest.exists():
        return
    binary = out / 'qemu-system-mipsel'
    previous = json.loads(manifest.read_text())
    if sha(binary) != previous['sha256']:
        raise RuntimeError('existing QEMU binary differs from its artifact receipt')
    archive = out.with_name('qemu-artifact-' + sha(manifest))
    if not archive.exists():
        with tempfile.TemporaryDirectory(prefix='qemu-archive-', dir=out.parent) as staging:
            snapshot = Path(staging) / 'artifact'
            snapshot.mkdir()
            shutil.copy2(binary, snapshot / binary.name)
            shutil.copy2(manifest, snapshot / manifest.name)
            snapshot.rename(archive)
    if sha(archive / manifest.name) != sha(manifest) or sha(archive / binary.name) != previous['sha256']:
        raise RuntimeError('archived QEMU binary or receipt differs from the previous build')
    # The previous pair remains usable if publication is interrupted.
    manifest.unlink()


def qemu():
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / '.qemu-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        checkout(QSRC, LOCK['qemu_commit'], 'Q2_QEMU_REPO', LOCK['qemu_url'])
        build_qemu()


def build_qemu():
    identity = qemu_identity()
    source_id = input_digest(identity)
    receipt_path = QEMU_OUT / 'artifact.json'
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text())
        if existing.get('source_inputs_sha256') == source_id:
            qemu_artifact()
            return

    staging = WORK / ('qemu-build-' + source_id)
    staging.mkdir(exist_ok=True)
    engine(['sh', '-ec', '''cd "$1"
shift
if [ ! -f build.ninja ]; then
    "$@"
fi
ninja -j2 qemu-system-mipsel''', 'sh', engine_path(staging),
            engine_path(QSRC / 'configure'), *QEMU_CONFIGURE_ARGS], network='host')
    verify(QSRC, LOCK['qemu_commit'])
    binary = staging / QEMU_BINARY
    record = {**identity, 'source_inputs_sha256': source_id, 'sha256': sha(binary)}
    QEMU_OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qemu-publish-', dir=WORK) as directory:
        prepared = Path(directory)
        shutil.copy2(binary, prepared / QEMU_BINARY)
        (prepared / receipt_path.name).write_text(json.dumps(record, indent=2) + '\n')
        preserve_qemu_artifact(QEMU_OUT)
        (prepared / QEMU_BINARY).replace(QEMU_OUT / QEMU_BINARY)
        (prepared / receipt_path.name).replace(receipt_path)
    qemu_artifact()

def kernel(initramfs, output=None, enable=(), disable=()):
    prepared, source_id, local_patches = kernel_source()
    out = WORK / (output or ('kernel-initramfs' if initramfs else 'kernel-disk'))
    revision = out / '.source-revision'
    manifest = out / 'artifact.json'
    previous = revision.read_text().strip() if revision.exists() else None
    if previous is None and manifest.exists():
        identity = json.loads(manifest.read_text())
        previous = identity.get('source_inputs_sha256', identity['source_commit'])
    if previous and previous != source_id:
        archive = WORK / (out.name + '-' + previous)
        if archive.exists():
            raise RuntimeError('kernel archive already exists; move it aside before changing baseline: ' + str(archive))
        out.rename(archive)
    elif out.exists() and (out / '.config').exists() and not previous:
        raise RuntimeError('kernel build has no source identity; move it aside before retrying')
    out.mkdir(exist_ok=True)
    revision.write_text(source_id + '\n')
    base = ['make', '-C', '/work/' + str(prepared.relative_to(ROOT)),
            'O=/work/' + str(out.relative_to(ROOT)), 'ARCH=mips',
            'CROSS_COMPILE=mipsel-linux-gnu-', '-j2']
    if not (out / '.config').exists():
        engine(base + ['shanling_q2_defconfig'])
    config = '/work/' + str((out / '.config').relative_to(ROOT))
    script = '/work/' + str((prepared / 'scripts/config').relative_to(ROOT))
    opts = [script, '--file', config]
    for opt in ['EXT4_FS', 'SQUASHFS', 'SQUASHFS_XZ', 'SQUASHFS_ZLIB',
                'RD_GZIP', 'UNIX', 'TMPFS', 'DEVTMPFS', 'DEVTMPFS_MOUNT', *enable]:
        engine(opts + ['--enable', opt])
    # UHI kexec supplies bootargs in /chosen. Fall back to legacy bootloader
    # arguments for cold boots whose DT has no bootargs property.
    engine(opts + ['--disable', 'MIPS_CMDLINE_FROM_BOOTLOADER'])
    engine(opts + ['--enable', 'MIPS_CMDLINE_FROM_DTB'])
    for opt in disable:
        engine(opts + ['--disable', opt])
    image = ''
    if initramfs:
        source = Path(initramfs).resolve()
        image = '/work/' + str(source.relative_to(ROOT))
    engine(opts + ['--set-str', 'INITRAMFS_SOURCE', image])
    engine(base + ['olddefconfig', 'uImage.gz', 'dtbs'])
    shutil.copyfile(out / 'arch/mips/boot/uImage.gz', out / 'uImage.gz')
    (out / 'artifact.json').write_text(json.dumps({
        'source_commit': PROFILES['os-kernel']['origin_commit'],
        'source_inputs_sha256': source_id,
        'kernel_sha256': sha(out / 'uImage.gz'),
        'config_sha256': sha(out / '.config'),
        'local_patches': local_patches,
        'initramfs_sha256': sha(source) if initramfs else None,
        'extra_enabled_config': list(enable),
        'extra_disabled_config': list(disable),
        'emulation_only': True}, indent=2) + '\n')

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['qemu', 'kernel', 'sources'])
    p.add_argument('--qemu-cache', help='local checkout for the pinned QEMU fork commit')
    p.add_argument('--initramfs')
    p.add_argument('--output', help='isolated kernel output name (kernel-NAME)')
    p.add_argument('--enable', action='append', default=[], help='additional kernel config symbol')
    p.add_argument('--disable', action='append', default=[], help='explicitly disabled kernel config symbol')
    a = p.parse_args()
    import re
    if a.output and not re.fullmatch(r'kernel-[a-zA-Z0-9_-]+', a.output): p.error('invalid kernel output name')
    if any(not re.fullmatch(r'[A-Z0-9_]+', item) for item in a.enable + a.disable): p.error('invalid config symbol')
    if a.action == 'qemu':
        if a.qemu_cache:
            os.environ['Q2_QEMU_REPO'] = str(Path(a.qemu_cache).resolve())
        qemu()
    elif a.action == 'kernel': kernel(a.initramfs, a.output, a.enable, a.disable)
    else: kernel_source()
