"""Validate an explicitly supplied bootstrap key with the actual target reader.

Private input and target diagnostics never enter build output. Only public-key
algorithm and SHA256 fingerprint are returned. No conversion is performed.
"""
import base64
import hashlib
from pathlib import Path
import struct
import subprocess

ALGORITHMS = {'ssh-ed25519', 'ssh-rsa', 'ssh-dss',
    'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'}


def validate_dropbear_host_key(key: Path, target: Path, qemu: Path) -> dict:
    try:
        with key.open('rb') as source:
            prefix = source.read(68)
    except OSError:
        raise ValueError('cannot read the supplied bootstrap host key') from None
    if prefix.startswith(b'-----BEGIN OPENSSH'):
        raise ValueError('bootstrap host key is OpenSSH format; convert the generated key with '
            'dropbearconvert openssh dropbear INPUT OUTPUT before building')
    size = struct.unpack('>I', prefix[:4])[0] if len(prefix) >= 4 else 0
    try:
        algorithm = prefix[4:4 + size].decode('ascii') if 0 < size <= 64 else ''
    except UnicodeError:
        algorithm = ''
    if algorithm not in ALGORITHMS:
        raise ValueError('bootstrap host key is not a supported native Dropbear private key')
    result = dropbearkey(target, qemu, '-y', '-f', key.resolve())
    if result.returncode:
        raise ValueError('actual target Dropbear rejected the bootstrap host key')
    public = [line.split() for line in result.stdout.splitlines()
        if line.split() and line.split()[0] in ALGORITHMS]
    if len(public) != 1 or len(public[0]) < 2 or public[0][0] != algorithm:
        raise ValueError('actual target Dropbear did not return the expected public key')
    try:
        blob = base64.b64decode(public[0][1], validate=True)
    except (ValueError, TypeError):
        raise ValueError('actual target Dropbear returned an invalid public key') from None
    fingerprint = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip('=')
    return {'algorithm': algorithm, 'public_fingerprint': 'SHA256:' + fingerprint,
        'validated_by_actual_target': True}


def dropbearkey(target: Path, qemu: Path, *args):
    reader = target / 'usr/bin/dropbearkey'
    if not qemu.is_file() or not reader.is_file():
        raise ValueError('host identity provisioning requires qemu-mipsel-static and the generated '
                         'target dropbearkey; use --qemu-user to select the emulator')
    try:
        command = [qemu.resolve(), '-cpu', 'P5600', '-L', target.resolve(), reader.absolute(), *args]
        return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('actual target Dropbear host-key operation could not run') from None
