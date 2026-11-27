#!/usr/bin/env python3
"""Build a paired kernel/external-initramfs trial, never a stock flash package."""
import argparse
import gzip
import json
import re
import subprocess
from build import WORK, ROOT, sha


def archive_file(name, data):
    name = name.encode() + b'\0'
    fields = [1, 0o100644, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), 0]
    header = b'070701' + b''.join(f'{x:08x}'.encode() for x in fields)
    body = header + name
    body += bytes(-len(body) % 4)
    body += data + bytes(-len(data) % 4)
    return body


def create(identity, broken=False):
    if not re.fullmatch('[a-zA-Z0-9_-]{1,64}', identity): raise ValueError('invalid bundle id')
    dest = WORK / 'bundles' / identity
    dest.mkdir(parents=True, exist_ok=False)
    kernel = WORK / 'kernel-disk/vmlinux'
    subprocess.run([WORK / 'out/host/bin/mipsel-buildroot-linux-gnu-strip', '--strip-debug', '-o', dest / 'kernel.elf', kernel], check=True)
    marker = archive_file('etc/q2-bundle-id', (identity+'\n').encode()) + archive_file('TRAILER!!!', b'')
    root = (WORK / 'out/images/rootfs.cpio.gz').read_bytes()
    if broken:
        # Valid checksums, deliberately unusable root: kernel must return to rescue.
        root = gzip.compress(b'not an initramfs', mtime=0)
    else:
        root += gzip.compress(marker, mtime=0)
    (dest / 'rootfs.cpio.gz').write_bytes(root)
    (dest / 'manifest.sha256').write_text(''.join(f'{sha(dest / n)}  {n}\n' for n in ['kernel.elf','rootfs.cpio.gz']))
    (dest / 'artifact.json').write_text(json.dumps({'id': identity, 'emulation_only': True, 'manifest_sha256':sha(dest / 'manifest.sha256'),
        'kernel_build':json.loads((kernel.parent/'artifact.json').read_text()), 'deliberately_invalid_root':broken},indent=2)+'\n')
    return dest

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('identity'); p.add_argument('--broken', action='store_true'); a=p.parse_args()
    print(create(a.identity,a.broken).relative_to(ROOT))
