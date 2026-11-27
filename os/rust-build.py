#!/usr/bin/env python3
"""Build custom Rust std, ABI probe and player with the generated GNU SDK."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from build import ROOT, WORK, LOCK, run, sha
sys.path.insert(0, str(ROOT / 'tools'))
import x2000_toolchain

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', default='out')
args = parser.parse_args()
if not args.output.replace('-', '').replace('_', '').isalnum(): parser.error('invalid output name')
out = WORK / args.output
startup_profile = out / 'artifacts/startup-read-ahead'
if startup_profile.exists() or startup_profile.is_symlink():
    raise SystemExit('preserving existing startup read-ahead profile; use a fresh --output')
for name in ('q2nd', 'q2-portal', 'q2-rust-probe'):
    if ((out / 'artifacts' / name).exists() or (out / 'artifacts' / (name + '.size.json')).exists()
            or (out / 'symbols' / name).exists()):
        raise SystemExit('preserving existing artifact/symbols; use a fresh --output')
sdk = out / 'host'
if not (sdk / 'mipsel-buildroot-linux-gnu/sysroot/usr/lib/libq2probe.so').exists():
    raise SystemExit('finish the Buildroot build, including q2-probes, first')
cc = sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
if not cc.exists(): raise SystemExit('build the Buildroot SDK first')
target = WORK / 'mipsel-q2-os-linux-gnu.json'
spec = json.loads((ROOT / 'q2/toolchain/mipsel-q2-linux-gnu.json').read_text())
# The X2000 toolchain (tools/x2000_toolchain.py, docs/x2000-toolchain.md);
# Q2_X2000=0 builds with the stock nightly and target specification.
x2000 = x2000_toolchain.enabled()
if x2000:
    spec = x2000_toolchain.spec(spec)
target.write_text(json.dumps(spec, indent=2) + '\n')
env = os.environ.copy()
env.update({
    'Q2_OS_CC': str(cc),
    'CC_mipsel_q2_os_linux_gnu': str(cc),
    'AR_mipsel_q2_os_linux_gnu': str(sdk / 'bin/mipsel-buildroot-linux-gnu-ar'),
    'CARGO_TARGET_MIPSEL_Q2_OS_LINUX_GNU_LINKER': str(ROOT / 'os/rust-link.py'),
    'CARGO_TARGET_DIR': str(WORK / (('rust-target' if args.output == 'out' else 'rust-target-' + args.output)
                                    + ('-x2000' if x2000 else ''))),
    'CARGO_BUILD_JOBS': '8',
    'SOURCE_DATE_EPOCH': str(LOCK['source_date_epoch']),
    'RUSTFLAGS': '--remap-path-prefix=' + str(ROOT) + '=/q2-source',
    'CARGO_PROFILE_RELEASE_DEBUG': 'line-tables-only',
    'CARGO_PROFILE_RELEASE_STRIP': 'none',
})
if x2000:
    env.update(x2000_toolchain.cargo_env(sdk=sdk))
# std's default build-std features with backtrace-trace-only: panic
# backtraces (RUST_BACKTRACE) print raw PCs, resolved off the device from the
# unstripped companion, and std links no symbolizer (gimli, addr2line).
base = ['cargo', '+' + LOCK['rust_toolchain'], 'build', '--release', '--offline',
        '-Zbuild-std=std,panic_abort', '-Zbuild-std-features=panic-unwind,backtrace,backtrace-trace-only', '-Zjson-target-spec', '--target', target]
run(['python3', ROOT / 'tools/build_stall_unwind.py', '--target', 'q2',
     '--sdk', sdk, '--out', out / 'diagnostic-unwinder'], env=env)
env['Q2_UNWIND_PREFIX'] = str(out / 'diagnostic-unwinder/install')
# Profile-guided optimization of the player (the device owner, 2026-11-17): profile this
# source in the emulator with this SDK (tools/app_pgo.py), then build with it.
pgo = out / 'pgo'
if pgo.exists(): shutil.rmtree(pgo)
run(['python3', ROOT / 'tools/app_pgo.py', 'profile', '--out', pgo, '--sdk', sdk], env=env)
player_env = {**env, 'RUSTFLAGS': env['RUSTFLAGS'] + ' -Cprofile-use=' + str(pgo / 'q2nd.profdata')}
run(base + ['--locked', '-p', 'q2nd', '--no-default-features'], env=player_env)
run(base + ['--locked', '-p', 'q2-portal'], env=env)
run(base + ['--locked', '--manifest-path', ROOT / 'os/probes/rust/Cargo.toml'], env=env)
dest = out / 'artifacts'
dest.mkdir(exist_ok=True)
for name in ['q2nd', 'q2-portal', 'q2-rust-probe']:
    source = Path(env['CARGO_TARGET_DIR']) / 'mipsel-q2-os-linux-gnu/release' / name
    run(['python3', ROOT / 'tools/elf_symbols.py', '--source', source,
         '--deployed', dest / name, '--symbols', out / 'symbols' / name,
         '--prefix', str(sdk / 'bin/mipsel-buildroot-linux-gnu-'),
         '--toolchain', LOCK['rust_toolchain'], '--source-commit',
         subprocess.check_output(['git', '-C', ROOT, 'rev-parse', 'HEAD'], text=True).strip()], env=env)
    print(name, sha(dest / name))
    run(['python3', ROOT / 'tools/elf_size.py', '--binary', dest / name,
         '--readelf', sdk / 'bin/mipsel-buildroot-linux-gnu-readelf',
         '--out', dest / (name + '.size.json')])
run(['python3', ROOT / 'tools/app_startup_profile.py', '--player', dest / 'q2nd',
     '--sdk', sdk, '--out', startup_profile], env=env)
