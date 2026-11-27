#!/usr/bin/env python3
"""Create disposable SD image: read-only squashfs root + ext4 state."""
import argparse
import json
from pathlib import Path
import shutil
import struct
from build import ROOT, WORK, LOCK, container, sha

MiB = 1024 * 1024
ROOT_START, ROOT_SIZE = MiB, 64 * MiB
STATE_START, STATE_SIZE = ROOT_START + ROOT_SIZE, 192 * MiB

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='out')
    p.add_argument('--name', default='q2-sd')
    a = p.parse_args()
    for value in (a.output, a.name):
        if not value.replace('-', '').replace('_', '').isalnum():
            p.error('names must be simple directory/file names')
    dest = WORK / a.output / 'images'
    root = dest / 'rootfs.squashfs'
    if root.stat().st_size > ROOT_SIZE:
        raise RuntimeError('rootfs exceeds emulator partition allocation')
    state = dest / (a.name + '-state.ext4')
    with state.open('wb') as f: f.truncate(STATE_SIZE)
    container(['mkfs.ext4', '-q', '-F', '-L', 'Q2STATE',
               '-U', '51324f53-0000-4000-8000-000000000002',
               '-E', 'lazy_itable_init=0,lazy_journal_init=0,hash_seed=51324f53-0000-4000-8000-000000000003',
               '/work/' + str(state.relative_to(ROOT))],
              extra=('-e', f'E2FSPROGS_FAKE_TIME={LOCK["source_date_epoch"]}'))
    image = dest / (a.name + '.img')
    mbr = bytearray(512)
    struct.pack_into('<I', mbr, 440, 0x51324f53)
    for i, (start, length) in enumerate([(ROOT_START, ROOT_SIZE), (STATE_START, STATE_SIZE)]):
        struct.pack_into('<B3sB3sII', mbr, 446 + i * 16, 0, b'\xfe\xff\xff', 0x83,
                         b'\xfe\xff\xff', start // 512, length // 512)
    mbr[510:] = b'\x55\xaa'
    with image.open('wb') as f:
        f.truncate(512 * MiB)  # QEMU SD capacity must be a power of two.
        f.write(mbr)
        for start, source in [(ROOT_START, root), (STATE_START, state)]:
            f.seek(start)
            with source.open('rb') as src: shutil.copyfileobj(src, f)
    manifest = {
        'emulation_only': True, 'buildroot': LOCK['buildroot'],
        'image': image.name, 'sha256': sha(image),
        'rootfs_sha256': sha(root), 'rootfs_bytes': root.stat().st_size,
        'root_start': ROOT_START, 'root_size': ROOT_SIZE,
        'state_start': STATE_START, 'state_size': STATE_SIZE, 'disk_bytes': 512 * MiB,
        'root_argument': 'root=PARTUUID=51324f53-01 rootfstype=squashfs ro rootwait',
    }
    (dest / (a.name + '.json')).write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))

if __name__ == '__main__': main()
