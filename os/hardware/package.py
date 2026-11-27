#!/usr/bin/env python3
"""Build/check SPL-executable update.tar; stage recovery payloads on a host card.
Never opens a device, changes ota, reboots, or authorizes a hardware installation.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tarfile
import tempfile
import zlib

KERNEL = 'recovery-update/xImage'
ROOTFS = 'recovery-update/rootfs.squashfs'
INFO = 'firmware_v20.info'
LIMITS = {KERNEL: 6 * 1024**2, ROOTFS: 75 * 1024**2, INFO: 512}


def digest(data): return hashlib.sha256(data).hexdigest()


def uimage(data):
    if not 64 < len(data) <= LIMITS[KERNEL]: raise ValueError('kernel partition size')
    magic, hcrc, timestamp, size, load, entry, crc, os_id, arch, kind, comp, name = struct.unpack('>7I4B32s', data[:64])
    header = bytearray(data[:64]); header[4:8] = bytes(4)
    if magic != 0x27051956 or zlib.crc32(header) != hcrc: raise ValueError('uImage header/CRC')
    if size != len(data) - 64 or zlib.crc32(data[64:]) != crc: raise ValueError('uImage payload size/CRC')
    if (os_id, arch, kind, comp) != (5, 5, 2, 0):
        raise ValueError('SPL requires an executable Linux/MIPS kernel payload: compression must be none')
    if not (0x80000000 <= load < load + size <= 0x88000000): raise ValueError('load outside Q2 RAM')
    if load % 4 or entry % 4 or not load <= entry < load + size: raise ValueError('entry outside executable payload')
    # This verifies the container, not decompressor placement/cache correctness.
    return {'load': hex(load), 'entry': hex(entry), 'payload_bytes': size}


def squashfs(data):
    if not 96 <= len(data) <= LIMITS[ROOTFS]: raise ValueError('rootfs partition size')
    if data[:4] != b'hsqs' or struct.unpack_from('<HH', data, 28) != (4, 0):
        raise ValueError('expected squashfs v4')
    used = struct.unpack_from('<Q', data, 40)[0]
    if not 96 <= used <= len(data): raise ValueError('squashfs bytes_used')


def manifest(blobs, tag):
    if not re.fullmatch(r'V[A-Za-z0-9.]{4}', tag): raise ValueError('invalid five-character version')
    return ('Shanling Q2\n' + tag + '\n' + ''.join(
        hashlib.md5(blobs[n]).hexdigest() + '  ' + n + '\n' for n in [KERNEL, ROOTFS])).encode()


def validate(blobs):
    if set(blobs) != set(LIMITS): raise ValueError('unexpected/missing archive payload')
    info = blobs[INFO].decode('ascii').splitlines()
    if len(info) != 4 or blobs[INFO] != manifest(blobs, info[1]): raise ValueError('model/version/checksum manifest')
    kernel = uimage(blobs[KERNEL]); squashfs(blobs[ROOTFS])
    return {'model': 'Shanling Q2', 'version': info[1], 'kernel': kernel,
            'files': {n: {'bytes': len(b), 'sha256': digest(b)} for n, b in blobs.items()},
            'hardware_qualified': False}


def read_package(path, expected=None):
    path = Path(path)
    if path.stat().st_size > sum(LIMITS.values()) + 65536: raise ValueError('oversized archive')
    raw = path.read_bytes()
    if expected is not None and (not re.fullmatch('[a-f0-9]{64}', expected) or digest(raw) != expected):
        raise ValueError('package SHA-256 differs from supplied trusted digest')
    blobs = {}; directories = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for m in archive:
            if m.isdir() and m.name.rstrip('/') == 'recovery-update':
                if directories: raise ValueError('duplicate directory')
                directories.add(m.name); continue
            if not m.isfile() or m.name not in LIMITS or m.name in blobs:
                raise ValueError('unexpected, duplicate or non-regular archive member: ' + m.name)
            if not 0 < m.size <= LIMITS[m.name]: raise ValueError('archive member size')
            blobs[m.name] = archive.extractfile(m).read()
    report = validate(blobs); report['update_tar_sha256'] = digest(raw)
    return blobs, report


def build_package(kernel, rootfs, output, tag, installed):
    if tag == installed: raise ValueError('stock menu rejects the installed version')
    blobs = {KERNEL: Path(kernel).read_bytes(), ROOTFS: Path(rootfs).read_bytes()}
    blobs[INFO] = manifest(blobs, tag)
    validate(blobs)
    # Exclusive creation prevents silently replacing a previously reviewed release.
    with Path(output).open('xb') as f, tarfile.open(fileobj=f, mode='w', format=tarfile.USTAR_FORMAT) as t:
        for n in [INFO, KERNEL, ROOTFS]:
            m = tarfile.TarInfo(n); m.size = len(blobs[n]); m.mode = 0o644; m.mtime = 0
            t.addfile(m, io.BytesIO(blobs[n]))
    return read_package(output)[1]


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def stage(path, card, expected):
    blobs, report = read_package(path, expected)
    card = Path(card)
    if card.is_symlink() or not card.is_dir(): raise ValueError('card root must be an existing real directory')
    lock = card / '.q2-update-stage.lock'
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    temp = None
    try:
        final = card / 'recovery-update'
        if final.exists() or final.is_symlink(): raise ValueError('recovery-update already exists; preserve or explicitly remove it first')
        temp = Path(tempfile.mkdtemp(prefix='.q2-update-stage-', dir=card))
        for n in [KERNEL, ROOTFS]:
            with (temp / Path(n).name).open('xb') as f:
                f.write(blobs[n]); f.flush(); os.fsync(f.fileno())
        sync_dir(temp)
        temp.rename(final); temp = None
        sync_dir(card)
        # No firmware_v20.info in the recovery directory: this is the exact
        # two-file contract the stock recovery consumes. Backup stays elsewhere.
        return report
    finally:
        if temp: shutil.rmtree(temp)
        os.close(fd); lock.unlink(); sync_dir(card)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    b = sub.add_parser('build')
    for name in ['kernel', 'rootfs', 'output', 'tag', 'installed-tag']: b.add_argument('--' + name, required=True)
    c = sub.add_parser('check'); c.add_argument('package'); c.add_argument('--sha256')
    s = sub.add_parser('stage'); s.add_argument('package'); s.add_argument('card_root'); s.add_argument('--sha256', required=True)
    a = p.parse_args()
    try:
        if a.action == 'build': result = build_package(a.kernel, a.rootfs, a.output, a.tag, a.installed_tag)
        elif a.action == 'check': result = read_package(a.package, a.sha256)[1]
        else: result = stage(a.package, a.card_root, a.sha256)
    except (ValueError, OSError, tarfile.TarError, UnicodeError) as e:
        p.exit(1, str(e) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
