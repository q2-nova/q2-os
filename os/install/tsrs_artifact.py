"""Admit a standalone tsrs Q2 build without executing its checkout."""
import hashlib
import json
from pathlib import Path

PIN = Path(__file__).with_name('tsrs.json')
DESTINATION = Path('usr/libexec/q2-network/tsrsd')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def admit(directory):
    """Return the verified daemon bytes and their installed-image provenance."""
    directory = directory.resolve()
    pin = json.loads(PIN.read_text())

    def read(name):
        source = directory / name
        if (source.is_symlink() or not source.is_file()
                or not source.resolve().is_relative_to(directory)):
            raise ValueError(f'tsrsd output must contain a regular {name}')
        return source.read_bytes()

    raw_record = read('result.json')
    record = json.loads(raw_record)
    if not isinstance(record, dict) or record.get('passed') is not True or record.get('target') != 'q2':
        raise ValueError('tsrsd requires a passing standalone tsrs Q2 build')
    for key in ('source_commit', 'tailscale_rs_commit', 'patch_sha256'):
        if record.get(key) != pin[key]:
            raise ValueError(f'tsrsd {key} differs from the OS pin')
    binary = read('tsrsd')
    if len(binary) < 52 or binary[:7] != b'\x7fELF\x01\x01\x01' or binary[18:20] != b'\x08\x00':
        raise ValueError('tsrsd must be a little-endian ELF32 MIPS binary')
    if binary[16:18] not in (b'\x02\x00', b'\x03\x00'):
        raise ValueError('tsrsd must be an executable ELF')
    if record.get('bytes') != len(binary):
        raise ValueError('tsrsd binary length differs from its build record')
    for key, raw in (('q2_sha256', binary), ('symbols_receipt_sha256', read('symbols/receipt.json')),
                     ('size_sha256', read('size.json'))):
        if record.get(key) != sha256(raw):
            raise ValueError(f'tsrsd build record mismatch for {key}')
    return binary, {
        'sha256': record['q2_sha256'],
        'bytes': len(binary),
        'source_commit': pin['source_commit'],
        'tailscale_rs_commit': pin['tailscale_rs_commit'],
        'patch_sha256': pin['patch_sha256'],
        'record_sha256': sha256(raw_record),
    }


def install(binary, root):
    target = root / DESTINATION
    if not target.parent.resolve().is_relative_to(root.resolve()):
        raise ValueError('tsrsd installation directory escapes the image root')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    target.write_bytes(binary)
    target.chmod(0o755)
