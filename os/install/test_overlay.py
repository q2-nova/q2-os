"""The installed root carries this checkout's os/overlay, not a stale Buildroot copy."""
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('install_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def overlay_files():
    return sorted(path.relative_to(build.OVERLAY) for path in build.OVERLAY.rglob('*')
                  if path.is_file() or path.is_symlink())


class OverlayTest(unittest.TestCase):
    def test_stale_target_receives_checkout_overlay(self):
        supervisor = Path('etc/init.d/S79q2-supervisor')
        self.assertIn(supervisor, overlay_files())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'root'
            # A target built at an earlier commit: an older supervisor start
            # script, one overlay file absent and a package file of its own.
            stale = root / supervisor
            stale.parent.mkdir(parents=True)
            stale.write_text('#!/bin/sh\n# generated stale start script\nsleep 0.1\n')
            stale.chmod(0o700)
            package = root / 'usr/bin/generated-package-tool'
            package.parent.mkdir(parents=True)
            package.write_bytes(b'generated package file\n')
            self.assertNotEqual(stale.read_bytes(), (build.OVERLAY / supervisor).read_bytes())

            build.apply_overlay(root)

            for name in overlay_files():
                source, installed = build.OVERLAY / name, root / name
                with self.subTest(path=str(name)):
                    if source.is_symlink():
                        self.assertEqual(os.readlink(installed), os.readlink(source))
                        continue
                    self.assertEqual(installed.read_bytes(), source.read_bytes())
                    self.assertEqual(stat.S_IMODE(installed.stat().st_mode), stat.S_IMODE(source.stat().st_mode))
            self.assertEqual(package.read_bytes(), b'generated package file\n')

    def test_post_build_writes_no_overlay_path(self):
        # apply_overlay repeats Buildroot's overlay step after post-build ran;
        # that is equivalent only while post-build leaves overlay files alone.
        post_build = (ROOT / 'os/post-build.py').read_text()
        for name in overlay_files():
            with self.subTest(path=str(name)):
                self.assertNotIn(str(name), post_build)


if __name__ == '__main__':
    unittest.main()
