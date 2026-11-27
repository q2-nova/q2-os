#!/usr/bin/env python3
"""Extract the exported SDK elsewhere and test its relocated default ABI."""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from audit import elf
from build import WORK, run, sha

ARCHIVE = 'mipsel-buildroot-linux-gnu_sdk-buildroot.tar.gz'


def qualify(archive, work):
    work.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'sdk_sha256': sha(archive)}
    with tempfile.TemporaryDirectory(prefix='sdk-relocation-', dir=work) as directory:
        with tarfile.open(archive) as stream:
            # SDK sysroot symlinks include absolute target paths; preserve the
            # trusted locally built archive rather than dereferencing its links.
            stream.extractall(directory, filter='fully_trusted')
        sdk = Path(directory) / archive.name.removesuffix('.tar.gz')
        run([sdk / 'relocate-sdk.sh'])
        cc = sdk / 'bin/mipsel-buildroot-linux-gnu-gcc'
        sysroot = Path(subprocess.check_output([cc, '-print-sysroot'], text=True).strip()).resolve()
        sysroot.relative_to(sdk.resolve())  # Reject an accidental original-tree dependency.
        source = sdk / 'probe.c'
        source.write_text('#include <time.h>\n#include <stdio.h>\n'
                          '_Static_assert(sizeof(time_t) == 8, "time64");\n'
                          'int main(void) { return puts("relocated Q2 SDK")==EOF; }\n')
        run([cc, '-D_TIME_BITS=64',
             '-D_FILE_OFFSET_BITS=64', source, '-o', sdk / 'probe'])
        report['elf'] = elf(sdk / 'probe')
        library = sysroot / 'usr/lib/libq2platform.so'
        nm = sdk / 'bin/mipsel-buildroot-linux-gnu-nm'
        symbols = {line.split()[-1] for line in subprocess.check_output(
            [nm, '-D', '--defined-only', library], text=True).splitlines() if line.split()}
        touchscreen = {'q2_touchscreen_open', 'q2_touchscreen_state'}
        if not touchscreen <= symbols:
            raise SystemExit('exported SDK platform bridge lacks touchscreen ABI')
        report['platform_touchscreen_symbols'] = sorted(touchscreen)
        report['sysroot_is_relocated'] = True
        report['passed'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=WORK / 'out/images' / ARCHIVE)
    parser.add_argument('--report', type=Path, default=WORK / 'sdk-test.json')
    args = parser.parse_args()
    if args.report.exists() or args.report.is_symlink():
        parser.error('choose a fresh qualification report path')
    report = qualify(args.archive, WORK)
    with args.report.open('x') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
