import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from trial import validate


class ImageValidation(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / 'build/recovery/tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.path = Path(self.temp.name) / 'image.tar'

    def tearDown(self):
        self.temp.cleanup()

    def image(self, extras):
        with tarfile.open(self.path, 'w') as t:
            for name in ('oldroot', 'tmp'):
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                t.addfile(item)
            item = tarfile.TarInfo('init')
            item.size = 1
            t.addfile(item, io.BytesIO(b'x'))
            for item in extras:
                t.addfile(item, io.BytesIO(b'x' * item.size) if item.isfile() else None)

    def test_valid_image(self):
        self.image([])
        validate(self.path)

    def test_rejects_path_escape(self):
        for name in ('/etc/test', '../test', 'a/../../test', './init'):
            with self.subTest(name=name):
                self.image([tarfile.TarInfo(name)])
                with self.assertRaises(ValueError):
                    validate(self.path)

    def test_rejects_duplicate(self):
        self.image([tarfile.TarInfo('init')])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            validate(self.path)

    def test_rejects_write_through_symlink_in_either_order(self):
        link = tarfile.TarInfo('alias')
        link.type, link.linkname = tarfile.SYMTYPE, 'tmp'
        file = tarfile.TarInfo('alias/test')
        for entries in ([link, file], [file, link]):
            self.image(entries)
            with self.assertRaisesRegex(ValueError, 'through symlink'):
                validate(self.path)

    def test_rejects_escaping_symlink(self):
        for target in ('/etc', '../../etc'):
            link = tarfile.TarInfo('alias')
            link.type, link.linkname = tarfile.SYMTYPE, target
            self.image([link])
            with self.assertRaisesRegex(ValueError, 'escaping'):
                validate(self.path)

    def test_rejects_special_files_and_setid(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.LNKTYPE):
            item = tarfile.TarInfo('special')
            item.type = kind
            self.image([item])
            with self.assertRaises(ValueError):
                validate(self.path)
        item = tarfile.TarInfo('setid')
        item.mode = 0o4755
        self.image([item])
        with self.assertRaisesRegex(ValueError, 'set-id'):
            validate(self.path)

    def test_oldroot_must_be_empty(self):
        self.image([tarfile.TarInfo('oldroot/hidden')])
        with self.assertRaisesRegex(ValueError, 'empty'):
            validate(self.path)

    def test_budget(self):
        self.path.write_bytes(b'')
        with self.path.open('r+b') as f:
            f.truncate(12 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, 'budget'):
            validate(self.path)


if __name__ == '__main__':
    unittest.main()
