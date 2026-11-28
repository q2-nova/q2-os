#!/usr/bin/env python3
"""Admission, installation and the image check of the Rust binaries' third-party notices."""
import json
from pathlib import Path
import tempfile
import unittest

import third_party_notices as notices


class Notices(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.build = self.base / 'paired-player'
        self.build.mkdir()
        self.app = self.build / 'q2nd'
        self.app.write_bytes(b'q2nd')
        self.texts = {name: f'Third-party software notices: {name}\n'.encode() for name in notices.PLAYER}
        for name, data in self.texts.items():
            (self.build / f'{name}.{notices.FILE}').write_bytes(data)
        self.record = {'artifacts': {'q2nd': {'sha256': 'a' * 64}, 'q2-portal': {'sha256': 'b' * 64}},
                       'third_party_notices': {'tool': 'cargo-about 0.9.3', 'files': {
                           name: {'path': f'{name}.{notices.FILE}', 'sha256': notices.digest(data)}
                           for name, data in self.texts.items()}}}
        self.write()
        self.root = self.base / 'root'
        for binary in ('usr/bin/q2nd', 'usr/libexec/q2-network/q2-portal', 'usr/libexec/q2-network/tsrsd'):
            (self.root / binary).parent.mkdir(parents=True, exist_ok=True)
            (self.root / binary).write_bytes(b'ELF')

    def write(self):
        (self.build / 'result.json').write_text(json.dumps(self.record))

    def admit(self):
        return notices.player(self.app, 'a' * 64, 'b' * 64)

    def test_player_notices_admitted_installed_and_validated(self):
        admitted = self.admit()
        self.assertEqual(admitted, self.texts)
        admitted['tsrsd'] = b'Third-party software notices: tsrsd\n'
        hashes = notices.install(self.root, admitted)
        self.assertEqual(set(hashes), {f'usr/share/licenses/{name}/THIRD-PARTY-NOTICES'
                                       for name in ('q2nd', 'q2-portal', 'tsrsd')})
        installed = self.root / 'usr/share/licenses/q2-portal/THIRD-PARTY-NOTICES'
        self.assertEqual(installed.read_bytes(), self.texts['q2-portal'])
        self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
        self.assertEqual(notices.validate(self.root, hashes), hashes)

    def test_build_without_notices_refused(self):
        del self.record['third_party_notices']
        self.write()
        with self.assertRaisesRegex(ValueError, 'records no third-party notices for q2nd'):
            self.admit()

    def test_changed_notices_refused(self):
        (self.build / f'q2-portal.{notices.FILE}').write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError, 'q2-portal third-party notices differ'):
            self.admit()

    def test_record_of_another_build_refused(self):
        with self.assertRaisesRegex(ValueError, 'another q2nd'):
            notices.player(self.app, 'c' * 64, 'b' * 64)

    def test_binary_without_notices_fails_the_image_check(self):
        hashes = notices.install(self.root, self.admit())
        with self.assertRaisesRegex(ValueError, 'tsrsd is installed without its third-party notices'):
            notices.validate(self.root, hashes)
        (self.root / 'usr/libexec/q2-network/tsrsd').unlink()
        self.assertEqual(notices.validate(self.root, hashes), hashes)
        (self.root / 'usr/share/licenses/q2nd/THIRD-PARTY-NOTICES').write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError, 'differ from the admitted ones'):
            notices.validate(self.root, hashes)

    def test_notices_without_binary_fail_the_image_check(self):
        admitted = dict(self.admit(), tsrsd=b'tsrsd notices\n')
        hashes = notices.install(self.root, admitted)
        (self.root / 'usr/libexec/q2-network/tsrsd').unlink()
        with self.assertRaisesRegex(ValueError, 'without their binary'):
            notices.validate(self.root, hashes)

    def test_symlinked_destination_refused(self):
        (self.root / 'usr/share').mkdir(parents=True)
        (self.root / 'usr/share/licenses').symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink|regular'):
            notices.install(self.root, self.admit())


if __name__ == '__main__':
    unittest.main()
