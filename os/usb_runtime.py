#!/usr/bin/env python3
"""Fail packaging when an installed USB ownership/watcher dependency is absent.

Only fixed public OS executables are inspected. No settings, keys or runtime
logs are input. The optional QEMU probe passes an invalid argument, which exits
the event helper before opening netlink, sysfs or any device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

REQUIRED = {
    'sbin/q2-installed-init': 'shell',
    'sbin/q2-usb-storage': 'shell',
    'usr/sbin/q2-usb-auto': 'shell',
    'usr/sbin/q2-usb-auto-events': 'elf',
    'usr/sbin/q2-usb-status-control': 'shell',
    'usr/sbin/q2-usb-status': 'elf',
}


def validate(root, qemu_user=None):
    root = Path(root).resolve()
    files = {}
    contents = {}
    for name, kind in REQUIRED.items():
        path = root / name
        if (path.is_symlink() or not path.is_file() or not path.stat().st_mode & 0o111
                or not path.resolve().is_relative_to(root)):
            raise ValueError(f'missing/non-executable installed USB runtime dependency: {name}')
        data = path.read_bytes()
        if kind == 'shell' and not data.startswith(b'#!/bin/sh\n'):
            raise ValueError(f'installed USB script has an invalid interpreter: {name}')
        if kind == 'elf':
            # Q2 uses 32-bit little-endian MIPS. An accidentally copied host
            # binary is executable on the build machine but unusable at boot.
            if (len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01'
                    or struct.unpack_from('<HHI', data, 16) not in [(2, 8, 1), (3, 8, 1)]):
                raise ValueError(f'installed USB helper is not a Q2 ELF32 MIPS executable: {name}')
        contents[name] = data
        files[name] = hashlib.sha256(data).hexdigest()
    if b'/usr/sbin/q2-usb-auto start' not in contents['sbin/q2-installed-init']:
        raise ValueError('installed init does not start the USB eject watcher')
    if b'EVENTS=/usr/sbin/q2-usb-auto-events' not in contents['usr/sbin/q2-usb-auto']:
        raise ValueError('installed USB manager does not declare its validated event helper')
    result = {'files': files, 'event_helper_loader_probe': 'not run'}
    if qemu_user is not None:
        try:
            probe = subprocess.run([str(qemu_user), '-L', str(root),
                                    str(root / 'usr/sbin/q2-usb-auto-events'),
                                    '--q2-runtime-check-invalid'], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('installed USB event helper loader probe failed') from error
        if probe.returncode != 2:
            raise ValueError('installed USB event helper cannot run against candidate libraries')
        result['event_helper_loader_probe'] = 'passed: invalid argument exits 2 before device I/O'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--qemu-user', type=Path)
    args = parser.parse_args()
    print(json.dumps({'usb_runtime': validate(args.root, args.qemu_user)}, indent=2))
