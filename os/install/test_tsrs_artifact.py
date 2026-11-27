#!/usr/bin/env python3
"""Exercise the installer admission boundary with disposable standalone outputs."""
import json
from pathlib import Path
import tempfile
import unittest

import tsrs_artifact


class StandaloneDaemon(unittest.TestCase):
    def setUp(self):
        staging = tempfile.TemporaryDirectory()
        self.addCleanup(staging.cleanup)
        self.fixture = Path(staging.name)
        self.bundle = self.fixture / 'standalone-output'
        (self.bundle / 'symbols').mkdir(parents=True)
        self.binary = b'\x7fELF\x01\x01\x01' + bytes(9) + b'\x02\x00\x08\x00' + bytes(32)
        symbols = b'{"fixture": "symbols"}'
        sizes = b'{"fixture": "size"}'
        (self.bundle / 'tsrsd').write_bytes(self.binary)
        (self.bundle / 'symbols/receipt.json').write_bytes(symbols)
        (self.bundle / 'size.json').write_bytes(sizes)
        pin = json.loads(tsrs_artifact.PIN.read_text())
        self.record = {
            **{key: pin[key] for key in ('source_commit', 'tailscale_rs_commit', 'patch_sha256')},
            'passed': True,
            'target': 'q2',
            'bytes': len(self.binary),
            'q2_sha256': tsrs_artifact.sha256(self.binary),
            'symbols_receipt_sha256': tsrs_artifact.sha256(symbols),
            'size_sha256': tsrs_artifact.sha256(sizes),
        }
        self.write_record()

    def write_record(self):
        (self.bundle / 'result.json').write_text(json.dumps(self.record))

    def test_admit_and_install_exact_bytes(self):
        binary, provenance = tsrs_artifact.admit(self.bundle)
        root = self.fixture / 'image'
        (self.bundle / 'tsrsd').write_bytes(b'changed after admission')
        tsrs_artifact.install(binary, root)
        staged = root / tsrs_artifact.DESTINATION
        self.assertEqual(staged.read_bytes(), self.binary)
        self.assertEqual(staged.stat().st_mode & 0o777, 0o755)
        self.assertEqual(provenance['source_commit'], self.record['source_commit'])

    def test_install_refuses_outside_destination(self):
        root = self.fixture / 'image'
        (root / 'usr').mkdir(parents=True)
        (root / 'usr/libexec').symlink_to(self.fixture, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'escapes the image root'):
            tsrs_artifact.install(self.binary, root)

    def test_wrong_source_commit(self):
        self.record['source_commit'] = '0' * 40
        self.write_record()
        with self.assertRaisesRegex(ValueError, 'source_commit differs'):
            tsrs_artifact.admit(self.bundle)

    def test_wrong_recipient_despite_matching_digest(self):
        wrong = self.binary[:18] + b'\x03\x00' + self.binary[20:]
        (self.bundle / 'tsrsd').write_bytes(wrong)
        self.record['q2_sha256'] = tsrs_artifact.sha256(wrong)
        self.write_record()
        with self.assertRaisesRegex(ValueError, 'ELF32 MIPS'):
            tsrs_artifact.admit(self.bundle)

    def test_modified_binary(self):
        (self.bundle / 'tsrsd').write_bytes(self.binary[:-1] + b'X')
        with self.assertRaisesRegex(ValueError, 'q2_sha256'):
            tsrs_artifact.admit(self.bundle)

    def test_modified_symbols_receipt(self):
        (self.bundle / 'symbols/receipt.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'symbols_receipt_sha256'):
            tsrs_artifact.admit(self.bundle)

    def test_binary_symlink(self):
        binary = self.bundle / 'tsrsd'
        outside = self.fixture / 'outside'
        binary.rename(outside)
        binary.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'regular tsrsd'):
            tsrs_artifact.admit(self.bundle)


if __name__ == '__main__':
    unittest.main()
