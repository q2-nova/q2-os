#!/usr/bin/env python3
"""Confirm that the SDK and our audit reject a real legacy-NaN object."""
import json
import subprocess
from audit import elf
from build import WORK

out = WORK / 'toolchain-negative'
out.mkdir(exist_ok=True)
cc = WORK / 'out/host/bin/mipsel-buildroot-linux-gnu-gcc'
(out / 'normal.c').write_text('int main(void) { return 0; }\n')
subprocess.run([cc, out / 'normal.c', '-o', out / 'normal'], check=True)
elf(out / 'normal')
(out / 'legacy.c').write_text('float legacy(float x) { return x + x; }\n')
(out / 'main.c').write_text('extern float legacy(float); int main(void) { return legacy(1) != 2; }\n')
subprocess.run([cc, '-mnan=legacy', '-c', out / 'legacy.c', '-o', out / 'legacy.o'], check=True)
result = subprocess.run([cc, out / 'main.c', out / 'legacy.o', '-o', out / 'rejected'], text=True, capture_output=True)
(out / 'link.log').write_text(result.stdout + result.stderr)
if result.returncode == 0 or 'NaN' not in result.stderr and 'nan' not in result.stderr:
    raise AssertionError('expected a NaN-mode linker rejection')
try:
    elf(out / 'legacy.o')
except ValueError as e:
    if 'NaN2008' not in str(e): raise
else: raise AssertionError('audit accepted legacy NaN')
(out / 'result.json').write_text(json.dumps({'passed': True, 'checks': ['ordinary SDK CRT linkage is o32/FP64/NaN2008 with non-executable stack', 'linker rejects legacy NaN object', 'audit rejects legacy NaN object']}, indent=2) + '\n')
print((out / 'result.json').read_text())
