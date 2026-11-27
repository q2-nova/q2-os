#!/usr/bin/env python3
"""Admit, pair and install the q2-portal companion from generated build outputs."""
import json
from pathlib import Path
import tempfile
import unittest

import portal_artifact as portal

METADATA = 'etc/q2-os-release.json'


class PortalArtifact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.image = self.workspace / 'root'
        self.image.mkdir()
        self.build = self.workspace / 'target'
        self.build.mkdir()
        self.binary = self.build / portal.NAME
        self.binary.write_bytes(b'generated target helper')
        self.symbols = self.build / 'symbols' / portal.NAME
        self.symbols.mkdir(parents=True)
        self.receipt = self.symbols / 'receipt.json'
        self.commit = 'f' * 40

    def write_receipt(self):
        full = self.symbols / (portal.NAME + '.unstripped')
        full.write_bytes(b'generated original')
        debug = self.symbols / (portal.NAME + '.debug')
        debug.write_bytes(b'generated debug')
        self.receipt.write_text(json.dumps(dict(
            passed=True, allocated_sections_unchanged=True, debuglink_verified=True,
            provenance={'source_commit': self.commit},
            artifacts={name: {'path': str(path), 'sha256': portal.digest(path), 'bytes': path.stat().st_size}
                       for name, path in [('deployed', self.binary), ('unstripped', full), ('debug', debug)]},
        )))

    def test_missing_wrong_source_and_tampered_build_artifacts_refuse(self):
        with self.assertRaisesRegex(ValueError, 'companion build receipt required'):
            portal.admit(self.binary, self.symbols, self.commit)
        self.write_receipt()
        with self.assertRaisesRegex(ValueError, 'paired source'):
            portal.admit(self.binary, self.symbols, 'a' * 40)
        portal.admit(self.binary, self.symbols, self.commit)
        self.binary.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed q2-portal artifact'):
            portal.admit(self.binary, self.symbols, self.commit)

    def test_paired_full_build_binds_player_and_helper(self):
        self.write_receipt()
        app = self.build / 'q2nd'
        app.write_bytes(b'generated player')
        (self.build / 'symbols/receipt.json').write_text(json.dumps(dict(
            passed=True, provenance={'source_commit': self.commit},
            artifacts={'deployed': {'path': str(app), 'sha256': portal.digest(app), 'bytes': app.stat().st_size}},
        )))
        binary, record, commit, digest = portal.paired_full_build(app)
        self.assertEqual((binary, commit, digest), (self.binary, self.commit, portal.digest(app)))
        self.assertEqual(record, {'protocol': portal.PROTOCOL, 'sha256': portal.digest(self.binary),
                                  'symbols_receipt_sha256': portal.digest(self.receipt)})
        app.write_bytes(b'changed player')
        with self.assertRaisesRegex(ValueError, 'q2nd artifact differs'):
            portal.paired_full_build(app)

    def test_install_adds_then_reuses_or_replaces_helper(self):
        self.write_receipt()
        record = portal.admit(self.binary, self.symbols, self.commit)
        self.assertTrue(portal.install(self.image, self.binary, record))
        self.assertFalse(portal.install(self.image, self.binary, record))
        installed = self.image / portal.INSTALLED
        self.assertEqual(installed.stat().st_mode & 0o7777, 0o755)
        self.assertEqual(portal.digest(installed), record['sha256'])
        self.binary.write_bytes(b'new helper')
        with self.assertRaisesRegex(ValueError, 'changed before copying'):
            portal.install(self.image, self.binary, record)
        self.write_receipt()
        replacement = portal.admit(self.binary, self.symbols, self.commit)
        self.assertTrue(portal.install(self.image, self.binary, replacement))
        self.assertEqual(portal.digest(installed), replacement['sha256'])

    def test_app_only_update_requires_installed_protocol_and_matching_bytes(self):
        self.write_receipt()
        record = portal.admit(self.binary, self.symbols, self.commit)
        metadata = self.image / METADATA
        metadata.parent.mkdir()
        metadata.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'compose a full OS image first'):
            portal.admit_app_only(metadata)
        portal.install(self.image, self.binary, record)
        metadata.write_text(json.dumps({'portal_helper': dict(record, protocol=2)}))
        with self.assertRaisesRegex(ValueError, 'compose a full OS image first'):
            portal.admit_app_only(metadata)
        metadata.write_text(json.dumps({'portal_helper': record}))
        portal.admit_app_only(metadata)
        (self.image / portal.INSTALLED).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'compose a full OS image first'):
            portal.admit_app_only(metadata)


if __name__ == '__main__':
    unittest.main()
