#!/usr/bin/env python3
"""Build optional basic-NTP chrony tools with the generated Q2 SDK.

No device access. NTS is deliberately not configured or silently downgraded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
VERSION = '4.8'
SHA256 = '33ea8eb2a4daeaa506e8fcafd5d6d89027ed6f2f0609645c6f149b560d301706'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--buildroot-output', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/os/network-time')
    a = p.parse_args()
    out = a.output.resolve()
    if not out.is_relative_to(ROOT / 'build'):
        p.error('output must be under build/')
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f'chrony-{VERSION}.tar.gz'
    if not archive.exists():
        with urllib.request.urlopen(f'https://chrony-project.org/releases/{archive.name}', timeout=60) as r:
            archive.write_bytes(r.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError('chrony source hash mismatch')
    source = out / f'chrony-{VERSION}'
    if not source.exists():
        with tarfile.open(archive) as t:
            t.extractall(out, filter='data')
    subprocess.run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
        '-v', f'{out}:/time', '-v', f'{a.buildroot_output.resolve()}:/br:ro',
        '-w', f'/time/chrony-{VERSION}', 'q2-os-build', 'sh', '-ec', '''
export PATH=/br/host/bin:$PATH
export CC=mipsel-buildroot-linux-gnu-gcc
export CFLAGS=-Os
./configure --host-system=Linux --host-machine=mipsel --prefix=/usr \
 --without-gnutls --without-nettle --without-nss --without-tomcrypt \
 --without-libcap --without-seccomp --without-editline --disable-readline \
 --disable-nts --disable-refclock --disable-debug --with-user=root
make -j4 chronyd chronyc
mkdir -p /time/bin
mipsel-buildroot-linux-gnu-strip -o /time/bin/chronyd chronyd
mipsel-buildroot-linux-gnu-strip -o /time/bin/chronyc chronyc
'''], check=True)
    overlay = out / 'overlay'
    shutil.copytree(Path(__file__).parent / 'overlay', overlay, dirs_exist_ok=True)
    for name in ('chronyd', 'chronyc'):
        target = overlay / 'usr/libexec/q2-network' / name
        shutil.copyfile(out / 'bin' / name, target)
        target.chmod(0o755)
    (out / 'artifact.json').write_text(json.dumps({
        'chrony_version': VERSION, 'source_sha256': SHA256,
        'profile': 'basic NTP, explicitly unauthenticated', 'nts_enabled': False,
        'provider': 'time.cloudflare.com', 'hardware_qualified': False,
        'builder_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'overlay_sha256': {str(f.relative_to(overlay)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(overlay.rglob('*')) if f.is_file()},
        'binaries': {n: {'bytes': (out / 'bin' / n).stat().st_size,
            'sha256': hashlib.sha256((out / 'bin' / n).read_bytes()).hexdigest()}
            for n in ('chronyd', 'chronyc')},
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
