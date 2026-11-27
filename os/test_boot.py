#!/usr/bin/env python3
"""Exercise generated Q2 images under full-system emulation only."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from build import ROOT, WORK, LOCK, sha, kernel_patch_hashes

from compatibility import PROFILES, kernel_input_digest, kernel_patches
from emulator import qemu_artifact

def immutable_hash(disk):
    from image import STATE_START
    with disk.open('rb') as f: return hashlib.sha256(f.read(STATE_START)).hexdigest()

class Guest:
    def __init__(self, kernel, disk=None, icount=False, exclusive_clock=None):
        self.out = WORK / 'tests' / ('boot-' + uuid.uuid4().hex[:10])
        self.out.mkdir(parents=True)
        self.name = 'q2-os-' + self.out.name
        self.kernel, self.disk = kernel, disk
        self.qemu, qemu_receipt = qemu_artifact()
        patches = kernel_patch_hashes()
        kernel_inputs = kernel_input_digest('os-kernel', kernel_patches('os-kernel'), patches)
        manifest = json.loads((kernel.parent / 'artifact.json').read_text())
        if (manifest['source_commit'] != PROFILES['os-kernel']['origin_commit']
                or manifest['kernel_sha256'] != sha(kernel)
                or manifest.get('source_inputs_sha256', kernel_inputs) != kernel_inputs):
            raise RuntimeError('stale or modified kernel artifact: ' + str(kernel.parent))
        if manifest.get('local_patches', {}) != patches:
            raise RuntimeError('stale OS-local kernel patches: ' + str(kernel.parent))
        self.proc = self.serial = None
        self.log = None
        self.result = {'test_run': os.environ.get('Q2_OS_ACCEPTANCE_ID'),
                       'passed': False, 'emulation_only': True, 'network': 'none',
                       'kernel_sha256': sha(kernel), 'qemu_sha256': qemu_receipt['sha256'],
                       'qemu_source_commit': qemu_receipt['source_commit'],
                       'kernel_local_patches': patches,
                       'icount': icount, 'checks': []}
        command = ['podman', 'run', '--rm', '--name', self.name, '--network', 'none',
                   '--security-opt', 'label=disable', '-v', f'{ROOT}:/work', '-w', '/work',
                   LOCK['kernel_build_container'], '/work/' + str(self.qemu.relative_to(ROOT)),
                   '-M', 'q2', '-nographic', '-nic', 'none',
                   '-kernel', '/work/' + str(kernel.relative_to(ROOT)),
                   '-serial', 'file:/work/' + str((self.out / 'console.log').relative_to(ROOT)),
                   '-serial', 'unix:/work/' + str((self.out / 'shell.sock').relative_to(ROOT)) + ',server=on,wait=off',
                   '-monitor', 'unix:/work/' + str((self.out / 'monitor.sock').relative_to(ROOT)) + ',server=on,wait=off']
        args = 'console=ttyS3,115200 earlycon mem=128M lpj=1000000 panic=5 q2.diagnostic=1'
        if disk:
            self.result['disk_path'] = str(disk.relative_to(ROOT))
            self.result['disk_sha256_before'] = sha(disk)
            self.result['immutable_sha256_before'] = immutable_hash(disk)
            command += ['-drive', 'if=sd,format=raw,file=/work/' + str(disk.relative_to(ROOT))]
            args += ' root=PARTUUID=51324f53-01 rootfstype=squashfs ro rootwait'
        if icount: command += ['-icount', 'shift=1']
        self.command = command + ['-append', args]
        # Wall-clock PCM/watchdog tests cannot tolerate this harness running
        # other full-system guests concurrently. Fixed-icount guests may share.
        # This coordinates our tests only; it does not reserve host CPU capacity.
        if exclusive_clock is None: exclusive_clock = not icount
        self.clock_lock = (WORK / '.guest-clock.lock').open('a')
        fcntl.flock(self.clock_lock, fcntl.LOCK_EX if exclusive_clock else fcntl.LOCK_SH)
        self.result['exclusive_realtime_guest'] = exclusive_clock

    def start(self):
        self.log = (self.out / 'qemu.log').open('w')
        self.proc = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT)
        self.wait_boot()
        self.serial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.serial.settimeout(1)
        self.serial.connect(str(self.out / 'shell.sock'))
        self.serial.sendall(b'\n')
        self.receive(rb'# ')
        self.check('serial shell', 'true')
        self.check('disable command echo', 'stty -echo')
        return self

    def wait_boot(self, previous=0):
        end = time.monotonic() + 180
        while time.monotonic() < end:
            console = self.out / 'console.log'
            text = console.read_text(errors='replace') if console.exists() else ''
            if text.count('Q2_OS_READY') > previous: return
            if self.proc.poll() is not None or re.search(r'Kernel panic|Oops:|BUG:', text):
                raise RuntimeError('guest boot failed: ' + str(self.out))
            time.sleep(.1)
        raise TimeoutError('guest boot readiness timed out: ' + str(self.out))

    def receive(self, pattern, timeout=120):
        end = time.monotonic() + timeout
        data = bytearray()
        while time.monotonic() < end:
            try: chunk = self.serial.recv(65536)
            except socket.timeout: continue
            if not chunk: raise RuntimeError('serial closed')
            data.extend(chunk)
            with (self.out / 'shell.log').open('ab') as f: f.write(chunk)
            if re.search(pattern, data): return bytes(data)
        raise TimeoutError('serial response timed out')

    def check(self, label, command, timeout=120):
        marker = 'OS_RC_' + uuid.uuid4().hex[:8]
        self.serial.sendall((command + f"; printf '\\n{marker}=%s\\n' $?\n").encode())
        data = self.receive(('\r?\n' + marker + '=([0-9]+)\r?\n').encode(), timeout)
        rc = re.search((marker + '=([0-9]+)').encode(), data).group(1)
        if rc != b'0': raise AssertionError(label + ' failed: ' + rc.decode())
        self.result['checks'].append(label)
        return data

    def fetch(self, guest_path, name):
        # Only generated test artifacts. Pace serial output and verify the full
        # payload; a busy guest must not turn truncated screenshots into evidence.
        if not re.fullmatch(r'/tmp/q2-[a-zA-Z0-9_.-]+', guest_path):
            raise ValueError('only explicitly generated test artifacts may be exported')
        for attempt in range(3):
            data = self.check('hash ' + name, f'sha256sum {guest_path}')
            match = re.search(rb'([0-9a-f]{64})  /tmp/q2-', data)
            if not match: continue
            expected = match.group(1).decode()
            command = (f"echo OS_FILE_BEGIN; base64 {guest_path} | "
                       "while IFS= read -r line; do printf '%s\\n' \"$line\"; sleep 0.01; done; echo OS_FILE_END")
            data = self.check('export ' + name, command, 180)
            encoded = data.split(b'OS_FILE_BEGIN', 1)[1].split(b'OS_FILE_END', 1)[0]
            try: payload = base64.b64decode(b''.join(encoded.split()), validate=True)
            except ValueError: continue
            if hashlib.sha256(payload).hexdigest() != expected: continue
            (self.out / name).write_bytes(payload)
            self.result['checks'].append('verified SHA-256 for ' + name)
            if attempt: self.result.setdefault('artifact_transfer_retries', {})[name] = attempt
            return
        raise RuntimeError('serial artifact checksum failed: ' + name)

    def screenshot(self, name='failure.ppm'):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+\.ppm',name): raise ValueError(name)
        path=self.out/name
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5);sock.connect(str(self.out/'monitor.sock'))
            def prompt():
                data=b''
                while b'(qemu)' not in data:
                    part=sock.recv(65536)
                    if not part:raise RuntimeError('monitor closed')
                    data+=part
            prompt();sock.sendall(('screendump /work/'+str(path.relative_to(ROOT))+'\n').encode());prompt()
        return path

    def seed_entropy(self):
        seed = base64.b64encode(os.urandom(64)).decode()
        self.check('fresh host entropy fixture', f"printf '%s' '{seed}' | base64 -d | q2-seed-entropy")
        self.check('refresh persistent seed when state is ready', '/etc/init.d/S21q2-seedrng reload')
        self.result['entropy'] = 'fresh host os.urandom injected with RNDADDENTROPY; not hardware RNG evidence'

    def wait_player(self):
        self.check('live rendered player', "timeout 60 sh -c 'until test -s /run/q2nd/ready && test \"$(cat /run/q2nd/ready)\" = \"$(cat /run/q2nd/player.pid)\" && kill -0 \"$(cat /run/q2nd/player.pid)\"; do sleep 1; done'", 90)

    def app_checks(self):
        self.check('Rust ABI/runtime', 'q2-rust-probe')
        self.check('Rust clock and stat after 2038', 'old=$(date +%s); date -u -s @2208988801; q2-rust-probe --time64; result=$?; date -u -s "@$old"; test "$result" = 0')
        self.wait_player()
        self.check('ALSA library and null PCM', 'aplay -D null /usr/share/q2-fixtures/tone.wav')
        for kind in ['decode', 'eq', 'image', 'radio', 'ui']:
            self.check('player ' + kind, f'q2nd --bench /usr/share/q2-fixtures/{kind}.json > /tmp/{kind}-result.json && cat /tmp/{kind}-result.json', 180)
        self.check('scripted software UI', 'mkdir -p /tmp/q2-script-data && cp /etc/q2-defaults/config.toml /tmp/q2-script-data/config.toml && timeout 90 q2nd --headless --data /tmp/q2-script-data --script /usr/share/q2-fixtures/ui.script > /tmp/script.log 2>&1 && test -s /tmp/q2-home.png && test -s /tmp/q2-menu.png', 120)
        self.fetch('/tmp/q2-home.png', 'home.png')
        self.fetch('/tmp/q2-menu.png', 'menu.png')
        for name in ['albums', 'tracks', 'playing']:
            self.fetch('/tmp/q2-' + name + '.png', name + '.png')
        self.check('recover crashed player process', 'old=$(cat /run/q2nd/player.pid); kill -KILL "$old"; timeout 60 sh -c \'until test -s /run/q2nd/ready && test "$(cat /run/q2nd/player.pid)" != "$1"; do sleep 1; done\' sh "$old"', 90)
        self.wait_player()
        self.check('reject bad update checksum', '! q2-app-update /usr/bin/q2nd ' + '0' * 64)
        self.check('stage failing trial', "printf '#!/bin/sh\\nexit 1\\n' > /tmp/bad-player; hash=$(sha256sum /tmp/bad-player); q2-app-update /tmp/bad-player \"${hash%% *}\"")
        self.check('failed trial rolls back', "timeout 75 sh -c 'until grep -q \"Restoring previous player\" /run/q2nd/launcher.log && ! test -e /state/app/player.trial && cmp -s /state/app/player /usr/bin/q2nd; do sleep 1; done'", 100)
        self.wait_player()
        self.check('stage working trial', 'hash=$(sha256sum /usr/bin/q2nd); q2-app-update /usr/bin/q2nd "${hash%% *}"')
        self.check('working trial confirms', "timeout 75 sh -c 'until grep -q \"SSH update confirmed: UI ready\" /run/q2nd/launcher.log && ! test -e /state/app/player.trial; do sleep 1; done'", 100)
        self.wait_player()

    def ssh_checks(self):
        self.check('generate synthetic SSH client key', 'mkdir -p /state/ssh/client && chmod 700 /state/ssh/client && dropbearkey -t ed25519 -f /state/ssh/client/test-key > /tmp/test-key.pub && sed -n \"/^ssh-ed25519 /p\" /tmp/test-key.pub > /state/ssh/client/authorized_keys && chmod 600 /state/ssh/client/authorized_keys')
        self.check('SSH key authentication over loopback', "timeout 30 dbclient -y -i /state/ssh/client/test-key root@127.0.0.1 'echo Q2_SSH_PASS' > /tmp/ssh-test.log 2>&1 && grep -q Q2_SSH_PASS /tmp/ssh-test.log", 45)
        self.check('persist SSH host identity', 'sha256sum /state/ssh/dropbear_*_host_key > /state/ssh/host-key-checksums && sync')

    def full_state_check(self):
        self.check('fill disposable state', '! dd if=/dev/zero of=/state/fill-test bs=1M 2>/tmp/fill.log')
        try:
            self.check('full state rejects app replacement', 'hash=$(sha256sum /usr/bin/q2nd); ! q2-app-update /usr/bin/q2nd "${hash%% *}"')
            self.check('full state retains working executable', 'cmp -s /state/app/player /usr/bin/q2nd && kill -0 "$(cat /run/q2nd/player.pid)"')
        finally: self.check('release test allocation', 'rm -f /state/fill-test && sync')

    def poweroff(self):
        self.serial.sendall(b'poweroff\n')
        code = self.proc.wait(timeout=45)
        if code != 0: raise AssertionError(f'QEMU poweroff exited with status {code}')
        console = (self.out / 'console.log').read_text(errors='replace')
        if 'Power down' not in console: raise AssertionError('missing orderly kernel poweroff marker')
        self.result['checks'].append('orderly Linux poweroff exits emulator')

    def close(self):
        if self.serial: self.serial.close()
        if self.proc:
            subprocess.run(['podman', 'stop', '--time', '1', self.name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            self.proc.wait(timeout=15)
        if self.log: self.log.close()
        fcntl.flock(self.clock_lock, fcntl.LOCK_UN)
        self.clock_lock.close()
        if self.disk:
            self.result['immutable_sha256_after'] = immutable_hash(self.disk)
            if self.result['immutable_sha256_before'] != self.result['immutable_sha256_after']:
                self.result['passed'] = False
                self.result['error'] = 'immutable disk region changed'
        console = self.out / 'console.log'
        unexpected_fault = console.exists() and re.search(r'sending SIG(?:SEGV|BUS|ILL)|Segmentation fault', console.read_text(errors='replace'))
        if unexpected_fault:
            self.result['passed'] = False
            self.result.setdefault('error', 'unexpected userspace fault in console')
        (self.out / 'result.json').write_text(json.dumps(self.result, indent=2) + '\n')
        print(json.dumps({'artifacts': str(self.out.relative_to(ROOT)), **self.result}, indent=2))
        if unexpected_fault or self.result.get('error') == 'immutable disk region changed':
            raise AssertionError(self.result['error'])

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', default='build/os/kernel-disk/uImage.gz')
    p.add_argument('--image', default='build/os/out/images/q2-sd.img')
    p.add_argument('--initramfs', action='store_true')
    p.add_argument('--icount', action='store_true')
    p.add_argument('--app', action='store_true')
    a = p.parse_args()
    if a.initramfs and a.app: p.error('--app requires the persistent SD image')
    disk = None
    if not a.initramfs:
        dest = WORK / 'tests' / ('disk-' + uuid.uuid4().hex[:10] + '.img')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / a.image, dest)
        disk = dest
    guest = Guest((ROOT / a.kernel).resolve(), disk, a.icount)
    try:
        guest.start()
        guest.seed_entropy()
        guest.check('two CPUs', 'test "$(cat /sys/devices/system/cpu/online)" = 0-1')
        guest.check('C ABI/runtime', 'q2-abi-probe')
        guest.check('mainline button interface', 'grep -q \'Name="Q2 buttons"\' /proc/bus/input/devices')
        guest.check('mainline battery interface', 'test -r /sys/class/power_supply/cw2015-battery/capacity && cat /sys/class/power_supply/cw2015-battery/capacity')
        guest.check('mainline backlight interface', 'test "$(cat /sys/class/backlight/backlight/max_brightness)" = 9 && echo 4 > /sys/class/backlight/backlight/brightness && test "$(cat /sys/class/backlight/backlight/brightness)" = 4 && echo 0 > /sys/class/backlight/backlight/brightness')
        if a.initramfs:
            guest.check('initramfs Rust ABI/runtime', 'q2-rust-probe')
        if disk:
            guest.check('read-only root', "grep ' / squashfs ro' /proc/mounts && ! touch /cannot-write-root")
            guest.check('writable state', 'test "$(cat /run/q2/state-status)" = ready && echo persisted > /state/test-marker && sync')
        if a.app:
            guest.app_checks()
            import tls_fixture
            tls_fixture.check(guest)
            guest.ssh_checks()
            guest.full_state_check()
        guest.check('no external route', '! ip route get 1.1.1.1')
        memory = guest.check('memory inventory', "grep -E 'MemTotal|MemFree|MemAvailable|Slab' /proc/meminfo")
        guest.result['memory_kib'] = {key.decode(): int(value) for key, value in
            re.findall(rb'(MemTotal|MemFree|MemAvailable|Slab):\s+([0-9]+)', memory)}
        if a.app:
            memory = guest.check('player memory high water', 'grep -E "VmPeak|VmHWM|VmRSS" /proc/$(cat /run/q2nd/player.pid)/status')
            guest.result['player_memory_kib'] = {key.decode(): int(value) for key, value in
                re.findall(rb'(VmPeak|VmHWM|VmRSS):\s+([0-9]+)', memory)}
        console = (guest.out / 'console.log').read_text(errors='replace')
        if re.search(r'Kernel panic|WARNING:|BUG:|Oops:|rcu:.*stall', console):
            raise AssertionError('kernel diagnostics failed')
        if a.app and disk:
            guest.check('persist interrupted unconfirmed trial', '/etc/init.d/S80q2-player stop && cp /state/app/player /state/app/player.previous && printf \"invalid trial\\n\" > /state/app/player && touch /state/app/player.trial && sync')
        guest.result['passed'] = True
    except Exception as e:
        guest.result['error'] = str(e)
        raise
    finally: guest.close()
    if disk:
        reboot = Guest((ROOT / a.kernel).resolve(), disk, a.icount)
        try:
            reboot.start()
            reboot.seed_entropy()
            reboot.check('state persists across boot', 'test "$(cat /state/test-marker)" = persisted')
            if a.app:
                reboot.wait_player()
                reboot.check('interrupted trial restored', 'cmp -s /state/app/player /usr/bin/q2nd && ! test -e /state/app/player.trial')
                reboot.check('SSH identity persists across boot', 'sha256sum -c /state/ssh/host-key-checksums')
            reboot.check('orderly state flush', 'sync')
            reboot.poweroff()
            reboot.result['passed'] = True
        except Exception as e:
            reboot.result['error'] = str(e)
            raise
        finally: reboot.close()

if __name__ == '__main__': main()
