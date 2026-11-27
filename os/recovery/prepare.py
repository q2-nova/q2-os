#!/usr/bin/env python3
"""Build recovery fixtures from the generated SDK; no deployment operations."""
import io
import gzip
import json
from pathlib import Path
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import ROOT, WORK, LOCK, run, sha
from audit import elf

OUT = WORK / 'recovery'
OUT.mkdir(exist_ok=True)
cc = WORK / 'out/host/bin/mipsel-buildroot-linux-gnu-gcc'
flags = ['-O2', '-Wall', '-Wextra', '-Werror', '-D_FILE_OFFSET_BITS=64',
         '-D_TIME_BITS=64', '-Wl,-z,noexecstack']
sources = {'trial': ROOT / 'os/recovery/trial.c',
           'trial-linux-watchdog': ROOT / 'os/recovery/watchdog-linux.c',
           'trial-watchdog': ROOT / 'os/recovery/watchdog-adapter.c',
           'daemon-fixture': ROOT / 'os/recovery/daemon-fixture.c',
           'kexec-probe': ROOT / 'os/recovery/kexec-probe.c'}
manifest = {'emulation_only': True, 'supervisor_sha256': sha(sources['trial']), 'artifacts': {}}
for name, source in sources.items():
    run([cc, *flags, source, '-o', OUT / name])
    manifest['artifacts'][name] = {'sha256': sha(OUT / name), 'elf': elf(OUT / name)}
with tarfile.open(OUT / 'root.tar', 'w') as archive:
    def add(name, data, mode=0o644):
        info = tarfile.TarInfo(name)
        info.mode, info.size = mode, len(data)
        archive.addfile(info, io.BytesIO(data))
    for name in ['bin', 'lib', 'oldroot', 'tmp']:
        info = tarfile.TarInfo(name); info.type = tarfile.DIRTYPE; info.mode = 0o755
        archive.addfile(info)
    for name in ['bin/busybox', 'lib/ld-linux-mipsn8.so.1', 'lib/libc.so.6', 'lib/libresolv.so.2']:
        add(name, (WORK / 'out/target' / name).read_bytes(), 0o755)
    add('bin/q2-detached-fixture', (OUT / 'daemon-fixture').read_bytes(), 0o755)
    for applet in ['sh', 'sleep', 'setsid', 'touch', 'cat', 'mkdir', 'mount', 'yes']:
        info = tarfile.TarInfo('bin/' + applet)
        info.type = tarfile.SYMTYPE; info.linkname = 'busybox'; info.mode = 0o777
        archive.addfile(info)
    add('trial-marker', b'synthetic source-built recovery fixture\n')
    add('init', b'#!/bin/sh\nset -eu\necho Q2_TRIAL_COMPLETE\n', 0o755)
manifest['artifacts']['root.tar'] = {'sha256': sha(OUT / 'root.tar')}
# A separate rescue image can reach its shell and fixtures without an SD card.
# Linux explicitly supports concatenated newc archives, each with its trailer.
entries = ['dir /usr/share/q2-recovery 0755 0 0']
for name in manifest['artifacts']:
    entries.append(f'file /usr/share/q2-recovery/{name} {OUT / name} 0755 0 0')
(OUT / 'rescue.list').write_text('\n'.join(entries) + '\n')
with (OUT / 'addon.cpio').open('wb') as stream:
    run([WORK / 'kernel-disk/usr/gen_init_cpio', '-t', str(LOCK['source_date_epoch']),
         OUT / 'rescue.list'], stdout=stream)
base = WORK / 'out/images/rootfs.cpio'
(OUT / 'rescue.cpio.gz').write_bytes(gzip.compress(
    base.read_bytes() + (OUT / 'addon.cpio').read_bytes(), mtime=0))
manifest['base_initramfs_sha256'] = sha(base)
manifest['rescue_initramfs_sha256'] = sha(OUT / 'rescue.cpio.gz')
(OUT / 'manifest.json.new').write_text(json.dumps(manifest, indent=2) + '\n')
(OUT / 'manifest.json.new').replace(OUT / 'manifest.json')
print(json.dumps(manifest, indent=2))
