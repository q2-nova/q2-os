"""Third-party notices of the Rust binaries an installed image carries.

Each binary's build writes its notices and records their SHA-256. q2-player's
tools/build-target.py writes NAME.THIRD-PARTY-NOTICES beside q2nd and
q2-portal and records them in result.json under `third_party_notices`; tsrs
`build.py q2` writes licenses/THIRD-PARTY-NOTICES and records `notices_sha256`
(os/install/tsrs_artifact.py admits it). install() copies the admitted bytes to
usr/share/licenses/NAME/THIRD-PARTY-NOTICES; validate() refuses an image root
that has one of the binaries without its notices.
"""
import hashlib
import json

import runtime_resources

FILE = 'THIRD-PARTY-NOTICES'
# Installed binary of each notices file.
BINARIES = {'q2nd': 'usr/bin/q2nd', 'q2-portal': 'usr/libexec/q2-network/q2-portal',
            'tsrsd': 'usr/libexec/q2-network/tsrsd'}
PLAYER = ('q2nd', 'q2-portal')


def destination(name):
    return f'usr/share/licenses/{name}/{FILE}'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(directory, name):
    path = directory / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f'{directory}: regular {name} required')
    return path.read_bytes()


def player(app, app_sha256, portal_sha256):
    """{name: bytes} of the notices of the paired q2nd/q2-portal build whose q2nd is `app`."""
    directory = app.parent
    record = json.loads(read(directory, 'result.json'))
    artifacts = record.get('artifacts', {})
    if (artifacts.get('q2nd', {}).get('sha256') != app_sha256
            or artifacts.get('q2-portal', {}).get('sha256') != portal_sha256):
        raise ValueError('the paired build result.json describes another q2nd or q2-portal')
    files = (record.get('third_party_notices') or {}).get('files', {})
    result = {}
    for name in PLAYER:
        entry = files.get(name) or {}
        if entry.get('path') != f'{name}.{FILE}':
            raise ValueError(f'the paired build records no third-party notices for {name}; '
                             'build it with a q2-player that writes them')
        data = read(directory, entry['path'])
        if not data.strip() or digest(data) != entry.get('sha256'):
            raise ValueError(f'{name} third-party notices differ from the paired build record')
        result[name] = data
    return result


def install(root, notices):
    """Copy {name: bytes} to the image root; return {installed path: SHA-256}."""
    for name, data in notices.items():
        if name not in BINARIES:
            raise ValueError('notices of an unknown binary: ' + name)
        path = destination(name)
        runtime_resources.parent(root, path)
        target = root / path
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('regular notices destination required: ' + path)
        target.write_bytes(data)
        target.chmod(0o644)
    return {destination(name): digest(data) for name, data in sorted(notices.items())}


def validate(root, expected):
    """Refuse a root where a binary is installed without its notices or the notices differ from `expected`."""
    result = {}
    for name, binary in BINARIES.items():
        path = destination(name)
        if not ((root / binary).is_file() or (root / binary).is_symlink()):
            if (root / path).exists() or path in expected:
                raise ValueError(f'third-party notices without their binary: {path}')
            continue
        try:
            target = runtime_resources.regular(root, path)
        except ValueError:
            raise ValueError(f'{binary} is installed without its third-party notices ({path})') from None
        if target.stat().st_mode & 0o7777 != 0o644:
            raise ValueError('third-party notices must be 0644: ' + path)
        actual = digest(target.read_bytes())
        if path not in expected:
            raise ValueError(f'{binary} is installed without admitted third-party notices ({path})')
        if actual != expected[path]:
            raise ValueError('installed third-party notices differ from the admitted ones: ' + path)
        result[path] = actual
    if set(result) != set(expected):
        raise ValueError('installed third-party notices differ from the admitted set')
    return result
