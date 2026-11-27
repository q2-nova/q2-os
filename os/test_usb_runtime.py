"""Generated packaging-fault fixtures; never loads owner configuration."""
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from usb_runtime import REQUIRED, validate

ROOT = Path(__file__).resolve().parents[1]


class UsbRuntimeTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/perf/usb-runtime-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=base))
        for name, kind in REQUIRED.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            # Header-shaped generated fixture, not a claimed runnable program.
            if kind == 'elf':
                data = bytearray(52)
                data[:7] = b'\x7fELF\x01\x01\x01'
                struct.pack_into('<HHI', data, 16, 3, 8, 1)
                path.write_bytes(data)
            else:
                path.write_text('#!/bin/sh\n')
            path.chmod(0o755)
        (self.root / 'sbin/q2-installed-init').write_text('#!/bin/sh\n/usr/sbin/q2-usb-auto start\n')
        (self.root / 'usr/sbin/q2-usb-auto').write_text('#!/bin/sh\nEVENTS=/usr/sbin/q2-usb-auto-events\n')

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_complete_generated_runtime_records_only_fixed_public_executables(self):
        result = validate(self.root)
        self.assertEqual(set(result['files']), set(REQUIRED))
        self.assertEqual(result['event_helper_loader_probe'], 'not run')

    def test_missing_watcher_dependency_reproduces_refreshed_image_failure(self):
        (self.root / 'usr/sbin/q2-usb-auto-events').unlink()
        with self.assertRaisesRegex(ValueError, 'q2-usb-auto-events'):
            validate(self.root)

    def test_each_missing_or_nonexecutable_dependency_blocks_packaging(self):
        for name in REQUIRED:
            path = self.root / name
            with self.subTest(name=name):
                path.chmod(0o600)
                with self.assertRaisesRegex(ValueError, name):validate(self.root)
                path.chmod(0o755)

    def test_host_architecture_corruption_and_external_link_block_packaging(self):
        path = self.root / 'usr/sbin/q2-usb-auto-events'
        original = path.read_bytes()
        for corrupt in [b'not ELF', original[:4] + b'\x02' + original[5:],
                        original[:18] + struct.pack('<H', 62) + original[20:]]:
            path.write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError, 'ELF32 MIPS'):validate(self.root)
        path.unlink()
        path.symlink_to('/bin/sh')
        with self.assertRaisesRegex(ValueError, 'q2-usb-auto-events'):validate(self.root)

    def test_missing_startup_hook_or_changed_manager_dependency_fails(self):
        init = self.root / 'sbin/q2-installed-init'
        original = init.read_bytes()
        init.write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'does not start'):validate(self.root)
        init.write_bytes(original)
        (self.root / 'usr/sbin/q2-usb-auto').write_text('#!/bin/sh\nEVENTS=/foreign/unvalidated-helper\n')
        with self.assertRaisesRegex(ValueError, 'does not declare'):validate(self.root)

    def test_optional_loader_probe_uses_only_invalid_argument_and_propagates_failure(self):
        probe = self.root / 'generated-qemu-probe'
        probe.write_text('#!/bin/sh\n[ "$1" = -L ] && [ "$#" = 4 ] && [ "$4" = --q2-runtime-check-invalid ] || exit 9\nexit 2\n')
        probe.chmod(0o755)
        self.assertIn('passed', validate(self.root, probe)['event_helper_loader_probe'])
        probe.write_text('#!/bin/sh\nexit 127\n')
        with self.assertRaisesRegex(ValueError, 'candidate libraries'):validate(self.root, probe)


if __name__ == '__main__':unittest.main()
