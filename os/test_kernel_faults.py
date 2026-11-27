#!/usr/bin/env python3
"""Host checks of the fault kernel builder and harness, without an emulator."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import kernel_faults
import test_storage_faults as storage
from build import LOCK, sha


def archive(members):
    out = b''.join(kernel_faults.cpio_member(name, mode, body, ino, 7)
                   for ino, (name, mode, body) in enumerate(members, 1))
    return out + kernel_faults.cpio_member('TRAILER!!!', 0, b'', 0, 0)


class Initramfs(unittest.TestCase):
    rescue = archive([('bin', 0o040755, b''), ('bin/busybox', 0o100755, b'busybox'),
                      ('bin/sh', 0o120777, b'busybox'), ('init', 0o100755, b'#!/bin/sh\nrescue\n')])

    def test_rescue_members_kept_and_init_replaced(self):
        files = {'init': (0o100755, b'#!/bin/sh\ntest\n'), 'bin/q2-sdfault': (0o100755, b'\x7fELF')}
        data = kernel_faults.initramfs(self.rescue, files, 99)
        self.assertEqual(len(data) % 512, 0)
        members = {name: (fields, body) for name, fields, body in kernel_faults.cpio_entries(data)}
        self.assertEqual(set(members), {'bin', 'bin/busybox', 'bin/sh', 'init', 'bin/q2-sdfault', 'dev/console'})
        self.assertEqual(members['init'][1], b'#!/bin/sh\ntest\n')
        self.assertEqual(members['bin/sh'][0][1], 0o120777)
        self.assertEqual(members['bin/busybox'][1], b'busybox')
        console = members['dev/console'][0]
        self.assertEqual((console[1], console[9], console[10]), (0o020600, 5, 1))
        inodes = [fields[0] for fields, _ in members.values()]
        self.assertEqual(len(inodes), len(set(inodes)))

    def test_not_newc_refused(self):
        with self.assertRaisesRegex(ValueError, 'newc'):
            kernel_faults.cpio_entries(b'\x1f\x8b' + bytes(200))

    def test_hard_links_refused(self):
        member = bytearray(kernel_faults.cpio_member('bin/a', 0o100755, b'x', 1, 0))
        member[6 + 8 * 4:6 + 8 * 5] = b'%08X' % 2
        with self.assertRaisesRegex(ValueError, 'hard links'):
            kernel_faults.cpio_entries(bytes(member) + kernel_faults.cpio_member('TRAILER!!!', 0, b'', 0, 0))


class Config(unittest.TestCase):
    def text(self, *drop):
        lines = ['CONFIG_BLK_DEV_INITRD=y', '# CONFIG_MIPS_CMDLINE_FROM_DTB is not set',
                 'CONFIG_MIPS_CMDLINE_DTB_EXTEND=y']
        return '\n'.join(line for line in lines if not any(d in line for d in drop)) + '\n'

    def test_requested_options_present(self):
        kernel_faults.check_config(self.text())

    def test_dropped_option_refused(self):
        for option in ('MIPS_CMDLINE_DTB_EXTEND', 'MIPS_CMDLINE_FROM_DTB', 'BLK_DEV_INITRD'):
            with self.subTest(option=option), self.assertRaises(ValueError):
                kernel_faults.check_config(self.text(option))

    def test_probe_flags(self):
        command = kernel_faults.probe_command('cc', '/uapi', Path('/s/sdfault.c'), Path('/o/q2-sdfault'))
        for flag in ('-mnan=2008', '-nostdlib', '-nostdinc', '-static', '-Werror'):
            self.assertIn(flag, command)
        self.assertEqual(command[-5:], ['-I', '/uapi', '-o', '/o/q2-sdfault', '/s/sdfault.c'])


class Artifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def kernel(self):
        kernel = self.tmp / 'kernel'
        kernel.mkdir()
        (kernel / 'xImage').write_bytes(b'kernel')
        record = {'linux': LOCK['linux_production'], 'source_inputs': kernel_faults.source_inputs(),
                  'xImage': {'sha256': sha(kernel / 'xImage')}}
        (kernel / 'artifact.json').write_text(json.dumps(record))
        return kernel, record

    def qemu(self, commit=None):
        qemu = self.tmp / 'qemu'
        (qemu / 'bin').mkdir(parents=True)
        (qemu / 'qemu-artifact.json').write_text(json.dumps(
            {'source_commit': commit or LOCK['qemu_installed']['commit']}))
        binary = qemu / 'bin/qemu-system-mipsel'
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        return qemu

    def test_current_kernel_admitted(self):
        kernel, record = self.kernel()
        self.assertEqual(kernel_faults.admit(kernel), record)

    def test_missing_kernel_refused(self):
        with self.assertRaisesRegex(ValueError, 'no fault kernel'):
            kernel_faults.admit(self.tmp)

    def test_altered_image_refused(self):
        kernel, _ = self.kernel()
        (kernel / 'xImage').write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'differs'):
            kernel_faults.admit(kernel)

    def test_stale_inputs_refused(self):
        kernel, record = self.kernel()
        record['source_inputs']['files']['sdfault.c'] = '0' * 64
        (kernel / 'artifact.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            kernel_faults.admit(kernel)

    def test_inputs_name_every_probe_and_the_init(self):
        files = kernel_faults.source_inputs()['files']
        for name in ('probe.h', 'init', *(p + '.c' for p in kernel_faults.PROBES)):
            self.assertIn(name, files)

    def test_qemu_from_the_source_lock_admitted(self):
        kernel_faults.check_qemu(self.qemu())

    def test_qemu_from_another_revision_refused(self):
        with self.assertRaisesRegex(ValueError, 'revision'):
            kernel_faults.check_qemu(self.qemu('0' * 40))

    def test_qemu_without_the_system_emulator_refused(self):
        qemu = self.qemu()
        (qemu / 'bin/qemu-system-mipsel').unlink()
        with self.assertRaisesRegex(ValueError, 'executable'):
            kernel_faults.check_qemu(qemu)

    def test_guest_commands(self):
        kernel, _ = self.kernel()
        qemu = self.qemu()
        with mock.patch.object(kernel_faults, 'RUNS', self.tmp / 'runs'), \
                mock.patch.object(kernel_faults, 'WORK', self.tmp / 'work'):
            native = kernel_faults.Guest('t', kernel_dir=kernel, qemu_dir=qemu, sd=self.tmp / 'sd.img',
                                         append='a=1', paused=True)
            boxed = kernel_faults.Guest('t', kernel_dir=kernel, qemu_dir=qemu, nand=self.tmp / 'nand.img',
                                        container='image')
            for guest in (native, boxed):
                guest.close()
        self.assertEqual(native.command[0], str(qemu / 'bin/qemu-system-mipsel'))
        for expected in (['-append', 'a=1'], ['-drive', f'if=sd,format=raw,file={self.tmp / "sd.img"}'], ['-S']):
            self.assertTrue(any(native.command[i:i + len(expected)] == expected for i in range(len(native.command))))
        self.assertIn('-qmp', native.command)
        self.assertEqual(boxed.command[:2], ['podman', 'run'])
        self.assertIn('image', boxed.command)
        for path in (kernel, qemu, self.tmp):
            self.assertIn(f'{path.resolve()}:{path.resolve()}', boxed.command)
        self.assertNotIn('-S', boxed.command)
        self.assertFalse(native.sockets.exists())
        self.assertTrue(json.loads((native.out / 'result.json').read_text())['emulation_only'])


class Fetch(unittest.TestCase):
    def test_pins_are_release_assets_with_digests(self):
        for name in ('qemu', 'rescue'):
            item = kernel_faults.INPUTS[name]
            self.assertTrue(item['url'].startswith('https://github.com/q2-nova/'))
            self.assertRegex(item['sha256'], '^[0-9a-f]{64}$')


class Replies(unittest.TestCase):
    def test_values(self):
        self.assertIsNone(kernel_faults.reply_value('ok'))
        self.assertEqual(kernel_faults.reply_value('ok {"fault":0,"hits":3}'), {'fault': 0, 'hits': 3})
        with self.assertRaisesRegex(RuntimeError, 'refused'):
            kernel_faults.reply_value('error: microSD busy')


class Media(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_nand_image_layout(self):
        path = self.tmp / 'nand.img'
        storage.nand_image(path)
        self.assertEqual(path.stat().st_size, storage.CHIP_PAGES * storage.RAW_PAGE)
        self.assertEqual(storage.USERDATA, 802)
        for block in (0, 1, 3):
            self.assertEqual(storage.raw_page(path, storage.nand_page(block) + 63), storage.DATA)
        for block in (2, 4, 5, 8, 9):
            self.assertEqual(storage.raw_page(path, storage.nand_page(block)), storage.ERASED)
        self.assertEqual(storage.raw_page(path, storage.nand_page(0) - 1), storage.ERASED)

    def test_io_pattern_matches_the_probe(self):
        source = (kernel_faults.SOURCES / 'nandfault.c').read_text()
        self.assertIn('(offset * 17 + (offset >> 8) * 37 + (offset >> 17) * 53) & 255', source)
        self.assertEqual([storage.io_pattern(o) for o in (0, 1, 256, 131072)], [0, 17, (256 * 17 + 37) & 255,
                                                                                (131072 * 17 + 512 * 37 + 53) & 255])

    def test_userdata_device(self):
        listing = 'mtd0 rootfs\nmtd0ro rootfs\nmtd1 ota\nmtd1ro ota\nmtd2 userdata\nmtd2ro userdata\n'
        self.assertEqual(storage.mtd_device(listing, 'userdata'), '/dev/mtd2')
        with self.assertRaises(AssertionError):
            storage.mtd_device('mtd0 rootfs\n', 'userdata')
        with self.assertRaises(AssertionError):
            storage.mtd_device('mtd1 userdata\nmtd2 userdata\n', 'userdata')

    @unittest.skipUnless(shutil.which('mkfs.exfat') and shutil.which('fsck.exfat'), 'needs exfatprogs')
    def test_sd_image_layout(self):
        path = self.tmp / 'sd.img'
        storage.sd_image(path)
        self.assertEqual(path.stat().st_size, storage.SD_BYTES)
        self.assertEqual(storage.sd_area(path), b'\xa5' * storage.SD_AREA)
        with path.open('rb') as f:
            f.seek(3)
            self.assertEqual(f.read(8), b'EXFAT   ')
        self.assertEqual(subprocess.run(['fsck.exfat', '-n', str(path)], capture_output=True).returncode, 0)


class Sources(unittest.TestCase):
    def test_probe_modes_the_harness_uses(self):
        for probe, modes in (('sdfault', ('read', 'read-fails', 'restore', 'write', 'write-fails', 'verify')),
                             ('nandfault', ('io', 'inject', 'recover')), ('wdtcheck', ('magic', 'hold', 'nowayout'))):
            source = (kernel_faults.SOURCES / (probe + '.c')).read_text()
            for mode in modes:
                with self.subTest(probe=probe, mode=mode):
                    self.assertIn(f'same(mode, "{mode}")' if probe != 'wdtcheck' else f'"{mode}"', source)

    def test_init_is_executable_shell(self):
        init = kernel_faults.SOURCES / 'init'
        self.assertTrue(os.access(init, os.X_OK))
        subprocess.run(['sh', '-n', str(init)], check=True)
        self.assertIn(kernel_faults.READY, init.read_text())


if __name__ == '__main__':
    unittest.main()
