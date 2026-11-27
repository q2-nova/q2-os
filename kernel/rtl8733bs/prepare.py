#!/usr/bin/env python3
"""Fetch the pinned Realtek RTL8733BS source, verify it and apply the Q2 port.

The source is Realtek's v5.15.17-113-g1924716b3.20250521 SDIO driver as
published at github.com/smp79/rtl8733BS_WiFi_linux_v5.15.17-113. GitHub
archive bytes are not guaranteed stable, so the check is a digest over the
extracted tree: sorted relative paths, executable bits and file SHA-256s.

usage: kernel/rtl8733bs/prepare.py [--dest build/rtl8733bs/src] [--stock-ko FILE]
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
REPO = 'smp79/rtl8733BS_WiFi_linux_v5.15.17-113'
COMMIT = '6b522b308fb7482cb43be290d639cfdf2bfd1838'
TREE_SHA256 = '1fa8efe2f4121a88ef6d148bd892738a039e2d419d5ed22707babfd91a0c5015'
DRIVERVERSION = 'v5.15.17-113-g1924716b3.20250521_COEX20241204-390f'
FW_FILE = 'hal/rtl8733b/hal8733b_fw.c'
# Firmware the stock Q2 rtl8733bs.ko embeds (normal mode; WoWLAN is not built).
STOCK_FW = {
    'array_mp_8733b_fw_nic': '3b041ee1667b3d35',
    'ccv_array_mp_8733b_fw_nic': '32ea5199897ccf86',
}
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Q2 source preparation', 'GIT_AUTHOR_EMAIL': 'noreply@localhost',
    'GIT_COMMITTER_NAME': 'Q2 source preparation', 'GIT_COMMITTER_EMAIL': 'noreply@localhost',
    'GIT_AUTHOR_DATE': '@0 +0000', 'GIT_COMMITTER_DATE': '@0 +0000',
}


def tree_digest(top):
    h = hashlib.sha256()
    for path in sorted(p for p in top.rglob('*') if p.is_file() and '.git' not in p.relative_to(top).parts):
        rel = path.relative_to(top).as_posix()
        mode = 'x' if os.access(path, os.X_OK) else '-'
        h.update(f'{rel}\0{mode}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n'.encode())
    return h.hexdigest()


def firmware_arrays(src):
    text = (src / FW_FILE).read_text(encoding='latin-1')
    out = {}
    for m in re.finditer(r'u8\s+(\w+)\[\]\s*=\s*\{(.*?)\};', text, re.S):
        data = bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})', m.group(2)))
        out[m.group(1)] = data
    return out


def selected_patches(*,sae=False,cold=False,cached=False):
    # The later diagnostic producers replay 0008/0009 with their own
    # qualified bases. Ordinary preparation never applies those experiments.
    optional = {'0004-': sae, '0006-': cold, '0007-': cached,
                '0008-': False, '0009-': False}
    return [p for p in sorted((HERE/'patches').glob('*.patch'))
            if all(enabled or not p.name.startswith(prefix) for prefix,enabled in optional.items())]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dest', type=Path, default=Path('build/rtl8733bs/src'))
    ap.add_argument('--stock-ko', type=Path, help='also check the firmware arrays against a stock rtl8733bs.ko')
    ap.add_argument('--sae-external-auth', action='store_true',
                    help='optional unqualified SAE external-auth layout correction')
    ap.add_argument('--cold-suspend', action='store_true',
                    help='experimental cold SDIO suspend lifecycle; separate hardware qualification required')
    ap.add_argument('--cached-power-state', action='store_true',
                    help='optional read-only cached power-state diagnostics; no policy change')
    a = ap.parse_args()
    dest = (ROOT / a.dest).resolve()
    if not dest.is_relative_to(ROOT / 'build'):
        sys.exit('--dest must be under build/')
    if dest.exists():
        sys.exit(f'Refusing existing tree: {dest}')
    cache = ROOT / 'build/rtl8733bs' / f'rtl8733bs-{COMMIT[:12]}.tar.gz'
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = f'https://codeload.github.com/{REPO}/tar.gz/{COMMIT}'
        req = urllib.request.Request(url, headers={'User-Agent': 'q2-navidrome'})
        with urllib.request.urlopen(req, timeout=120) as r:
            cache.with_suffix('.download').write_bytes(r.read())
        cache.with_suffix('.download').rename(cache)

    staging = dest.with_name(dest.name + '.staging')
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(cache) as tf:
        members = []
        for m in tf.getmembers():
            parts = m.name.split('/', 1)
            if len(parts) == 2 and parts[1]:
                m.name = parts[1]
                members.append(m)
        tf.extractall(staging, members=members, filter='data')
    digest = tree_digest(staging)
    if digest != TREE_SHA256:
        shutil.rmtree(staging)
        sys.exit(f'Source tree digest {digest} does not match {TREE_SHA256}')
    version = (staging / 'include/rtw_version.h').read_text()
    if DRIVERVERSION not in version:
        sys.exit('Unexpected DRIVERVERSION')
    arrays = firmware_arrays(staging)
    for name, prefix in STOCK_FW.items():
        if hashlib.sha256(arrays[name]).hexdigest()[:16] != prefix:
            sys.exit(f'Firmware array {name} differs from the stock Q2 module')
    if a.stock_ko:
        ko = a.stock_ko.read_bytes()
        for name in STOCK_FW:
            if arrays[name] not in ko:
                sys.exit(f'{name} not found in {a.stock_ko}')
        print(f'Firmware arrays verified against {a.stock_ko}')

    def git(*args):
        subprocess.run(['git', *args], cwd=staging, check=True, env={**os.environ, **GIT_ENV},
                       stdout=subprocess.DEVNULL)
    git('init', '-q')
    git('add', '-A')
    git('-c', 'commit.gpgsign=false', 'commit', '-q', '-m', f'Realtek RTL8733BS {DRIVERVERSION} ({REPO}@{COMMIT[:12]})')
    patches = selected_patches(sae=a.sae_external_auth,cold=a.cold_suspend,cached=a.cached_power_state)
    git('-c', 'commit.gpgsign=false', 'am', '-q', '--committer-date-is-author-date', *map(str, patches))
    staging.rename(dest)
    print(f'Prepared {dest.relative_to(ROOT)}: {len(patches)} patches on {COMMIT[:12]}')


if __name__ == '__main__':
    main()
