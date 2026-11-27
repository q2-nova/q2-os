#!/usr/bin/env python3
"""Controls for tools/fetch_firmware.py with a fake source:
python3 -m unittest tools/test_fetch_firmware.py"""
import hashlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fetch_firmware as ff  # noqa: E402

FW = b'Realtech' + bytes(range(256)) * 4
CONFIG = b'\x55\xab\x23\x87' + bytes(29)
FAKE_FILES = {'rtl8733bs_fw': hashlib.sha256(FW).hexdigest(),
              'rtl8733bs_config': hashlib.sha256(CONFIG).hexdigest()}
MEMBER = 'Q2 Firmware VTEST/update.tar'
# Under build/, not /tmp: the /tmp quota here is small.
TMP = ff.ROOT / 'build/test-tmp'


def fake_zip(rootfs):
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode='w') as t:
        for name, data in [('firmware_v20.info', b'Shanling Q2\nVTEST\n'), (ff.ROOTFS, rootfs)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(MEMBER, tar.getvalue())
    return out.getvalue()


def tree_extractor(files):
    """A stand-in for unsquashfs that writes `files` under dest."""
    def extract(image, dest):
        assert pathlib.Path(image).read_bytes()[:4] == b'hsqs'
        for name, data in files.items():
            path = pathlib.Path(dest) / ff.FIRMWARE_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(data, str):
                path.symlink_to(data)
            else:
                path.write_bytes(data)
    return extract


class Opener:
    """A fake urlopen that serves fixed bytes and counts calls."""
    def __init__(self, data):
        self.data, self.calls = data, 0

    def __call__(self, url):
        self.calls += 1
        return io.BytesIO(self.data)


class FetchTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.zip = fake_zip(b'hsqs' + bytes(92))
        source = {'url': 'https://example.invalid/q2.zip', 'page': 'https://example.invalid/',
                  'sha256': hashlib.sha256(self.zip).hexdigest(), 'bytes': len(self.zip), 'member': MEMBER}
        patches = [patch.dict(ff.SOURCES, {'fake': source}),
                   patch.dict(ff.BLUETOOTH_FIRMWARE, FAKE_FILES, clear=True)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.out = self.dir / 'out'
        self.cache = self.dir / 'cache'
        self.good = tree_extractor({'rtl8733bs_fw': FW, 'rtl8733bs_config': CONFIG})

    def tearDown(self):
        shutil.rmtree(self.dir)

    def fetch(self, opener=None, **kw):
        return ff.run('fake', self.out, self.cache, extractor=kw.pop('extractor', self.good),
                      opener=opener or Opener(self.zip), **kw)

    def test_download_caches_extracts_and_verifies(self):
        opener = Opener(self.zip)
        out, changed = self.fetch(opener)
        self.assertTrue(changed)
        self.assertEqual((out / 'rtl8733bs_fw').read_bytes(), FW)
        self.assertEqual((out / 'rtl8733bs_config').read_bytes(), CONFIG)
        self.assertTrue((out / 'provenance.json').is_file())
        self.assertEqual(len(list(self.cache.iterdir())), 1)
        # A verified output is reused; the cache serves a forced re-extraction offline.
        self.assertEqual(self.fetch(opener), (out, False))
        self.assertEqual(self.fetch(opener, offline=True, force=True), (out, True))
        self.assertEqual(opener.calls, 1)

    def test_offline_without_cache_never_opens_the_network(self):
        opener = Opener(self.zip)
        with self.assertRaisesRegex(ff.FetchError, 'offline'):
            self.fetch(opener, offline=True)
        self.assertEqual(opener.calls, 0)
        self.assertFalse(self.out.exists())

    def test_a_local_zip_is_used_offline_after_its_hash_is_checked(self):
        local = self.dir / 'local.zip'
        local.write_bytes(self.zip)
        self.fetch(Opener(b''), offline=True, local_zip=local)
        self.assertTrue(ff.verify(self.out))
        local.write_bytes(self.zip + b'x')
        with self.assertRaisesRegex(ff.FetchError, 'differs'):
            self.fetch(offline=True, local_zip=local, force=True)

    def test_a_download_with_the_wrong_hash_leaves_no_cache(self):
        with self.assertRaisesRegex(ff.FetchError, 'differs from the pinned'):
            self.fetch(Opener(self.zip[:-1] + bytes([self.zip[-1] ^ 1])))
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertFalse(self.out.exists())

    def test_an_oversized_download_is_cut_off(self):
        with self.assertRaisesRegex(ff.FetchError, 'exceeds'):
            ff.download('u', self.cache / 'x.zip', 'h', limit=10, opener=Opener(self.zip))
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_a_corrupted_cache_is_refused(self):
        self.fetch()
        cached = next(self.cache.iterdir())
        cached.write_bytes(b'not the zip')
        with self.assertRaisesRegex(ff.FetchError, 'cached ZIP'):
            self.fetch(force=True)

    def test_unexpected_firmware_keeps_the_previous_output(self):
        self.fetch()
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        for files, message in [({'rtl8733bs_fw': FW + b'x', 'rtl8733bs_config': CONFIG}, 'differs'),
                               ({'rtl8733bs_fw': FW}, 'missing'),
                               ({'rtl8733bs_fw': '/etc/passwd', 'rtl8733bs_config': CONFIG}, 'not a regular file')]:
            with self.subTest(message), self.assertRaisesRegex(ff.FetchError, message):
                self.fetch(force=True, extractor=tree_extractor(files))
            self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()}, before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['cache', 'out'], 'no stage left behind')

    def test_a_rootfs_that_is_not_squashfs_is_refused(self):
        self.zip = fake_zip(b'ELF!' + bytes(92))
        ff.SOURCES['fake']['sha256'] = hashlib.sha256(self.zip).hexdigest()
        with self.assertRaisesRegex(ff.FetchError, 'not a squashfs'):
            self.fetch()

    def test_the_command_line_reports_failure(self):
        with patch('sys.stderr', io.StringIO()) as err:
            code = ff.main(['--source', 'shanling-q2-v1.32', '--offline', '--out', str(self.out),
                            '--cache', str(self.cache)])
        self.assertEqual(code, 1)
        self.assertIn('pass --zip', err.getvalue())

    def test_a_real_squashfs_through_unsquashfs(self):
        extractor = ff.find_unsquashfs()
        mksquashfs = extractor and pathlib.Path(extractor[1]).with_name('mksquashfs')
        if not (extractor and extractor[0] == 'squashfs-tools' and mksquashfs.is_file()):
            self.skipTest('no mksquashfs beside unsquashfs')
        tree = self.dir / 'tree'
        (tree / ff.FIRMWARE_DIR).mkdir(parents=True)
        (tree / ff.FIRMWARE_DIR / 'rtl8733bs_fw').write_bytes(FW)
        (tree / ff.FIRMWARE_DIR / 'rtl8733bs_config').write_bytes(CONFIG)
        (tree / 'bin').mkdir()
        (tree / 'bin/other').write_bytes(b'not extracted')
        image = self.dir / 'image.sqfs'
        subprocess.run([str(mksquashfs), str(tree), str(image), '-noappend', '-all-root', '-no-xattrs',
                        '-no-progress', '-quiet', '-comp', 'gzip'], check=True, stdout=subprocess.DEVNULL)
        self.zip = fake_zip(image.read_bytes())
        ff.SOURCES['fake']['sha256'] = hashlib.sha256(self.zip).hexdigest()
        out, _ = self.fetch(extractor=extractor)
        self.assertTrue(ff.verify(out))
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['provenance.json', 'rtl8733bs_config', 'rtl8733bs_fw'])


class CandidateTest(unittest.TestCase):
    def setUp(self):
        TMP.mkdir(parents=True, exist_ok=True)
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=TMP))
        self.addCleanup(shutil.rmtree, self.dir)
        pin = {'base': 'https://example.invalid/fw/', 'files': dict(FAKE_FILES)}
        p = patch.dict(ff.CANDIDATES, {'fake': pin})
        p.start()
        self.addCleanup(p.stop)

    def opener(self, files):
        calls = []
        def urlopen(url):
            calls.append(url)
            return io.BytesIO(files[url.rsplit('/', 1)[1]])
        return urlopen, calls

    def test_a_candidate_is_fetched_by_hash_and_then_served_offline(self):
        urlopen, calls = self.opener({'rtl8733bs_fw': FW, 'rtl8733bs_config': CONFIG})
        out = ff.fetch_candidate('fake', self.dir / 'cand', self.dir / 'cache', opener=urlopen)
        self.assertEqual((out / 'rtl8733bs_fw').read_bytes(), FW)
        self.assertEqual(len(calls), 2)
        ff.fetch_candidate('fake', self.dir / 'cand', self.dir / 'cache', offline=True, opener=urlopen)
        self.assertEqual(len(calls), 2)

    def test_a_changed_candidate_is_refused_and_leaves_nothing(self):
        urlopen, _ = self.opener({'rtl8733bs_fw': FW + b'x', 'rtl8733bs_config': CONFIG})
        with self.assertRaisesRegex(ff.FetchError, 'differs'):
            ff.fetch_candidate('fake', self.dir / 'cand', self.dir / 'cache', opener=urlopen)
        self.assertFalse((self.dir / 'cand').exists())

    def test_a_candidate_never_replaces_the_pinned_directory(self):
        with self.assertRaisesRegex(ff.FetchError, 'never replaces'):
            ff.fetch_candidate('fake', ff.DEFAULT_OUT, self.dir / 'cache', offline=True)


@unittest.skipUnless(os.environ.get('Q2_STOCK_ZIP'), 'set Q2_STOCK_ZIP to a stock Q2 update ZIP')
class StockZipTest(unittest.TestCase):
    def test_the_stock_zip_yields_the_pinned_files(self):
        path = pathlib.Path(os.environ['Q2_STOCK_ZIP'])
        digest = ff.file_digest(path)
        source = next(name for name, pin in ff.SOURCES.items() if pin['sha256'] == digest)
        TMP.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            out, _ = ff.run(source, pathlib.Path(d) / 'out', pathlib.Path(d) / 'cache', offline=True, local_zip=path)
            self.assertTrue(ff.verify(out))


if __name__ == '__main__':
    unittest.main()
