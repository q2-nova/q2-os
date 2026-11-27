#!/usr/bin/env python3
"""Build the fault kernel and run it under the emulator; emulation only.

The fault kernel is the production Linux revision and configuration that
os/sources.json pins (`linux_production`), with the test-only option in
CONFIG and an embedded initramfs: the released rescue userspace, the
freestanding probes in os/kernel-faults/ and a test init that gives a root
shell on UART0. It runs on the released Q2 QEMU, whose q2ctl channel injects
microSD, NAND and watchdog faults. os/test_storage_faults.py and
os/test_watchdog_uapi.py drive it; docs/kernel-faults.md describes the suites.

  python3 os/kernel_faults.py fetch
  python3 os/install/prepare.py --dest build/os/fault-kernel-src
  python3 os/kernel_faults.py build --kernel-source build/os/fault-kernel-src
  python3 os/kernel_faults.py probes --cc mipsel-linux-gnu-gcc \\
      --include /usr/mipsel-linux-gnu/include --out build/os/fault-probes
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import LOCK, ROOT, WORK, fetch as fetch_pinned, sha  # noqa: E402
from fork_checkout import verify  # noqa: E402

SOURCES = ROOT / 'os/kernel-faults'
INPUTS = json.loads((SOURCES / 'inputs.json').read_text())
HOME = WORK / 'kernel-faults'
QEMU_DIR = HOME / 'qemu'
KERNEL_DIR = HOME / 'kernel'
RUNS = HOME / 'runs'
PROBES = ('sdfault', 'nandfault', 'wdtcheck')
# The one test-only change to the production configuration: the DT command
# line is extended with the emulator's, so a run can add kernel parameters
# such as ingenic_x2000_wdt.nowayout=1. The production kernel takes its
# command line from the DT alone.
CONFIG = {'MIPS_CMDLINE_FROM_DTB': 'n', 'MIPS_CMDLINE_DTB_EXTEND': 'y'}
CFLAGS = ('-Os', '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-nostdinc', '-nostdlib',
          '-static', '-mips32r2', '-msoft-float', '-mnan=2008', '-mno-abicalls', '-fno-pic',
          '-fno-pie', '-no-pie', '-fno-stack-protector', '-G0', '-Wl,-e,_start')
READY = 'Q2_FAULT_SHELL_READY'
CROSS = 'mipsel-linux-gnu-'


def source_inputs():
    """What the fault kernel is built from besides Linux: the committed
    probe and init sources, the configuration change, the probe flags and
    the rescue archive. A change to any of them makes a built kernel stale."""
    files = {p.name: sha(p) for p in sorted(SOURCES.iterdir()) if p.is_file() and p.name != 'inputs.json'}
    return {'files': files, 'config': CONFIG, 'cflags': list(CFLAGS), 'rescue': INPUTS['rescue']}


# Pinned inputs ---------------------------------------------------------------

def fetch(qemu_dir=QEMU_DIR):
    """The released QEMU, unpacked and checked against the source lock, and
    the released rescue archive. Returns (qemu directory, rescue archive)."""
    archive = fetch_pinned(INPUTS['qemu'])
    rescue = fetch_pinned(INPUTS['rescue'])
    receipt = qemu_dir / 'qemu-artifact.json'
    if not receipt.exists():
        qemu_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=qemu_dir.parent, prefix='qemu-'))
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter='data')
            check_qemu(tmp)
            tmp.rename(qemu_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    check_qemu(qemu_dir)
    return qemu_dir, rescue


def check_qemu(qemu_dir):
    record = json.loads((qemu_dir / 'qemu-artifact.json').read_text())
    if record.get('source_commit') != LOCK['qemu_installed']['commit']:
        raise ValueError('QEMU release is not the source lock\'s installed QEMU revision')
    for name in ('qemu-system-mipsel',):
        if not os.access(qemu_dir / 'bin' / name, os.X_OK):
            raise ValueError('QEMU release has no executable ' + name)
    return record


# Initramfs -------------------------------------------------------------------

def cpio_entries(data):
    """(name, header fields, file data) for each member of a newc archive."""
    entries, offset = [], 0
    while True:
        header = data[offset:offset + 110]
        if len(header) < 110 or header[:6] != b'070701':
            raise ValueError('not a newc cpio archive')
        fields = [int(header[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        start = offset + 110
        name = data[start:start + namesize - 1].decode()
        offset = (start + namesize + 3) & ~3
        body = data[offset:offset + size]
        offset = (offset + size + 3) & ~3
        if name == 'TRAILER!!!':
            return entries
        if fields[4] > 1 and fields[1] & 0o170000 != 0o040000:
            raise ValueError('hard links are not supported: ' + name)
        entries.append((name, fields, body))


def cpio_member(name, mode, body, ino, mtime, rdev=(0, 0)):
    raw = name.encode() + b'\0'
    nlink = 2 if mode & 0o170000 == 0o040000 else 1
    fields = [ino, mode, 0, 0, nlink, mtime, len(body), 0, 0, *rdev, len(raw), 0]
    out = b'070701' + b''.join(b'%08X' % f for f in fields) + raw
    out += b'\0' * (-len(out) % 4) + body
    return out + b'\0' * (-len(out) % 4)


# The kernel opens /dev/console for init before init can mount devtmpfs.
CONSOLE = ('dev/console', 0o020600, (5, 1))


def initramfs(rescue, files, mtime):
    """The rescue archive with `files` ({path: (mode, bytes)}) added or
    replacing members of the same name, and a console device node."""
    kept = [e for e in cpio_entries(rescue) if e[0] not in files and e[0] != CONSOLE[0]]
    out, ino = bytearray(), 1
    for name, fields, body in kept:
        out += cpio_member(name, fields[1], body, ino, fields[5], (fields[9], fields[10]))
        ino += 1
    for name in sorted(files):
        mode, body = files[name]
        out += cpio_member(name, mode, body, ino, mtime)
        ino += 1
    out += cpio_member(CONSOLE[0], CONSOLE[1], b'', ino, mtime, CONSOLE[2])
    out += cpio_member('TRAILER!!!', 0, b'', 0, 0)
    return bytes(out) + b'\0' * (-len(out) % 512)


# Builds ----------------------------------------------------------------------

def probe_command(cc, include, source, output):
    return [str(cc), *CFLAGS, '-I', str(include), '-o', str(output), str(source)]


def compile_probes(cc, include, out, runner=subprocess.run):
    out.mkdir(parents=True, exist_ok=True)
    for name in PROBES:
        runner(probe_command(cc, include, SOURCES / (name + '.c'), out / ('q2-' + name)), check=True)
    return {name: out / ('q2-' + name) for name in PROBES}


def check_config(text):
    """Refuse a configuration where olddefconfig dropped a requested option."""
    for option, value in CONFIG.items():
        line = f'CONFIG_{option}=y' if value == 'y' else f'# CONFIG_{option} is not set'
        if line not in text.splitlines():
            raise ValueError(f'fault kernel configuration lacks {line!r}')
    if 'CONFIG_BLK_DEV_INITRD=y' not in text.splitlines():
        raise ValueError('fault kernel configuration has no initramfs support')


def build(kernel_source, out=KERNEL_DIR, container=None, jobs=None):
    from install import kernel_build
    pin = LOCK['linux_production']
    kernel_source = kernel_source.resolve()
    verify(kernel_source, pin['commit'])
    _, rescue = fetch()
    image = container or kernel_build.build_kernel_container()
    image_id = subprocess.check_output(['podman', 'image', 'inspect', '--format', '{{.Id}}', image],
                                       text=True).strip()
    inside = kernel_build.inside
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    kout = out / 'obj'
    make = ['make', '-s', '-C', inside(kernel_source), 'O=' + inside(kout), 'ARCH=mips',
            'CROSS_COMPILE=' + CROSS, f'-j{jobs or os.cpu_count()}']
    engine = lambda args: kernel_build.engine(args, image=image_id)  # noqa: E731
    engine(make + [pin['defconfig']])
    config = [inside(kernel_source / 'scripts/config'), '--file', inside(kout / '.config')]
    for option, value in CONFIG.items():
        config += ['--enable' if value == 'y' else '--disable', option]
    engine(config + ['--set-str', 'INITRAMFS_SOURCE', inside(out / 'initramfs.cpio')])
    engine(make + ['olddefconfig', 'headers_install', 'INSTALL_HDR_PATH=' + inside(out / 'uapi')])
    check_config((kout / '.config').read_text())
    probes = out / 'probes'
    probes.mkdir()
    for name in PROBES:
        engine(probe_command(CROSS + 'gcc', inside(out / 'uapi/include'), inside(SOURCES / (name + '.c')),
                             inside(probes / ('q2-' + name))))
    files = {'init': (0o100755, (SOURCES / 'init').read_bytes())}
    files |= {'bin/q2-' + n: (0o100755, (probes / ('q2-' + n)).read_bytes()) for n in PROBES}
    (out / 'initramfs.cpio').write_bytes(initramfs(rescue.read_bytes(), files, LOCK['source_date_epoch']))
    engine(make + ['uzImage.bin'])
    shutil.copyfile(kout / 'arch/mips/boot/uzImage.bin', out / 'xImage')
    shutil.copyfile(kout / '.config', out / 'linux.config')
    verify(kernel_source, pin['commit'])
    record = {'schema': 1, 'linux': pin, 'config': CONFIG, 'container': image_id,
              'rescue': INPUTS['rescue'], 'source_inputs': source_inputs(),
              'initramfs_sha256': sha(out / 'initramfs.cpio'),
              'probes': {n: sha(probes / ('q2-' + n)) for n in PROBES},
              'xImage': {'sha256': sha(out / 'xImage'), 'bytes': (out / 'xImage').stat().st_size}}
    shutil.rmtree(kout)
    (out / 'artifact.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def admit(kernel_dir=KERNEL_DIR):
    """The fault kernel's record, refused when it is stale or altered."""
    path = kernel_dir / 'artifact.json'
    if not path.exists():
        raise ValueError(f'no fault kernel in {kernel_dir}; run os/kernel_faults.py build')
    record = json.loads(path.read_text())
    if record.get('linux', {}).get('commit') != LOCK['linux_production']['commit']:
        raise ValueError('fault kernel was built from another Linux revision')
    if record.get('source_inputs') != source_inputs():
        raise ValueError('fault kernel inputs changed since it was built; rebuild it')
    if sha(kernel_dir / 'xImage') != record['xImage']['sha256']:
        raise ValueError('fault kernel image differs from its record')
    return record


# Guest -----------------------------------------------------------------------

def reply_value(reply):
    """The JSON value of a q2ctl `ok {...}` reply, or None for a bare `ok`."""
    if reply == 'ok':
        return None
    if reply.startswith('ok '):
        return json.loads(reply[3:])
    raise RuntimeError('q2ctl refused: ' + reply)


class Guest:
    """One emulator run of the fault kernel with generated media.

    UART3 (the console) goes to console.log and UART0 carries the test
    shell. q2ctl injects faults; `paused` starts the machine stopped so
    faults can be armed before the first instruction. With `container`,
    QEMU runs inside that image with every path mounted at the same place.
    """

    def __init__(self, suite, *, kernel_dir=KERNEL_DIR, qemu_dir=QEMU_DIR, sd=None, nand=None,
                 append='', container=None, paused=False, exclusive_clock=False):
        self.record = admit(kernel_dir)
        qemu = check_qemu(qemu_dir)
        self.out = RUNS / f'{suite}-{uuid.uuid4().hex[:10]}'
        self.out.mkdir(parents=True)
        self.name = 'q2-kernel-faults-' + self.out.name
        # Socket paths are limited to 108 bytes; keep them short.
        self.sockets = Path(tempfile.mkdtemp(prefix='q2-kf-'))
        self.paused, self.serial, self.ctl, self.proc, self.log = paused, None, None, None, None
        self.transcript = bytearray()
        machine = 'q2,nand-ds35q1ga=on'
        command = [str(qemu_dir / 'bin/qemu-system-mipsel'), '-L', str(qemu_dir / 'share/qemu'),
                   '-M', machine, '-nographic', '-nic', 'none', '-monitor', 'none',
                   '-d', 'guest_errors', '-kernel', str(kernel_dir / 'xImage'),
                   '-serial', f'file:{self.out}/console.log',
                   '-serial', f'unix:{self.sockets}/shell.sock,server=on,wait=off',
                   '-chardev', f'socket,id=q2ctl,path={self.sockets}/ctl.sock,server=on,wait=off']
        if append:
            command += ['-append', append]
        if sd:
            command += ['-drive', f'if=sd,format=raw,file={sd}']
        if nand:
            command += ['-drive', f'if=mtd,format=raw,file={nand}']
        if paused:
            command += ['-S', '-qmp', f'unix:{self.sockets}/qmp.sock,server=on,wait=off']
        if container:
            mounts = {kernel_dir.resolve(), qemu_dir.resolve(), self.out.resolve(), self.sockets,
                      *(Path(p).resolve().parent for p in (sd, nand) if p)}
            volumes = [arg for m in sorted(mounts) for arg in ('-v', f'{m}:{m}')]
            command = ['podman', 'run', '--rm', '--name', self.name, '--network', 'none',
                       '--security-opt', 'label=disable', *volumes, container, *command]
        self.command = command
        self.container = container
        self.result = {'suite': suite, 'passed': False, 'emulation_only': True,
                       'kernel': {k: self.record.get(k) for k in ('linux', 'source_inputs', 'xImage', 'probes')},
                       'qemu_source_commit': qemu['source_commit'], 'machine': machine,
                       'append': append, 'checks': []}
        # Wall-clock watchdog runs must not share the host with other guests
        # of this harness; storage runs may share it with each other.
        WORK.mkdir(parents=True, exist_ok=True)
        self.clock_lock = (WORK / '.guest-clock.lock').open('a')
        fcntl.flock(self.clock_lock, fcntl.LOCK_EX if exclusive_clock else fcntl.LOCK_SH)

    def start(self):
        self.log = (self.out / 'qemu.log').open('w')
        self.proc = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT)
        self.ctl = self.connect('ctl.sock').makefile('rwb', buffering=0)
        if not self.paused:
            self.boot()
        return self

    def resume(self):
        """Start a paused machine."""
        with self.connect('qmp.sock') as sock, sock.makefile('rwb', buffering=0) as qmp:
            qmp.readline()
            for command in ('qmp_capabilities', 'cont'):
                qmp.write((json.dumps({'execute': command}) + '\n').encode())
                while True:
                    reply = json.loads(qmp.readline())
                    if 'event' in reply:
                        continue
                    if 'return' not in reply:
                        raise RuntimeError(f'QMP {command}: {reply}')
                    break
        self.boot()

    def connect(self, name, timeout=30):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            sock = socket.socket(socket.AF_UNIX)
            try:
                sock.connect(str(self.sockets / name))
                sock.settimeout(10)
                return sock
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                if self.proc.poll() is not None:
                    raise RuntimeError('emulator exited; see ' + str(self.out / 'qemu.log'))
                time.sleep(.05)
        raise TimeoutError(name)

    def console(self):
        path = self.out / 'console.log'
        return path.read_text(errors='replace') if path.exists() else ''

    def boots(self):
        return self.console().count(READY)

    def resets(self):
        self.log.flush()
        return (self.out / 'qemu.log').read_text(errors='replace').count('x2000-wdt: watchdog reset')

    def wait_boot(self, count, timeout=180, panic=False):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            text = self.console()
            if text.count(READY) >= count:
                return text
            if self.proc.poll() is not None:
                raise RuntimeError('emulator exited; see ' + str(self.out))
            if not panic and re.search(r'Oops:|BUG:|Kernel panic', text):
                raise RuntimeError('guest kernel fault; see ' + str(self.out / 'console.log'))
            time.sleep(.1)
        raise TimeoutError(f'boot {count} did not reach the shell; see {self.out}')

    def boot(self):
        self.wait_boot(1)
        self.serial = self.connect('shell.sock')
        self.serial.settimeout(.5)
        self.shell_ready()

    def shell_ready(self):
        """Discard output queued by an earlier shell, then wait for a prompt
        from the current one and turn its echo and prompt off, so command
        output arrives alone."""
        while True:
            try:
                data = self.serial.recv(65536)
            except socket.timeout:
                break
            if not data:
                raise RuntimeError('serial closed')
            self.record_serial(data)
        self.serial.sendall(b'\n')
        self.receive(rb'# $')
        self.run('serial shell', "stty -echo; PS1=''")

    def record_serial(self, data):
        self.transcript.extend(data)
        (self.out / 'shell.log').write_bytes(self.transcript)

    def receive(self, pattern, timeout=120):
        end = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < end:
            try:
                chunk = self.serial.recv(65536)
            except socket.timeout:
                if self.proc.poll() is not None:
                    raise RuntimeError('emulator exited; see ' + str(self.out))
                continue
            if not chunk:
                raise RuntimeError('serial closed')
            data.extend(chunk)
            self.record_serial(chunk)
            if re.search(pattern, data):
                return bytes(data)
        raise TimeoutError(f'no {pattern!r} from the guest; see {self.out / "shell.log"}')

    def send(self, command):
        self.serial.sendall(command.encode() + b'\n')

    def run(self, label, command, timeout=120):
        """Run a shell command; it must exit 0. Returns its output."""
        marker = 'Q2_RC_' + uuid.uuid4().hex[:8]
        self.send(f"{command}; printf '\\n{marker}=%s\\n' $?")
        data = self.receive(('\r?\n' + marker + r'=(\d+)\r?\n').encode(), timeout)
        rc = re.search((marker + r'=(\d+)').encode(), data).group(1).decode()
        if rc != '0':
            raise AssertionError(f'{label}: exit status {rc}; see {self.out / "shell.log"}')
        self.result['checks'].append(label)
        return data[:data.rindex(marker.encode())].decode(errors='replace')

    def probe(self, label, name, *args, timeout=180):
        """Run a probe and require its pass line."""
        output = self.run(label, ' '.join(['q2-' + name, *args]), timeout)
        if f'Q2_PROBE_PASS {name} {args[0]}' not in output:
            raise AssertionError(f'{label}: no pass line; see {self.out / "shell.log"}')
        return output

    def control(self, command):
        """One q2ctl command; returns the JSON value of its reply or None."""
        self.ctl.write((command + '\n').encode())
        reply = self.ctl.readline().decode().strip()
        return reply_value(reply)

    def close(self):
        for handle in (self.serial, self.ctl):
            if handle:
                handle.close()
        if self.proc:
            if self.container:
                subprocess.run(['podman', 'stop', '--time', '1', self.name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.log:
            self.log.close()
        fcntl.flock(self.clock_lock, fcntl.LOCK_UN)
        self.clock_lock.close()
        shutil.rmtree(self.sockets, ignore_errors=True)
        (self.out / 'result.json').write_text(json.dumps(self.result, indent=2) + '\n')
        print(json.dumps({'artifacts': str(self.out), 'passed': self.result['passed'],
                          'checks': len(self.result['checks']),
                          **({'error': self.result['error']} if 'error' in self.result else {})}, indent=2))


def guest_arguments(parser):
    parser.add_argument('--kernel-dir', type=Path, default=KERNEL_DIR)
    parser.add_argument('--qemu-dir', type=Path, default=QEMU_DIR)
    parser.add_argument('--container', help='run QEMU inside this image (a host without its runtime libraries)')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('fetch', help='download and check the pinned QEMU and rescue releases')
    p = sub.add_parser('build', help='build the fault kernel')
    p.add_argument('--kernel-source', type=Path, required=True,
                   help='clean checkout of linux_production (os/install/prepare.py)')
    p.add_argument('--container', help='kernel build image; default: build the pinned recipe')
    p.add_argument('--jobs', type=int)
    p = sub.add_parser('probes', help='compile the probes with a host cross compiler')
    p.add_argument('--cc', required=True)
    p.add_argument('--include', type=Path, required=True, help='Linux UAPI headers for MIPS')
    p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'fetch':
            qemu, rescue = fetch()
            print(json.dumps({'qemu': str(qemu), 'rescue': str(rescue)}, indent=2))
        elif args.action == 'build':
            print(json.dumps(build(args.kernel_source, container=args.container, jobs=args.jobs), indent=2))
        else:
            for path in compile_probes(args.cc, args.include, args.out).values():
                print(path)
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
