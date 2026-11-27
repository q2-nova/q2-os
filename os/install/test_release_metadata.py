#!/usr/bin/env python3
"""The installed release record names the platform library the root installs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import release_metadata

FIELDS = dict(release_tag='V0.02', qemu_fixture=False, kernel_source_commit='1' * 40,
              app_source_commit='2' * 40, app_sha256='3' * 64, portal_helper={'sha256': '4' * 64},
              portal_phone_radio=None)


class ReleaseMetadata(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'etc').mkdir()
        (self.root / 'usr/lib').mkdir(parents=True)
        self.library = self.root / release_metadata.PLATFORM
        self.library.write_bytes(b'generated platform library')

    def test_the_record_names_the_installed_platform_library(self):
        path = release_metadata.write(self.root, release_metadata.record(self.root, **FIELDS))
        record = json.loads(path.read_text())
        self.assertEqual(record['platform_library_sha256'],
                         hashlib.sha256(b'generated platform library').hexdigest())
        self.assertEqual((record['release_tag'], record['profile'], record['hardware_qualified']),
                         ('V0.02', 'split-installed-candidate', False))
        self.assertEqual(path, self.root / 'etc/q2-os-release.json')

    def test_a_missing_or_linked_library_is_refused(self):
        self.library.unlink()
        with self.assertRaisesRegex(ValueError, 'installed platform library required'):
            release_metadata.record(self.root, **FIELDS)
        (self.root / 'real.so').write_bytes(b'generated')
        self.library.symlink_to(self.root / 'real.so')
        with self.assertRaisesRegex(ValueError, 'installed platform library required'):
            release_metadata.record(self.root, **FIELDS)

    def test_the_record_replaces_an_earlier_file(self):
        (self.root / release_metadata.PATH).write_text('{"release_tag": "old"}')
        release_metadata.write(self.root, release_metadata.record(self.root, **FIELDS))
        self.assertEqual(json.loads((self.root / release_metadata.PATH).read_text())['release_tag'], 'V0.02')


if __name__ == '__main__':
    unittest.main()
