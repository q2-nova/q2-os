#!/usr/bin/env python3
"""Link against the generated SDK, retaining only a checked LLVM metadata fix."""
import os
from pathlib import Path
import struct
import sys

for arg in sys.argv[1:]:
    p = Path(arg)
    if p.name != 'symbols.o': continue
    data = bytearray(p.read_bytes())
    if data[:6] != b'\x7fELF\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 8:
        raise SystemExit('unexpected Rust symbols.o format')
    # Only metadata objects with no executable code may receive this flag fix.
    off = struct.unpack_from('<I', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 46)
    for i in range(count):
        flags = struct.unpack_from('<I', data, off + i * size + 8)[0]
        length = struct.unpack_from('<I', data, off + i * size + 20)[0]
        if flags & 4 and length: raise SystemExit('refusing to relabel executable Rust symbols.o')
    flags = struct.unpack_from('<I', data, 36)[0]
    struct.pack_into('<I', data, 36, flags | 0x400)
    p.write_bytes(data)
compiler = os.environ['Q2_OS_CC']
os.execv(compiler, [compiler, '-mabi=32', '-march=mips32r2', '-mfp64',
                     '-mnan=2008', '-mabs=2008', *sys.argv[1:]])
