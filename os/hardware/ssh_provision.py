"""Admit explicit external SSH inputs and stage the installed SSH profile."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from hostkey import dropbearkey, validate_dropbear_host_key

AUTHORIZED_KEYS = 'root/.ssh/authorized_keys'
HOST_KEY = 'etc/q2-bootstrap/hostkey'
CLIENT_ALGORITHMS = {'ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
                     'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521'}


def external_key(path, *, private=False):
    """Read bounded regular files outside Git worktrees, with redacted errors."""
    try:
        original = path.absolute()
        resolved = original.resolve(strict=True)
        for candidate in (original, resolved):
            if any((parent / '.git').exists() for parent in candidate.parents):
                raise ValueError('SSH key inputs must be outside Git worktrees')
            git = subprocess.run(['git', '-C', str(candidate.parent), 'rev-parse',
                                  '--absolute-git-dir'], capture_output=True)
            if git.returncode == 0:
                raise ValueError('SSH key inputs must be outside Git worktrees')
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            mode = os.fstat(source.fileno()).st_mode
            if not stat.S_ISREG(mode) or (private and mode & 0o077):
                raise ValueError('SSH keys must be regular files and private keys must be owner-only')
            data = source.read(8193)
        if not data or len(data) > 8192:
            raise ValueError('SSH key input has an invalid size')
        return data
    except OSError:
        raise ValueError('cannot access supplied SSH key input') from None


def client_public_key(path):
    try:
        lines = external_key(path).decode('utf-8').strip().splitlines()
    except UnicodeError:
        raise ValueError('provide one plain SSH client public key') from None
    fields = lines[0].split() if len(lines) == 1 else []
    if len(fields) < 2 or fields[0] not in CLIENT_ALGORITHMS:
        raise ValueError('provide one plain SSH client public key')
    # Ignore the input comment, which can contain a developer's name or hostname.
    key = f'{fields[0]} {fields[1]}'.encode() + b'\n'
    result = subprocess.run(['ssh-keygen', '-lf', '/dev/stdin'], input=key, capture_output=True)
    if result.returncode:
        raise ValueError('invalid SSH client public key')
    return key


def host_identity(path, target, qemu):
    with tempfile.TemporaryDirectory(prefix='q2-host-identity-') as temporary:
        key = Path(temporary) / 'hostkey'
        if path is None:
            if dropbearkey(target, qemu, '-t', 'ed25519', '-f', key).returncode:
                raise ValueError('actual target Dropbear could not generate a host key')
        else:
            write_key(key, external_key(path, private=True))
        receipt = validate_dropbear_host_key(key, target, qemu)
        return key.read_bytes(), receipt


def write_key(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)


class InstalledSsh:
    def __init__(self, public_key, host_key, target, qemu):
        if public_key is None and host_key is not None:
            raise ValueError('--host-key requires --authorized-key for a personal image')
        self.public_key = client_public_key(public_key) if public_key else None
        self.host_key = None
        self.receipt = {'profile': 'disabled'}
        if public_key is not None:
            self.host_key, identity = host_identity(host_key, target, qemu)
            self.receipt = {'profile': 'personal',
                            'client_public_key_sha256': hashlib.sha256(self.public_key).hexdigest(),
                            'host_identity': identity}

    def stage(self, tree):
        for parent in ('root', 'etc'):
            if (tree / parent).is_symlink():
                raise ValueError('SSH staging requires canonical root and etc directories')
        # The input target and integration overlay cannot provision these paths.
        for relative in ('root/.ssh', 'etc/q2-bootstrap', 'etc/dropbear'):
            path = tree / relative
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        if self.public_key is None:
            return
        for relative, data in ((AUTHORIZED_KEYS, self.public_key), (HOST_KEY, self.host_key)):
            path = tree / relative
            path.parent.mkdir(mode=0o700)
            write_key(path, data)
