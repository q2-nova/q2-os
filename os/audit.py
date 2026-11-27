#!/usr/bin/env python3
"""Audit the staged ELF ABI and produce a content manifest (no private input)."""
import argparse
import json
from pathlib import Path
import struct
from build import ROOT, WORK, LOCK, sha


def elf(path):
    data = path.read_bytes()
    if data[:4] != b'\x7fELF': return None
    if data[4:6] != b'\x01\x01': raise ValueError(f'{path}: not ELF32 little endian')
    machine, = struct.unpack_from('<H', data, 18)
    flags, = struct.unpack_from('<I', data, 36)
    if machine != 8 or not flags & 0x1000 or not flags & 0x400:
        raise ValueError(f'{path}: requires MIPS o32 NaN2008, flags={flags:#x}')
    if flags & 0xf0000000 != 0x70000000:
        raise ValueError(f'{path}: requires MIPS32r2, flags={flags:#x}')
    offset, = struct.unpack_from('<I', data, 32)
    size, count, names_index = struct.unpack_from('<HHH', data, 46)
    sections = [struct.unpack_from('<10I', data, offset + i * size) for i in range(count)]
    names = sections[names_index]
    names = data[names[4]:names[4]+names[5]]
    needed = []
    interpreter = None
    stack_flags = None
    phoff, = struct.unpack_from('<I', data, 28)
    phsize, phcount = struct.unpack_from('<HH', data, 42)
    for i in range(phcount):
        ph = struct.unpack_from('<8I', data, phoff + i * phsize)
        if ph[0] == 0x6474e551: stack_flags = ph[6]
        if ph[0] == 3:
            interpreter = data[ph[1]:ph[1]+ph[4]].rstrip(b'\0').decode()
    fp = None
    for section in sections:
        name = names[section[0]:].split(b'\0', 1)[0]
        if section[1] == 6:
            strings = sections[section[6]]
            strings = data[strings[4]:strings[4]+strings[5]]
            for pos in range(section[4], section[4]+section[5], 8):
                tag, value = struct.unpack_from('<II', data, pos)
                if tag == 1: needed.append(strings[value:].split(b'\0', 1)[0].decode())
        if name == b'.MIPS.abiflags':
            fields = struct.unpack_from('<H6B4I', data, section[4])
            fp = fields[6]
            if fields[1:6] != (32, 2, 1, 2, 0) or fp != 6:
                raise ValueError(f'{path}: unexpected MIPS ABI flags {fields}')
    if stack_flags != 6: raise ValueError(f'{path}: missing non-executable stack contract ({stack_flags})')
    if fp is None: raise ValueError(f'{path}: missing MIPS ABI flags')
    return {'flags': hex(flags), 'fp_abi': fp, 'needed': needed, 'interpreter': interpreter, 'stack_flags': stack_flags}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='out')
    args = p.parse_args()
    if not args.output.replace('-', '').replace('_', '').isalnum(): p.error('invalid output name')
    target = WORK / args.output / 'target'
    entries = []
    for path in sorted(target.rglob('*')):
        name = '/' + str(path.relative_to(target))
        if path.is_symlink():
            entries.append({'path': name, 'link': str(path.readlink())})
        elif path.is_file():
            entry = {'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path),
                     'mode': oct(path.stat().st_mode & 0o7777)}
            with path.open('rb') as f: magic = f.read(4)
            if magic == b'\x7fELF': entry['elf'] = elf(path)
            entries.append(entry)
    if not any('elf' in entry for entry in entries): raise RuntimeError('no ELF files found')
    paths = {entry['path'] for entry in entries}
    for entry in entries:
        if 'elf' not in entry: continue
        abi = entry['elf']
        if abi['interpreter'] and abi['interpreter'] not in paths:
            raise RuntimeError(f"{entry['path']}: missing interpreter {abi['interpreter']}")
        for library in abi['needed']:
            if not any(directory + library in paths for directory in ['/lib/', '/usr/lib/']):
                raise RuntimeError(f"{entry['path']}: missing library {library}")
    manifest = {'sources': LOCK, 'emulation_only': True, 'files': entries}
    dest = WORK / args.output / 'images/rootfs-manifest.json'
    dest.write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'manifest': str(dest.relative_to(ROOT)), 'files': len(entries),
                      'elf_files': sum('elf' in e for e in entries), 'sha256': sha(dest)}))

if __name__ == '__main__': main()
