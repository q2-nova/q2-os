#!/usr/bin/env python3
"""Audit explicit generated image refreshes without accessing hardware.

Inputs must be generated build trees, never a mounted device or owner data.
Only declared regular-file replacements and release metadata may change. Kernel,
recovery, modules, symlinks and modes retain their baseline identities. Platform
replacement requires a separate explicit before/after digest pair; otherwise it
also remains unchanged. This audit does not prove platform ABI or runtime behavior.
The report contains identities and paths, never file contents or credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat

ARTIFACTS = ('xImage', 'vmlinux', 'shanling-q2.dtb', 'rescue.cpio',
             'rootfs.squashfs', 'update.tar')
IMMUTABLE = ARTIFACTS[:4]
METADATA = 'etc/q2-os-release.json'
PLATFORM = 'usr/lib/libq2platform.so'


def valid_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_relative(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('non-canonical relative path')
    return path


def regular(root, name):
    path = root
    for part in safe_relative(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink in required regular path: ' + name)
    if not path.is_file():
        raise ValueError('missing regular file: ' + name)
    return path


def inventory(root):
    result = {'.': ('directory', stat.S_IMODE(root.lstat().st_mode), '')}

    def visit(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                result[name] = ('link', stat.S_IMODE(mode), str(path.readlink()))
            elif stat.S_ISDIR(mode):
                result[name] = ('directory', stat.S_IMODE(mode), '')
                visit(path)
            elif stat.S_ISREG(mode):
                result[name] = ('file', stat.S_IMODE(mode), digest(path))
            else:
                raise ValueError('unsupported generated root inode: ' + name)
    visit(root)
    return result


def load_image(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('generated image directory required')
    manifest = json.loads(regular(directory, 'artifact.json').read_text())
    for name in ARTIFACTS:
        path = regular(directory, name)
        record = manifest['artifacts'][name]
        if digest(path) != record['sha256'] or path.stat().st_size != record['bytes']:
            raise ValueError('artifact differs from manifest: ' + name)
    root = directory / 'root'
    if root.is_symlink() or not root.is_dir():
        raise ValueError('generated root directory required')
    return manifest, root


def audit(base, candidate, replacements, *, platform_before=None, platform_after=None):
    before, base_root = load_image(Path(base))
    after, next_root = load_image(Path(candidate))
    if before['release_tag'] == after['release_tag']:
        raise ValueError('refresh requires a distinct release tag')
    platform_change = None
    if platform_before is not None or platform_after is not None:
        if not valid_digest(platform_before) or not valid_digest(platform_after) or platform_before == platform_after:
            raise ValueError('platform replacement requires distinct before/after digests')
        if (before['platform_library_sha256'], after['platform_library_sha256']) != (platform_before, platform_after):
            raise ValueError('platform replacement differs from explicit before/after identities')
        if replacements.get(PLATFORM) != platform_after:
            raise ValueError('platform replacement must also be an explicit root replacement')
        platform_change = dict(before_sha256=platform_before, after_sha256=platform_after,
                               abi_verified=False, runtime_verified=False)
    for name in IMMUTABLE:
        if before['artifacts'][name] != after['artifacts'][name]:
            raise ValueError('kernel/recovery artifact changed: ' + name)
    for name, expected in replacements.items():
        safe_relative(name)
        if name == METADATA or not valid_digest(expected):
            raise ValueError('invalid replacement declaration')
        if digest(regular(next_root, name)) != expected:
            raise ValueError('replacement differs from declared identity: ' + name)
    a, b = inventory(base_root), inventory(next_root)
    changed = sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))
    if set(changed) != set(replacements) | {METADATA}:
        raise ValueError('root changes differ from explicit replacement set')
    for name in changed:
        # This narrow refresh tool intentionally cannot change tree structure or
        # permissions. A full builder is required for those broader changes.
        if name not in a or name not in b or a[name][:2] != b[name][:2] or b[name][0] != 'file':
            raise ValueError('refresh changed inode type/mode or added/removed a file: ' + name)
    for manifest, root in ((before, base_root), (after, next_root)):
        info = json.loads(regular(root, METADATA).read_text())
        actual_app = digest(regular(root, 'usr/bin/q2nd'))
        if info['release_tag'] != manifest['release_tag'] or info['app_sha256'] != actual_app or manifest['app_sha256'] != actual_app:
            raise ValueError('release/app identity mismatch')
        if digest(regular(root, PLATFORM)) != manifest['platform_library_sha256']:
            raise ValueError('platform identity mismatch')
        for name, expected in manifest['modules'].items():
            if digest(regular(root, name)) != expected:
                raise ValueError('module identity mismatch: ' + name)
    if before['modules'] != after['modules']:
        raise ValueError('modules/platform changed in narrow refresh: modules')
    if platform_change is None and before['platform_library_sha256'] != after['platform_library_sha256']:
        raise ValueError('modules/platform changed in narrow refresh: platform requires explicit identities')
    return dict(schema=1, passed=True, scope='generated-image-refresh-audit',
                base_release=before['release_tag'], release=after['release_tag'],
                base_manifest_sha256=digest(Path(base) / 'artifact.json'),
                manifest_sha256=digest(Path(candidate) / 'artifact.json'),
                app_sha256=after['app_sha256'], changed_root_paths=changed,
                replacements=replacements, immutable_artifacts=list(IMMUTABLE),
                platform_change=platform_change,
                hardware=False, packed_root_equivalence_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--replace', action='append', default=[], metavar='PATH=SHA256')
    parser.add_argument('--platform-before', help='explicit baseline platform SHA256; requires --platform-after and --replace')
    parser.add_argument('--platform-after', help='explicit replacement platform SHA256; does not certify ABI/runtime')
    args = parser.parse_args()
    build = Path(__file__).resolve().parents[2] / 'build'
    for directory in (args.base, args.candidate):
        if not directory.resolve().is_relative_to(build):
            parser.error('use generated image directories under this checkout build/')
    replacements = {}
    for item in args.replace:
        name, separator, value = item.partition('=')
        if not separator or name in replacements:
            parser.error('each replacement requires a unique PATH=SHA256')
        replacements[name] = value
    try:
        print(json.dumps(audit(args.base, args.candidate, replacements,
                               platform_before=args.platform_before, platform_after=args.platform_after), indent=2))
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, 'Refresh audit failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
