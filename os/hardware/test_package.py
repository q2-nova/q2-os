"""Host fault tests with generated payloads; never device firmware/config."""
import importlib.util
import io
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location('package', Path(__file__).with_name('package.py'))
p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p)


def kernel(comp=0):
    payload = b'generated test fixture' * 10
    h = struct.pack('>7I4B32s', 0x27051956, 0, 0, len(payload), 0x82000000, 0x82000000,
                    zlib.crc32(payload), 5, 5, 2, comp, b'fixture')
    return h[:4] + struct.pack('>I', zlib.crc32(h)) + h[8:] + payload


def rootfs():
    b = bytearray(128); b[:4] = b'hsqs'
    struct.pack_into('<HH', b, 28, 4, 0); struct.pack_into('<Q', b, 40, 128)
    return bytes(b)


class PackageTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).resolve().parents[2] / 'build/os/hardware/tests'
        runtime.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=runtime); self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d/'kernel').write_bytes(kernel()); (self.d/'rootfs').write_bytes(rootfs())
        self.pkg = self.d/'update.tar'
        self.report = p.build_package(self.d/'kernel', self.d/'rootfs', self.pkg, 'V0.01', 'V1.32')

    def test_valid_stage_is_exact_and_durable(self):
        card = self.d/'card'; card.mkdir()
        p.stage(self.pkg, card, self.report['update_tar_sha256'])
        self.assertEqual(sorted(x.name for x in (card/'recovery-update').iterdir()), ['rootfs.squashfs', 'xImage'])
        self.assertEqual((card/'recovery-update/xImage').read_bytes(), kernel())
        with self.assertRaises(ValueError): p.stage(self.pkg, card, self.report['update_tar_sha256'])

    def test_stale_lock_refuses(self):
        (self.d/'.q2-update-stage.lock').touch()
        with self.assertRaises(FileExistsError): p.stage(self.pkg, self.d, self.report['update_tar_sha256'])
        self.assertFalse((self.d/'recovery-update').exists())

    def test_wrong_sha_does_not_stage(self):
        with self.assertRaises(ValueError): p.stage(self.pkg, self.d, '0'*64)
        self.assertFalse((self.d/'recovery-update').exists())

    def test_gzip_is_not_spl_executable(self):
        with self.assertRaisesRegex(ValueError, 'compression'): p.uimage(kernel(1))

    def test_payload_and_header_corruption(self):
        for offset in [12, 80]:
            b = bytearray(kernel()); b[offset] ^= 1
            with self.assertRaises(ValueError): p.uimage(b)

    def test_bad_rootfs_bounds_and_version(self):
        for offset in [0, 28, 40]:
            b = bytearray(rootfs()); b[offset] = 255
            with self.assertRaises(ValueError): p.squashfs(b)

    def test_same_version_rejected(self):
        with self.assertRaises(ValueError): p.build_package(self.d/'kernel', self.d/'rootfs', self.d/'next.tar', 'V0.01', 'V0.01')

    def test_symlink_destination_refused(self):
        (self.d/'recovery-update').symlink_to(self.d/'absent')
        with self.assertRaises(ValueError): p.stage(self.pkg, self.d, self.report['update_tar_sha256'])
        self.assertFalse((self.d/'absent').exists())

    def test_archive_attack_members(self):
        good, _ = p.read_package(self.pkg)
        for name, kind in [('../escape', tarfile.REGTYPE), (p.KERNEL, tarfile.REGTYPE),
                           ('evil', tarfile.SYMTYPE), ('/absolute', tarfile.REGTYPE),
                           ('recovery-update/extra', tarfile.REGTYPE)]:
            attack = self.d/'attack.tar'
            with tarfile.open(attack, 'w') as t:
                for n, b in good.items():
                    m=tarfile.TarInfo(n); m.size=len(b); t.addfile(m,io.BytesIO(b))
                m=tarfile.TarInfo(name); m.type=kind; m.linkname='../escape'; m.size=1 if kind==tarfile.REGTYPE else 0
                t.addfile(m,io.BytesIO(b'x'))
            with self.assertRaises(ValueError): p.read_package(attack)

    def test_manifest_mismatch(self):
        blobs, _ = p.read_package(self.pkg)
        blobs[p.INFO] = blobs[p.INFO].replace(b'Shanling', b'Invalid')
        with self.assertRaises(ValueError): p.validate(blobs)


if __name__ == '__main__': unittest.main()
