"""Install the q2-portal binary paired with the target player build."""
import hashlib
import json
from pathlib import Path
import stat
import subprocess

NAME = 'q2-portal'
SOURCE = 'crates/portal/Cargo.toml'  # in the q2-player checkout
INSTALLED = 'usr/libexec/q2-network/q2-portal'
PROTOCOL = 1
CHECKPOINT_FILES = (NAME, 'symbols/q2-portal/receipt.json',
                    'symbols/q2-portal/q2-portal.unstripped', 'symbols/q2-portal/q2-portal.debug')


def required(root, commit=None):
    if commit is None:
        return (root / SOURCE).is_file()
    return subprocess.run(['git', '-C', root, 'cat-file', '-e', f'{commit}:{SOURCE}'],
                          capture_output=True).returncode == 0


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def admit(binary, symbols, commit):
    receipt = symbols / 'receipt.json'
    if receipt.resolve() != receipt.absolute() or not receipt.is_file():
        raise ValueError('q2-portal companion build receipt required')
    record = json.loads(receipt.read_text())
    if (record.get('passed') is not True or record.get('allocated_sections_unchanged') is not True
            or record.get('debuglink_verified') is not True
            or record.get('provenance', {}).get('source_commit') != commit):
        raise ValueError('q2-portal companion must come from the paired source build')
    paths = {'deployed': binary, 'unstripped': symbols / (NAME + '.unstripped'),
             'debug': symbols / (NAME + '.debug')}
    for name, path in paths.items():
        identity = record.get('artifacts', {}).get(name, {})
        if (path.resolve() != path.absolute() or not path.is_file() or Path(identity.get('path', '')).name != path.name
                or identity.get('sha256') != digest(path) or identity.get('bytes') != path.stat().st_size):
            raise ValueError('missing or changed q2-portal artifact: ' + name)
    return {'protocol': PROTOCOL, 'sha256': record['artifacts']['deployed']['sha256'],
            'symbols_receipt_sha256': digest(receipt)}


def install(root, binary, record):
    """Return whether the paired helper is a new path or has changed bytes."""
    target = root / INSTALLED
    if target.parent.resolve() != target.parent.absolute() or target.is_symlink():
        raise ValueError('q2-portal destination must be a regular path')
    payload = binary.read_bytes()
    if hashlib.sha256(payload).hexdigest() != record['sha256']:
        raise ValueError('q2-portal companion changed before copying')
    if target.exists():
        if not target.is_file() or stat.S_IMODE(target.stat().st_mode) != 0o755:
            raise ValueError('installed q2-portal must be a regular 0755 file')
        if digest(target) == record['sha256']:
            return False
        target.write_bytes(payload)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(payload)
        target.chmod(0o755)
    return True


def paired_full_build(app):
    """Accept the two output layouts produced by the maintained target builders."""
    if app.parent.name == 'artifacts':
        symbols = app.parent.parent / 'symbols'
        player_receipt = symbols / 'q2nd/receipt.json'
    else:
        symbols = app.parent / 'symbols'
        player_receipt = symbols / 'receipt.json'
    if player_receipt.is_symlink() or not player_receipt.is_file():
        raise ValueError('paired q2nd and q2-portal target build with symbols required')
    record = json.loads(player_receipt.read_text())
    ident = record.get('artifacts', {}).get('deployed', {})
    commit = record.get('provenance', {}).get('source_commit')
    if (record.get('passed') is not True or not commit or app.resolve() != app.absolute() or not app.is_file()
            or Path(ident.get('path', '')).name != app.name or ident.get('sha256') != digest(app)
            or ident.get('bytes') != app.stat().st_size):
        raise ValueError('q2nd artifact differs from its paired target build')
    binary = app.parent / NAME
    return binary, admit(binary, symbols / NAME, commit), commit, ident['sha256']


def admit_app_only(os_release):
    record = json.loads(os_release.read_text()).get('portal_helper')
    target = os_release.parent.parent / INSTALLED
    if (not isinstance(record, dict) or record.get('protocol') != PROTOCOL
            or target.resolve() != target.absolute() or not target.is_file()
            or stat.S_IMODE(target.stat().st_mode) != 0o755
            or digest(target) != record.get('sha256')):
        raise ValueError('app-only update requires an installed q2-portal protocol 1 helper; compose a full OS image first')
