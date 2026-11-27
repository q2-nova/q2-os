#!/usr/bin/env python3
"""Fetch and extract the RTL8733BS Bluetooth patch and configuration.

The two files are Realtek binaries that Shanling ships in the Q2 update
under /firmware/rtlbt. They are not in linux-firmware and are not committed
here (docs/bluetooth-firmware.md). This tool downloads Shanling's update ZIP
from a pinned URL, checks its pinned SHA-256, reads update.tar and
recovery-update/rootfs.squashfs from it, extracts only the two files and
checks their pinned hashes. The output directory is the input of
`os/install/build.py --bluetooth-firmware DIR`.

    python3 tools/fetch_firmware.py                        # download if not cached
    python3 tools/fetch_firmware.py --offline              # cache or --zip only
    python3 tools/fetch_firmware.py --zip 'Q2 Firmware V1.32.zip' --source shanling-q2-v1.32
    python3 tools/fetch_firmware.py --candidate coreelec-2026-07-15  # a newer build, for a device trial only

A candidate goes to its own directory. Its hashes differ from the pinned
ones, so the image build refuses it; it is for `q2nd --bt-probe --firmware DIR`.

Nothing here runs code from the archive. Extraction uses `unsquashfs`
(squashfs-tools, from PATH or the rig SDK) or `unsquashfs-backhand`
(`cargo install --locked backhand-cli`).
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'build/firmware/bluetooth'
DEFAULT_CACHE = ROOT / 'build/firmware/downloads'

# The files os/install/build.py --bluetooth-firmware accepts (name -> SHA-256).
BLUETOOTH_FIRMWARE = {
    'rtl8733bs_fw': '99603578f7f2b969e40a8156d1bdfd96f3418b4dc39770b6dc9a2eec1cb7bbe8',
    'rtl8733bs_config': 'e1bd37cbbf18362e429b05b508a3341846127c51a11565d0514b977281087297',
}
FIRMWARE_DIR = 'firmware/rtlbt'
ROOTFS = 'recovery-update/rootfs.squashfs'

# Shanling's Q2 update ZIPs. V1.36 is the one https://en.shanling.com/download/150
# links (Google Drive) on 2026-11-21; V1.32 is no longer linked and is accepted
# from a local copy only. Both carry byte-identical rtlbt files.
SOURCES = {
    'shanling-q2-v1.36': {
        'url': 'https://drive.usercontent.google.com/download?id=1HyST2-QkhUa6BdJP_ljBjkICxd-u8act&export=download&confirm=t',
        'page': 'https://en.shanling.com/download/150',
        'sha256': '4e298cab6c12d86cfc55565645f7e3ae11d3fb44677a93ca13991e7184ecec4e',
        'bytes': 50293829,
        'member': 'Q2 Firmware V1.36/update.tar',
    },
    'shanling-q2-v1.32': {
        'url': None,
        'page': 'https://en.shanling.com/download/150',
        'sha256': '154c17822d09be001be35c03d2d3488424dee195221790bd70864480d55b0f00',
        'bytes': 50059588,
        'member': 'Q2 Firmware V1.32/update.tar',
    },
}
DEFAULT_SOURCE = 'shanling-q2-v1.36'

# Newer RTL8733BS builds published elsewhere, fetched for a device trial only
# (docs/bluetooth-firmware.md). No licence file accompanies them.
CANDIDATES = {
    'coreelec-2026-07-15': {
        'base': 'https://raw.githubusercontent.com/CoreELEC/rtkbt-firmware-aml/b44fadaab4d74bd269f74d9a85854b42806a33bb/',
        'files': {
            'rtl8733bs_fw': '45cc76eb84ae075849b5b14504ebcc7132cc0a8d09ce06bd576ccc6641ad70ec',
            'rtl8733bs_config': 'efa8915db59c5bc30aaa23e1f264656bbf425fcaf091db0954c27752a1d8f7a0',
        },
    },
}
FILE_LIMIT = 1024**2
ZIP_LIMIT = 80 * 1024**2
TAR_LIMIT = 80 * 1024**2


class FetchError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def download(url, target, expected, limit=ZIP_LIMIT, opener=urllib.request.urlopen):
    """Download to target.part, check size and hash, then rename to target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    h = hashlib.sha256()
    size = 0
    try:
        with opener(url) as response, open(part, 'wb') as out:
            for block in iter(lambda: response.read(1 << 20), b''):
                size += len(block)
                if size > limit:
                    raise FetchError(f'download exceeds {limit} bytes')
                h.update(block)
                out.write(block)
        if h.hexdigest() != expected:
            raise FetchError(f'downloaded SHA-256 {h.hexdigest()} differs from the pinned {expected}')
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


def obtain(source, cache, offline, local_zip=None, opener=urllib.request.urlopen):
    """Path of a ZIP whose SHA-256 matches the source's pin."""
    pin = SOURCES[source]
    if local_zip is not None:
        if file_digest(local_zip) != pin['sha256']:
            raise FetchError(f'{local_zip}: SHA-256 differs from the pinned {source} ZIP')
        return Path(local_zip)
    cached = Path(cache) / f"{source}-{pin['sha256'][:16]}.zip"
    if cached.is_file():
        if file_digest(cached) == pin['sha256']:
            return cached
        raise FetchError(f'{cached}: cached ZIP does not match its pin; delete it')
    if offline:
        raise FetchError(f'offline and no cached {source} ZIP at {cached}; pass --zip')
    if not pin['url']:
        raise FetchError(f"{source} has no download URL (see {pin['page']}); pass --zip")
    return download(pin['url'], cached, pin['sha256'], opener=opener)


def rootfs_from_zip(zip_path, member):
    """The rootfs.squashfs bytes inside the ZIP's update.tar."""
    with zipfile.ZipFile(zip_path) as z:
        info = z.getinfo(member)
        if info.file_size > TAR_LIMIT:
            raise FetchError('update.tar is larger than expected')
        tar = z.read(info)
    with tarfile.open(fileobj=io.BytesIO(tar), mode='r:') as t:
        m = t.getmember(ROOTFS)
        if not m.isfile():
            raise FetchError(f'{ROOTFS} is not a regular file')
        data = t.extractfile(m).read()
    if data[:4] != b'hsqs':
        raise FetchError(f'{ROOTFS} is not a squashfs image')
    return data


def find_unsquashfs():
    """(kind, path) of an available squashfs extractor, or None."""
    tool = shutil.which('unsquashfs')
    if tool:
        return 'squashfs-tools', tool
    try:
        sys.path.insert(0, str(ROOT / 'tools'))
        import rig_config
        sdk = rig_config.path('sdk') / 'bin/unsquashfs'
        if sdk.is_file():
            return 'squashfs-tools', str(sdk)
    except Exception:  # an unconfigured rig is not an error here
        pass
    tool = shutil.which('unsquashfs-backhand')
    if tool:
        return 'backhand', tool
    return None


def unsquash(image, dest, extractor=None):
    """Extract FIRMWARE_DIR from the image file into dest (an empty directory)."""
    if callable(extractor):  # tests
        extractor(image, dest)
        return
    extractor = extractor or find_unsquashfs()
    if extractor is None:
        raise FetchError('no unsquashfs: install squashfs-tools or `cargo install --locked backhand-cli`')
    kind, tool = extractor
    if kind == 'squashfs-tools':
        cmd = [tool, '-no-progress', '-quiet', '-no-xattrs', '-d', str(dest), str(image),
               *(f'{FIRMWARE_DIR}/{name}' for name in BLUETOOTH_FIRMWARE)]
    else:
        cmd = [tool, '--quiet', '--path-filter', '/' + FIRMWARE_DIR, '-d', str(dest), str(image)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def extract(zip_path, member, workdir, extractor=None):
    """(stage, result): result holds the verified files; the caller removes stage."""
    stage = Path(tempfile.mkdtemp(prefix='fetch-firmware-', dir=workdir))
    try:
        image = stage / 'rootfs.squashfs'
        image.write_bytes(rootfs_from_zip(zip_path, member))
        tree = stage / 'root'
        unsquash(image, tree, extractor)
        result = stage / 'out'
        result.mkdir()
        for name, expected in BLUETOOTH_FIRMWARE.items():
            path = tree / FIRMWARE_DIR / name
            if path.is_symlink() or not path.is_file():
                raise FetchError(f'/{FIRMWARE_DIR}/{name} is missing or not a regular file')
            data = path.read_bytes()
            if digest(data) != expected:
                raise FetchError(f'/{FIRMWARE_DIR}/{name}: SHA-256 {digest(data)} differs from the pinned {expected}')
            (result / name).write_bytes(data)
            (result / name).chmod(0o644)
        return stage, result
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def install(result, out, provenance):
    """Replace out with result, so a failed run never leaves partial output."""
    (result / 'provenance.json').write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n')
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    old = out.with_name(out.name + '.old')
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)
    result.rename(out)
    shutil.rmtree(old, ignore_errors=True)


def verify(out):
    """True when out holds exactly the pinned files."""
    out = Path(out)
    return all((out / n).is_file() and file_digest(out / n) == h for n, h in BLUETOOTH_FIRMWARE.items())


def run(source=DEFAULT_SOURCE, out=DEFAULT_OUT, cache=DEFAULT_CACHE, offline=False, local_zip=None,
        extractor=None, opener=urllib.request.urlopen, force=False):
    out = Path(out)
    if not force and verify(out):
        return out, False
    zip_path = obtain(source, cache, offline, local_zip, opener)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage, result = extract(zip_path, SOURCES[source]['member'], out.parent, extractor)
    try:
        pin = SOURCES[source]
        install(result, out, {
            'source': source, 'zip_sha256': pin['sha256'], 'url': pin['url'], 'page': pin['page'],
            'member': pin['member'], 'rootfs': ROOTFS, 'path': '/' + FIRMWARE_DIR,
            'files': dict(BLUETOOTH_FIRMWARE),
            'licence': 'Realtek binary shipped by Shanling; not redistributable from this repository',
        })
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return out, True


def fetch_candidate(name, out, cache=DEFAULT_CACHE, offline=False, opener=urllib.request.urlopen):
    """Download a candidate's files (each pinned) into out; never the default output."""
    pin = CANDIDATES[name]
    out = Path(out)
    if out.resolve() == DEFAULT_OUT.resolve():
        raise FetchError('a candidate never replaces the pinned firmware directory')
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='fetch-candidate-', dir=out.parent))
    stage.chmod(0o755)
    try:
        for file, expected in pin['files'].items():
            cached = Path(cache) / f'{name}-{file}-{expected[:16]}'
            if not (cached.is_file() and file_digest(cached) == expected):
                if offline:
                    raise FetchError(f'offline and no cached {name} {file} at {cached}')
                download(pin['base'] + file, cached, expected, limit=FILE_LIMIT, opener=opener)
            shutil.copyfile(cached, stage / file)
            (stage / file).chmod(0o644)
        install(stage, out, {'candidate': name, 'url': pin['base'], 'files': dict(pin['files']),
                             'note': 'not the shipped firmware; for a device trial only'})
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--source', choices=sorted(SOURCES), default=DEFAULT_SOURCE)
    p.add_argument('--zip', type=Path, help='a local copy of the source ZIP (checked against its pin)')
    p.add_argument('--out', type=Path, default=DEFAULT_OUT)
    p.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    p.add_argument('--offline', action='store_true', help='never use the network')
    p.add_argument('--force', action='store_true', help='extract again even if out is already verified')
    p.add_argument('--candidate', choices=sorted(CANDIDATES),
                   help='fetch a newer published build into build/firmware/bluetooth-candidate/NAME instead')
    a = p.parse_args(argv)
    if a.candidate:
        try:
            out = fetch_candidate(a.candidate, a.out if a.out != DEFAULT_OUT else
                                  ROOT / 'build/firmware/bluetooth-candidate' / a.candidate, a.cache, a.offline)
        except (FetchError, OSError) as e:
            print(f'fetch_firmware: {e}', file=sys.stderr)
            return 1
        print(f'candidate {a.candidate}: {out} (not accepted by the image build)')
        return 0
    try:
        out, changed = run(a.source, a.out, a.cache, a.offline, a.zip, force=a.force)
    except (FetchError, OSError, KeyError, zipfile.BadZipFile, tarfile.TarError, subprocess.CalledProcessError) as e:
        print(f'fetch_firmware: {e}', file=sys.stderr)
        return 1
    print(f"{'extracted' if changed else 'verified'}: {out}")
    for name, h in BLUETOOTH_FIRMWARE.items():
        print(f'  {name} {h}')
    print(f'use: os/install/build.py ... --bluetooth-firmware {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
