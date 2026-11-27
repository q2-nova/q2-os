#!/usr/bin/env python3
"""Generated ELF/config properties; no owner state or device access."""
import random
import argparse
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from profile_inventory import config_inventory, elf_inventory, inventory, named_file
from profile_strip_experiment import ROOT, experiment


def elf(wide=False, endian='<', symbols=b'host symbols', payload=b'kernel', bss=4096):
    names = b'\0.text\0.bss\0.symtab\0.shstrtab\0'
    hfmt = endian + ('HHIQQQIHHHHHH' if wide else 'HHIIIIIHHHHHH')
    sfmt = endian + ('IIQQQQIIQQ' if wide else 'IIIIIIIIII')
    hsize, ssize = 16 + struct.calcsize(hfmt), struct.calcsize(sfmt)
    text_offset = hsize
    sym_offset = text_offset + len(payload)
    names_offset = sym_offset + len(symbols)
    table_offset = names_offset + len(names)
    header = struct.pack(hfmt, 2, 8, 1, 0x80000000, 0, table_offset, 0,
                         hsize, 0, 0, ssize, 5, 4)
    def section(name, kind, flags, address, offset, length):
        return struct.pack(sfmt, name, kind, flags, address, offset, length, 0, 0, 1, 0)
    sections = [bytes(ssize), section(1, 1, 6, 0x80000000, text_offset, len(payload)),
                section(7, 8, 3, 0x81000000, 0x7fffffff, bss),
                section(12, 2, 0, 0, sym_offset, len(symbols)),
                section(20, 3, 0, 0, names_offset, len(names))]
    return b'\x7fELF' + bytes([2 if wide else 1, 1 if endian == '<' else 2, 1]) + bytes(9) + header + payload + symbols + names + b''.join(sections)


class ProfileInventory(unittest.TestCase):
    def test_sparse_config_distinguishes_missing_and_disabled(self):
        result = config_inventory(b'CONFIG_LTO_NONE=y\n# CONFIG_AUDIT is not set\n')
        self.assertEqual(result['CONFIG_AUDIT'], 'n')
        self.assertIsNone(result['CONFIG_KFENCE'])
        self.assertEqual(result['CONFIG_LTO_NONE'], 'y')

    def test_config_omits_unrelated_and_private_values(self):
        result = config_inventory(b'password=synthetic\nCONFIG_INITRAMFS_SOURCE="synthetic/path"\nBR2_INIT_BUSYBOX=y\n')
        self.assertNotIn('synthetic', str(result))
        with self.assertRaises(ValueError): config_inventory(b'password=synthetic\n')
        with self.assertRaises(ValueError): config_inventory(b'CONFIG_LTO_NONE=y\nCONFIG_LTO_NONE=n\n')

    def test_seeded_elf_classes_endianness_and_strip_invariant(self):
        rng = random.Random(0x5132)
        for _ in range(128):
            wide, endian = rng.choice([False, True]), rng.choice(['<', '>'])
            payload = rng.randbytes(rng.randrange(1, 1024))
            bss = rng.randrange(1, 65536)
            before = elf_inventory(elf(wide, endian, rng.randbytes(80), payload, bss))
            after = elf_inventory(elf(wide, endian, b'', payload, bss))
            self.assertEqual(before['allocated_sha256'], after['allocated_sha256'])
            self.assertEqual(before['allocated_file_bytes'], len(payload))
            self.assertEqual(before['allocated_nobits_bytes'], bss)
            self.assertEqual(before['nonallocated_file_bytes'] - after['nonallocated_file_bytes'], 80)
            changed = elf_inventory(elf(wide, endian, b'', payload + b'x', bss))
            self.assertNotEqual(after['allocated_sha256'], changed['allocated_sha256'])

    def test_seeded_truncation_is_rejected(self):
        full = elf()
        for end in range(4, len(full)):
            with self.assertRaises(ValueError): elf_inventory(full[:end])
        self.assertIsNone(elf_inventory(b'not ELF'))

    def test_output_deterministic_and_labelled_without_runtime_claim(self):
        self.assertEqual(inventory('baseline', [], []), inventory('baseline', [], []))
        self.assertIn('not runtime RAM', inventory('baseline', [], [])['measurement'])

    def test_explicit_input_and_experiment_failure_boundaries(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/perf') as directory:
            root = Path(directory)
            source = root / 'kernel'
            source.write_bytes(elf())
            alias = root / 'alias'; alias.symlink_to(source)
            with self.assertRaisesRegex(ValueError, 'explicit regular file'):
                inventory('baseline', [], [('alias', alias)])
            with self.assertRaisesRegex(ValueError, 'duplicate label'):
                inventory('baseline', [], [('kernel', source), ('kernel', source)])
            with self.assertRaisesRegex(ValueError, 'NEW directory'):
                experiment(Path('unused'), [('kernel', source)], root)
            with self.assertRaisesRegex(ValueError, 'NEW directory'):
                experiment(Path('unused'), [('kernel', source)], ROOT / 'outside-build')
            with self.assertRaisesRegex(ValueError, 'uniquely labelled'):
                experiment(Path('unused'), [('kernel', source)] * 2, root / 'duplicate')
            for label in ('.', '..', '../escape', '/absolute'):
                with self.assertRaises(argparse.ArgumentTypeError): named_file(label + '=file')
            with self.assertRaisesRegex(ValueError, 'reserved'):
                experiment(Path('unused'), [('result.json', source)], root / 'reserved')

    def test_strip_copy_checks_allocated_bytes_and_unchanged_input(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/perf') as directory:
            root = Path(directory)
            source = root / 'kernel'; original = elf()
            source.write_bytes(original)
            def strip_copy(args, **kwargs):
                # Simulate a faulty strip producing different loaded code.
                Path(args[3]).write_bytes(elf(payload=b'changed'))
            with patch('profile_strip_experiment.subprocess.check_output', return_value='generated strip 1\n'), \
                 patch('profile_strip_experiment.subprocess.run', side_effect=strip_copy):
                with self.assertRaisesRegex(ValueError, 'allocated sections changed'):
                    experiment(Path('generated'), [('kernel', source)], root / 'bad')
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((root / 'bad/result.json').exists())
            def unchanged_copy(args, **kwargs):
                Path(args[3]).write_bytes(original)
            with patch('profile_strip_experiment.subprocess.check_output', return_value='generated strip 1\n'), \
                 patch('profile_strip_experiment.subprocess.run', side_effect=unchanged_copy):
                result = experiment(Path('generated'), [('kernel', source)], root / 'good')
            self.assertEqual(result['saved_bytes'], {'kernel': 0})
            self.assertTrue((root / 'good/result.json').is_file())


if __name__ == '__main__':
    unittest.main()
