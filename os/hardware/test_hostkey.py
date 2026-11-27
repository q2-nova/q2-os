"""Host-key validation tests with keys generated at run time.

Every key is made by ssh-keygen (or the target's dropbearkey) in a fresh
temporary directory outside the repository and removed afterwards; no key
is read from or written to the checkout. The target-reader test runs the
image's dropbearkey under qemu-user when both are present (Q2_HOSTKEY_TEST_TARGET
and Q2_HOSTKEY_TEST_QEMU, default build/os/out/target and
build/qemu/qemu-mipsel-static) and is skipped otherwise.

  python3 os/hardware/test_hostkey.py
"""
import base64
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('q2_hostkey', Path(__file__).with_name('hostkey.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
TARGET = Path(os.environ.get('Q2_HOSTKEY_TEST_TARGET', ROOT / 'build/os/out/target'))
QEMU = Path(os.environ.get('Q2_HOSTKEY_TEST_QEMU', ROOT / 'build/qemu/qemu-mipsel-static'))


def string(data):
    return struct.pack('>I', len(data)) + data


def read_string(data, offset):
    size, = struct.unpack_from('>I', data, offset)
    return data[offset + 4:offset + 4 + size], offset + 4 + size


def dropbear_ed25519(openssh_private):
    """The native Dropbear form of an unencrypted OpenSSH Ed25519 private key:
    string "ssh-ed25519", string (32-byte seed || 32-byte public key)."""
    lines = openssh_private.decode().strip().splitlines()
    data = base64.b64decode(''.join(lines[1:-1]))
    if not data.startswith(b'openssh-key-v1\0'):
        raise ValueError('not an OpenSSH private key')
    offset = len(b'openssh-key-v1\0')
    cipher, offset = read_string(data, offset)
    _, offset = read_string(data, offset)  # KDF name
    _, offset = read_string(data, offset)  # KDF options
    if cipher != b'none' or struct.unpack_from('>I', data, offset)[0] != 1:
        raise ValueError('expected one unencrypted key')
    _, offset = read_string(data, offset + 4)  # public key blob
    private, _ = read_string(data, offset)
    offset = 8  # two check integers
    kind, offset = read_string(private, offset)
    public, offset = read_string(private, offset)
    secret, _ = read_string(private, offset)
    if kind != b'ssh-ed25519' or len(public) != 32 or len(secret) != 64 or secret[32:] != public:
        raise ValueError('unexpected Ed25519 key layout')
    return string(kind) + string(secret)


def keygen_fingerprint(public_line):
    result = subprocess.run(['ssh-keygen', '-l', '-E', 'sha256', '-f', '/dev/stdin'], input=public_line.encode(),
                            capture_output=True, check=True)
    return result.stdout.decode().split()[1]


class HostKey(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='q2-hostkey-test-')
        self.root = Path(self.temporary.name)
        if self.root.resolve().is_relative_to(ROOT):
            self.skipTest('the system temporary directory is inside this checkout')
        self.target = self.root / 'target'
        (self.target / 'usr/bin').mkdir(parents=True)
        (self.target / 'usr/bin/dropbearkey').touch()
        self.qemu = self.root / 'qemu'
        self.qemu.touch()
        self.openssh = self.root / 'openssh'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'generated', '-f', str(self.openssh)],
                       check=True)
        self.public_line = self.openssh.with_suffix('.pub').read_text().split()
        self.secret = self.openssh.read_text().strip().splitlines()[1]
        self.key = self.root / 'key'
        self.key.write_bytes(dropbear_ed25519(self.openssh.read_bytes()))
        self.key.chmod(0o600)

    def tearDown(self):
        self.temporary.cleanup()

    def validate(self, key=None):
        return module.validate_dropbear_host_key(key or self.key, self.target, self.qemu)

    def reader_output(self):
        return f'Public key portion is:\n{self.public_line[0]} {self.public_line[1]} root@q2\nFingerprint: sha1!! x\n'

    def test_openssh_key_rejected_before_target_execution(self):
        with patch.object(module.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'OpenSSH format'):
                self.validate(self.openssh)
            run.assert_not_called()

    def test_native_key_read_by_target_command_returns_only_public_fingerprint(self):
        completed = subprocess.CompletedProcess([], 0, self.reader_output(), self.secret)
        with patch.object(module.subprocess, 'run', return_value=completed) as run:
            result = self.validate()
        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ['-y', '-f', self.key.resolve()])
        self.assertIn(self.target / 'usr/bin/dropbearkey', command)
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(result, {'algorithm': 'ssh-ed25519',
                                  'public_fingerprint': keygen_fingerprint(' '.join(self.public_line[:2])),
                                  'validated_by_actual_target': True})
        self.assertNotIn(self.secret, str(result))

    def test_target_rejection_and_timeout_never_echo_key_material(self):
        for response in (subprocess.CompletedProcess([], 1, self.secret, self.secret),
                         subprocess.TimeoutExpired([self.secret], 10)):
            side_effect = response if isinstance(response, Exception) else None
            with patch.object(module.subprocess, 'run', side_effect=side_effect, return_value=response):
                with self.assertRaises(ValueError) as caught:
                    self.validate()
            self.assertNotIn(self.secret, str(caught.exception))

    def test_malformed_native_key_and_missing_tools_fail_closed(self):
        native = self.key.read_bytes()
        for data in (b'', b'-----BEGIN RSA PRIVATE KEY-----', struct.pack('>I', 999) + native[4:],
                     string(b'ssh-unknown') + native[15:]):
            self.key.write_bytes(data)
            with patch.object(module.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    self.validate()
                run.assert_not_called()
        self.key.write_bytes(native)
        self.qemu.unlink()
        with self.assertRaisesRegex(ValueError, 'qemu-mipsel'):
            self.validate()

    def test_ambiguous_or_wrong_public_output_fails_closed(self):
        line = ' '.join(self.public_line[:2])
        for text in ('ssh-rsa ' + self.public_line[1], line + '\n' + line, 'ssh-ed25519 !invalid!', ''):
            with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, text, '')):
                with self.assertRaises(ValueError):
                    self.validate()

    def test_both_builders_reject_openssh_before_creating_an_image(self):
        self.openssh.chmod(0o600)
        work = ROOT / 'build/hostkey-tests'
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as inside:
            source = Path(inside) / 'src'
            (source / '.git').mkdir(parents=True)
            common = ['--kernel-source', source, '--buildroot-target', self.target,
                      '--authorized-key', self.openssh.with_suffix('.pub'), '--host-key', self.openssh]
            for script, extra in (('os/hardware/build-player.py', []),
                                  ('os/install/build.py', ['--app', self.root / 'unused', '--player-source', self.root / 'unused',
                                                           '--wifi-tools', self.root / 'unused', '--tsrsd', self.root / 'unused'])):
                out = Path(inside) / ('out-' + Path(script).parent.name)
                result = subprocess.run([sys.executable, ROOT / script, *map(str, common + extra), '--output', str(out)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('OpenSSH format', result.stderr)
                self.assertNotIn(self.secret, result.stderr)
                self.assertFalse(out.exists(), 'a bad host key must fail before the image is touched')


@unittest.skipUnless((TARGET / 'usr/bin/dropbearkey').is_file() and QEMU.is_file(),
                     'needs a Buildroot target with dropbearkey and qemu-mipsel (Q2_HOSTKEY_TEST_TARGET, Q2_HOSTKEY_TEST_QEMU)')
class TargetReader(unittest.TestCase):
    def test_target_generated_key_validates_with_its_own_fingerprint(self):
        with tempfile.TemporaryDirectory(prefix='q2-hostkey-test-') as temporary:
            key = Path(temporary) / 'hostkey'
            generated = module.dropbearkey(TARGET, QEMU, '-t', 'ed25519', '-f', key)
            self.assertEqual(generated.returncode, 0)
            line = next(l for l in generated.stdout.splitlines() if l.startswith('ssh-ed25519 '))
            result = module.validate_dropbear_host_key(key, TARGET, QEMU)
            self.assertEqual(result['algorithm'], 'ssh-ed25519')
            self.assertEqual(result['public_fingerprint'], keygen_fingerprint(' '.join(line.split()[:2])))

    def test_converted_ssh_keygen_key_is_read_by_target(self):
        with tempfile.TemporaryDirectory(prefix='q2-hostkey-test-') as temporary:
            openssh = Path(temporary) / 'openssh'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'generated', '-f', str(openssh)],
                           check=True)
            key = Path(temporary) / 'hostkey'
            key.write_bytes(dropbear_ed25519(openssh.read_bytes()))
            result = module.validate_dropbear_host_key(key, TARGET, QEMU)
            public = ' '.join(openssh.with_suffix('.pub').read_text().split()[:2])
            self.assertEqual(result['public_fingerprint'], keygen_fingerprint(public))


if __name__ == '__main__':
    unittest.main()
