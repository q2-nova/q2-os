#!/usr/bin/env python3
"""Cross-build qualification Wi-Fi tools using a generated Buildroot SDK.

Source versions/digests match Buildroot 2026.08.1. No credentials or device access.
The default preserves the WPA2 qualification profile. --wpa3 adds SAE/PMF
using a statically linked OpenSSL EC backend, in an isolated output directory.
Build the private phone AP payload with either profile or separately with --phone-only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'os'))
import portal_phone_runtime
SOURCES = {
    'libnl-3.12.0': ('https://github.com/thom311/libnl/releases/download/libnl3_12_0/libnl-3.12.0.tar.gz',
                   'fc51ca7196f1a3f5fdf6ffd3864b50f4f9c02333be28be4eeca057e103c0dd18'),
    'wpa_supplicant-2.12': ('https://w1.fi/releases/wpa_supplicant-2.12.tar.gz',
                          '08e23937e16d0155e55cab2b51f51fbe10d80a1aa91c4e15442645059b737ef6'),
}
OPENSSL = ('https://github.com/openssl/openssl/releases/download/openssl-3.6.5/openssl-3.6.5.tar.gz',
           'a2157c2830efdec3788939b00c9b0638306d3f0bbb76dc4832ee503bb397df98')

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def supplicant_config(wpa3):
    config = '''CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_CTRL_IFACE=y
CONFIG_BACKEND=file
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_INTERNAL_LIBTOMMATH_FAST=y
CFLAGS += -Os -I/wifi/sysroot/usr/include/libnl3
LIBS += -L/wifi/sysroot/usr/lib -lm
'''
    if wpa3:
        config = config.replace('CONFIG_TLS=internal\n', 'CONFIG_TLS=openssl\n')
        # Upstream 2.12 gates the real PMKSA cache behind IEEE8021X_EAPOL.
        # RTL8733BS starts station association from SET_PMKSA after external
        # authentication succeeds; SAE alone otherwise builds no-op cache APIs.
        config += '''CONFIG_SAE=y
CONFIG_IEEE80211W=y
CONFIG_IEEE8021X_EAPOL=y
CFLAGS += -I/wifi/sysroot/usr/include
'''
    return config

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--buildroot-output', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/os/hardware-wifi-tools')
    p.add_argument('--wpa3', action='store_true', help='optional SAE/PMF profile; use a separate output directory')
    p.add_argument('--phone-only', action='store_true', help='build only the private portal phone AP payload')
    a = p.parse_args()
    if a.phone_only and a.wpa3:
        p.error('--phone-only does not build a supplicant')
    out = a.output.resolve()
    common = Path(subprocess.check_output(
        ['git', '-C', str(ROOT), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
        text=True).strip()).parent
    if not out.is_relative_to(ROOT / 'build') and not (
            a.phone_only and out.is_relative_to(common / 'build')):
        p.error('output must be under build/')
    if (out / 'portal-phone.json').exists():
        p.error('use a fresh output to preserve the existing radio receipt')
    out.mkdir(parents=True, exist_ok=True)
    phone_recipes = portal_phone_runtime.recipes()
    sources = dict(SOURCES if not a.phone_only else {'libnl-3.12.0': SOURCES['libnl-3.12.0']})
    if a.wpa3:
        if out == ROOT / 'build/os/hardware-wifi-tools':
            p.error('--wpa3 requires an explicit isolated --output')
        sources['openssl-3.6.5'] = OPENSSL
    for name, (url, expected) in (sources | portal_phone_runtime.SOURCES).items():
        archive = out / url.rsplit('/', 1)[1]
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != expected: raise RuntimeError('download hash mismatch')
            archive.write_bytes(data)
        if digest(archive) != expected: raise RuntimeError('cached hash mismatch')
        if not (out / name).exists():
            with tarfile.open(archive) as t: t.extractall(out, filter='data')
    def engine(cwd, args):
        subprocess.run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
            '-v', f'{out}:/wifi', '-v', f'{a.buildroot_output.resolve()}:/br:ro', '-w', '/wifi/' + cwd,
            'q2-os-build', *args], check=True)
    engine('libnl-3.12.0', ['sh', '-ec', '''export PATH=/br/host/bin:$PATH
CC=mipsel-buildroot-linux-gnu-gcc ./configure --host=mipsel-buildroot-linux-gnu --prefix=/usr --disable-cli --disable-pthreads --disable-shared --enable-static
make -j4
make DESTDIR=/wifi/sysroot install'''])
    if a.phone_only:
        portal_phone_runtime.build(out, engine, phone_recipes)
        return
    if a.wpa3:
        engine('openssl-3.6.5', ['sh', '-ec', '''export PATH=/br/host/bin:$PATH
./Configure linux-generic32 --cross-compile-prefix=mipsel-buildroot-linux-gnu- --prefix=/usr --libdir=lib \
 no-asm no-shared no-tests no-apps no-module no-engine no-dso no-legacy -Os -DOPENSSL_SMALL_FOOTPRINT -DBROKEN_CLANG_ATOMICS
make -j4 build_libs
make DESTDIR=/wifi/sysroot install_dev'''])
    wpa = out / 'wpa_supplicant-2.12/wpa_supplicant'
    (wpa / '.config').write_text(supplicant_config(a.wpa3))
    engine('wpa_supplicant-2.12/wpa_supplicant', ['make', '-j4', 'CC=/br/host/bin/mipsel-buildroot-linux-gnu-gcc',
        'PKG_CONFIG=/br/host/bin/pkg-config', 'wpa_supplicant', 'wpa_cli'])
    (out / 'bin').mkdir(exist_ok=True)
    for name in ('wpa_supplicant', 'wpa_cli'):
        engine('', ['/br/host/bin/mipsel-buildroot-linux-gnu-strip', '-o', '/wifi/bin/' + name,
                    '/wifi/wpa_supplicant-2.12/wpa_supplicant/' + name])
    portal_phone_runtime.build(out, engine, phone_recipes)
    (out / 'artifact.json').write_text(json.dumps({
        'sources': sources, 'binaries': {n: digest(out / 'bin' / n) for n in ('wpa_supplicant', 'wpa_cli')},
        'profile': 'WPA2/WPA3 SAE with PMF, static OpenSSL EC' if a.wpa3 else 'WPA2 internal crypto',
        'binary_bytes': {n: (out / 'bin' / n).stat().st_size for n in ('wpa_supplicant', 'wpa_cli')},
        'hardware_qualified': False, 'builder_sha256': digest(Path(__file__)),
    }, indent=2) + '\n')

if __name__ == '__main__': main()
