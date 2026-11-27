"""Opt-in packaging only: preserve runtime mappings, archive full debug originals."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

from profile_inventory import elf_inventory

NAMES = ('tailscale', 'tailscaled')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mapped_image(data):
    """Pinned static MIPS ELF loader layout and mapped bytes, except section locators."""
    if data[:7] != b'\x7fELF\x01\x01\x01' or len(data) < 52:
        raise ValueError('expected static little-endian MIPS ELF32')
    h = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    if h[:3] != (2, 8, 1) or h[7:9] != (52, 32) or not h[9]:
        raise ValueError('unexpected MIPS executable header')
    end = h[4] + h[8] * h[9]
    if end > len(data):
        raise ValueError('truncated program headers')
    programs = [struct.unpack_from('<IIIIIIII', data, h[4] + i * h[8]) for i in range(h[9])]
    if any(p[0] in (2, 3) for p in programs):
        raise ValueError('dynamic executable is outside this profile')
    normalized = bytearray(data)
    normalized[32:36] = bytes(4)  # e_shoff: strip relocates the section directory
    normalized[46:52] = bytes(6)  # e_shentsize/e_shnum/e_shstrndx: non-loader metadata
    segments = []
    for p in programs:
        if p[0] == 1:
            if p[1] + p[4] > len(data) or p[4] > p[5]:
                raise ValueError('invalid load segment')
            segments.append((p, hashlib.sha256(normalized[p[1]:p[1] + p[4]]).hexdigest()))
    if not segments:
        raise ValueError('missing load segments')
    return bytes(normalized[:52]), data[h[4]:end], segments


def restore_mappings(original, stripped):
    """GNU strip may trim mapped zero padding; retain every original mapped byte.

    Only the section directory locator fields in the ELF header may differ.
    The obsolete original section directory remains inert mapped padding, while
    ELF tools use strip's relocated directory. No executable code is modified.
    """
    mapped_image(original)
    mapped_image(stripped)
    if elf_inventory(original)['allocated_sha256'] != elf_inventory(stripped)['allocated_sha256']:
        raise ValueError('strip changed allocated sections')
    result = bytearray(stripped)
    directory = stripped[32:36], stripped[46:52]
    h = struct.unpack_from('<HHIIIIIHHHHHH', original, 16)
    for i in range(h[9]):
        p = struct.unpack_from('<IIIIIIII', original, h[4] + i * h[8])
        if p[0] == 1:
            if p[1] + p[4] > len(result):
                raise ValueError('stripped copy is shorter than original mappings')
            result[p[1]:p[1] + p[4]] = original[p[1]:p[1] + p[4]]
    result[:52] = original[:52]
    result[h[4]:h[4] + h[8] * h[9]] = original[h[4]:h[4] + h[8] * h[9]]
    result[32:36], result[46:52] = directory
    if mapped_image(original) != mapped_image(result):
        raise ValueError('ELF loader mapping changed')
    before, after = elf_inventory(original), elf_inventory(result)
    if before['allocated_sha256'] != after['allocated_sha256']:
        raise ValueError('allocated sections changed')
    for table in ('.symtab', '.strtab'):
        if not any(s['name'] == table for s in after['sections']):
            raise ValueError('symbol table removed: ' + table)
    return bytes(result)


def production(root, output, manifest, strip_tool):
    """Transform only the verified Tailscale pair, with full off-device symbols."""
    root, output, strip_tool = Path(root), Path(output), Path(strip_tool).resolve()
    archive = output / 'symbols/tailscale'
    if archive.exists() or archive.is_symlink():
        raise ValueError('symbol archive already exists; use a fresh production output')
    inputs = {n: root / 'usr/libexec/q2-network' / n for n in NAMES}
    for name, path in inputs.items():
        if path.is_symlink() or not path.is_file() or sha(path) != manifest['files'][name]['q2_sha256']:
            raise ValueError('staged Tailscale manifest mismatch: ' + name)
    archive.mkdir(parents=True)
    record = {'schema': 1, 'packaging_profile': 'production', 'hardware_qualified': False,
              'transform': 'GNU --strip-debug; restore original loader mappings except section-directory locators',
              'strip_tool_sha256': sha(strip_tool),
              'strip_version': subprocess.check_output([str(strip_tool), '--version'], text=True).splitlines()[0],
              'upstream': manifest, 'files': {}}
    # Verify both candidates before changing either deployed file.
    for name, source in inputs.items():
        original = archive / name
        shutil.copyfile(source, original)
        target = archive / (name + '.packaged')
        subprocess.run([str(strip_tool), '--strip-debug', '-o', str(target), str(original)], check=True)
        target.write_bytes(restore_mappings(original.read_bytes(), target.read_bytes()))
        if sha(original) != manifest['files'][name]['q2_sha256'] or sha(source) != sha(original):
            raise ValueError('input changed during packaging')
        record['files'][name] = {'original_sha256': sha(original), 'packaged_sha256': sha(target),
                                'original_bytes': original.stat().st_size, 'packaged_bytes': target.stat().st_size,
                                'symbols': str(original.relative_to(output)),
                                'allocated_sha256': elf_inventory(target.read_bytes())['allocated_sha256'],
                                'loader_mappings_preserved': True}
    if any(sha(source) != manifest['files'][name]['q2_sha256'] for name, source in inputs.items()):
        raise ValueError('input changed before packaging commit')
    for name, source in inputs.items():
        target = archive / (name + '.packaged')
        shutil.copyfile(target, source)
        source.chmod(0o755)
        target.unlink()
    (archive / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    return record
