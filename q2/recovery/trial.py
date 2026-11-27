#!/usr/bin/env python3
"""Stage a trusted userspace trial in RAM; retain stock init/player/SSH.

No bootloader, flash, saved configuration or persistent boot selection writes.
Kernel replacement is deliberately unsupported on the stock kernel.
"""
import argparse
import hashlib
import json
import posixpath
import shlex
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from device_ssh import ssh  # noqa: E402


def validate(path):
    if path.stat().st_size > 12 * 1024 * 1024:
        raise ValueError('archive exceeds 12 MiB RAM staging budget')
    with tarfile.open(path, 'r:') as archive:
        members = archive.getmembers()
        if len(members) > 2048 or sum(m.size for m in members) > 12 * 1024 * 1024:
            raise ValueError('expanded image exceeds budget')
        names, links = set(), set()
        for m in members:
            name = m.name
            if name.startswith('/') or posixpath.normpath(name) != name or name.startswith('../') or name in ('.', '..'):
                raise ValueError('unsafe archive path')
            if name in names:
                raise ValueError('duplicate archive path')
            names.add(name)
            if not (m.isfile() or m.isdir() or m.issym()):
                raise ValueError('only regular files, directories and relative symlinks allowed')
            if m.issym():
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), m.linkname))
                if m.linkname.startswith('/') or target == '..' or target.startswith('../'):
                    raise ValueError('escaping symlink')
                links.add(name)
            if m.mode & 0o6000:
                raise ValueError('set-id files are not allowed')
        for name in names:
            parent = posixpath.dirname(name)
            while parent:
                if parent in links:
                    raise ValueError('archive writes through symlink')
                parent = posixpath.dirname(parent)
        kinds = {m.name: m for m in members}
        if not kinds.get('init') or not kinds['init'].isfile():
            raise ValueError('regular /init required')
        for name in ('oldroot', 'tmp'):
            if name not in kinds or not kinds[name].isdir():
                raise ValueError(f'/{name} directory required')
        if any(name.startswith('oldroot/') for name in names):
            raise ValueError('/oldroot must be empty')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('image', type=Path)
    p.add_argument('--runner', type=Path, default=ROOT / 'build/recovery/trial')
    p.add_argument('--host', required=True)
    p.add_argument('--keys', type=Path, default=ROOT / 'build/ssh/device', help="the player's client_ed25519 and known_hosts (q2/ssh/provision.py)")
    p.add_argument('--seconds', type=int, default=30)
    p.add_argument('--lease', type=int, default=6)
    p.add_argument('--jz-watchdog', action='store_true', help='vendor watchdog; qualify reset/recovery before driver trials')
    p.add_argument('--stop-heartbeats-after', type=float, help='fault injection: connected host stops renewing lease')
    p.add_argument('--close-input-after', type=float, help='fault injection: close the host heartbeat channel')
    args = p.parse_args()
    validate(args.image)
    if not 2 <= args.lease <= min(30, args.seconds) or not args.seconds <= 600:
        p.error('require 2 <= lease <= min(30, seconds), seconds <= 600')
    token = uuid.uuid4().hex[:12]
    base = f'/run/q2trial-{token}'
    out = ROOT / 'build/recovery' / token
    out.mkdir(parents=True)
    connection = ssh(args.host, args.keys)
    def remote(command, **kwargs):
        return subprocess.run(connection + [command], timeout=30, check=True, **kwargs)
    mounted = False
    proc = None
    def close_input():
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass
    try:
        remote(f'mkdir {base} && mount -t tmpfs -o size=32m,mode=700 tmpfs {base}')
        mounted = True
        for local, name in ((args.image, 'root.tar'), (args.runner, 'trial')):
            data = local.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            manifest = shlex.quote(f'{digest}  {base}/{name}')
            remote(f"cat > {base}/{name} && printf '%s\\n' {manifest} | sha256sum -c -", input=data)
        remote(f'chmod 700 {base}/trial && mkdir {base}/root && cd {base}/root && tar xf ../root.tar && rm ../root.tar')
        argv = [f'{base}/trial', f'{base}/root', str(args.seconds), str(args.lease)]
        if args.jz_watchdog:
            argv.append('--jz-watchdog')
        with (out / 'supervisor.log').open('wb') as log:
            proc = subprocess.Popen(connection + ['exec ' + shlex.join(argv)], stdin=subprocess.PIPE, stdout=log, stderr=log)
            started = time.monotonic()
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if args.close_input_after is not None and elapsed >= args.close_input_after:
                    close_input()
                if not proc.stdin.closed and (args.stop_heartbeats_after is None or elapsed < args.stop_heartbeats_after):
                    try:
                        proc.stdin.write(b'P\n')
                        proc.stdin.flush()
                    except BrokenPipeError:
                        break
                if time.monotonic() - started > args.seconds + 15:
                    raise TimeoutError('trial failed to return before its bounded deadline')
                time.sleep(.5)
            result = proc.wait(timeout=10)
            close_input()
        with (out / 'candidate.log').open('wb') as log:
            remote(f'cat {base}/candidate.log', stdout=log)
        summary = {'exit': result, 'image_sha256': hashlib.sha256(args.image.read_bytes()).hexdigest(),
                   'watchdog': args.jz_watchdog, 'seconds': args.seconds, 'lease': args.lease,
                   'replacement_kernel': False, 'persistent_boot_selection': False}
        (out / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(out)
        print((out / 'supervisor.log').read_text())
        return result
    finally:
        if proc is not None and proc.poll() is None:
            close_input()  # EOF triggers device cleanup even on host Ctrl-C.
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
        if mounted:
            # Do not lazy-unmount: a failed cleanup must remain visible.
            try:
                remote(f'umount {base} && rmdir {base}')
            except (subprocess.SubprocessError, OSError) as error:
                print(f'RAM cleanup not confirmed: {type(error).__name__}; path {base}', file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
