#!/usr/bin/env python3
"""os/x2000_c.py: a clang build replaces only a file its GCC control reproduces."""
import json
import shutil
import struct
from pathlib import Path
import tempfile
import unittest

import x2000_c


def elf(stack_protector=True, relro=True, bind_now=True, pie=True, tag=b''):
    """A little-endian ELF32 with only what x2000_c.hardening reads."""
    strings = b'\0libc.so.6\0' + (b'__stack_chk_fail\0' if stack_protector else b'') + tag + b'\0'
    count = 3 if relro else 2
    dyn_at = 52 + 32 * count
    dynamic = [(5, 0), (10, len(strings))]
    if bind_now or pie:
        dynamic.append((0x6ffffffb, (0x1 if bind_now else 0) | (0x08000000 if pie else 0)))
    dynamic.append((0, 0))
    str_at = dyn_at + 8 * len(dynamic)
    dynamic[0] = (5, str_at)
    size = str_at + len(strings)
    header = b'\x7fELF\x01\x01\x01' + bytes(9) + struct.pack('<HHIIIIIHHHHHH', 3, 8, 1, 0, 52, 0, 0x70001407, 52, 32, count, 40, 0, 0)
    phdrs = struct.pack('<8I', 1, 0, 0, 0, size, size, 5, 0x1000) + struct.pack('<8I', 2, dyn_at, dyn_at, dyn_at, 8 * len(dynamic), 8 * len(dynamic), 6, 4)
    if relro:
        phdrs += struct.pack('<8I', 0x6474e552, dyn_at, dyn_at, dyn_at, 8 * len(dynamic), 8 * len(dynamic), 4, 1)
    return header + phdrs + b''.join(struct.pack('<iI', t, v) for t, v in dynamic) + strings


def result(directory, tests_same=True):
    for arm, data in (('gcc', b'gcc busybox'), ('x2000', b'clang busybox')):
        (directory/'busybox'/arm).mkdir(parents=True)
        (directory/'busybox'/arm/'busybox').write_bytes(data)
    for arm, data in (('gcc', b'gcc rtc'), ('x2000', b'clang rtc')):
        (directory/'probes'/arm).mkdir(parents=True, exist_ok=True)
        (directory/'probes'/arm/'q2-rtc-probe').write_bytes(data)
    (directory/'result.json').write_text(json.dumps({
        'passed': True, 'x2000': {'patch_sha256': 'p'}, 'packages': {'busybox': {}, 'probes': {}},
        'tests': [{'package': 'busybox', 'same': tests_same, 'ran': True},
                  {'package': 'probes', 'same': True, 'ran': True}]}))


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out, self.root = self.tmp/'out', self.tmp/'root'
        (self.root/'bin').mkdir(parents=True)
        (self.root/'usr/bin').mkdir(parents=True)

    def test_replaces_only_reproduced_files(self):
        result(self.out)
        (self.root/'bin/busybox').write_bytes(b'gcc busybox')
        (self.root/'usr/bin/q2-rtc-probe').write_bytes(b'other rtc')
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'clang busybox')
        self.assertEqual((self.root/'usr/bin/q2-rtc-probe').read_bytes(), b'other rtc')
        self.assertEqual(set(record['files']), {'bin/busybox'})
        self.assertIn('usr/bin/q2-rtc-probe', record['kept_on_gcc'])

    def test_failed_package_tests_install_nothing(self):
        result(self.out, tests_same=False)
        (self.root/'bin/busybox').write_bytes(b'gcc busybox')
        with self.assertRaisesRegex(ValueError, 'did not pass|nothing to install'):
            x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'gcc busybox')

    def test_base_with_this_clang_build_is_admitted_again(self):
        result(self.out)
        (self.root/'bin/busybox').write_bytes(b'clang busybox')
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'clang busybox')
        self.assertEqual(set(record['files']), {'bin/busybox'})
        self.assertTrue(record['files']['bin/busybox']['base_had_x2000'])
        self.assertEqual(record['files']['bin/busybox']['gcc_sha256'], x2000_c.sha(self.out/'busybox/gcc/busybox'))

    def test_base_with_another_clang_build_is_kept(self):
        result(self.out)
        (self.root/'bin/busybox').write_bytes(b'older clang busybox')
        with self.assertRaisesRegex(ValueError, 'nothing to install'):
            x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'older clang busybox')

    def test_gcc_control_names_the_arm_beside_the_shipped_clang_file(self):
        result(self.out)
        clang = x2000_c.sha(self.out/'probes/x2000/q2-rtc-probe')
        self.assertEqual(x2000_c.gcc_control(self.out, clang, {'patch_sha256': 'p'}), self.out/'probes/gcc/q2-rtc-probe')
        self.assertIsNone(x2000_c.gcc_control(self.out, x2000_c.sha(self.out/'probes/gcc/q2-rtc-probe'), {'patch_sha256': 'p'}))

    def wpa_result(self):
        for arm in ('gcc', 'x2000'):
            (self.out/'wpa'/arm).mkdir(parents=True)
            for name in ('wpa_supplicant', 'wpa_cli'):
                (self.out/'wpa'/arm/name).write_bytes(f'{arm} {name}'.encode())
        (self.out/'result.json').write_text(json.dumps({
            'passed': True, 'x2000': {'patch_sha256': 'p'}, 'packages': {'wpa': {}},
            'tests': [{'package': 'wpa', 'same': True, 'ran': True}]}))
        (self.root/'usr/sbin').mkdir(parents=True)

    def test_pair_replaced_together(self):
        self.wpa_result()
        (self.root/'usr/sbin/wpa_supplicant').write_bytes(b'gcc wpa_supplicant')
        (self.root/'usr/sbin/wpa_cli').write_bytes(b'gcc wpa_cli')
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual(set(record['files']), {'usr/sbin/wpa_supplicant', 'usr/sbin/wpa_cli'})
        self.assertEqual((self.root/'usr/sbin/wpa_cli').read_bytes(), b'x2000 wpa_cli')

    def test_pair_kept_together(self):
        # The supplicant's GCC control matches; the control client is another build.
        self.wpa_result()
        (self.root/'usr/sbin/wpa_supplicant').write_bytes(b'gcc wpa_supplicant')
        (self.root/'usr/sbin/wpa_cli').write_bytes(b'other wpa_cli')
        with self.assertRaisesRegex(ValueError, 'nothing to install'):
            x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'usr/sbin/wpa_supplicant').read_bytes(), b'gcc wpa_supplicant')
        _, candidates = x2000_c.admit(self.out)
        _, kept = x2000_c.plan(candidates, lambda t: x2000_c.sha(self.root/t))
        self.assertIn('rest of its pair', kept['usr/sbin/wpa_supplicant'])

    def test_targets_name_each_installed_path_once(self):
        paths = [target for files in x2000_c.TARGETS.values() for target in files.values()]
        self.assertEqual(len(paths), len(set(paths)))
        for path in ('usr/sbin/dropbear', 'usr/lib/libz.so.1.3.2', 'usr/lib/libcrypt.so.2.0.0', 'usr/sbin/fsck.exfat',
                     'usr/lib/libq2platform.so'):
            self.assertIn(path, paths)
        # Built, but no emulator test runs more than their version or usage.
        for path in ('usr/sbin/alsactl', 'usr/bin/alsamixer', 'usr/bin/aserver'):
            self.assertNotIn(path, paths)
        self.assertLessEqual(x2000_c.PAIRED, set(x2000_c.TARGETS))

    def platform_result(self):
        for arm in ('gcc', 'x2000'):
            (self.out/'platform'/arm).mkdir(parents=True)
            (self.out/'platform'/arm/'libq2platform.so').write_bytes(f'{arm} platform'.encode())
        (self.out/'result.json').write_text(json.dumps({
            'passed': True, 'x2000': {'patch_sha256': 'p'}, 'packages': {'platform': {}},
            'tests': [{'package': 'platform', 'same': True, 'ran': True}]}))
        (self.root/'usr/lib').mkdir(parents=True)

    def test_platform_library_and_manifest_digest(self):
        self.platform_result()
        (self.root/'usr/lib/libq2platform.so').write_bytes(b'gcc platform')
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        clang = x2000_c.sha(self.out/'platform/x2000/libq2platform.so')
        self.assertEqual(x2000_c.sha(self.root/x2000_c.PLATFORM), clang)
        self.assertEqual(record['files'][x2000_c.PLATFORM]['gcc_sha256'], x2000_c.sha(self.out/'platform/gcc/libq2platform.so'))
        self.assertEqual(x2000_c.platform_sha256(record, 'shipped'), clang)
        self.assertEqual(x2000_c.platform_sha256({'files': {'bin/busybox': {}}}, 'shipped'), 'shipped')

    def test_platform_library_of_another_build_is_kept(self):
        self.platform_result()
        (self.root/'usr/lib/libq2platform.so').write_bytes(b'another gcc platform')
        with self.assertRaisesRegex(ValueError, 'nothing to install'):
            x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual((self.root/'usr/lib/libq2platform.so').read_bytes(), b'another gcc platform')

    def test_hardening_read_from_the_elf(self):
        path = self.tmp/'elf'
        for flags in ({}, {'stack_protector': False}, {'relro': False}, {'bind_now': False}, {'pie': False},
                      {'bind_now': False, 'pie': False}):
            path.write_bytes(elf(**flags))
            self.assertEqual(x2000_c.hardening(path), {k: flags.get(k, True) for k in x2000_c.HARDENING})
        path.write_bytes(b'#!/bin/sh\n')
        self.assertIsNone(x2000_c.hardening(path))

    def test_clang_file_weaker_than_its_gcc_control_is_refused(self):
        # A missing protection is refused whether or not the base already has the clang file.
        for missing in x2000_c.HARDENING:
            for base_had_x2000 in (False, True):
                with self.subTest(missing=missing, base_had_x2000=base_had_x2000):
                    shutil.rmtree(self.out, ignore_errors=True)
                    shutil.rmtree(self.root/'usr/lib', ignore_errors=True)
                    self.platform_result()
                    (self.out/'platform/gcc/libq2platform.so').write_bytes(elf(tag=b'gcc'))
                    (self.out/'platform/x2000/libq2platform.so').write_bytes(elf(**{missing: False}, tag=b'clang'))
                    shipped = self.out/'platform'/('x2000' if base_had_x2000 else 'gcc')/'libq2platform.so'
                    (self.root/x2000_c.PLATFORM).write_bytes(shipped.read_bytes())
                    with self.assertRaisesRegex(ValueError, 'nothing to install'):
                        x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
                    _, candidates = x2000_c.admit(self.out)
                    _, kept = x2000_c.plan(candidates, lambda t: x2000_c.sha(self.root/t))
                    self.assertEqual(kept[x2000_c.PLATFORM], "the clang build lacks the GCC control's " + missing)
                    self.assertEqual((self.root/x2000_c.PLATFORM).read_bytes(), shipped.read_bytes())

    def test_clang_file_as_hardened_or_more_is_installed(self):
        self.platform_result()
        (self.out/'platform/gcc/libq2platform.so').write_bytes(elf(stack_protector=False, pie=False, tag=b'gcc'))
        (self.out/'platform/x2000/libq2platform.so').write_bytes(elf(pie=False, tag=b'clang'))
        (self.root/x2000_c.PLATFORM).write_bytes((self.out/'platform/gcc/libq2platform.so').read_bytes())
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'})
        self.assertEqual(set(record['files']), {x2000_c.PLATFORM})

    def test_earlier_clang_build_replaced_over_its_recorded_gcc_control(self):
        result(self.out)
        (self.root/'bin/busybox').write_bytes(b'older clang busybox')
        (self.root/'usr/bin/q2-rtc-probe').write_bytes(b'older clang rtc')
        gcc = x2000_c.sha(self.out/'busybox/gcc/busybox')
        manifest = {'kernel_release_integration': {'x2000_c': {'files': {
            'bin/busybox': {'gcc_sha256': gcc, 'x2000_sha256': x2000_c.sha(self.root/'bin/busybox')},
            'usr/bin/q2-rtc-probe': {'gcc_sha256': 'another control', 'x2000_sha256': x2000_c.sha(self.root/'usr/bin/q2-rtc-probe')}}}}}
        base = x2000_c.base_record(manifest)
        self.assertEqual(x2000_c.gcc_control(self.out, base['bin/busybox']['x2000_sha256'], {'patch_sha256': 'p'}, base, 'bin/busybox'),
                         self.out/'busybox/gcc/busybox')
        self.assertIsNone(x2000_c.gcc_control(self.out, base['bin/busybox']['x2000_sha256'], {'patch_sha256': 'p'}))
        record = x2000_c.install(self.out, self.root, {'patch_sha256': 'p'}, base)
        self.assertEqual((self.root/'bin/busybox').read_bytes(), b'clang busybox')
        self.assertEqual(record['files']['bin/busybox']['base_x2000_sha256'], base['bin/busybox']['x2000_sha256'])
        self.assertEqual(record['files']['bin/busybox']['gcc_sha256'], gcc)
        # Recorded over another GCC control: kept.
        self.assertEqual((self.root/'usr/bin/q2-rtc-probe').read_bytes(), b'older clang rtc')
        self.assertIn('usr/bin/q2-rtc-probe', record['kept_on_gcc'])
        self.assertEqual(x2000_c.base_record({}), {})

    def test_other_toolchain_refused(self):
        result(self.out)
        with self.assertRaisesRegex(ValueError, 'another X2000 toolchain'):
            x2000_c.install(self.out, self.root, {'patch_sha256': 'q'})


if __name__ == '__main__':
    unittest.main()
