#!/usr/bin/env python3
"""Rebuild OS C programs with the X2000 clang and test them against the GCC builds.

The OS image's C userspace is built with the SDK's GCC. This rebuilds each
package from the same source and configuration with q2/toolchain/q2-x2000-cc
and runs both builds under the rig qemu-user. A clang build is offered for
the image only when every output is identical.

  os/x2000-c-build.py --buildroot-out build/.../os/out --out build/x2000-c/NAME [PACKAGE...]

Packages (default: all):
  busybox    the image's: the configured Buildroot tree with the ash patch and
             the blockdev applet, as tests/perf/build_storage_tools.py builds
             it, both compilers here; 33 applet runs on generated data
  dropbear   the image's: the Buildroot tree configured again with Buildroot's
             configure arguments and flags and made with its make line, both
             compilers here; config.h compared, key files read by both builds,
             SSH sessions between the builds in both directions, exit status
             with authorized_keys from -D
  alsa-lib   from the Buildroot tree, reconfigured; the GCC aplay through each
             libasound into a file with format, rate and channel conversion
  probes     os/probes, both compilers here: the package's own compiler lines,
             and tools/build_supervisor.py's for the supervisor and the thrash
             guard (the composer's OS layer builds those); q2-abi-probe output
  chrony     the release os/network-time/build.py pins, both compilers here;
             its own unit tests and the configuration print of chronyd
  wpa        the image's wpa_supplicant 2.12 and wpa_cli: the SAE/PMF build of
             os/hardware/build-wifi-tools.py --wpa3 (static libnl, OpenSSL
             3.6.5) in its q2-os-build container, both compilers; versions,
             wpa_passphrase, a configuration parse, the upstream module tests
  alsa-utils, alsa-lib-tools, ncurses, libxcrypt, zlib, kexec
             Buildroot's configured trees made again (buildroot_make: at
             Buildroot's own paths in the q2-os-build container, `make clean`
             and `make`, CC on the make line for clang): aplay, arecord,
             alsactl, alsamixer; libatopology, aserver; libncurses, libform,
             libmenu, libpanel; libcrypt; libz; kexec, vmcore-dmesg. Tests:
             aplay conversions, a topology load/save/build, curses drawing on
             4 terminal types, crypt hashes of 11 methods, zlib's example and
             minigzip, kexec -d -l of the release kernel; each library's
             exported symbols and versions. kexec needs --kernel-image.
  fsck       fsck.exfat as tests/perf/build_storage_tools.py builds it
             (exfatprogs 1.4.3, static); -V, -n on clean and damaged images,
             -y restoring a boot sector
  platform   libq2platform.so as tests/perf/build_platform_library.py builds
             the shipped file (from the canonical checkout: the cwd is in the
             debug companion the deployed file's CRC covers); the rotation of
             q2_display_begin on generated frames into buffers of 4 pitches,
             byte for byte against a model; error, page-flip, evdev and
             clicker paths through pipes. The Q2 timing is
             q2-factory gates/perf/x2000_cost_device.py platform.

--arms adds clang arms that separate causes in a Q2 A/B of busybox:
`clang` (clang without any X2000 change) and `gensched` (X2000 costs and
lowering, generic scheduling). The `x2000` arm is the default of
q2-x2000-cc: CPU `x2000`, with its scheduling model and machine scheduler.

Output per package: OUT/PACKAGE/{gcc,x2000,...}/ with the programs, and
OUT/result.json. Each gcc arm is built (or, for alsa-lib, copied) as the image
ships it, so os/x2000_c.py installs a clang build only over the identical GCC
file. The dropbear and wpa gcc arms reproduce the shipped files byte for byte
(V0.129: usr/sbin/dropbear ec8c591b, usr/sbin/wpa_supplicant a11ac701,
usr/sbin/wpa_cli 6684a4ae). The wpa package needs podman and the
q2-os-build image, as os/hardware/build-wifi-tools.py does. The Q2 comparison of busybox is
q2-factory gates/perf/x2000_cost_device.py applets.

Hardening: first, the flags the SDK's GCC wrapper adds (stack protector,
RELRO and BIND_NOW, PIE, reproducibility) are compared with what
q2/toolchain/q2-x2000-cc adds, in 8 kinds of call; a difference stops the
run. Each package then has a test that no clang file lacks a protection of
its GCC file (os/x2000_c.py's admission refuses such a file too).

Not rebuilt: glibc and libgcc (docs/x2000-toolchain.md: glibc's clang
build is experimental and for aarch64 and x86_64 only).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import rig_config  # noqa: E402
import x2000_toolchain  # noqa: E402

WRAPPER = ROOT / 'q2/toolchain/q2-x2000-cc'
EXTRA_ARMS = {'clang': '-march=mips32r2',
              'gensched': '-march=mips32r5 -mmsa -Xclang -target-feature -Xclang +x2000'}
BUSYBOX_TESTS = [t.split('|') for t in [
    'sha256sum|DATA', 'sha1sum|DATA', 'md5sum|DATA', 'sha512sum|DATA', 'sha3sum|DATA',
    'cksum|DATA', 'crc32|DATA',
    'gzip|-1|-c|DATA', 'gzip|-9|-c|DATA', 'gunzip|-c|DATA.gz', 'xz|-dc|DATA.xz',
    'base64|DATA', 'od|-x|DATA', 'hexdump|-C|DATA', 'wc|DATA', 'sort|DATA', 'uniq|-c|DATA',
    'tr|a-f|A-F', 'sed|s/line/LINE/|DATA', 'awk|{s+=$2}END{print s}|DATA', 'grep|-c|7|DATA',
    'cut|-c1-20|DATA', 'fold|-w|30|DATA',
    'dd|if=DATA|bs=4096|conv=swab', 'strings|DATA.gz',
    'factor|600851475143|4294967291', 'seq|1|3|100000', 'printf|%08.3f\\n|3.14159', 'dc|-e|2 200 ^ p',
    'expr|123456|*|789', 'date|-u|-d|@1793404800', 'sh|-c|echo $((1<<20)); echo $((7*191))',
    '--list',
]]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    def __init__(self, a):
        self.br = a.buildroot_out
        self.sdk = self.br / 'host'
        self.sysroot = self.sdk / 'mipsel-buildroot-linux-gnu/sysroot'
        self.cross = str(self.sdk / 'bin/mipsel-buildroot-linux-gnu-')
        self.out = a.out
        self.qemu = rig_config.path('qemu_user')
        self.extra = a.arms
        self.kernel = (a.kernel_image / 'vmlinux', a.kernel_image / 'shanling-q2.dtb') if a.kernel_image else None
        self.env = {**os.environ, **x2000_toolchain.cargo_env(), 'Q2_SDK': str(self.sdk),
                    'PATH': f"{self.sdk / 'bin'}:{os.environ['PATH']}"}
        self.env.pop('RUSTC', None)
        self.rows, self.failed, self.skipped = [], False, []
        self.notes = {}

    def arms(self):
        """Compiler arms after `gcc`: name -> (CC, extra environment)."""
        arms = {'x2000': (str(WRAPPER), {})}
        for name in self.extra:
            arms[name] = (str(WRAPPER), {'Q2_X2000_CC_ARCH': EXTRA_ARMS[name]})
        return arms

    def sh(self, script, cwd, log, env=None):
        with open(log, 'a') as f:
            p = subprocess.run(['sh', '-ec', script], cwd=cwd, env={**self.env, **(env or {})}, stdout=f, stderr=subprocess.STDOUT)
        if p.returncode:
            raise SystemExit(f'build failed; see {log}')

    def container(self, mounts, cwd, script, log, env=None):
        """`script` in the q2-os-build container. mounts: [(host, container
        path, writable)]; the X2000 clang and the wrapper are mounted
        read-only at their host paths. env: the arm's extra environment."""
        install = x2000_toolchain.installed()
        variables = {'Q2_X2000_CLANG': self.env['Q2_X2000_CLANG'],
                     'Q2_X2000_CC_ARCH': self.env.get('Q2_X2000_CC_ARCH', '-march=x2000 -mmsa'), **(env or {})}
        volumes = [f'{host}:{inside}' + ('' if writable else ':ro') for host, inside, writable in mounts]
        volumes += [f'{install}:{install}:ro', f'{WRAPPER.parent}:{WRAPPER.parent}:ro']
        command = ['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
                   *[a for v in volumes for a in ('-v', v)], '-w', str(cwd),
                   *[a for k, v in variables.items() for a in ('-e', f'{k}={v}')], 'q2-os-build', 'sh', '-ec', script]
        with open(log, 'a') as f:
            if subprocess.run(command, stdout=f, stderr=subprocess.STDOUT).returncode:
                raise SystemExit(f'build failed; see {log}')

    def strip(self, source, target, sections=True):
        """sections=False strips as os/network-time/build.py does (keeps .comment and .note)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        extra = ['--remove-section=.comment', '--remove-section=.note'] if sections else []
        subprocess.run([self.cross + 'strip', *extra, '-o', str(target), str(source)], check=True)

    def command(self, binary, args, env=None, sysroot=None):
        cmd = [str(self.qemu), '-cpu', 'P5600', '-L', str(sysroot or self.sysroot)]
        for k, v in (env or {}).items():
            cmd += ['-E', f'{k}={v}']
        return [*cmd, str(binary), *map(str, args)]

    def run(self, binary, args, stdin=None, env=None, timeout=600, cwd=None, sysroot=None):
        return subprocess.run(self.command(binary, args, env, sysroot), input=stdin, capture_output=True, timeout=timeout, cwd=cwd)

    def record(self, package, test, outputs, ran=True):
        """outputs: arm -> comparable value. Every arm must equal the first (gcc)."""
        values = list(outputs.values())
        same = all(v == values[0] for v in values)
        ok = same and ran
        self.failed |= not ok
        self.rows.append({'package': package, 'test': test, 'same': same, 'ran': ran, 'arms': list(outputs)})
        print(f"{package:9}{'same' if same else 'DIFFERS':8}{'' if ran else '(did not run) '}{test}", flush=True)

    def msa(self, binary):
        text = subprocess.run([self.cross + 'objdump', '-d', str(binary)], capture_output=True, text=True).stdout
        return sum(1 for line in text.splitlines() if '$w' in line)


def make_data(directory):
    import gzip
    import lzma
    directory.mkdir(parents=True, exist_ok=True)
    s, lines = 12345, []
    for i in range(131072):
        s = (s * 1103515245 + 12345) % 2147483648
        lines.append('%08x %07d line %d\n' % (s, s % 9973, i))
    data = ''.join(lines).encode()[:4194304]
    (directory / 'data').write_bytes(data)
    (directory / 'data.gz').write_bytes(gzip.compress(data, 1, mtime=0))
    (directory / 'data.xz').write_bytes(lzma.compress(data, preset=1))


def buildroot_tree(c, prefix):
    trees = sorted((c.br / 'build').glob(prefix + '-[0-9]*'))
    if len(trees) != 1:
        raise SystemExit(f'{c.br / "build"}: expected one {prefix} tree')
    return trees[0]


def busybox(c):
    """The image's BusyBox: the cached Buildroot tree with the ash patch and the
    blockdev applet, built as tests/perf/build_storage_tools.py builds the
    shipped bin/busybox (same patch, configuration switch, make options,
    CFLAGS and strip). The gcc arm is that build, so it can be compared with
    the image's file; the x2000 arm differs only in CC."""
    storage = load_module('build_storage_tools', ROOT / 'tests/perf/build_storage_tools.py')
    out = c.out / 'busybox'
    source = buildroot_tree(c, 'busybox')
    if sha256(source / '.config') != storage.CONFIG:
        raise SystemExit(f'{source}: not the cached BusyBox configuration build_storage_tools.py reproduces')
    binaries = {}
    env = {'SOURCE_DATE_EPOCH': str(storage.EPOCH), 'KCONFIG_NOTIMESTAMP': '1', 'CFLAGS': storage.CFLAGS,
           'CFLAGS_busybox': '', 'LC_ALL': 'C', 'TZ': 'UTC'}
    for arm, (cc, arm_env) in {'gcc': (c.cross + 'gcc', {}), **c.arms()}.items():
        tree = out / f'src-{arm}'
        shutil.copytree(source, tree, symlinks=True)
        subprocess.run(['patch', '-p1', '-i', str(storage.PATCH)], cwd=tree, check=True, stdout=subprocess.DEVNULL, timeout=30)
        text = (tree / '.config').read_text()
        if text.splitlines().count(storage.CONFIG_OFF) != 1:
            raise SystemExit('cached BusyBox configuration without the blockdev line')
        (tree / '.config').write_text(text.replace(storage.CONFIG_OFF + '\n', storage.CONFIG_ON + '\n'))
        common = (f'ARCH=mips CROSS_COMPILE={c.cross} CC={cc} AR={c.cross}ar NM={c.cross}nm RANLIB={c.cross}ranlib '
                  'SKIP_STRIP=y EXTRA_LDFLAGS=-Wl,-z,noexecstack CONFIG_PREFIX=/unused HOSTCC=gcc')
        c.sh(f'make {common} oldconfig < /dev/null && make {common} clean && make -j{os.cpu_count()} {common} all busybox.links',
             tree, out / f'build-{arm}.log', {**env, **arm_env})
        if not any(line.endswith('/blockdev') for line in (tree / 'busybox.links').read_text().splitlines()):
            raise SystemExit(f'busybox {arm}: no blockdev applet')
        binaries[arm] = out / arm / 'busybox'
        c.strip(tree / 'busybox_unstripped', binaries[arm])
        shutil.rmtree(tree)
    c.notes['busybox'] = {'patch_sha256': sha256(storage.PATCH), 'config_sha256': storage.CONFIG,
                          'config_switch': storage.CONFIG_ON, 'cflags': storage.CFLAGS,
                          'buildroot_target_sha256': sha256(c.br / 'target/bin/busybox')}
    data = out / 'data'
    make_data(data)
    stdin = (data / 'data').read_bytes()[:65536]
    for template in BUSYBOX_TESTS:
        argv = [part.replace('DATA', str(data / 'data')) for part in template]
        results = {arm: c.run(binary, argv, stdin=stdin) for arm, binary in binaries.items()}
        c.record('busybox', ' '.join(template), {arm: (p.returncode, p.stdout) for arm, p in results.items()},
                 ran=all(p.returncode == 0 and p.stdout for p in results.values()))
    shutil.rmtree(data)
    return binaries


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# Buildroot's package/dropbear/dropbear.mk: DROPBEAR_PROGRAMS with the client.
DROPBEAR_MAKE = 'MULTI=1 SCPPROGRESS=1 PROGRAMS="dropbear dropbearkey dropbearconvert scp dbclient"'


def buildroot_make(c, package, prefix, products, directories=('',), make='', clang='', clang_config='', make_line_cc=True):
    """A Buildroot package rebuilt in its configured tree, in both arms, in the
    q2-os-build container at Buildroot's own paths: Buildroot's output
    directory read-only where it was built (/work/build/os/out, from the
    tree's Makefile), a copy of the package's tree over its directory there.
    In each of `directories`: `make clean`, then `make` with the tree's
    configuration (Buildroot's configure results, flags and libtool script);
    the clang arm sets CC (and CCLD, and `clang` with {cc}) on the make
    line; `make`: further make arguments for both. Reconfiguring is not
    done: in the relocated SDK the libtool .la files make libtool add an
    RPATH that Buildroot's own build did not have. Each of `products`
    ({name: path in the tree}) is stripped as Buildroot strips. Returns
    {arm: {name: file}}."""
    out = c.out / package
    source = buildroot_tree(c, prefix)
    inside = next(m.group(1) for makefile in [source / 'Makefile', *sorted(source.glob('*/Makefile'))] if makefile.is_file()
                  for m in [re.search(r'^CC\s*=\s*(\S+)/host/bin/', makefile.read_text(errors='replace'), re.M)] if m)  # /work/build/os/out
    cross = f'{inside}/host/bin/mipsel-buildroot-linux-gnu-'
    jobs = os.cpu_count()
    files = {}
    for arm, (cc, env) in {'gcc': (None, {}), **c.arms()}.items():
        tree = out / f'src-{arm}'
        shutil.copytree(source, tree, symlinks=True)
        (out / arm).mkdir(parents=True)
        where = f'{inside}/build/{source.name}'
        compiler = '' if cc is None or not make_line_cc else f'CC={cc} CCLD={cc} ' + clang.format(cc=cc)
        steps = [clang_config.replace('{cc}', cc)] if cc is not None and clang_config else []
        steps += [f'make -C ./{d} clean > /dev/null && make -j{jobs} -C ./{d} {compiler} {make}' for d in directories]
        steps += [f'{cross}strip --remove-section=.comment --remove-section=.note -o /result/{name} {path}' for name, path in products.items()]
        script = f'export PATH={inside}/host/bin:{inside}/host/sbin:$PATH\n' + '\n'.join(steps) + '\n'
        c.container([(c.br, inside, False), (tree, where, True), (out / arm, '/result', True)], where, script,
                    out / f'build-{arm}.log', {'Q2_SDK': f'{inside}/host', **env})
        files[arm] = {name: out / arm / name for name in products}
        shutil.rmtree(tree)
    c.notes[package] = {'buildroot_tree': source.name, 'directories': list(directories), 'make': make, 'products': products}
    return files


def buildroot_shipped(c, package, files, targets):
    """Note, per product, whether the gcc arm is the Buildroot target file ({name: target path})."""
    c.notes[package]['gcc_is_buildroot_target'] = {
        name: (c.br / 'target' / path).is_file() and sha256(c.br / 'target' / path) == sha256(files['gcc'][name])
        for name, path in targets.items()}
    print(f'{package:9}gcc arm is the Buildroot target file: {c.notes[package]["gcc_is_buildroot_target"]}', flush=True)


def aplay_conversions(c, package, aplay, libraries, work):
    """`aplay` through the plug layer into a file: format, rate and channel
    conversion. aplay: {arm: program}; libraries: {arm: directory for
    LD_LIBRARY_PATH or None}. Every arm must write the same file."""
    s, samples = 99, bytearray()
    for _ in range(4 * 44100):
        s = (s * 1103515245 + 12345) % 2147483648
        samples += ((s >> 8) & 0xffff).to_bytes(2, 'little')
    (work / 'in.raw').write_bytes(samples)
    cases = {'S16 44.1k stereo to S32 48k stereo': ('S16_LE', 44100, 2, 'S32_LE', 48000, 2),
             'S16 44.1k stereo to S24_3LE 44.1k stereo': ('S16_LE', 44100, 2, 'S24_3LE', 44100, 2),
             'S16 22.05k mono to FLOAT 44.1k stereo': ('S16_LE', 22050, 1, 'FLOAT_LE', 44100, 2),
             'S16 48k stereo to U8 8k mono': ('S16_LE', 48000, 2, 'U8', 8000, 1),
             'S16 44.1k stereo to S16 44.1k stereo (no conversion)': ('S16_LE', 44100, 2, 'S16_LE', 44100, 2)}
    for label, (fmt, rate, channels, out_fmt, out_rate, out_channels) in cases.items():
        results = {}
        for arm, program in aplay.items():
            target = work / f'out-{arm}.raw'
            target.unlink(missing_ok=True)
            conf = work / 'asound.conf'
            conf.write_text(f'''pcm.sink {{ type file file "{target}" format raw slave.pcm {{ type null }} }}
pcm.conv {{ type plug slave {{ pcm sink format {out_fmt} rate {out_rate} channels {out_channels} }} }}
''')
            env = {'ALSA_CONFIG_PATH': f"{c.br / 'target/usr/share/alsa/alsa.conf'}:{conf}", 'ALSA_CONFIG_DIR': str(c.br / 'target/usr/share/alsa')}
            if libraries.get(arm):
                env['LD_LIBRARY_PATH'] = str(libraries[arm])
            p = c.run(program, ['-q', '-D', 'conv', '-t', 'raw', '-f', fmt, '-r', rate, '-c', channels, work / 'in.raw'], env=env, timeout=300)
            results[arm] = (p.returncode, sha256(target) if target.exists() else None, target.stat().st_size if target.exists() else 0)
        c.record(package, 'aplay through plug: ' + label, results, ran=all(rc == 0 and size > 0 for rc, _, size in results.values()))


ALSA_UTILS = {'aplay': 'aplay/aplay', 'arecord': 'aplay/aplay', 'alsactl': 'alsactl/alsactl', 'alsamixer': 'alsamixer/alsamixer'}
ALSA_UTILS_TARGETS = {'aplay': 'usr/bin/aplay', 'arecord': 'usr/bin/arecord', 'alsactl': 'usr/sbin/alsactl',
                      'alsamixer': 'usr/bin/alsamixer'}


def alsa_utils(c):
    """aplay (also installed as arecord), alsactl and alsamixer from
    Buildroot's alsa-utils tree. Tests: each arm's aplay through the GCC
    libasound with format, rate and channel conversion into a file; the
    versions. alsactl needs a card for anything more; alsamixer reads the
    terminal first (TCGETS2, which qemu-user does not translate) and exits
    without output, so it has no test here and os/x2000_c.py keeps it on
    GCC."""
    files = buildroot_make(c, 'alsa-utils', 'alsa-utils', ALSA_UTILS, ('aplay', 'alsactl', 'alsamixer'))
    buildroot_shipped(c, 'alsa-utils', files, ALSA_UTILS_TARGETS)
    work = c.out / 'alsa-utils/work'
    work.mkdir()
    aplay_conversions(c, 'alsa-utils', {arm: f['aplay'] for arm, f in files.items()}, {}, work)
    for name, args in (('alsactl', ['--version']), ('aplay', ['--version'])):
        results = {arm: c.run(f[name], args) for arm, f in files.items()}
        c.record('alsa-utils', f'{name} {" ".join(args)}', {arm: (p.returncode, p.stdout) for arm, p in results.items()},
                 ran=all(p.returncode == 0 and b'1.2.16' in p.stdout for p in results.values()))
    shutil.rmtree(work)
    return {arm: f['aplay'] for arm, f in files.items()}


TESTS = ROOT / 'os/x2000-c-tests'


def test_program(c, package, source, libraries):
    """A test program from os/x2000-c-tests, compiled once with the SDK's GCC
    against the staging libraries; each arm runs it with its own libraries."""
    program = c.out / package / 'test' / Path(source).stem
    program.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([c.cross + 'gcc', '-O2', '-Wall', '-Werror', '-o', str(program), str(TESTS / source), *libraries], check=True)
    return program


def library_dirs(c, package, files, sonames):
    """{arm: a directory with each library under its soname}; sonames: {name: soname}."""
    dirs = {}
    for arm, f in files.items():
        d = c.out / package / 'lib' / arm
        d.mkdir(parents=True)
        for name, soname in sonames.items():
            (d / soname).symlink_to(f[name])
        dirs[arm] = d
    return dirs


def exported_symbols(c, package, files):
    """Each shared library's defined dynamic symbols with their versions,
    types and binding, and its version definitions: the same in every arm."""
    for name in files['gcc']:
        if '.so' not in name:
            continue
        results = {}
        for arm, f in files.items():
            readelf = lambda option: subprocess.run([c.cross + 'readelf', '-W', option, str(f[name])], capture_output=True, text=True).stdout
            symbols = sorted(' '.join((p[7], p[3], p[4], p[5])) for p in (line.split() for line in readelf('--dyn-syms').splitlines())
                             if len(p) >= 8 and p[0].endswith(':') and p[0] != 'Num:' and p[6] != 'UND')
            definitions = sorted(line.split('Name: ')[1].split()[0] for line in readelf('-V').splitlines() if 'Rev:' in line and 'Name: ' in line)
            results[arm] = (symbols, definitions)
        c.record(package, f'{name}: exported symbols and versions ({len(results["gcc"][0])})', results,
                 ran=all(symbols for symbols, _ in results.values()))


def run_with(c, package, label, program, args, dirs, env=None, check=lambda p: p.returncode == 0 and p.stdout, stdin=None):
    """`program` once per arm with LD_LIBRARY_PATH at the arm's libraries; every output must equal the first."""
    results = {arm: c.run(program, args, env={'LD_LIBRARY_PATH': str(d), **(env or {})}, stdin=stdin) for arm, d in dirs.items()}
    c.record(package, label, {arm: (p.returncode, p.stdout) for arm, p in results.items()}, ran=all(check(p) for p in results.values()))


ALSA_LIB_TOOLS = {'libatopology.so.2': 'src/topology/.libs/libatopology.so.2.0.0', 'aserver': 'aserver/aserver'}
ALSA_LIB_TOOLS_TARGETS = {'libatopology.so.2': 'usr/lib/libatopology.so.2.0.0', 'aserver': 'usr/bin/aserver'}


def alsa_lib_tools(c):
    """libatopology and aserver from Buildroot's alsa-lib tree, made in their
    directories only, against the tree's GCC libasound (libasound itself is
    the alsa-lib package's). Tests: a topology configuration parsed, saved as
    text and built as a binary; aserver's usage."""
    files = buildroot_make(c, 'alsa-lib-tools', 'alsa-lib', ALSA_LIB_TOOLS, ('src/topology', 'aserver'))
    buildroot_shipped(c, 'alsa-lib-tools', files, ALSA_LIB_TOOLS_TARGETS)
    exported_symbols(c, 'alsa-lib-tools', files)
    dirs = library_dirs(c, 'alsa-lib-tools', files, {'libatopology.so.2': 'libatopology.so.2'})
    program = test_program(c, 'alsa-lib-tools', 'topology.c', ['-latopology', '-lasound'])
    run_with(c, 'alsa-lib-tools', 'libatopology: a configuration loaded, saved and built', program, [], dirs,
             check=lambda p: p.returncode == 0 and b'--- binary' in p.stdout)
    results = {arm: c.run(f['aserver'], ['-h']) for arm, f in files.items()}
    c.record('alsa-lib-tools', 'aserver -h', {arm: (p.returncode, (p.stdout + p.stderr).replace(str(files[arm]['aserver']).encode(), b'aserver'))
                                             for arm, p in results.items()},
             ran=all(b'Usage' in p.stdout + p.stderr for p in results.values()))
    return {arm: f['libatopology.so.2'] for arm, f in files.items()}


NCURSES = {'libncurses.so.6': 'lib/libncurses.so.6.6', 'libform.so.6': 'lib/libform.so.6.6',
           'libmenu.so.6': 'lib/libmenu.so.6.6', 'libpanel.so.6': 'lib/libpanel.so.6.6'}
NCURSES_TARGETS = {'libncurses.so.6': 'usr/lib/libncurses.so.6.6', 'libform.so.6': 'usr/lib/libform.so.6.6',
                   'libmenu.so.6': 'usr/lib/libmenu.so.6.6', 'libpanel.so.6': 'usr/lib/libpanel.so.6.6'}


def ncurses(c):
    """libncurses, libform, libmenu and libpanel from Buildroot's tree. Test:
    windows, panels, a menu and a form drawn on a pipe for 4 terminal types
    of the image's terminfo; the escape sequences and the screen's cells."""
    files = buildroot_make(c, 'ncurses', 'ncurses', NCURSES, ('ncurses', 'form', 'menu', 'panel'))
    buildroot_shipped(c, 'ncurses', files, NCURSES_TARGETS)
    exported_symbols(c, 'ncurses', files)
    dirs = library_dirs(c, 'ncurses', files, {name: name for name in NCURSES})
    program = test_program(c, 'ncurses', 'curses.c', ['-lform', '-lmenu', '-lpanel', '-lncurses'])
    for term in ('vt100', 'linux', 'xterm-256color', 'screen'):
        run_with(c, 'ncurses', f'windows, panels, menu and form on {term}', program, [term], dirs,
                 env={'TERMINFO': str(c.br / 'target/usr/share/terminfo'), 'LINES': '24', 'COLUMNS': '80'},
                 check=lambda p: p.returncode == 0 and b'item Songs field [user q2' in p.stdout and b'4533' in p.stdout)
    return {arm: f['libncurses.so.6'] for arm, f in files.items()}


def libxcrypt(c):
    """libcrypt from Buildroot's libxcrypt tree. GCC's configure found the
    symver function attribute (GCC 10 and later); clang ignores it, and a
    clang libcrypt built with that config.h exports no symbol versions (a
    program linked against the shipped library then fails to resolve
    crypt_gensalt_rn@XCRYPT_2.0). The clang arm takes the .symver assembler
    path instead: HAVE_FUNC_ATTRIBUTE_SYMVER removed from its config.h.
    Tests: the exported symbols and versions; each method's hash of 4
    phrases with fixed salts, and crypt_gensalt_rn with fixed bytes."""
    files = buildroot_make(c, 'libxcrypt', 'libxcrypt', {'libcrypt.so.2': '.libs/libcrypt.so.2.0.0'},
                           clang_config="sed -i '/^#define HAVE_FUNC_ATTRIBUTE_SYMVER /d' config.h")
    buildroot_shipped(c, 'libxcrypt', files, {'libcrypt.so.2': 'usr/lib/libcrypt.so.2.0.0'})
    exported_symbols(c, 'libxcrypt', files)
    dirs = library_dirs(c, 'libxcrypt', files, {'libcrypt.so.2': 'libcrypt.so.2'})
    program = test_program(c, 'libxcrypt', 'crypt.c', ['-lcrypt'])
    run_with(c, 'libxcrypt', 'crypt_rn: 11 methods x 4 phrases; crypt_gensalt_rn', program, [], dirs,
             check=lambda p: p.returncode == 0 and b'$y$j9T$' in p.stdout and b'$6$rounds=5000$' in p.stdout)
    return {arm: f['libcrypt.so.2'] for arm, f in files.items()}


def zlib(c):
    """libz from Buildroot's libzlib tree (configure's Makefile; the clang arm
    sets LDSHARED as configure wrote it, with its compiler), and zlib's own
    example and minigzip linked against it. Tests: example's self-test;
    minigzip at levels 1, 6 and 9 and back, on 4 MiB of generated text and
    on 1 MiB of random bytes."""
    tools = {'examplesh': 'examplesh', 'minigzipsh': 'minigzipsh'}
    files = buildroot_make(c, 'zlib', 'libzlib', {'libz.so.1': 'libz.so.1.3.2', **tools}, make='all examplesh minigzipsh',
                           clang='LDSHARED="{cc} -shared -Wl,-soname,libz.so.1,--version-script,zlib.map"')
    buildroot_shipped(c, 'zlib', files, {'libz.so.1': 'usr/lib/libz.so.1.3.2'})
    exported_symbols(c, 'zlib', files)
    dirs = library_dirs(c, 'zlib', files, {'libz.so.1': 'libz.so.1'})
    work = c.out / 'zlib/work'
    make_data(work)
    import random
    (work / 'random').write_bytes(random.Random(5).randbytes(1 << 20))
    results = {arm: c.run(f['examplesh'], [], env={'LD_LIBRARY_PATH': str(dirs[arm])}, cwd=work) for arm, f in files.items()}
    c.record('zlib', 'example (zlib self-test, each arm its own build)', {arm: (p.returncode, p.stdout) for arm, p in results.items()},
             ran=all(p.returncode == 0 and b'inflate with dictionary' in p.stdout for p in results.values()))
    for name in ('data', 'random'):
        data = (work / name).read_bytes()
        for level in ('-1', '-6', '-9'):
            results = {}
            for arm, f in files.items():
                env = {'LD_LIBRARY_PATH': str(dirs[arm])}
                packed = c.run(f['minigzipsh'], [level], stdin=data, env=env)
                unpacked = c.run(f['minigzipsh'], ['-d'], stdin=packed.stdout, env=env)
                results[arm] = (packed.returncode, hashlib.sha256(packed.stdout).hexdigest(), unpacked.stdout == data)
            c.record('zlib', f'minigzip {level} and -d, {name}', results, ran=all(rc == 0 and back for rc, _, back in results.values()))
    shutil.rmtree(work)
    return {arm: f['libz.so.1'] for arm, f in files.items()}


def kexec(c):
    """kexec and vmcore-dmesg from Buildroot's kexec-tools tree (the
    purgatory too, which kexec carries). Tests: the version; `kexec -d -l`
    of the release kernel with its device tree, which reads the kernel,
    builds the segments and the purgatory and prints them before the
    kexec_load call (refused in the emulator); vmcore-dmesg on that kernel
    (not a vmcore: the ELF header parse and its refusal)."""
    # CC in the Makefile, not on the make line: a make-line CC would also
    # replace the host compiler of its bin-to-hex helper (CC=$(BUILD_CC)).
    files = buildroot_make(c, 'kexec', 'kexec', {'kexec': 'build/sbin/kexec', 'vmcore-dmesg': 'build/sbin/vmcore-dmesg'},
                           clang_config="sed -i 's|^CC\\t*= .*|CC = {cc}|' Makefile", make_line_cc=False)
    buildroot_shipped(c, 'kexec', files, {'kexec': 'usr/sbin/kexec', 'vmcore-dmesg': 'usr/sbin/vmcore-dmesg'})
    results = {arm: c.run(f['kexec'], ['--version']) for arm, f in files.items()}
    c.record('kexec', 'kexec --version', {arm: (p.returncode, p.stdout) for arm, p in results.items()},
             ran=all(b'2.0.32' in p.stdout for p in results.values()))
    kernel, dtb = c.kernel
    results = {arm: c.run(f['kexec'], ['-d', '-l', kernel, '--dtb', dtb, '--command-line', 'console=ttyS2,115200'])
               for arm, f in files.items()}
    c.record('kexec', 'kexec -d -l (kernel and device tree read, segments and purgatory built)',
             {arm: (p.returncode, p.stdout, p.stderr) for arm, p in results.items()},
             ran=all(b'segment' in (p.stdout + p.stderr).lower() for p in results.values()))
    results = {arm: c.run(f['vmcore-dmesg'], [kernel]) for arm, f in files.items()}
    c.record('kexec', 'vmcore-dmesg on a kernel ELF (refused)', {arm: (p.returncode, p.stdout, p.stderr) for arm, p in results.items()},
             ran=all(p.returncode != 0 and p.stderr for p in results.values()))
    return {arm: f['kexec'] for arm, f in files.items()}


def fsck_exfat(c):
    """fsck.exfat as tests/perf/build_storage_tools.py builds the shipped one:
    exfatprogs 1.4.3's fsck and lib files compiled directly, static, in the
    q2-os-build container with the SDK at /work/build/os/out and the build
    under /probe, stripped as Buildroot strips. Tests: -V; -n on a clean
    image from the host's mkfs.exfat; -n on a copy with a damaged boot
    region; -y on that copy (the repaired image's bytes compared)."""
    storage = load_module('build_storage_tools', ROOT / 'tests/perf/build_storage_tools.py')
    out = c.out / 'fsck'
    tarball = storage.fetch()
    cross = '/work/build/os/out/host/bin/mipsel-buildroot-linux-gnu-'
    files = {}
    for arm, (cc, env) in {'gcc': (cross + 'gcc', {}), **c.arms()}.items():
        work = out / f'src-{arm}'
        (work / 'exfatprogs').mkdir(parents=True)
        with tarfile.open(tarball) as archive:
            archive.extractall(work / 'exfatprogs', filter='data')
        build = work / 'exfatprogs/build-a'
        build.mkdir()
        (build / 'config.h').write_text(storage.CONFIG_H)
        objects, script = [], []
        for name in storage.FSCK_SOURCES:
            obj = name.replace('/', '_').replace('.c', '.o')
            objects.append(obj)
            script.append(f'{cc} {storage.CFLAGS} -Wall -include config.h -I. -I../{storage.EXFATPROGS}/include -fno-common '
                          f'-c ../{storage.EXFATPROGS}/{name} -o {obj}')
        script.append(f'{cc} -static -Wl,-z,noexecstack -o fsck.exfat.unstripped {" ".join(objects)}')
        script.append(f'{cross}strip --remove-section=.comment --remove-section=.note -o fsck.exfat fsck.exfat.unstripped')
        c.container([(c.br, '/work/build/os/out', False), (work, '/probe', True)], '/probe/exfatprogs/build-a', '\n'.join(script),
                    out / f'build-{arm}.log', {'Q2_SDK': '/work/build/os/out/host', 'SOURCE_DATE_EPOCH': str(storage.EPOCH), **env})
        files[arm] = out / arm / 'fsck.exfat'
        files[arm].parent.mkdir(parents=True)
        shutil.copy2(build / 'fsck.exfat', files[arm])
        shutil.rmtree(work)
    c.notes['fsck'] = {'recipe': 'tests/perf/build_storage_tools.py', 'tarball_sha256': storage.EXFATPROGS_SHA}
    work = out / 'work'
    work.mkdir()
    results = {arm: c.run(f, ['-V']) for arm, f in files.items()}
    c.record('fsck', 'fsck.exfat -V', {arm: (p.returncode, (p.stdout + p.stderr).replace(str(f).encode(), b'fsck.exfat'))
                                       for (arm, p), f in zip(results.items(), files.values())},
             ran=all(b'Usage' in p.stdout + p.stderr for p in results.values()))
    clean = work / 'clean.img'
    with clean.open('wb') as f:
        f.truncate(16 << 20)
    subprocess.run(['mkfs.exfat', '-q', '-L', 'Q2TEST', str(clean)], check=True, capture_output=True, timeout=60)
    damaged = work / 'damaged.img'
    shutil.copyfile(clean, damaged)
    with damaged.open('r+b') as f:
        f.seek(11)
        f.write(b'\xff' * 53)
        f.seek(512 * 12)
        f.write(b'\xff' * 512)
    for label, image, ok in (('clean image', clean, lambda rc: rc == 0), ('damaged boot region', damaged, lambda rc: rc != 0)):
        results = {arm: c.run(f, ['-n', image]) for arm, f in files.items()}
        c.record('fsck', f'fsck.exfat -n, {label}', {arm: (p.returncode, p.stdout, p.stderr) for arm, p in results.items()},
                 ran=all(ok(p.returncode) for p in results.values()))
    # Only the main boot sector damaged: -y restores it from the backup.
    repairable = work / 'repairable.img'
    shutil.copyfile(clean, repairable)
    with repairable.open('r+b') as f:
        f.seek(11)
        f.write(b'\xff' * 53)
    results = {}
    for arm, f in files.items():
        copy = work / f'repair-{arm}.img'
        shutil.copyfile(repairable, copy)
        p = c.run(f, ['-y', copy])
        results[arm] = (p.returncode, p.stdout.replace(str(copy).encode(), b'IMAGE'), sha256(copy))
    c.record('fsck', 'fsck.exfat -y, main boot sector restored from the backup (output and image bytes)', results,
             ran=all(sha == sha256(clean) for _, _, sha in results.values()))
    shutil.rmtree(work)
    return files


def dropbear(c):
    """The image's dropbear: the Buildroot tree (with Buildroot's
    localoptions.h), configured again with the arguments and the CFLAGS,
    CPPFLAGS and LDFLAGS Buildroot gave configure (config.log), made with
    Buildroot's make line and stripped as Buildroot strips. The gcc arm is
    byte for byte target/usr/sbin/dropbear; the arms' config.h are compared
    (pty, utmp, crypt and the other detections)."""
    out = c.out / 'dropbear'
    source = buildroot_tree(c, 'dropbear')
    text = (source / 'config.log').read_text().splitlines()
    configure = next(line.split('$ ./configure', 1)[1].strip() for line in text if line.startswith('  $ ./configure'))
    variables = {}
    for name in ('CFLAGS', 'CPPFLAGS', 'LDFLAGS'):
        value = next(line.split('=', 1)[1] for line in text if line.startswith(f'ac_cv_env_{name}_value='))
        variables[name] = ' '.join(shlex.split(value))
    environment = ' '.join(f'{k}={shlex.quote(v)}' for k, v in variables.items())
    binaries, config = {}, {}
    for arm, (cc, env) in {'gcc': (c.cross + 'gcc', {}), **c.arms()}.items():
        tree = out / f'src-{arm}'
        shutil.copytree(source, tree, symlinks=True, ignore=shutil.ignore_patterns('*.o', '*.a', 'dropbearmulti', 'obj'))
        cpp = c.cross + 'cpp' if arm == 'gcc' else f'{cc} -E'
        c.sh(f'make distclean > /dev/null 2>&1 || :; CC={cc} CPP={shlex.quote(cpp)} {environment} ./configure {configure} && '
             f'make -j{os.cpu_count()} {DROPBEAR_MAKE}', tree, out / f'build-{arm}.log', env)
        binaries[arm] = out / arm / 'dropbearmulti'
        c.strip(tree / 'dropbearmulti', binaries[arm])
        config[arm] = (tree / 'config.h').read_text()
        shutil.rmtree(tree)
    shipped = sha256(c.br / 'target/usr/sbin/dropbear')
    c.notes['dropbear'] = {'configure_environment': variables, 'make': DROPBEAR_MAKE, 'buildroot_target_sha256': shipped,
                           'gcc_is_buildroot_target': sha256(binaries['gcc']) == shipped}
    c.record('dropbear', 'configure results (config.h)', config)
    work = out / 'work'
    work.mkdir()
    # Keys written by the GCC build, read by every build.
    for kind in ('ed25519', 'ecdsa', 'rsa'):
        key = work / f'{kind}.key'
        made = c.run(binaries['gcc'], ['dropbearkey', '-t', kind, '-f', key])
        results = {arm: c.run(binary, ['dropbearkey', '-y', '-f', key]) for arm, binary in binaries.items()}
        c.record('dropbear', f'dropbearkey -y ({kind} public key and fingerprint)', {arm: p.stdout for arm, p in results.items()},
                 ran=made.returncode == 0 and all(p.returncode == 0 and b'Fingerprint' in p.stdout for p in results.values()))
    # A session between every pair of builds, each as server: key exchange,
    # host key signature, public-key authentication, cipher and MAC in both
    # directions, and 256 KiB of data through the channel.
    # The emulated programs look the user up in the sysroot's /etc/passwd,
    # which has no entry for whoever runs this: a root of links to the
    # sysroot with its own /etc.
    user, home = 'q2test', work / 'home'
    root = work / 'root'
    (root / 'etc').mkdir(parents=True)
    for entry in c.sysroot.iterdir():
        if entry.name != 'etc':
            (root / entry.name).symlink_to(entry)
    (root / 'etc/passwd').write_text(f'{user}:x:{os.getuid()}:{os.getgid()}::{home}:/bin/sh\n')
    (root / 'etc/group').write_text(f'{user}:x:{os.getgid()}:\n')
    (home / '.ssh').mkdir(parents=True)
    public = c.run(binaries['gcc'], ['dropbearkey', '-y', '-f', work / 'ed25519.key']).stdout.decode().splitlines()
    (home / '.ssh/authorized_keys').write_text(next(line for line in public if line.startswith('ssh-')) + '\n')
    (home / '.ssh/authorized_keys').chmod(0o600)
    (home / '.ssh').chmod(0o700)
    home.chmod(0o700)
    # The layout of q2-factory gates/perf/x2000_cost_device.py: authorized_keys in a
    # directory outside the home given with -D. dropbear checks every
    # directory up to / then, so none may be group- or world-writable (under
    # /tmp the key is refused: the empty echo of q2-dropbear-01).
    keys = work / 'keys'
    (keys / 'ak').mkdir(parents=True)
    shutil.copy2(home / '.ssh/authorized_keys', keys / 'ak/authorized_keys')
    keys.chmod(0o700)
    (keys / 'ak').chmod(0o700)

    def session(server, client, command, stdin=b'', options=(), name='session'):
        port = free_port()
        cmd = [str(c.qemu), '-cpu', 'P5600', '-L', str(root), '-E', f'HOME={home}', str(server), 'dropbear', '-F', '-E',
               '-s', '-p', f'127.0.0.1:{port}', '-r', str(work / 'rsa.key'), '-r', str(work / 'ecdsa.key'),
               '-r', str(work / 'ed25519.key'), '-P', str(work / 'pid'), *options]
        daemon = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            time.sleep(1.0)
            p = c.run(client, ['dbclient', '-y', '-y', '-i', work / 'ed25519.key', '-p', port, f'{user}@127.0.0.1', *command],
                      stdin=stdin, env={'HOME': str(home)}, timeout=120, sysroot=root)
        finally:
            daemon.terminate()
            log = daemon.communicate(timeout=20)[0]
        (out / f'{name}.log').write_bytes(log + b'\n--- client ---\n' + p.stderr)
        return p

    payload = bytes((i * 7 + (i >> 8)) & 255 for i in range(262144))
    for server_arm, server in binaries.items():
        for client_arm, client in binaries.items():
            if 'gcc' not in (server_arm, client_arm) and server_arm != client_arm:
                continue
            p = session(server, client, ['cat'], payload, name=f'session-{server_arm}-{client_arm}')
            c.record('dropbear', f'session: server {server_arm}, client {client_arm}, 256 KiB echoed',
                     {'expected': hashlib.sha256(payload).hexdigest(), 'got': hashlib.sha256(p.stdout).hexdigest()},
                     ran=p.returncode == 0 and p.stdout == payload)
    # Each build as server and client: the command's exit status and output,
    # the keys from -D. (No pty: the emulated dbclient cannot read a
    # terminal's modes, "Failed reading termmodes", in either build.)
    results = {}
    for arm, binary in binaries.items():
        p = session(binary, binary, ['echo $((6 * 7)); exit 3'], options=('-D', str(keys / 'ak')), name=f'status-{arm}')
        results[arm] = (p.returncode, p.stdout)
    c.record('dropbear', 'session: exit status 3 and output, authorized_keys from -D outside the home', results,
             ran=all(rc == 3 and text == b'42\n' for rc, text in results.values()))
    shutil.rmtree(work)
    return binaries


def alsa_lib(c):
    out = c.out / 'alsa-lib'
    source = buildroot_tree(c, 'alsa-lib')
    libraries = {'gcc': out / 'gcc/libasound.so.2'}
    libraries['gcc'].parent.mkdir(parents=True)
    shutil.copy2(c.br / 'target/usr/lib/libasound.so.2.0.0', libraries['gcc'])
    text = (source / 'config.log').read_text().splitlines()
    configure = next(line.split('$ ./configure', 1)[1].strip() for line in text if line.startswith('  $ ./configure'))
    cflags = next(line.split('=', 1)[1].strip("'") for line in text if line.startswith("CFLAGS='"))
    for arm, (cc, env) in c.arms().items():
        tree = out / f'src-{arm}'
        shutil.copytree(source, tree, symlinks=True, ignore=shutil.ignore_patterns('*.o', '*.lo', '*.la', '.libs', '*.a'))
        c.sh(f'rm -f config.status config.cache; ./configure {configure} CC={cc} CFLAGS="{cflags}" && make -j{os.cpu_count()} -C include && '
             f'make -j{os.cpu_count()} -C src', tree, out / f'build-{arm}.log', env)
        libraries[arm] = out / arm / 'libasound.so.2'
        c.strip(tree / 'src/.libs/libasound.so.2.0.0', libraries[arm])
        shutil.rmtree(tree)
    work = out / 'work'
    work.mkdir()
    aplay = c.br / 'target/usr/bin/aplay'
    s, samples = 99, bytearray()
    for _ in range(4 * 44100):
        s = (s * 1103515245 + 12345) % 2147483648
        samples += ((s >> 8) & 0xffff).to_bytes(2, 'little')
    (work / 'in.raw').write_bytes(samples)
    # The plug layer converts into a file: format, rate and channel count.
    cases = {'S16 44.1k stereo to S32 48k stereo': ('S16_LE', 44100, 2, 'S32_LE', 48000, 2),
             'S16 44.1k stereo to S24_3LE 44.1k stereo': ('S16_LE', 44100, 2, 'S24_3LE', 44100, 2),
             'S16 22.05k mono to FLOAT 44.1k stereo': ('S16_LE', 22050, 1, 'FLOAT_LE', 44100, 2),
             'S16 48k stereo to U8 8k mono': ('S16_LE', 48000, 2, 'U8', 8000, 1),
             'S16 44.1k stereo to S16 44.1k stereo (no conversion)': ('S16_LE', 44100, 2, 'S16_LE', 44100, 2)}
    for label, (fmt, rate, channels, out_fmt, out_rate, out_channels) in cases.items():
        results = {}
        for arm, library in libraries.items():
            target = work / f'out-{arm}.raw'
            target.unlink(missing_ok=True)
            conf = work / 'asound.conf'
            conf.write_text(f'''pcm.sink {{ type file file "{target}" format raw slave.pcm {{ type null }} }}
pcm.conv {{ type plug slave {{ pcm sink format {out_fmt} rate {out_rate} channels {out_channels} }} }}
''')
            p = c.run(aplay, ['-q', '-D', 'conv', '-t', 'raw', '-f', fmt, '-r', rate, '-c', channels, work / 'in.raw'],
                      env={'LD_LIBRARY_PATH': str(library.parent), 'ALSA_CONFIG_PATH': f"{c.br / 'target/usr/share/alsa/alsa.conf'}:{conf}",
                           'ALSA_CONFIG_DIR': str(c.br / 'target/usr/share/alsa')}, timeout=300)
            results[arm] = (p.returncode, sha256(target) if target.exists() else None, target.stat().st_size if target.exists() else 0)
        c.record('alsa-lib', 'aplay through plug: ' + label, results, ran=all(rc == 0 and size > 0 for rc, _, size in results.values()))
    shutil.rmtree(work)
    return libraries


def probes(c):
    """os/probes as the image builds them. Seven programs with the q2-probes
    package's own lines (the Buildroot target's copies are compared too);
    the player supervisor and the thrash guard, which the release composer
    rebuilds into every image (its OS layer), with tools/build_supervisor.py's
    command: the same flags, run from this checkout, unstripped. Both arms
    compile this checkout's sources."""
    supervisor = load_module('build_supervisor', ROOT / 'tools/build_supervisor.py')
    composed = {'q2-player-supervisor': supervisor.SOURCE, 'q2-memory-guard': supervisor.MEMORY_GUARD}
    out = c.out / 'probes'
    mk = (ROOT / 'os/package/q2-probes/q2-probes.mk').read_text()
    block = mk.split('define Q2_PROBES_BUILD_CMDS', 1)[1].split('endef', 1)[0].replace('\\\n', ' ')
    binaries = {}
    cflags = '-D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -D_TIME_BITS=64 -O2 -g0 -D_FORTIFY_SOURCE=1'
    names = []
    for arm, (cc, env) in {'gcc': (c.cross + 'gcc', {}), **c.arms()}.items():
        tree = out / f'src-{arm}'
        shutil.copytree(ROOT / 'os/probes', tree, ignore=shutil.ignore_patterns('rust'))
        binaries[arm] = out / arm
        binaries[arm].mkdir(parents=True)
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            command = line.replace('$(TARGET_CC)', cc).replace('$(TARGET_CFLAGS)', cflags).replace('$(TARGET_LDFLAGS)', '-Wl,-z,noexecstack').replace('$(@D)', str(tree))
            name = re.search(r'\s-o\s+(\S+)', command).group(1)
            if Path(name).name in composed:
                continue
            c.sh(command, tree, out / f'build-{arm}.log', env)
            c.strip(name, binaries[arm] / Path(name).name)
            if arm == 'gcc':
                names.append(Path(name).name)
        shutil.rmtree(tree)
        for name, source in composed.items():
            c.sh(' '.join([cc, *supervisor.FLAGS, '-o', str(binaries[arm] / name), str(source)]), ROOT, out / f'build-{arm}.log', env)
            if arm == 'gcc':
                names.append(name)
    target = {}
    for name in names:
        installed = next((p for p in (c.br / 'target').rglob(name) if p.is_file()), None)
        target[name] = {'buildroot_target_sha256': sha256(installed) if installed else None,
                        'gcc_is_buildroot_target': bool(installed) and sha256(installed) == sha256(binaries['gcc'] / name)}
    c.notes['probes'] = {'sources': {p.name: sha256(p) for p in sorted((ROOT / 'os/probes').glob('*.c'))},
                         'supervisor_flags': supervisor.FLAGS, 'programs': target}
    results = {arm: c.run(directory / 'q2-abi-probe', []) for arm, directory in binaries.items()}
    c.record('probes', f'q2-abi-probe output ({len(names)} programs compiled with -Wall -Wextra -Werror)',
             {arm: (p.returncode, p.stdout) for arm, p in results.items()},
             ran=all(p.returncode == 0 and p.stdout for p in results.values()))
    return {arm: directory / 'q2-abi-probe' for arm, directory in binaries.items()}


def fetch(url, sha, target):
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            target.write_bytes(r.read())
    if sha256(target) != sha:
        raise SystemExit(f'{target}: hash mismatch')
    return target


def chrony(c):
    out = c.out / 'chrony'
    spec = importlib.util.spec_from_file_location('network_time_build', ROOT / 'os/network-time/build.py')
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    archive = fetch(f'https://chrony-project.org/releases/chrony-{recipe.VERSION}.tar.gz', recipe.SHA256,
                    c.out.parent / f'chrony-{recipe.VERSION}.tar.gz')
    configure = ('./configure --host-system=Linux --host-machine=mipsel --prefix=/usr --without-gnutls --without-nettle --without-nss '
                 '--without-tomcrypt --without-libcap --without-seccomp --without-editline --disable-readline --disable-nts '
                 '--disable-refclock --disable-debug --with-user=root')
    binaries, tests = {}, {}
    arms = {'gcc': (c.cross + 'gcc', {}), **c.arms()}
    for arm, (cc, env) in arms.items():
        tree = out / f'src-{arm}'
        tree.mkdir(parents=True)
        with tarfile.open(archive) as t:
            t.extractall(tree, filter='data')
        tree = tree / f'chrony-{recipe.VERSION}'
        c.sh(f'CC={cc} CFLAGS=-Os {configure} && make -j{os.cpu_count()} chronyd chronyc && make -j{os.cpu_count()} -C test/unit', tree,
             out / f'build-{arm}.log', env)
        for name in ('chronyd', 'chronyc'):
            c.strip(tree / name, out / arm / name, sections=False)
        binaries[arm] = out / arm / 'chronyd'
        tests[arm] = tree / 'test/unit'
    conf = out / 'chrony.conf'
    conf.write_text('pool pool.ntp.org iburst\nmakestep 1.0 3\nrtcsync\ndriftfile /var/lib/chrony/drift\nmaxupdateskew 100.0\n')
    results = {arm: c.run(binary, ['-p', '-f', conf]) for arm, binary in binaries.items()}
    c.record('chrony', 'chronyd -p (configuration parse and print)', {arm: (p.returncode, p.stdout) for arm, p in results.items()},
             ran=all(p.returncode == 0 and p.stdout for p in results.values()))
    # chrony's own unit tests, each build's under the emulator: all must pass.
    names = sorted(p.name for p in tests['gcc'].glob('*.test'))
    for name in names:
        results = {arm: c.run(directory / name, [], timeout=900, cwd=directory) for arm, directory in tests.items()}
        if results['gcc'].returncode:
            # Not a compiler matter: the GCC build's own test does not pass
            # under the emulator (socket.test needs real socket options).
            print(f'chrony   skipped unit test {name}: fails in the GCC build under the emulator', flush=True)
            c.skipped.append(f'chrony unit test {name}')
            continue
        c.record('chrony', f'unit test {name}', {arm: p.returncode for arm, p in results.items()},
                 ran=all(p.returncode == 0 for p in results.values()))
    for arm in arms:
        shutil.rmtree(out / f'src-{arm}')
    return binaries


# The configuration test's networks, in the forms tests/perf/wpa3_qualify.py
# writes (SAE, transition, WPA2), and an open one.
WPA_CONFIG = '''ctrl_interface=DIR=CTRLDIR
update_config=0
sae_pwe=2
network={
	ssid="Q2 SAE"
	scan_ssid=1
	key_mgmt=SAE
	sae_password="correct horse battery staple"
	ieee80211w=2
}
network={
	ssid="Q2 transition"
	key_mgmt=WPA-PSK SAE
	psk="transition passphrase"
	sae_password="transition passphrase"
	ieee80211w=1
}
network={
	ssid="Q2 WPA2"
	key_mgmt=WPA-PSK
	psk="12345678"
	ieee80211w=0
}
network={
	ssid="Q2 open"
	key_mgmt=NONE
}
'''
WPA_MODULE_TESTS = 'CONFIG_MODULE_TESTS=y\nMS_FUNCS=y\nNEED_FIPS186_2_PRF=y\nNEED_GAS=y\n'
# Lines the module tests print as they pass. common_module_tests() stops at
# the first failing test, so "rsn_ie_parse tests" means the SAE vectors
# passed; test_ms_funcs() is the last crypto test.
WPA_PASSED = ['wpa_supplicant module tests', 'utils module tests', 'common module tests', 'SAE: Derive PWE from PT',
              'rsn_ie_parse tests', 'crypto module tests', 'OMAC1-AES-128 test cases passed',
              'AES key wrap/unwrap test cases passed', 'MD5 test cases passed', 'SHA1 test cases passed',
              'SHA256 test cases passed', 'Extract-and-Expand HKDF test cases passed']
# The failures of test_ms_funcs(): MD4 is not in OpenSSL built no-legacy.
WPA_MD4 = {'nt_password_hash failed', 'generate_nt_response failed', 'hash_nt_password_hash failed',
           'generate_authenticator_response failed', 'ms_funcs: 4 errors'}


def module_tests_passed(log):
    lines = log.splitlines()
    failures = {line for line in lines if line.endswith(' failed') or line.endswith(' errors')}
    return all(line in lines for line in WPA_PASSED) and failures <= WPA_MD4


def wpa(c):
    """The image's wpa_supplicant and wpa_cli: os/hardware/build-wifi-tools.py
    --wpa3 (static libnl 3.12 and OpenSSL 3.6.5, the SAE/PMF configuration),
    run as that script runs it: in the q2-os-build container with the
    sources under /wifi and the Buildroot output at /br. Its commands are
    repeated here, not changed or factored out: the release composer admits
    the shipped pair by the script's digest. The gcc arm is that build; the
    clang arm differs in the compiler of all three (OpenSSL's Configure takes
    CC, AR and RANLIB instead of the cross-compile prefix). The X2000 clang
    and the wrapper are mounted read-only at their host paths.

    Tests: the versions; wpa_passphrase (PBKDF2 through OpenSSL); a
    configuration parse with nl80211 (the emulator has no generic netlink, so
    it stops at the driver); and the upstream module tests (the SAE and H2E
    vectors of IEEE 802.11 Annex J.10, crypto, common and utility tests) from
    a CONFIG_MODULE_TESTS build of the same tree, through the global control
    interface. wpa_passphrase and the module-test build are made after the
    shipped pair is copied and are not installed."""
    out = c.out / 'wpa'
    recipe = load_module('wifi_tools_build', ROOT / 'os/hardware/build-wifi-tools.py')
    sources = {**recipe.SOURCES, 'openssl-3.6.5': recipe.OPENSSL}
    archives = [fetch(url, sha, c.out.parent / (name + '.tar.gz')) for name, (url, sha) in sources.items()]
    install = x2000_toolchain.installed()
    binaries, tests = {}, {}
    jobs = os.cpu_count()
    for arm, (cc, env) in {'gcc': ('/br/host/bin/mipsel-buildroot-linux-gnu-gcc', {}), **c.arms()}.items():
        work = out / f'src-{arm}'
        work.mkdir(parents=True)
        for archive in archives:
            with tarfile.open(archive) as t:
                t.extractall(work, filter='data')
        log = out / f'build-{arm}.log'
        variables = {'Q2_SDK': '/br/host', 'Q2_X2000_CLANG': c.env['Q2_X2000_CLANG'],
                     'Q2_X2000_CC_ARCH': c.env.get('Q2_X2000_CC_ARCH', '-march=x2000 -mmsa'), **env}

        def engine(cwd, script):
            command = ['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
                       '-v', f'{work}:/wifi', '-v', f'{c.br}:/br:ro', '-v', f'{install}:{install}:ro',
                       '-v', f'{WRAPPER.parent}:{WRAPPER.parent}:ro', '-w', '/wifi/' + cwd,
                       *[a for k, v in variables.items() for a in ('-e', f'{k}={v}')], 'q2-os-build', 'sh', '-ec', script]
            with open(log, 'a') as f:
                if subprocess.run(command, stdout=f, stderr=subprocess.STDOUT).returncode:
                    raise SystemExit(f'build failed; see {log}')
        engine('libnl-3.12.0', f'''export PATH=/br/host/bin:$PATH
CC={cc if arm != "gcc" else "mipsel-buildroot-linux-gnu-gcc"} ./configure --host=mipsel-buildroot-linux-gnu --prefix=/usr --disable-cli --disable-pthreads --disable-shared --enable-static
make -j{jobs}
make DESTDIR=/wifi/sysroot install''')
        if arm == 'gcc':
            configure = './Configure linux-generic32 --cross-compile-prefix=mipsel-buildroot-linux-gnu-'
        else:
            configure = f'CC={cc} AR=mipsel-buildroot-linux-gnu-ar RANLIB=mipsel-buildroot-linux-gnu-ranlib ./Configure linux-generic32'
        engine('openssl-3.6.5', f'''export PATH=/br/host/bin:$PATH
{configure} --prefix=/usr --libdir=lib \\
 no-asm no-shared no-tests no-apps no-module no-engine no-dso no-legacy -Os -DOPENSSL_SMALL_FOOTPRINT -DBROKEN_CLANG_ATOMICS
make -j{jobs} build_libs
make DESTDIR=/wifi/sysroot install_dev''')
        tree = work / 'wpa_supplicant-2.12/wpa_supplicant'
        (tree / '.config').write_text(recipe.supplicant_config(True))
        make = f'make -j{jobs} CC={cc} PKG_CONFIG=/br/host/bin/pkg-config'
        engine('wpa_supplicant-2.12/wpa_supplicant', f'{make} wpa_supplicant wpa_cli')
        for name in ('wpa_supplicant', 'wpa_cli'):
            c.strip(tree / name, out / arm / name, sections=False)
        binaries[arm] = out / arm / 'wpa_supplicant'
        tests[arm] = out / arm / 'test'
        engine('wpa_supplicant-2.12/wpa_supplicant', f'{make} wpa_passphrase')
        c.strip(tree / 'wpa_passphrase', tests[arm] / 'wpa_passphrase', sections=False)
        # The objects the module tests call into beyond the image's
        # configuration (MS-CHAPv2, FIPS 186-2 PRF, GAS), as make variables
        # only, so no other feature is compiled in.
        (tree / '.config').write_text((tree / '.config').read_text() + WPA_MODULE_TESTS)
        engine('wpa_supplicant-2.12/wpa_supplicant', f'make clean > /dev/null && {make} wpa_supplicant')
        c.strip(tree / 'wpa_supplicant', tests[arm] / 'wpa_supplicant-module-tests', sections=False)
        shutil.rmtree(work)
    c.notes['wpa'] = {'builder_sha256': sha256(ROOT / 'os/hardware/build-wifi-tools.py'), 'profile': 'wpa3',
                      'sources': {name: sha for name, (_, sha) in sources.items()}}
    for ssid, phrase in (('Q2 test network', 'correct horse battery staple'), ('x', '12345678'), ('ssid-with-32-characters-exactly!!', 'p' * 63)):
        results = {arm: c.run(d / 'wpa_passphrase', [ssid, phrase]) for arm, d in tests.items()}
        c.record('wpa', f'wpa_passphrase (PBKDF2-SHA1, 4096 rounds), SSID of {len(ssid)} characters',
                 {arm: (p.returncode, p.stdout) for arm, p in results.items()}, ran=all(p.returncode == 0 and b'psk=' in p.stdout for p in results.values()))
    for name in ('wpa_supplicant', 'wpa_cli'):
        results = {arm: c.run(binary.parent / name, ['-v']) for arm, binary in binaries.items()}
        c.record('wpa', f'{name} -v', {arm: (p.returncode, p.stdout) for arm, p in results.items()},
                 ran=all(b'v2.12' in p.stdout for p in results.values()))
    work = out / 'work'
    work.mkdir()
    # The configuration as read, field by field (-dd); then nl80211 fails in
    # the emulator. Paths that name the arm are made the same.
    results = {}
    for arm, binary in binaries.items():
        conf = work / f'{arm}.conf'
        conf.write_text(WPA_CONFIG.replace('CTRLDIR', str(work / f'ctrl-{arm}')))
        p = c.run(binary, ['-dd', '-c', conf, '-i', 'lo', '-D', 'nl80211'], timeout=120)
        text = p.stdout.decode(errors='replace').replace(f'{arm}.conf', 'ARM.conf').replace(f'ctrl-{arm}', 'ctrl-ARM')
        results[arm] = (p.returncode, text)
    c.record('wpa', 'wpa_supplicant -dd -c: SAE, transition, WPA2 and open networks parsed, nl80211 to the driver', results,
             ran=all('Reading configuration file' in t and "id=3 ssid='Q2 open'" in t and t.count('PSK (from passphrase)') == 2 for _, t in results.values()))
    # The module tests: the daemon with a global control socket and no
    # interface, MODULE_TESTS through the arm's own wpa_cli, then TERMINATE.
    results = {}
    for arm, directory in tests.items():
        # The socket path relative to the working directory: a UNIX socket
        # path is at most 107 bytes. The log to a file: a full pipe would
        # stop the daemon mid-test.
        control, log = f'g-{arm}', out / f'module-tests-{arm}.log'
        with open(log, 'wb') as f:
            daemon = subprocess.Popen(c.command(directory / 'wpa_supplicant-module-tests', ['-d', '-g', control]),
                                      stdout=f, stderr=subprocess.STDOUT, cwd=work)
        try:
            for _ in range(200):
                if (work / control).exists():
                    break
                time.sleep(0.1)
            cli = binaries[arm].parent / 'wpa_cli'
            p = c.run(cli, ['-g', control, 'raw', 'MODULE_TESTS'], timeout=900, cwd=work)
            c.run(cli, ['-g', control, 'terminate'], timeout=60, cwd=work)
            daemon.wait(timeout=60)
        finally:
            if daemon.poll() is None:
                daemon.kill()
                daemon.wait()
        # After the PWE is found, SAE's remaining hunting-and-pecking rounds
        # use a random stub password (sae.c): their results are not compared.
        text = '\n'.join(line for line in log.read_text(errors='replace').replace(control, 'CONTROL').splitlines()
                         if not re.fullmatch(r'SAE: pwd-seed result [01] found=0xff', line))
        results[arm] = (p.returncode, p.stdout.strip(), text)
    c.record('wpa', 'module tests (SAE and H2E vectors of Annex J.10, crypto, common, utilities): the same log; '
             'all pass but the MS-CHAPv2 test (MD4 is in no-legacy OpenSSL\'s legacy provider)',
             results, ran=all(rc == 0 and module_tests_passed(text) for rc, _, text in results.values()))
    shutil.rmtree(work)
    return binaries


def platform_frames(content):
    """Two 375x320 XRGB frames (little-endian words, as this host and the Q2
    store them) for the rotation tests."""
    import array
    assert sys.byteorder == 'little'
    n = 375 * 320
    def lcg(seed, mask=0xffffffff):
        out = array.array('I', bytes(4 * n))
        for i in range(n):
            seed = (seed * 1103515245 + 12345) & 0xffffffff
            out[i] = (seed ^ (seed >> 13) * 2654435761) & mask
        return out
    if content == 'zero and all-ones':
        a, b = array.array('I', [0]) * n, array.array('I', [0xffffffff]) * n
    elif content == 'index':
        a = array.array('I', range(n))
        b = array.array('I', ((i * 2654435761) & 0xffffffff for i in range(n)))
    elif content == 'random':
        a, b = lcg(1), lcg(2)
    elif content == 'alpha only and colour only':
        a, b = lcg(3, 0xff000000), lcg(4, 0x00ffffff)
    elif content == 'gradient':
        a = array.array('I', (((i % 375) * 255 // 374) << 16 | ((i // 375) * 255 // 319) << 8 | (i % 375 ^ i // 375) & 0xff
                              for i in range(n)))
        b = array.array('I', (0x80000000 | (0xffffff - v) for v in a))
    else:
        raise ValueError(content)
    return a.tobytes() + b.tobytes()


def platform_rotation(frames, pitch, tail=4096):
    """What `platform rotate PITCH` prints for a correct q2_display_begin
    (display.c: destination row x, column c is source pixel (319 - c) * 375 + x
    with alpha 0xff; row padding and the tail untouched): the return values
    and `shown` after each frame, then buffer 1 and buffer 0."""
    import array
    n = 375 * 320
    def rotate(words, fill):
        rows = []
        for x in range(375):
            row = array.array('I', words[x::375])
            row.reverse()
            rows.append(row.tobytes() + fill * (pitch - 1280))
        return b''.join(rows) + fill * tail
    words = array.array('I', (v | 0xff000000 for v in array.array('I', frames)))
    header = b''.join(v.to_bytes(4, 'little', signed=True) for v in (0, 1, 0, 0))
    return header + rotate(words[:n], b'\xc3') + rotate(words[n:], b'\x5a')


# What `platform api` prints for the shipped library's code (display.c,
# input.c), errno numbers of MIPS Linux: EIO 5, EBADF 9, EAGAIN 11, EBUSY 16,
# EINVAL 22. The input_event bytes are time64 o32's 16-byte layout.
PLATFORM_API = '''\
begin count 0: -1 errno 22
begin count 119999: -1 errno 22
begin count 120001: -1 errno 22
begin count 240000: -1 errno 22
begin count 4294967295: -1 errno 22
begin while pending: -1 errno 16
enable 1 while pending: -1 errno 16
enable 0 while pending: -1 errno 16
buffers unchanged by refused calls: 1; shown 0 pending 2
finish with nothing pending: -1 errno 22
begin: 0
shown 1 pending 0 rotated 1 other buffer untouched 1
present (scanout disabled): 0
shown 0 pending 0 rotated 1 first frame kept 1
present count 119999: -1 errno 22
descriptor is the device fd: 1
finish, no event yet: -1 errno 11
  shown 0 pending 2
finish, flip of buffer 1: 0
  shown 1 pending 0
finish, other user_data: -1 errno 5
  shown 1 pending 1
finish, vblank event: -1 errno 5
  shown 1 pending 1
finish, wrong length: -1 errno 5
  shown 1 pending 1
finish, short event: -1 errno 5
  shown 1 pending 1
finish, flip of buffer 0: 0
  shown 0 pending 0
descriptor after q2_display_close: -1 errno 9
q2_display_close(NULL) returned
q2_input_event_size 16 (harness 16)
q2_input_bridge_size 24 (harness 24)
q2_input_read, empty: 0
q2_input_read: 1 seconds 0 microseconds 0 type 1 code 116 value 1
q2_input_read: 1 seconds 1793404800 microseconds 999999 type 1 code 163 value 0
q2_input_read: 1 seconds 2147483647 microseconds 5 type 3 code 0 value 200
q2_input_read: 1 seconds 2147483648 microseconds 6 type 3 code 24 value -1
q2_input_read: 1 seconds 4294967295 microseconds 7 type 5 code 2 value 1
q2_input_read: 1 seconds 12 microseconds 13 type 0 code 0 value 0
q2_input_read, 7 bytes: -1 errno 5
q2_input_read, writer closed: -1 errno 5
q2_clicker_set 1: 0
  written: 00 00 00 00 00 00 00 00 12 00 01 00 01 00 00 00 (16 bytes)
q2_clicker_set 0: 0
  written: 00 00 00 00 00 00 00 00 12 00 01 00 00 00 00 00 (16 bytes)
q2_clicker_set 2: -1 errno 22
  written: (-1 bytes)
q2_clicker_set -1: -1 errno 22
  written: (-1 bytes)
q2_clicker_tone 440: 0
  written: 00 00 00 00 00 00 00 00 12 00 02 00 b8 01 00 00 (16 bytes)
q2_clicker_tone 0: 0
  written: 00 00 00 00 00 00 00 00 12 00 02 00 00 00 00 00 (16 bytes)
q2_clicker_tone 8000: 0
  written: 00 00 00 00 00 00 00 00 12 00 02 00 40 1f 00 00 (16 bytes)
q2_clicker_tone 8001: -1 errno 22
  written: (-1 bytes)
q2_clicker_tone -1: -1 errno 22
  written: (-1 bytes)
q2_clicker_set on the read end: -1 errno 9
q2_clicker_tone on the read end: -1 errno 9
'''.encode()
PLATFORM_CASES = [('zero and all-ones', 1280), ('index', 1280), ('random', 1280), ('alpha only and colour only', 1280),
                  ('gradient', 1280), ('random', 1284), ('index', 1344), ('random', 2048)]


def canonical_checkout():
    return Path(subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                        text=True).strip()).parent


def platform(c):
    """libq2platform.so as tests/perf/build_platform_library.py builds the
    shipped file: its flags and sources, frozen under one directory that
    -ffile-prefix-map names /q2-platform, compiled from the canonical
    checkout (the cwd is DW_AT_comp_dir, so it is in the debug companion
    whose CRC the deployed file carries), linked with its line and stripped
    with a debug link by tools/elf_symbols.py. The gcc arm is V0.130's
    usr/lib/libq2platform.so (06d38cac) byte for byte. The test program
    os/x2000-c-tests/platform.c is built once with GCC and run with each
    arm's library: q2_display_begin with scanout disabled rotating two
    frames into buffers of 4 pitches (8 cases, compared with
    platform_rotation byte for byte), the error, page-flip, evdev and
    clicker paths through pipes (PLATFORM_API), and its bench mode, which
    q2-factory gates/perf/x2000_cost_device.py platform times on the Q2."""
    builder = load_module('build_platform_library', ROOT / 'tests/perf/build_platform_library.py')
    symbols = load_module('elf_symbols', ROOT / 'tools/elf_symbols.py')
    out = c.out / 'platform'
    frozen = out / 'source'
    frozen.mkdir(parents=True)
    for relative in builder.SOURCES:
        shutil.copyfile(ROOT / relative, frozen / Path(relative).name)
    cwd = canonical_checkout()
    env = {'LC_ALL': 'C', 'SOURCE_DATE_EPOCH': '1793404800'}
    files = {}
    for arm, (cc, arm_env) in {'gcc': (c.cross + 'gcc', {}), **c.arms()}.items():
        work, log = out / f'build-{arm}', out / f'build-{arm}.log'
        work.mkdir()
        objects = []
        for stem in ('display', 'input'):
            objects.append(work / (stem + '.o'))
            c.sh(shlex.join([cc, *builder.FLAGS, f'-ffile-prefix-map={frozen}=/q2-platform', '-MD', '-MF', str(work / (stem + '.d')),
                             '-c', str(frozen / (stem + '.c')), '-o', str(objects[-1])]), cwd, log, {**env, **arm_env})
        full = work / 'libq2platform.full.so'
        c.sh(shlex.join([cc, '-shared', *map(str, objects), '-Wl,-z,noexecstack', '-Wl,--build-id', '-o', str(full)]), cwd, log,
             {**env, **arm_env})
        (out / arm).mkdir()
        symbols.preserve(full, out / arm / 'libq2platform.so', out / arm / 'symbols', c.cross,
                         {'source_inputs': {p: sha256(ROOT / p) for p in builder.SOURCES}, 'compiler': cc, 'arm': arm})
        files[arm] = {'libq2platform.so': out / arm / 'libq2platform.so'}
        shutil.rmtree(work)
    rotation = {}
    for arm, f in files.items():
        text = subprocess.run([c.cross + 'objdump', '-d', '--disassemble=q2_display_begin', str(f['libq2platform.so'])],
                              capture_output=True, text=True, check=True).stdout
        rotation[arm] = sum(1 for line in text.splitlines() if '$w' in line)
    c.notes['platform'] = {'source_inputs': {p: sha256(ROOT / p) for p in builder.SOURCES}, 'flags': builder.FLAGS,
                           'comp_dir': str(cwd), 'q2_display_begin_msa_instructions': rotation}
    exported_symbols(c, 'platform', files)
    test = out / 'test'
    test.mkdir()
    text = (frozen / 'display.c').read_text()
    start, end = text.index('struct buffer {'), text.index('};', text.index('struct display {')) + 2
    (test / 'platform-display.h').write_text('/* display.c\'s structs, copied by os/x2000-c-build.py */\n' + text[start:end] + '\n')
    program = test / 'platform'
    subprocess.run([c.cross + 'gcc', *builder.FLAGS, '-I', str(test), '-I', str(frozen), '-o', str(program), str(TESTS / 'platform.c'),
                    '-L', str(out / 'gcc'), '-lq2platform'], check=True)
    dirs = {arm: out / arm for arm in files}
    for content, pitch in PLATFORM_CASES:
        frames = platform_frames(content)
        expected = platform_rotation(frames, pitch)
        run_with(c, 'platform', f'q2_display_begin, two {content} frames, pitch {pitch}: buffers byte for byte', program,
                 ['rotate', pitch], dirs, stdin=frames, check=lambda p: p.returncode == 0 and p.stdout == expected)
    lines = PLATFORM_API.count(b'\n')
    run_with(c, 'platform', f'error, page-flip, evdev and clicker paths ({lines} lines)', program, ['api'], dirs,
             check=lambda p: p.returncode == 0 and p.stdout == PLATFORM_API)
    results = {arm: c.run(program, ['bench', 4, 'heap'], env={'LD_LIBRARY_PATH': str(d)}) for arm, d in dirs.items()}
    lines = {arm: p.stdout.decode().splitlines() for arm, p in results.items()}
    c.record('platform', 'bench 4 heap: the library loaded and the output hash',
             {arm: (p.returncode, lines[arm][1].split()[:2] if len(lines[arm]) == 2 else None) for arm, p in results.items()},
             ran=all(p.returncode == 0 and len(lines[arm]) == 2 and lines[arm][0] == f'library {dirs[arm] / "libq2platform.so"}'
                     for arm, p in results.items()))
    return {arm: f['libq2platform.so'] for arm, f in files.items()}


x2000_c = load_module('x2000_c', ROOT / 'os/x2000_c.py')
# Calls whose added flags the SDK's GCC wrapper and q2-x2000-cc must agree on.
WRAPPER_CASES = {'compile': ['-c', 'in.c', '-o', 'in.o'], 'compile -fPIC': ['-fPIC', '-c', 'in.c', '-o', 'in.o'],
                 'program': ['in.o', '-o', 'in'], 'shared': ['-shared', 'in.o', '-o', 'in.so'],
                 'static': ['-static', 'in.o', '-o', 'in'], 'relocatable': ['-r', 'in.o', '-o', 'out.o'],
                 'kernel': ['-D__KERNEL__', '-c', 'in.c'], 'no input': ['--version']}


def wrapper_flags(c):
    """The flags Buildroot's GCC wrapper (BR2_DEBUG_WRAPPER) and q2-x2000-cc
    (with a clang that prints its arguments) add before the caller's, per
    WRAPPER_CASES. They must be the same set in every case: the wrapper's
    stack protector, RELRO and BIND_NOW, PIE and reproducibility flags."""
    work = c.out / 'wrapper'
    work.mkdir()
    echo = work / 'clang'
    echo.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    echo.chmod(0o755)
    flags = {}
    for case, args in WRAPPER_CASES.items():
        p = subprocess.run([c.cross + 'gcc', *args], cwd=work, capture_output=True, text=True,
                           env={**os.environ, 'BR2_DEBUG_WRAPPER': '1', 'LC_ALL': 'C'})
        words = shlex.split(p.stderr.split('Toolchain wrapper executing:', 1)[1].split('\n\n', 1)[0].strip().splitlines()[0])
        gcc = words[3:len(words) - len(args)] if words[1] == '--sysroot' else None
        if gcc is None or words[len(words) - len(args):] != args:
            raise SystemExit(f'SDK GCC wrapper: unexpected command {words}')
        out = subprocess.run([str(WRAPPER), *args], cwd=work, capture_output=True, text=True, check=True,
                             env={**c.env, 'Q2_X2000_CLANG': str(echo)}).stdout.splitlines()
        clang = out[out.index('-Wno-unused-command-line-argument') + 1:len(out) - len(args)]
        if sorted(gcc) != sorted(clang) or out[len(out) - len(args):] != args:
            raise SystemExit(f'{WRAPPER.name} adds {clang} for {case!r} where the SDK GCC wrapper adds {gcc}')
        flags[case] = gcc
    shutil.rmtree(work)
    c.notes['compiler_flags'] = flags
    print(f"wrapper  same    added flags of the GCC wrapper and {WRAPPER.name} in {len(flags)} kinds of call", flush=True)


def hardening_record(c, package):
    """Every ELF of the package's gcc arm against the same file of each clang
    arm: no protection the GCC file has may be missing (x2000_c.weaker, which
    os/x2000_c.py's admission also applies)."""
    rows = {}
    for gcc in sorted((c.out / package / 'gcc').glob('*')):
        if not gcc.is_file() or x2000_c.hardening(gcc) is None:
            continue
        for arm in c.arms():
            other = c.out / package / arm / gcc.name
            if other.is_file():
                rows.setdefault(arm, {})[gcc.name] = x2000_c.weaker(gcc, other)
    if rows:
        files = sorted(next(iter(rows.values())))
        c.record(package, f'stack protector, RELRO, BIND_NOW and PIE as in the GCC file ({len(files)} files)',
                 {'gcc': {name: [] for name in files}, **rows})
    c.notes.setdefault('hardening', {})[package] = {
        arm: {f.name: x2000_c.hardening(f) for f in sorted((c.out / package / arm).glob('*')) if f.is_file() and x2000_c.hardening(f)}
        for arm in ('gcc', *c.arms())}


PACKAGES = {'busybox': busybox, 'dropbear': dropbear, 'alsa-lib': alsa_lib, 'probes': probes, 'chrony': chrony, 'wpa': wpa,
            'alsa-utils': alsa_utils, 'alsa-lib-tools': alsa_lib_tools, 'ncurses': ncurses, 'libxcrypt': libxcrypt, 'zlib': zlib,
            'kexec': kexec, 'fsck': fsck_exfat, 'platform': platform}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--buildroot-out', type=Path, required=True, help='Buildroot output directory (build/, host/, target/)')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--arms', nargs='*', default=[], choices=sorted(EXTRA_ARMS), help='extra clang arms')
    ap.add_argument('--kernel-image', type=Path, help='kexec: a directory with vmlinux and shanling-q2.dtb (a release image)')
    ap.add_argument('packages', nargs='*', default=[], help='default: all of ' + ', '.join(PACKAGES))
    a = ap.parse_args()
    a.buildroot_out, a.out = a.buildroot_out.resolve(), a.out.resolve()
    unknown = set(a.packages) - set(PACKAGES)
    if unknown:
        ap.error(f'unknown package {sorted(unknown)}')
    if 'kexec' in (a.packages or PACKAGES) and not (a.kernel_image and (a.kernel_image / 'vmlinux').is_file()):
        ap.error('kexec needs --kernel-image DIR with vmlinux and shanling-q2.dtb')
    if a.kernel_image:
        a.kernel_image = a.kernel_image.resolve()
    rig_config.require('qemu_user')
    a.out.mkdir(parents=True, exist_ok=False)
    c = Context(a)
    wrapper_flags(c)
    summary = {}
    for name in a.packages or PACKAGES:
        binaries = PACKAGES[name](c)
        hardening_record(c, name)
        summary[name] = {arm: {'path': str(p.relative_to(a.out)), 'bytes': p.stat().st_size, 'sha256': sha256(p), 'msa_instructions': c.msa(p),
                               **(x2000_c.hardening(p) or {})}
                         for arm, p in binaries.items() if p.exists()}
        print(f"{name}: " + '; '.join(f"{arm} {v['bytes']} bytes, {v['msa_instructions']} MSA instructions" for arm, v in summary[name].items()), flush=True)
    receipt = json.loads(x2000_toolchain.receipt_path(x2000_toolchain.work_dir()).read_text())
    result = {'passed': not c.failed, 'tests': c.rows, 'skipped': c.skipped, 'packages': summary, 'x2000': x2000_toolchain.patch_identity(),
              'toolchain': {'rustc_version': receipt.get('rustc_version'), 'files': receipt.get('files'),
                            'wrapper_sha256': sha256(WRAPPER)},
              'source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
              'source_dirty': bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain', '--', 'os', 'q2/toolchain', 'tools', 'tests/perf'], text=True).strip()),
              'buildroot_out': str(a.buildroot_out), 'notes': c.notes}
    (a.out / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    print(f"{'FAILED' if c.failed else 'passed'}: {sum(r['same'] and r['ran'] for r in c.rows)} of {len(c.rows)} tests")
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main())
