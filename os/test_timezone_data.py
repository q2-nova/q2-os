#!/usr/bin/env python3
"""Packaging regressions use generated public TZif-shaped files, never settings."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from timezone_data import ROOT, required_zones, validate


class TimezoneData(unittest.TestCase):
    def setUp(self):
        output = ROOT / "build"
        output.mkdir(exist_ok=True)
        fixture = TemporaryDirectory(prefix="timezone-data-test.", dir=output)
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.directory = self.root / "usr/share/zoneinfo"
        for zone in required_zones():
            path=self.directory/zone;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'TZif'+bytes(40))

    def test_all_public_iana_zones_and_utc_are_required(self):
        result=validate(self.root)
        self.assertIn('America/Chicago',result)
        self.assertIn('Etc/UTC',result)
        self.assertIn('Asia/Kathmandu', result)
        self.assertIn('America/Coyhaique', result)
        self.assertNotIn('CST6',result)

    def test_missing_chicago_is_a_build_failure(self):
        (self.directory/'America/Chicago').unlink()
        with self.assertRaisesRegex(ValueError,'America/Chicago'):validate(self.root)

    def test_corrupt_zone_is_a_build_failure(self):
        (self.directory/'America/Chicago').write_bytes(b'not tzdata')
        with self.assertRaisesRegex(ValueError,'America/Chicago'):validate(self.root)

    def test_relative_in_tree_tzif_alias_is_allowed(self):
        path=self.directory/'America/Chicago';path.unlink();path.symlink_to('../Etc/UTC')
        validate(self.root)

    def test_alias_cannot_escape_to_host_zoneinfo(self):
        path=self.directory/'America/Chicago';path.unlink();path.symlink_to('/usr/share/zoneinfo/America/Chicago')
        with self.assertRaisesRegex(ValueError,'America/Chicago'):validate(self.root)


if __name__=='__main__':unittest.main()
