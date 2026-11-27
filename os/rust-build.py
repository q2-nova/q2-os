#!/usr/bin/env python3
r"""Build the Rust ABI probe and stage the player for the source-built emulator image.

  python3 os/rust-build.py --toolchain-repo Q2_TOOLCHAIN_CHECKOUT \
    [--compiler-work X2000_COMPILER_WORK] [--player PAIRED_PLAYER_BUILD] [--output NAME]

Builds q2-rust-probe (os/probes/rust) with the generated SDK in
build/os/NAME/host and the X2000 compiler of a q2-toolchain checkout
(Q2_X2000=0: the stock nightly), into build/os/NAME/artifacts/. With
--player, copies q2nd from a q2-player paired build (its
tools/build-target.py output) there too, after checking it against that
build's result.json. os/post-build.py installs both into the image.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
from build import ROOT, WORK, LOCK, run, sha

TARGET = 'mipsel-q2-os-linux-gnu'
PROBE = 'q2-rust-probe'


def toolchain(repo, compiler_work):
    """The checkout's x2000_toolchain module, with Q2_X2000_WORK set for it."""
    path = repo / 'tools/x2000_toolchain.py'
    if not path.is_file():
        raise SystemExit(f'{path}: not a q2-toolchain checkout')
    if compiler_work is not None:
        os.environ['Q2_X2000_WORK'] = str(compiler_work.resolve())
    spec = importlib.util.spec_from_file_location('x2000_toolchain', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paired_player(build):
    """q2nd of a q2-player paired build, checked against its result.json."""
    player = build / 'q2nd'
    try:
        result = json.loads((build / 'result.json').read_text())
        if (result.get('passed') is not True or result.get('target') != TARGET
                or result.get('artifacts', {}).get('q2nd', {}).get('sha256') != sha(player)):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        raise SystemExit(f'{build}: not a passed q2-player paired build whose q2nd matches result.json') from None
    return player


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', default='out')
    parser.add_argument('--toolchain-repo', type=Path, required=True)
    parser.add_argument('--compiler-work', type=Path, help='built X2000 compiler; not needed with Q2_X2000=0')
    parser.add_argument('--player', type=Path, help='q2-player tools/build-target.py output directory')
    args = parser.parse_args()
    if not args.output.replace('-', '').replace('_', '').isalnum():
        parser.error('invalid output name')
    out = WORK / args.output
    dest = out / 'artifacts'
    for name in (PROBE, 'q2nd'):
        if (dest / name).exists():
            parser.error(f'preserving existing {dest / name}; use a fresh --output')
    player = paired_player(args.player.resolve()) if args.player else None
    sdk = out / 'host'
    if not (sdk / 'mipsel-buildroot-linux-gnu/sysroot/usr/lib/libq2probe.so').exists():
        parser.error('finish the Buildroot build, including q2-probes, first')
    cc = sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
    repo = args.toolchain_repo.resolve()
    x2000 = toolchain(repo, args.compiler_work)
    spec = json.loads((repo / 'q2/toolchain/mipsel-q2-linux-gnu.json').read_text())
    if x2000.enabled():
        if args.compiler_work is None:
            parser.error('--compiler-work is required unless Q2_X2000=0')
        receipt = json.loads(x2000.receipt_path(x2000.work_dir()).read_text())
        if receipt['pin']['rust_toolchain'] != LOCK['rust_toolchain']:
            parser.error('the X2000 compiler and os/sources.json name different Rust toolchains')
        spec = x2000.spec(spec)
    target = WORK / (TARGET + '.json')
    target.write_text(json.dumps(spec, indent=2) + '\n')
    env = os.environ.copy()
    for name in ('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS'):
        env.pop(name, None)
    env.update({
        'Q2_OS_CC': str(cc),
        'CC_mipsel_q2_os_linux_gnu': str(cc),
        'AR_mipsel_q2_os_linux_gnu': str(sdk / 'bin/mipsel-buildroot-linux-gnu-ar'),
        'CARGO_TARGET_MIPSEL_Q2_OS_LINUX_GNU_LINKER': str(ROOT / 'os/rust-link.py'),
        'CARGO_TARGET_DIR': str(WORK / ('rust-target-' + args.output + ('-x2000' if x2000.enabled() else ''))),
        'CARGO_BUILD_JOBS': '2',
        'SOURCE_DATE_EPOCH': str(LOCK['source_date_epoch']),
        'RUSTFLAGS': '--remap-path-prefix=' + str(ROOT) + '=/q2-source',
        'CARGO_PROFILE_RELEASE_STRIP': 'symbols',
    })
    if x2000.enabled():
        env.update(x2000.cargo_env(sdk=sdk))
    run(['cargo', '+' + LOCK['rust_toolchain'], 'build', '--release', '--offline', '--locked',
         '-Zbuild-std=std,panic_abort', '-Zbuild-std-features=panic-unwind,backtrace,backtrace-trace-only',
         '-Zjson-target-spec',
         '--target', target, '--manifest-path', ROOT / 'os/probes/rust/Cargo.toml'], env=env)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(env['CARGO_TARGET_DIR']) / TARGET / 'release' / PROBE, dest / PROBE)
    (dest / PROBE).chmod(0o755)
    print(PROBE, sha(dest / PROBE))
    if player is not None:
        shutil.copyfile(player, dest / 'q2nd')
        (dest / 'q2nd').chmod(0o755)
        print('q2nd', sha(dest / 'q2nd'))


if __name__ == '__main__':
    main()
