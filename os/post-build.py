#!/usr/bin/env python3
"""Stage declared application artifacts and deterministic development fixtures."""
import json
import os
from pathlib import Path
import shutil
import sys
from fixtures import generate
from compatibility import PROFILES
from timezone_data import validate as validate_timezone_data

root = Path(__file__).resolve().parent.parent
target = Path(sys.argv[1]).resolve()
if 'build' not in target.parts or target.name != 'target':
    raise SystemExit('refusing unexpected Buildroot target directory')
validate_timezone_data(target)
# The packaged service runs before writable state and assumes a writable /var.
(target / 'etc/init.d/S01seedrng').unlink(missing_ok=True)
artifacts = target.parent / 'artifacts'
player = target / 'usr/bin/q2nd'
has_player = (artifacts / 'q2nd').is_file() or player.is_file()
for name in ['q2nd', 'q2-rust-probe']:
    source = artifacts / name
    if source.exists():
        shutil.copyfile(source, target / 'usr/bin' / name)
        (target / 'usr/bin' / name).chmod(0o755)
dest = target / 'usr/libexec'
dest.mkdir(exist_ok=True)
launcher = dest / 'q2-launcher'
launcher.unlink(missing_ok=True)
if has_player:
    shutil.copyfile(root / 'os/launcher.sh', launcher)
    launcher.chmod(0o755)
for name, link in [('etc/dropbear', '/state/ssh'), ('root/.ssh', '/state/ssh/client')]:
    p = target / name
    if p.is_symlink(): p.unlink()
    if not p.exists(): p.symlink_to(link)
generate(target / 'usr/share/q2-fixtures')
lock = json.loads((root / 'os/sources.json').read_text())
(target / 'etc/q2-build-epoch').write_text(str(lock['source_date_epoch']) + '\n')
(target / 'etc/q2-os-release.json').write_text(json.dumps({
    'buildroot': lock['buildroot']['version'], 'kernel_source_commit': PROFILES['os-kernel']['origin_commit'],
    'emulation_only': True, 'private_configuration_imported': False}, indent=2) + '\n')
