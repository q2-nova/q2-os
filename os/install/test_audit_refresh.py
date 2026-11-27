#!/usr/bin/env python3
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from audit_refresh import ARTIFACTS, METADATA, PLATFORM, audit, digest


class RefreshAudit(unittest.TestCase):
    def setUp(self):
        generated = Path(__file__).resolve().parents[2] / 'build/perf'
        generated.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=generated)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'base'
        self.candidate = Path(self.temp.name) / 'candidate'
        root = self.base / 'root'
        for name, content in {
            'usr/bin/q2nd': b'generated app baseline',
            'usr/lib/libq2platform.so': b'generated platform',
            'lib/modules/generated.ko': b'generated module',
            'sbin/q2-usb-storage': b'#!/bin/sh\nexit 0\n',
            METADATA: b'{}',
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (root / 'usr/bin/alias').symlink_to('q2nd')
        for name in ARTIFACTS:
            (self.base / name).write_bytes(('generated ' + name).encode())
        self.manifest(self.base, 'V0.01')
        shutil.copytree(self.base, self.candidate, symlinks=True)
        (self.candidate / 'root/usr/bin/q2nd').write_bytes(b'generated new app')
        for name in ('rootfs.squashfs', 'update.tar'):
            (self.candidate / name).write_bytes(b'generated changed package')
        self.manifest(self.candidate, 'V0.02')
        self.replacements = {'usr/bin/q2nd': digest(self.candidate / 'root/usr/bin/q2nd')}

    def manifest(self, directory, tag):
        root = directory / 'root'
        app = digest(root / 'usr/bin/q2nd')
        (root / METADATA).write_text(json.dumps({'release_tag': tag, 'app_sha256': app}))
        value = dict(release_tag=tag, app_sha256=app,
                     platform_library_sha256=digest(root / 'usr/lib/libq2platform.so'),
                     modules={'lib/modules/generated.ko': digest(root / 'lib/modules/generated.ko')},
                     artifacts={name: {'sha256': digest(directory / name),
                                       'bytes': (directory / name).stat().st_size} for name in ARTIFACTS})
        (directory / 'artifact.json').write_text(json.dumps(value))

    def check(self):
        return audit(self.base, self.candidate, self.replacements)

    def test_exact_declared_refresh_and_evidence_limits(self):
        result = self.check()
        self.assertTrue(result['passed'])
        self.assertFalse(result['hardware'])
        self.assertFalse(result['packed_root_equivalence_verified'])
        self.assertEqual(result['changed_root_paths'], [METADATA, 'usr/bin/q2nd'])

    def test_unmanifested_or_changed_kernel_cannot_pass(self):
        path = self.candidate / 'xImage'
        path.write_bytes(b'unexpected kernel')
        with self.assertRaisesRegex(ValueError, 'artifact differs'):
            self.check()
        self.manifest(self.candidate, 'V0.02')
        with self.assertRaisesRegex(ValueError, 'kernel/recovery'):
            self.check()

    def test_unexpected_helper_mode_link_and_directory_mutations(self):
        root = self.candidate / 'root'
        helper = root / 'sbin/q2-usb-storage'
        original = helper.read_bytes()
        helper.write_bytes(b'unexpected helper')
        with self.assertRaisesRegex(ValueError, 'explicit replacement'):
            self.check()
        helper.write_bytes(original)
        app = root / 'usr/bin/q2nd'
        old_mode = app.stat().st_mode & 0o7777
        app.chmod(old_mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, 'inode type/mode'):
            self.check()
        app.chmod(old_mode)
        alias = root / 'usr/bin/alias'
        alias.unlink(); alias.symlink_to('/outside-generated-tree')
        with self.assertRaisesRegex(ValueError, 'explicit replacement'):
            self.check()
        alias.unlink(); alias.symlink_to('q2nd')
        (root / 'extra-directory').mkdir()
        with self.assertRaisesRegex(ValueError, 'explicit replacement'):
            self.check()

    def test_app_release_and_module_metadata_must_match_real_files(self):
        path = self.candidate / 'artifact.json'
        original = path.read_text()
        for mutation in ('app_sha256', 'release_tag', 'modules'):
            with self.subTest(mutation=mutation):
                value = json.loads(original)
                value[mutation] = {'lib/modules/generated.ko': '0' * 64} if mutation == 'modules' else 'wrong'
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    self.check()
        path.write_text(original)
        self.assertTrue(self.check()['passed'])

    def test_replacement_cannot_follow_symlink_or_escape_root(self):
        path = self.candidate / 'root/usr/bin/q2nd'
        data = path.read_bytes()
        outside = Path(self.temp.name) / 'outside'
        outside.write_bytes(data)
        path.unlink(); path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.check()
        for name in ('../outside', '/outside', 'usr//bin/q2nd', './outside'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'relative path'):
                audit(self.base, self.candidate, {name: digest(outside)})

    def test_cannot_disguise_platform_or_module_change_as_regular_replacement(self):
        path = self.candidate / 'root/lib/modules/generated.ko'
        path.write_bytes(b'changed module')
        self.replacements['lib/modules/generated.ko'] = digest(path)
        self.manifest(self.candidate, 'V0.02')
        with self.assertRaisesRegex(ValueError, 'modules/platform changed'):
            self.check()

    def platform_candidate(self):
        before = digest(self.base / 'root' / PLATFORM)
        path = self.candidate / 'root' / PLATFORM
        path.write_bytes(b'generated replacement platform')
        after = digest(path)
        self.replacements[PLATFORM] = after
        self.manifest(self.candidate, 'V0.02')
        return dict(platform_before=before, platform_after=after)

    def test_platform_change_requires_separate_exact_pair_and_reports_limits(self):
        pair = self.platform_candidate()
        with self.assertRaisesRegex(ValueError, 'platform requires explicit'):
            self.check()
        result = audit(self.base, self.candidate, self.replacements, **pair)
        self.assertEqual(result['platform_change'], dict(
            before_sha256=pair['platform_before'], after_sha256=pair['platform_after'],
            abi_verified=False, runtime_verified=False))
        self.assertFalse(result['hardware'])
        self.assertFalse(result['packed_root_equivalence_verified'])
        for bad in (dict(platform_before=pair['platform_before']),
                    dict(platform_after=pair['platform_after']),
                    dict(platform_before='0'*64, platform_after=pair['platform_after']),
                    dict(platform_before=pair['platform_before'], platform_after='1'*64),
                    dict(platform_before=pair['platform_before'], platform_after=pair['platform_before']),
                    dict(platform_before='invalid', platform_after=pair['platform_after'])):
            with self.subTest(pair=bad), self.assertRaisesRegex(ValueError, 'platform replacement'):
                audit(self.base, self.candidate, self.replacements, **bad)
        del self.replacements[PLATFORM]
        with self.assertRaisesRegex(ValueError, 'explicit root replacement'):
            audit(self.base, self.candidate, self.replacements, **pair)

    def test_platform_opt_in_preserves_module_and_mode_guards(self):
        pair = self.platform_candidate()
        path = self.candidate / 'root' / PLATFORM
        mode = path.stat().st_mode & 0o7777
        path.chmod(mode ^ 0o100)
        with self.assertRaisesRegex(ValueError, 'inode type/mode'):
            audit(self.base, self.candidate, self.replacements, **pair)
        path.chmod(mode)
        module = self.candidate / 'root/lib/modules/generated.ko'
        module.write_bytes(b'undeclared ABI module change')
        self.replacements['lib/modules/generated.ko'] = digest(module)
        self.manifest(self.candidate, 'V0.02')
        with self.assertRaisesRegex(ValueError, 'modules/platform changed'):
            audit(self.base, self.candidate, self.replacements, **pair)

    def test_platform_identity_pair_cannot_mask_wrong_file_or_unexpected_helper(self):
        pair = self.platform_candidate()
        path = self.candidate / 'root' / PLATFORM
        path.write_bytes(b'wrong replacement payload')
        with self.assertRaisesRegex(ValueError, 'replacement differs'):
            audit(self.base, self.candidate, self.replacements, **pair)
        pair = self.platform_candidate()
        (self.candidate / 'root/sbin/q2-usb-storage').write_bytes(b'unexpected helper')
        with self.assertRaisesRegex(ValueError, 'explicit replacement'):
            audit(self.base, self.candidate, self.replacements, **pair)


if __name__ == '__main__':
    unittest.main()
