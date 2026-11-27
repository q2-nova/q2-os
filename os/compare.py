#!/usr/bin/env python3
"""Compare independently built release artifacts; never compare booted disks."""
import json
from build import WORK, sha

names = ['artifacts/q2-rust-probe',
         'images/rootfs.squashfs', 'images/rootfs.cpio.gz',
         'images/rootfs.ext4', 'images/q2-sd-state.ext4', 'images/q2-sd.img']
report = {'passed': True, 'outputs': ['out', 'repeat'], 'artifacts': {}}
for name in names:
    hashes = {output: sha(WORK / output / name) for output in report['outputs']}
    equal = len(set(hashes.values())) == 1
    report['artifacts'][name] = {'equal': equal, 'sha256': hashes}
    report['passed'] &= equal
(WORK / 'reproducibility.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
if not report['passed']: raise SystemExit('independent build artifacts differ')
