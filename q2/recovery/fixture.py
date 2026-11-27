#!/usr/bin/env python3
"""Build a tiny synthetic userspace image from an extracted stock firmware.
Never point this at a live device root. No saved configuration is included.
"""
import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

INIT = {
    'success': 'echo Q2_TRIAL_ROOT_OK\ntest -f /trial-marker\ntest ! -e /oldroot/etc\necho Q2_TRIAL_COMPLETE\n',
    'failure': 'echo Q2_TRIAL_EXPECTED_FAILURE\nexit 7\n',
    'hang': 'echo Q2_TRIAL_WAITING\nwhile :; do /bin/sleep 1; done\n',
    'daemon': '/bin/setsid /bin/sh -c "while :; do /bin/sleep 1; done" &\necho Q2_TRIAL_DAEMON\nexit 0\n',
}


def build(stock, output, case):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w') as archive:
        def add(name, data, mode=0o644):
            info = tarfile.TarInfo(name)
            info.mode, info.size = mode, len(data)
            archive.addfile(info, io.BytesIO(data))
        for name in ('bin', 'lib', 'oldroot', 'tmp'):
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(info)
        for name in ('bin/busybox', 'lib/ld-2.28.so', 'lib/libc-2.28.so', 'lib/libresolv-2.28.so'):
            add(name, (stock / name).read_bytes(), 0o755)
        links = {f'bin/{x}': 'busybox' for x in ('sh', 'sleep', 'setsid')}
        links.update({'lib/ld-linux-mipsn8.so.1': 'ld-2.28.so',
                      'lib/libc.so.6': 'libc-2.28.so', 'lib/libresolv.so.2': 'libresolv-2.28.so'})
        for name, target in links.items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname, info.mode = tarfile.SYMTYPE, target, 0o777
            archive.addfile(info)
        add('init', ('#!/bin/sh\nset -eu\n' + INIT[case]).encode(), 0o755)
        add('trial-marker', b'synthetic Q2 recovery fixture\n')
    manifest = {'format': 'q2-userspace-pivot-v1', 'case': case,
                'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'persistent_boot_selection': False, 'replacement_kernel': False}
    output.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--case', choices=INIT, default='success')
    args = parser.parse_args()
    build(args.stock, args.output, args.case)
