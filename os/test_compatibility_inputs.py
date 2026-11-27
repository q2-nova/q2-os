"""Check adopted inputs and retention of the last completed emulator build."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import compatibility
import emulator
from build import sha
from emulator import preserve_qemu_artifact


class CompatibilityInputs(unittest.TestCase):
    def test_all_profiles_and_changed_series(self):
        for name in ('os-kernel', 'diagnostic', 'diagnostic-wifi'):
            with self.subTest(name=name):
                self.assertTrue(compatibility.kernel_patches(name))
        with tempfile.TemporaryDirectory() as directory:
            inputs = Path(directory) / 'compat'
            shutil.copytree(compatibility.INPUTS, inputs)
            with patch.object(compatibility, 'INPUTS', inputs):
                patches = compatibility.kernel_patches('os-kernel')
                patches[0].write_bytes(patches[0].read_bytes() + b'\n')
                with self.assertRaisesRegex(RuntimeError, patches[0].name):
                    compatibility.kernel_patches('os-kernel')
                series = inputs / 'kernel/os-kernel.series'
                series.write_text('\n'.join(reversed(series.read_text().splitlines())) + '\n')
                with self.assertRaisesRegex(RuntimeError, 'os-kernel.series'):
                    compatibility.kernel_patches('os-kernel')

    def test_qemu_reuses_only_verified_fork_build(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            binary = out / emulator.QEMU_BINARY
            receipt = out / 'artifact.json'
            binary.write_bytes(b'fork emulator')
            identity = emulator.qemu_identity()
            record = {
                **identity,
                'source_inputs_sha256': compatibility.input_digest(identity),
                'sha256': sha(binary),
            }
            receipt.write_text(json.dumps(record))
            with patch.object(emulator, 'QEMU_OUT', out), patch.object(emulator, 'engine') as engine:
                self.assertEqual(emulator.qemu_artifact(), (binary, record))
                before = receipt.read_bytes()
                emulator.build_qemu()
                engine.assert_not_called()
                self.assertEqual(receipt.read_bytes(), before)
                binary.write_bytes(b'changed executable')
                with self.assertRaisesRegex(RuntimeError, 'modified QEMU artifact'):
                    emulator.build_qemu()
                self.assertEqual(receipt.read_bytes(), before)

    def test_failed_new_qemu_build_preserves_previous_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            out = work / 'qemu-out'
            out.mkdir()
            binary = out / emulator.QEMU_BINARY
            receipt = out / 'artifact.json'
            binary.write_bytes(b'previous emulator')
            previous = json.dumps({'sha256': sha(binary), 'source_inputs_sha256': 'old'})
            receipt.write_text(previous)
            with (patch.object(emulator, 'WORK', work), patch.object(emulator, 'QEMU_OUT', out),
                  patch.object(emulator, 'engine_path', side_effect=str),
                  patch.object(emulator, 'engine', side_effect=RuntimeError('Ninja failed'))):
                with self.assertRaisesRegex(RuntimeError, 'Ninja failed'):
                    emulator.build_qemu()
            self.assertEqual(binary.read_bytes(), b'previous emulator')
            self.assertEqual(receipt.read_text(), previous)
            self.assertFalse(list(work.glob('qemu-artifact-*')))

    def test_completed_qemu_pair_survives_relink_and_failed_archive_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'qemu-out'
            out.mkdir()
            binary, receipt = out / 'qemu-system-mipsel', out / 'artifact.json'
            binary.write_bytes(b'original emulator')
            original = json.dumps({'sha256': sha(binary)})
            receipt.write_text(original)
            archive = out.with_name('qemu-artifact-' + sha(receipt))
            preserve_qemu_artifact(out)
            binary.write_bytes(b'new build')
            self.assertFalse(receipt.exists())
            self.assertEqual((archive / binary.name).read_bytes(), b'original emulator')
            self.assertEqual((archive / receipt.name).read_text(), original)
            receipt.write_text(original)
            with self.assertRaisesRegex(RuntimeError, 'existing QEMU binary'):
                preserve_qemu_artifact(out)
            binary.write_bytes(b'original emulator')
            (archive / binary.name).write_bytes(b'damaged archive')
            with self.assertRaisesRegex(RuntimeError, 'archived QEMU binary'):
                preserve_qemu_artifact(out)
            self.assertEqual(receipt.read_text(), original)


if __name__ == '__main__':
    unittest.main()
