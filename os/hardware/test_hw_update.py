"""Native fault tests: test build replaces only MTD/reboot and mount gating.
Production still gets compiled with -Werror and no injectable device paths.
"""
import hashlib
import fcntl
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from test_package import kernel, rootfs, p

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT/'build/os/update-tests'


class UpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.tool = WORK/'q2-hw-update'
        for target, flags in [(cls.tool, ['-DQ2_UPDATE_TEST']), (WORK/'production-host-check', [])]:
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',*flags,
                            str(ROOT/'os/hardware/update/q2-hw-update.c'),'-o',str(target)],check=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK); self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name); self.card = self.d/'card'; self.card.mkdir()
        (self.d/'kernel').write_bytes(kernel()); (self.d/'rootfs').write_bytes(rootfs())
        self.pkg = self.d/'update.tar'
        self.sha = p.build_package(self.d/'kernel',self.d/'rootfs',self.pkg,'V0.01','V1.32')['update_tar_sha256']

    def call(self, *args, ok=True):
        result = subprocess.run([str(self.tool),*map(str,args)],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,ok,result.stdout+result.stderr)
        return result

    def stage(self): self.call('stage',self.pkg,self.sha,self.card)

    def test_stage_does_not_publish_or_arm(self):
        self.stage()
        self.assertFalse((self.card/'recovery-update').exists())
        self.assertFalse((self.card/'TEST-selector-written').exists())
        self.assertEqual((self.card/'.q2-update'/self.sha/'update.tar').read_bytes(),self.pkg.read_bytes())

    def test_apply_rechecks_publishes_and_preserves_archive(self):
        self.stage(); self.call('apply',self.sha,self.card)
        self.assertEqual((self.card/'recovery-update/xImage').read_bytes(),kernel())
        self.assertEqual((self.card/'TEST-selector-written').read_bytes(), b'ota:kernel2'.ljust(256,b' ')+b'\xff'*1792)
        self.assertTrue((self.card/'.q2-update'/self.sha/'update.tar').exists())

    def test_payload_tamper_never_arms(self):
        self.stage(); (self.card/'.q2-update'/self.sha/'recovery-update/xImage').write_bytes(b'bad')
        self.call('apply',self.sha,self.card,ok=False)
        self.assertFalse((self.card/'recovery-update').exists())
        self.assertFalse((self.card/'TEST-selector-written').exists())

    def test_retained_archive_tamper_never_arms(self):
        self.stage(); (self.card/'.q2-update'/self.sha/'update.tar').write_bytes(b'bad')
        self.call('apply',self.sha,self.card,ok=False)
        self.assertFalse((self.card/'TEST-selector-written').exists())

    def test_wrong_digest_and_missing_stage(self):
        self.call('check',self.pkg,'0'*64,ok=False)
        self.call('apply',self.sha,self.card,ok=False)
        self.assertFalse((self.card/'TEST-selector-written').exists())

    def test_stage_alone_rejects_bad_source_before_retention_or_apply(self):
        # No preliminary check command: the actual staging entry point must
        # inspect before retaining a package or publishing/selecting recovery.
        original=self.pkg.read_bytes()
        for data,digest in [(original,'0'*64),(b'bad',self.sha),
                            (original[:1024],hashlib.sha256(original[:1024]).hexdigest()),
                            (original[:512]+b'X'+original[513:],hashlib.sha256(original[:512]+b'X'+original[513:]).hexdigest())]:
            with self.subTest(bytes=len(data),digest=digest):
                self.pkg.write_bytes(data)
                self.call('stage',self.pkg,digest,self.card,ok=False)
                self.assertFalse((self.card/'.q2-update'/digest).exists())
                self.assertFalse((self.card/'recovery-update').exists())
                self.assertFalse((self.card/'TEST-selector-written').exists())
                self.assertFalse(list((self.card/'.q2-update').glob('.stage-*')))

    def test_existing_recovery_is_not_replaced(self):
        (self.card/'recovery-update').mkdir()
        self.call('stage',self.pkg,self.sha,self.card,ok=False)

    def test_symlink_payload_refused(self):
        self.stage(); f=self.card/'.q2-update'/self.sha/'recovery-update/xImage'
        f.unlink(); f.symlink_to(self.d/'kernel')
        self.call('apply',self.sha,self.card,ok=False)
        self.assertFalse((self.card/'TEST-selector-written').exists())

    def test_host_directory_is_not_a_real_card(self):
        tool=WORK/'production-host-check'
        result=subprocess.run([str(tool),'stage',str(self.pkg),self.sha,str(self.card)],capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.card/'.q2-update').exists())

    def test_next_version_has_its_own_retained_archive(self):
        self.stage(); self.call('apply',self.sha,self.card)
        # Simulated stock recovery consumes the published directory and selector.
        shutil.rmtree(self.card/'recovery-update'); (self.card/'TEST-selector-written').unlink()
        nextpkg=self.d/'next.tar'; sha2=p.build_package(self.d/'kernel',self.d/'rootfs',nextpkg,'V0.02','V0.01')['update_tar_sha256']
        self.call('stage',nextpkg,sha2,self.card); self.call('apply',sha2,self.card)
        self.assertTrue((self.card/'.q2-update'/self.sha/'update.tar').exists())
        self.assertTrue((self.card/'.q2-update'/sha2/'update.tar').exists())

    def test_consumed_archive_can_be_staged_again(self):
        self.stage(); self.call('apply',self.sha,self.card)
        shutil.rmtree(self.card/'recovery-update'); (self.card/'TEST-selector-written').unlink()
        self.stage(); self.call('apply',self.sha,self.card)
        self.assertEqual((self.card/'recovery-update/xImage').read_bytes(),kernel())

    def test_exclusive_update_lock(self):
        with (self.card/'.q2-update.lock').open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.call('stage',self.pkg,self.sha,self.card,ok=False)
        self.assertFalse((self.card/'.q2-update').exists())

    def test_kernel_crc_and_executable_format_are_independent_checks(self):
        bad_header=bytearray(kernel()); bad_header[4]^=1
        bad_payload=bytearray(kernel()); bad_payload[80]^=1
        for bad in [kernel(1),bytes(bad_header),bytes(bad_payload)]:
            blobs={p.KERNEL:bad,p.ROOTFS:rootfs()}
            blobs[p.INFO]=p.manifest(blobs,'V0.01')
            archive=self.d/'bad-kernel.tar'
            with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as t:
                for name,data in blobs.items():
                    m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
            self.call('check',archive,hashlib.sha256(archive.read_bytes()).hexdigest(),ok=False)

    def test_archive_attack_members(self):
        blobs,_=p.read_package(self.pkg)
        for name,kind in [('../escape',tarfile.REGTYPE),(p.KERNEL,tarfile.REGTYPE),('evil',tarfile.SYMTYPE)]:
            attack=self.d/'attack.tar'
            with tarfile.open(attack,'w',format=tarfile.USTAR_FORMAT) as t:
                for n,b in blobs.items():
                    m=tarfile.TarInfo(n);m.size=len(b);t.addfile(m,io.BytesIO(b))
                m=tarfile.TarInfo(name);m.type=kind;m.linkname='../escape';m.size=1 if kind==tarfile.REGTYPE else 0
                t.addfile(m,io.BytesIO(b'x'))
            sha=hashlib.sha256(attack.read_bytes()).hexdigest()
            self.call('stage',attack,sha,self.card,ok=False)
            self.assertFalse((self.card/'TEST-selector-written').exists())


if __name__=='__main__': unittest.main()
