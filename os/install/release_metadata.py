"""The installed root's /etc/q2-os-release.json: what the split composer built.

record() names the release, its kernel, the paired player and portal helper,
and the SHA-256 of the installed platform library (usr/lib/libq2platform.so)
as platform_library_sha256. A reader on the device compares the running
library with that digest: q2-factory releases/scorecard.py collect refuses
a release whose record does not name the library it runs.

write() replaces the file in place; the root's etc must be a real directory.
"""
import hashlib
import json
from pathlib import Path

PATH = 'etc/q2-os-release.json'
PLATFORM = 'usr/lib/libq2platform.so'


def digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'installed platform library required: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(root, *, release_tag, qemu_fixture, kernel_source_commit, app_source_commit, app_sha256,
           portal_helper, portal_phone_radio):
    return {'profile': 'split-installed-candidate', 'release_tag': release_tag,
            'qemu_fixture': qemu_fixture, 'kernel_source_commit': kernel_source_commit,
            'hardware_qualified': False, 'private_configuration_imported': False,
            'app_source_commit': app_source_commit, 'app_sha256': app_sha256, 'portal_helper': portal_helper,
            'portal_phone_radio': portal_phone_radio,
            'platform_library_sha256': digest(Path(root) / PLATFORM)}


def write(root, value):
    path = Path(root) / PATH
    if path.parent.resolve() != path.parent:
        raise RuntimeError('release metadata requires a canonical directory')
    path.unlink(missing_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')
    return path
