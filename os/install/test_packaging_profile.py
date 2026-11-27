"""Generated static ELF properties and fail-closed packaging controls; no device data."""
import hashlib
import json
from pathlib import Path
import random
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from packaging_profile import mapped_image, production, restore_mappings
ROOT = Path(__file__).resolve().parents[2]


def elf(payload=b'generated code', debug=b'DWARF', symbols=True):
    # All debug/table payload lives outside the static PT_LOAD mapping.
    names = b'\0.text\0.bss\0.symtab\0.strtab\0.shstrtab\0.debug_info\0'
    textoff, padding = 84, b'\0' * 14
    sym = b'generated symbols' if symbols else b''
    strings = b'\0synthetic\0'
    strings_off = textoff + len(payload) + len(padding) + len(sym)
    names_off = strings_off + len(strings)
    debug_off = names_off + len(names)
    table = debug_off + len(debug)
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    header = struct.pack('<HHIIIIIHHHHHH', 2, 8, 1, 0x10054, 52, table, 0x50001404, 52, 32, 1, 40, 7, 5)
    program = struct.pack('<IIIIIIII', 1, 0, 0x10000, 0x10000, textoff + len(payload) + len(padding), textoff + len(payload) + 128, 5, 4096)
    def section(name, kind, flags, address, off, size):
        return struct.pack('<IIIIIIIIII', names.index(name), kind, flags, address, off, size, 0, 0, 1, 0)
    sections = [bytes(40), section(b'.text', 1, 6, 0x10054, textoff, len(payload)),
                section(b'.bss', 8, 3, 0x20000, 0, 128),
                section(b'.symtab' if symbols else b'.debug_info', 2, 0, 0, textoff + len(payload) + len(padding), len(sym)),
                section(b'.strtab', 3, 0, 0, strings_off, len(strings)),
                section(b'.shstrtab', 3, 0, 0, names_off, len(names)),
                section(b'.debug_info', 1, 0, 0, debug_off, len(debug))]
    return ident + header + program + payload + padding + sym + strings + names + debug + b''.join(sections)


class PackagingProfile(unittest.TestCase):
    def test_seeded_mapping_and_allocation_properties(self):
        rng = random.Random(0x53545249)
        for _ in range(128):
            payload = rng.randbytes(rng.randrange(1, 512))
            original = elf(payload, rng.randbytes(rng.randrange(1, 512)))
            candidate = bytearray(elf(payload, b''))
            # Model GNU strip trimming zero tail bytes from p_filesz.
            struct.pack_into('<I', candidate, 52 + 16, 84 + len(payload))
            fixed = restore_mappings(original, bytes(candidate))
            self.assertEqual(mapped_image(original), mapped_image(fixed))
            self.assertLess(len(fixed), len(original))
            self.assertNotEqual(original[32:36], fixed[32:36])

    def test_corruption_and_symbols_fail_closed(self):
        original = elf()
        for candidate in (b'not ELF', elf(symbols=False), elf(payload=b'changed code size')):
            with self.assertRaises(ValueError):
                restore_mappings(original, candidate)
        for end in range(84):
            with self.assertRaises((ValueError, struct.error)):
                mapped_image(original[:end])
        dynamic = bytearray(original)
        struct.pack_into('<I', dynamic, 52, 3)
        with self.assertRaisesRegex(ValueError, 'dynamic'):
            mapped_image(dynamic)

    def test_archives_match_deployed_and_original_sources(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/perf') as directory:
            base = Path(directory)
            root, out, tool = base / 'root', base / 'output', base / 'strip'
            tool.write_bytes(b'generated tool identity')
            files = root / 'usr/libexec/q2-network'; files.mkdir(parents=True)
            original = elf(debug=b'generated dwarf' * 128)
            manifest = {'archive_sha256': 'synthetic', 'files': {}}
            for name in ('tailscale', 'tailscaled'):
                (files / name).write_bytes(original)
                manifest['files'][name] = {'q2_sha256': hashlib.sha256(original).hexdigest()}
            def strip(args, **kwargs):
                Path(args[3]).write_bytes(elf(debug=b''))
            with patch('packaging_profile.subprocess.check_output', return_value='generated strip 1\n'), \
                 patch('packaging_profile.subprocess.run', side_effect=strip):
                result = production(root, out, manifest, tool)
            for name, row in result['files'].items():
                self.assertEqual((out / row['symbols']).read_bytes(), original)
                self.assertEqual(hashlib.sha256((files / name).read_bytes()).hexdigest(), row['packaged_sha256'])
                self.assertTrue(row['loader_mappings_preserved'])
            self.assertEqual(json.loads((out / 'symbols/tailscale/manifest.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                production(root, out, manifest, tool)

    def test_input_mismatch_symlink_and_bad_strip_preserve_pair(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/perf') as directory:
            base = Path(directory); root = base / 'root'
            files = root / 'usr/libexec/q2-network'; files.mkdir(parents=True)
            original = elf()
            manifest = {'files': {}}
            for name in ('tailscale', 'tailscaled'):
                (files / name).write_bytes(original)
                manifest['files'][name] = {'q2_sha256': hashlib.sha256(original).hexdigest()}
            (files / 'tailscaled').unlink(); (files / 'tailscaled').symlink_to(files / 'tailscale')
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                production(root, base / 'out', manifest, base / 'unused')
            self.assertFalse((base / 'out').exists())
            (files / 'tailscaled').unlink(); (files / 'tailscaled').write_bytes(b'wrong input')
            with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                production(root, base / 'mismatch', manifest, base / 'unused')
            (files / 'tailscaled').write_bytes(original)
            tool = base / 'strip'; tool.write_bytes(b'fixture tool')
            def bad_strip(args, **kwargs): Path(args[3]).write_bytes(b'corrupted')
            with patch('packaging_profile.subprocess.check_output', return_value='generated strip 1\n'), \
                 patch('packaging_profile.subprocess.run', side_effect=bad_strip):
                with self.assertRaises(ValueError): production(root, base / 'bad', manifest, tool)
            self.assertEqual((files / 'tailscale').read_bytes(), original)
            self.assertEqual((files / 'tailscaled').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
