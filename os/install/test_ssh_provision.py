#!/usr/bin/env python3
"""Exercise installed SSH staging, packing, and boot admission with generated keys."""
import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'os/hardware'))
from ssh_provision import AUTHORIZED_KEYS, HOST_KEY, InstalledSsh, client_public_key


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def pack_fixture(tree, out, rescue):
    if rescue:
        entries = b'\0'.join(str(path.relative_to(tree)).encode()
                              for path in sorted(tree.rglob('*'))) + b'\0'
        archive = out / 'rescue.cpio'
        with archive.open('wb') as stream:
            run(['cpio', '--quiet', '--null', '-o', '-H', 'newc', '--reproducible'],
                input=entries, stdout=stream, cwd=tree)
        unpacked = out / 'rescue-extracted'
        unpacked.mkdir()
        with archive.open('rb') as stream:
            run(['cpio', '--quiet', '-id'], stdin=stream, cwd=unpacked)
        return unpacked
    mount = lambda path: Path('/work') / path.relative_to(ROOT)
    command = ['podman', 'run', '--rm', '--network', 'none', '--security-opt',
               'label=disable', '-v', f'{ROOT}:/work', 'q2-os-build']
    archive = out / 'rootfs.squashfs'
    unpacked = out / 'normal-extracted'
    run(command + ['mksquashfs', mount(tree), mount(archive), '-noappend',
                   '-all-root', '-no-xattrs', '-processors', '1', '-quiet', '-no-progress'])
    run(command + ['unsquashfs', '-processors', '1', '-quiet', '-no-progress',
                   '-d', mount(unpacked), mount(archive)])
    return unpacked


class Provision(unittest.TestCase):
    def setUp(self):
        external = tempfile.TemporaryDirectory(prefix='q2-ssh-fixture-')
        self.addCleanup(external.cleanup)
        self.external = Path(external.name)
        (ROOT / 'build').mkdir(exist_ok=True)
        local = tempfile.TemporaryDirectory(prefix='ssh-test-', dir=ROOT / 'build')
        self.addCleanup(local.cleanup)
        self.local = Path(local.name)
        self.target, self.runner = CONFIG.target, CONFIG.qemu_user
        if CONFIG.native_dropbearkey:
            self.target = self.local / 'target'
            reader = self.target / 'usr/bin/dropbearkey'
            reader.parent.mkdir(parents=True)
            reader.symlink_to(CONFIG.native_dropbearkey)
            self.runner = self.local / 'native-reader'
            self.runner.write_text('#!/bin/sh\nshift 4\nexec "$@"\n')
            self.runner.chmod(0o700)

    def client(self, name):
        private = self.external / name
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
             'synthetic-private-comment', '-f', private])
        return private.with_suffix('.pub')

    def packaging(self, name, provision):
        output = self.local / name
        output.mkdir(mode=0o700)
        unpacked = []
        for kind in ('normal', 'rescue'):
            tree = output / kind
            for directory in ('root/.ssh', 'etc/q2-bootstrap', 'etc/dropbear'):
                (tree / directory).mkdir(parents=True, exist_ok=True)
            for filename in (AUTHORIZED_KEYS, HOST_KEY, 'etc/dropbear/dropbear_rsa_host_key'):
                (tree / filename).write_bytes(b'synthetic stale input')
            provision.stage(tree)
            helper = tree / 'sbin/q2-diagnostic-ssh'
            helper.parent.mkdir()
            shutil.copyfile(HERE / 'diagnostic-ssh', helper)
            helper.chmod(0o755)
            unpacked.append(pack_fixture(tree, output, kind == 'rescue'))
        return unpacked

    def launch(self, root, expected):
        # Redirect absolute paths for the host fixture; capture the final daemon argv.
        script = (root / 'sbin/q2-diagnostic-ssh').read_text()
        transport = self.local / 'transport'
        transport.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        transport.chmod(0o700)
        for path, replacement in (
                (f'/{AUTHORIZED_KEYS}', root / AUTHORIZED_KEYS),
                (f'/{HOST_KEY}', root / HOST_KEY),
                ('/usr/sbin/dropbear', transport)):
            script = script.replace(path, str(replacement))
        result = run(['sh'], input=script, text=True, capture_output=True)
        command = ['-F', '-E', '-s', '-g', '-r', str(root / HOST_KEY), '-p', '192.168.77.2:22']
        self.assertEqual(result.stdout.splitlines(), command if expected else [])

    def native_login(self, root, public_key, other_public_key):
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        key_tool = CONFIG.native_dropbearkey
        private_host = root / HOST_KEY
        identity = run([key_tool, '-y', '-f', private_host], capture_output=True, text=True)
        public_host = next(line.split()[:2] for line in identity.stdout.splitlines()
                           if line.startswith('ssh-ed25519 '))
        known_hosts = self.external / 'known_hosts'
        known_hosts.write_text(f'[127.0.0.1]:{port} ' + ' '.join(public_host) + '\n')
        mount = ['--security-opt', 'label=disable', '-v', f'{root / "root/.ssh"}:/root/.ssh:ro',
                 '-v', f'{private_host}:/generated-host:ro', '-v', f'{CONFIG.native_dropbear}:/test-dropbear:ro']
        if CONFIG.native_lib_dir:
            mount += ['-v', f'{CONFIG.native_lib_dir}:/test-libs:ro', '-e', 'LD_LIBRARY_PATH=/test-libs']
        server = run(['podman', 'run', '-d', '--rm', '--network', 'host', *mount, 'q2-os-build',
                      '/test-dropbear', '-F', '-E', '-s', '-g', '-r', '/generated-host',
                      '-p', f'127.0.0.1:{port}'], text=True, capture_output=True).stdout.strip()
        try:
            for _ in range(50):
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail('generated Dropbear listener did not become ready')
            command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                       '-o', 'IdentityAgent=none', '-o', 'GlobalKnownHostsFile=/dev/null',
                       '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known_hosts}',
                       '-o', 'ConnectTimeout=2', '-p', str(port)]
            for key, allowed in ((public_key, True), (other_public_key, False)):
                check = subprocess.run([*command, '-i', key.with_suffix(''), 'root@127.0.0.1',
                                        'printf Q2_GENERATED_SSH_OK'],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(check.returncode, 0 if allowed else 255)
                if allowed:
                    self.assertEqual(check.stdout, 'Q2_GENERATED_SSH_OK')
                else:
                    self.assertIn('Permission denied', check.stderr)
        finally:
            subprocess.run(['podman', 'stop', '-t', '1', server], capture_output=True)

    def test_keyless_archives_and_boot_admission(self):
        provision = InstalledSsh(None, None, None, None)
        self.assertEqual(provision.receipt, {'profile': 'disabled'})
        for root in self.packaging('keyless', provision):
            for name in (AUTHORIZED_KEYS, HOST_KEY, 'etc/dropbear/dropbear_rsa_host_key'):
                self.assertFalse((root / name).exists(), name)
            self.launch(root, False)

    def test_distinct_personal_archives(self):
        clients = [self.client(name) for name in ('a', 'b')]
        provisions = [InstalledSsh(key, None, self.target, self.runner) for key in clients]
        self.assertNotEqual(provisions[0].receipt['host_identity'],
                            provisions[1].receipt['host_identity'])
        self.assertNotEqual(provisions[0].receipt['client_public_key_sha256'],
                            provisions[1].receipt['client_public_key_sha256'])
        for index, provision in enumerate(provisions):
            for root in self.packaging(f'personal-{index}', provision):
                public_file, host_file = root / AUTHORIZED_KEYS, root / HOST_KEY
                self.assertEqual(public_file.read_bytes(), client_public_key(clients[index]))
                self.assertNotEqual(public_file.read_bytes(), client_public_key(clients[1 - index]))
                self.assertEqual(host_file.read_bytes(), provision.host_key)
                self.assertFalse((root / 'etc/dropbear/dropbear_rsa_host_key').exists())
                self.assertNotIn(b'synthetic-private-comment', public_file.read_bytes())
                for path in (public_file, host_file):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.launch(root, True)
                if CONFIG.native_dropbear:
                    self.native_login(root, clients[index], clients[1 - index])
                public_file.unlink()
                self.launch(root, False)
        existing = self.external / 'generated-host'
        existing.write_bytes(provisions[0].host_key)
        existing.chmod(0o600)
        override = InstalledSsh(clients[1], existing, self.target, self.runner)
        self.assertEqual(override.receipt['host_identity'], provisions[0].receipt['host_identity'])
        self.assertNotIn(str(self.external), json.dumps(override.receipt))
        existing.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'owner-only'):
            InstalledSsh(clients[0], existing, self.target, self.runner)

    def test_cli_rejects_tracked_client_key_before_build(self):
        key = self.client('tracked')
        run(['git', '-C', self.external, 'init', '-q'])
        run(['git', '-C', self.external, 'add', key])
        result = subprocess.run([sys.executable, HERE / 'build.py', '--authorized-key', key,
            '--kernel-source', 'unused', '--player-source', 'unused', '--buildroot-target', 'unused', '--app', 'unused',
            '--platform-library', 'unused', '--wifi-tools', 'unused'],
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('SSH key inputs must be outside Git worktrees', result.stderr)
        self.assertNotIn(str(key), result.stderr)
        self.assertNotIn(key.read_text().split()[1], result.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=lambda path: Path(path).resolve())
    parser.add_argument('--qemu-user', type=lambda path: Path(path).resolve())
    parser.add_argument('--native-dropbearkey', type=lambda path: Path(path).resolve(),
                        help='real host Dropbear reader when the SDK is still building')
    parser.add_argument('--native-dropbear', type=lambda path: Path(path).resolve(),
                        help='also authenticate to real host Dropbear over isolated loopback')
    parser.add_argument('--native-lib-dir', type=lambda path: Path(path).resolve(),
                        help='package library directory for native Dropbear inside the test container')
    CONFIG = parser.parse_args()
    if CONFIG.native_dropbear and not CONFIG.native_dropbearkey:
        parser.error('--native-dropbear requires --native-dropbearkey')
    if not CONFIG.native_dropbearkey and not (CONFIG.target and CONFIG.qemu_user):
        parser.error('provide --target and --qemu-user, or --native-dropbearkey')
    unittest.main(argv=[sys.argv[0]], verbosity=2)
