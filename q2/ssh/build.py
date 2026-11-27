#!/usr/bin/env python3
"""Build the pinned Dropbear server against the Q2's stock glibc.

  q2/ssh/build.py --toolchain-repo Q2_TOOLCHAIN_CHECKOUT

start.o comes from q2/toolchain/start.c of a q2-toolchain checkout. The
stock firmware's glibc sysroot is build/sysroot.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
VERSION = '2026.94'
SHA256 = 'e098034a843699200c8c977a991fff73159735bf795d5f72ef672c41a6b1ae81'
BUILD = ROOT / 'build/ssh'

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--toolchain-repo', type=Path, required=True)
    args = parser.parse_args()
    start = args.toolchain_repo.resolve() / 'q2/toolchain/start.c'
    if not start.is_file():
        parser.error(f'{args.toolchain_repo}: not a q2-toolchain checkout')
    BUILD.mkdir(parents=True, exist_ok=True)
    archive = BUILD / f'dropbear-{VERSION}.tar.bz2'
    if not archive.exists():
        subprocess.run(['curl', '-fL', f'https://matt.ucc.asn.au/dropbear/releases/{archive.name}', '-o', str(archive)], check=True)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == SHA256, 'Dropbear source hash mismatch'
    source = BUILD / 'source'
    if not source.exists():
        source.mkdir()
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                member.name = '/'.join(member.name.split('/')[1:])
                if member.name:
                    tar.extract(member, source, filter='data')
    shutil.copyfile(ROOT / 'q2/ssh/localoptions.h', source / 'localoptions.h')
    server = source / 'src/svr-main.c'
    text = server.read_text()
    if 'q2_ssh_bind_parent();' not in text:
        assert text.count('\tsvr_getopts(argc, argv);') == 1
        server.write_text(text.replace('\tsvr_getopts(argc, argv);', '\tq2_ssh_bind_parent();\n\tsvr_getopts(argc, argv);'))
    auth = source / 'src/svr-auth.c'
    text = auth.read_text()
    if 'q2_ssh_bind_parent();' not in text:
        before = '}\n\nvoid svr_raise_gid_utmp(void) {'
        assert text.count(before) == 1
        auth.write_text(text.replace(before, '\tq2_ssh_bind_parent();\n' + before))
    nightly = os.environ.get('Q2_NIGHTLY', 'nightly-2026-08-21')
    rust = Path(subprocess.check_output(['rustc', '+' + nightly, '--print', 'sysroot'], text=True).strip())
    llvm = rust / 'lib/rustlib/x86_64-unknown-linux-gnu/bin'
    env = os.environ.copy()
    env.update(Q2_SYSROOT=str(ROOT / 'build/sysroot'), Q2_LLD_DIR=str(llvm / 'gcc-ld'), Q2_START=str(BUILD / 'start.o'))
    subprocess.run(['clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mfp64', '-mnan=2008', '-mabicalls', '-fno-pic', '-c',
                    str(start), '-o', env['Q2_START']], check=True)
    env.update(CC=str(ROOT / 'q2/ssh/cc'), AR=str(llvm / 'llvm-ar'), RANLIB=str(llvm / 'llvm-ar') + ' s',
               CFLAGS='-Os -D_GNU_SOURCE -D_FILE_OFFSET_BITS=64')
    subprocess.run(['./configure', '--host=mipsel-linux-gnu', '--disable-zlib', '--disable-harden', '--disable-shadow',
                    '--disable-lastlog', '--disable-utmp', '--disable-utmpx', '--disable-wtmp', '--disable-wtmpx',
                    '--disable-loginfunc', '--disable-pututline', '--disable-pututxline'], cwd=source, env=env, check=True)
    subprocess.run(['make', '-j4', 'PROGRAMS=dropbear dropbearkey',
                    f'CFLAGS={env["CFLAGS"]} -include {ROOT}/q2/ssh/parent.h'], cwd=source, env=env, check=True)
    out = BUILD / 'bin'
    out.mkdir(exist_ok=True)
    for name in ('dropbear', 'dropbearkey'):
        subprocess.run([str(llvm / 'llvm-strip'), '-o', str(out / name), str(source / name)], check=True)
    shutil.copyfile(source / 'LICENSE', out / 'DROPBEAR-LICENSE')
    print('Built SSH files in', out)

if __name__ == '__main__':
    main()
