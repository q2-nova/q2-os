#!/usr/bin/env python3
"""Exercise one-shot RAM trials and modeled reset on disposable Q2 OS disks.

Never imports the power harness or opens SSH. Recovery uses the standard
Linux watchdog driver; it does not program watchdog registers through /dev/mem.
"""
import argparse
import base64
import fcntl
import json
import re
import shutil
import socket
from pathlib import Path
from build import ROOT, WORK, container, run, sha
from test_boot import Guest
from test_faults import disk_copy
from image import STATE_START, STATE_SIZE


def prepare_disk_locked():
    run(['python3', ROOT / 'os/recovery/prepare.py'])
    disk = disk_copy()
    state = disk.with_suffix('.state.ext4')
    with disk.open('rb') as source, state.open('wb') as target:
        source.seek(STATE_START)
        target.write(source.read(STATE_SIZE))
    commands = ['mkdir /recovery-fixtures']
    manifest = json.loads((WORK / 'recovery/manifest.json').read_text())
    for name in manifest['artifacts']:
        source = WORK / 'recovery' / name
        commands.append(f'write /work/{source.relative_to(ROOT)} /recovery-fixtures/{name}')
    batch = state.with_suffix('.debugfs')
    batch.write_text('\n'.join(commands) + '\n')
    container(['debugfs', '-w', '-f', '/work/' + str(batch.relative_to(ROOT)),
               '/work/' + str(state.relative_to(ROOT))])
    with state.open('rb') as source, disk.open('r+b') as target:
        target.seek(STATE_START)
        shutil.copyfileobj(source, target)
    state.unlink()
    return disk, manifest


def prepare_disk():
    (WORK / 'recovery').mkdir(exist_ok=True)
    with (WORK / 'recovery/.prepare.lock').open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        return prepare_disk_locked()


def boot_id(guest):
    data = guest.check('read boot identity', 'cat /proc/sys/kernel/random/boot_id')
    return re.search(rb'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', data).group().decode()


def init_script(guest, path, body):
    encoded = base64.b64encode(('#!/bin/sh\nset -eu\n' + body).encode()).decode()
    guest.check('write synthetic trial init', f"printf '%s' '{encoded}' | base64 -d > {path}/root/init")


def start_trial(guest, name, body, *, mode='normal', watchdog=False, deadline=8, lease=3):
    path = '/run/q2trial-' + name
    guest.trial_path = path
    guest.check('stage RAM-only candidate ' + name,
        f'mkdir {path} && mount -t tmpfs -o size=32m,mode=700 tmpfs {path} && '
        f'mkdir {path}/root && tar xf {guest.fixture_dir}/root.tar -C {path}/root && mkfifo {path}/pulse')
    init_script(guest, path, body)
    pulse = "printf 'P\\n'; sleep 0.25" if mode == 'eof' else (
        "while printf 'X\\n'; do sleep 0.25; done" if mode == 'lease' else
        "while printf 'P\\n'; do sleep 0.25; done")
    helper = 'trial-linux-watchdog' if watchdog else 'trial'
    option = ' --jz-watchdog' if watchdog else ''
    guest.check('launch isolated supervisor ' + name,
        f'({pulse}) > {path}/pulse & echo $! > {path}/writer.pid; '
        f'(Q2_EMULATOR_TEST=1 /run/recovery-tools/{helper} {path}/root {deadline} {lease}{option} '
        f'< {path}/pulse > {path}/supervisor.log 2>&1 & trial_pid=$!; echo "$trial_pid" > {path}/pid; '
        f'wait "$trial_pid"; echo $? > {path}/exit) & true')
    guest.check('supervisor started ' + name,
        f"timeout 15 sh -c 'until grep -q ^START {path}/supervisor.log; do sleep 0.1; done' && cat {path}/supervisor.log")
    return path


def finish_trial(guest, path, reason, code):
    guest.check('bounded supervisor completion',
        f"timeout 20 sh -c 'until test -s {path}/exit; do sleep 0.1; done'", 35)
    guest.check('supervisor result and clean descendants',
        f'result=$(cat {path}/exit); kill "$(cat {path}/writer.pid)" 2>/dev/null || :; '
        f'cat {path}/supervisor.log; test "$result" = {code} && '
        f"grep -q 'END reason={reason} clean=1 result={code}' {path}/supervisor.log")
    guest.check('candidate cannot leave a child mount in recovery namespace',
        f"! grep -q ' {path}/root/tmp ' /proc/mounts && "
        f"timeout 5 sh -c 'until umount {path} 2>/dev/null; do sleep 0.1; done' && rmdir {path}")
    if not guest.rescue:
        guest.check('recovery player remains alive', 'kill -0 "$(cat /run/q2nd/player.pid)"')


def stage_tools(guest, manifest):
    for name, item in manifest['artifacts'].items():
        guest.check('verify fixture ' + name,
            f"test \"$(sha256sum {guest.fixture_dir}/{name} | cut -d ' ' -f 1)\" = {item['sha256']}")
    guest.check('copy test tools into volatile runtime',
        f'mkdir -p /run/recovery-tools && cp {guest.fixture_dir}/trial* {guest.fixture_dir}/kexec-probe '
        '/run/recovery-tools/ && chmod 700 /run/recovery-tools/*')


def reconnect_after_reset(guest, previous, initial_id, path):
    guest.serial.close(); guest.serial = None
    guest.wait_boot(previous)
    guest.serial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    guest.serial.settimeout(1)
    guest.serial.connect(str(guest.out / 'shell.sock'))
    guest.serial.sendall(b'\n')
    guest.receive(rb'# ')
    guest.check('disable rebooted shell echo', 'stty -echo')
    guest.seed_entropy()
    recovered = boot_id(guest)
    if recovered == initial_id: raise AssertionError('reset reused the old boot identity')
    guest.check('reset removes the volatile trial and loaded kernel',
        f'test ! -e {path} && test ! -e /run/recovery-tools && '
        'test "$(cat /sys/kernel/kexec_loaded)" = 0')
    if guest.rescue:
        guest.check('card-independent rescue root restored',
            'test "$(cat /run/q2/state-status)" = missing && '
            'test ! -e /etc/q2-transient-trial && test ! -e /dev/mmcblk0')
    else:
        guest.wait_player()
        guest.check('reset returns to known-good disk root',
            'grep -q " / squashfs ro" /proc/mounts && cmp -s /usr/bin/q2nd /state/app/player')
    guest.result.setdefault('reset_boot_ids', []).append({'before': initial_id, 'after': recovered})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icount', action='store_true')
    parser.add_argument('--nowayout', action='store_true', help='require reset even after successful candidate cleanup')
    parser.add_argument('--rescue', action='store_true', help='boot embedded rescue userspace with no SD card')
    args = parser.parse_args()
    if args.rescue:
        disk = None
        manifest = json.loads((WORK / 'recovery/manifest.json').read_text())
        kernel = WORK / 'kernel-rescue/uImage.gz'
        artifact = json.loads((kernel.parent / 'artifact.json').read_text())
        if artifact['initramfs_sha256'] != manifest['rescue_initramfs_sha256']:
            raise RuntimeError('rescue kernel has stale recovery fixtures')
    else:
        disk, manifest = prepare_disk()
        kernel = WORK / 'kernel-recovery/uImage.gz'
    guest = Guest(kernel, disk, args.icount)
    guest.rescue = args.rescue
    guest.fixture_dir = '/usr/share/q2-recovery' if args.rescue else '/state/recovery-fixtures'
    # Panic itself must not auto-reboot; recovery must come from the watchdog.
    guest.command[-1] = guest.command[-1].replace('panic=5', 'panic=0')
    if args.nowayout: guest.command[-1] += ' ingenic_x2000_wdt.nowayout=1'
    guest.command += ['-d', 'guest_errors']
    guest.result['watchdog_reset_log_enabled'] = True
    guest.result['nowayout'] = args.nowayout
    guest.result['recovery_fixtures'] = manifest
    guest.result['card_independent_rescue'] = args.rescue
    guest.result['watchdog_interface'] = 'standard /dev/watchdog0 via WDIOC_*; unchanged supervisor compatibility layer'
    try:
        guest.start(); guest.seed_entropy()
        if not args.rescue: guest.wait_player()
        stage_tools(guest, manifest)
        initial_id = boot_id(guest)
        guest.check('two online CPUs', 'test "$(cat /sys/devices/system/cpu/online)" = 0-1')
        guest.check('C and Rust runtime on recovery kernel', 'q2-abi-probe && q2-rust-probe')
        probe = guest.check('load-only kexec probe', '/run/recovery-tools/kexec-probe && test "$(cat /sys/kernel/kexec_loaded)" = 0')
        guest.result['kexec_load'] = ('accepted-and-unloaded' if b'Q2_KEXEC_LOAD_ACCEPTED' in probe
                                      else 'rejected-EINVAL')
        guest.result['replacement_kernel_executed'] = False
        watchdog = guest.check('inspect standard watchdog availability',
            'if test -c /dev/watchdog0 || test -c /dev/watchdog; then echo WDT_PRESENT; else echo WDT_ABSENT; fi')
        guest.result['linux_watchdog_available'] = b'WDT_PRESENT' in watchdog
        if not guest.result['linux_watchdog_available']: raise AssertionError('standard watchdog missing')
        guest.check('watchdog identity and explicit nowayout policy',
            'test "$(cat /sys/class/watchdog/watchdog0/identity)" = "Ingenic X2000 watchdog" && '
            f'test "$(cat /sys/class/watchdog/watchdog0/nowayout)" = {int(args.nowayout)}')
        guest.check('remove MMIO device to require standard watchdog interface', 'rm -f /dev/mem')
        success = ('test -f /trial-marker\ntest ! -e /oldroot/etc\ntest ! -e /proc/1\n'
                   'test ! -e /sys\ntest ! -e /dev\ntest ! -e /state\n'
                   'touch /tmp/candidate-write\necho Q2_TRIAL_COMPLETE\n')
        path = start_trial(guest, 'success', success)
        guest.check('pivot hid recovery root and wrote only private tmpfs',
            f"timeout 10 sh -c 'until grep -q Q2_TRIAL_COMPLETE {path}/candidate.log; do sleep 0.1; done' && "
            'test ! -e /tmp/candidate-write')
        finish_trial(guest, path, 'candidate_exit', 0)
        daemon = 'exec /bin/q2-detached-fixture\n'
        cases = [('failure', 'exit 7\n', 'normal', 'candidate_exit', 7)]
        cases += [(f'daemon-{index}', daemon, 'normal', 'candidate_exit', 0) for index in range(10)]
        cases += [
            ('eof', 'while :; do sleep 1; done\n', 'eof', 'host_eof', 124),
            ('lease', 'while :; do sleep 1; done\n', 'lease', 'host_lease', 124),
            ('deadline', 'while :; do sleep 1; done\n', 'normal', 'deadline', 124),
            ('signal', 'while :; do sleep 1; done\n', 'normal', 'signal', 124),
        ]
        for name, body, mode, reason, code in cases:
            path = start_trial(guest, name, body, mode=mode, deadline=4 if name == 'deadline' else 8, lease=2)
            if name == 'signal': guest.check('terminate supervisor', f'kill -TERM "$(cat {path}/pid)"')
            if name.startswith('daemon-'):
                guest.check('detached child is reaped',
                    f"timeout 10 sh -c 'until grep -q ^END {path}/supervisor.log; do sleep 0.1; done' && "
                    f'pid=$(sed -n "s/^DETACHED=//p" {path}/candidate.log); test -n "$pid" && ! kill -0 "$pid" 2>/dev/null')
            finish_trial(guest, path, reason, code)
        if boot_id(guest) != initial_id: raise AssertionError('ordinary trials unexpectedly reset the guest')
        path = start_trial(guest, 'watchdog-stop', success, watchdog=True)
        guest.check('supervisor owns the standard watchdog', f'grep -q LINUX_WATCHDOG {path}/supervisor.log')
        if args.nowayout:
            previous = (guest.out / 'console.log').read_text().count('Q2_OS_READY')
            guest.check('nowayout refuses disarm after clean candidate exit',
                f"timeout 3 sh -c 'until grep -q RESET_REQUIRED {path}/supervisor.log; do sleep 0.05; done' && "
                f'! test -e {path}/exit')
            reconnect_after_reset(guest, previous, initial_id, path)
            guest.result['reset_boot_ids'][-1]['fault'] = 'nowayout-clean-exit'
        else:
            finish_trial(guest, path, 'candidate_exit', 0)
            guest.check('disarmed watchdog remains stopped past programmed interval', 'sleep 6')
            if boot_id(guest) != initial_id: raise AssertionError('watchdog failed to stop')
        for fault in ['kill', 'stop', 'panic']:
            stage_tools(guest, manifest)
            guest.check('require standard watchdog after reset', 'rm -f /dev/mem')
            initial_id = boot_id(guest)
            path = start_trial(guest, 'watchdog-' + fault, 'while :; do sleep 1; done\n',
                               watchdog=True, deadline=120, lease=10)
            guest.check('watchdog is armed before fault', f'grep -q hardware_watchdog=1 {path}/supervisor.log')
            guest.check('standard keepalive survives beyond five-second timeout',
                f'sleep 6; kill -0 "$(cat {path}/pid)" && grep -q LINUX_WATCHDOG {path}/supervisor.log')
            if args.rescue: guest.check('mark volatile rescue root', 'touch /etc/q2-transient-trial')
            previous = (guest.out / 'console.log').read_text().count('Q2_OS_READY')
            if fault == 'panic':
                guest.serial.sendall(b'echo c > /proc/sysrq-trigger\n')
            else:
                guest.check('inject supervisor ' + fault, f'kill -{fault.upper()} "$(cat {path}/pid)"')
            # wait_boot normally rejects a panic; here it is the deliberate stimulus.
            if fault == 'panic':
                import time
                end = time.monotonic() + 90
                while (guest.out / 'console.log').read_text().count('Q2_OS_READY') <= previous:
                    if guest.proc.poll() is not None or time.monotonic() > end: raise RuntimeError('watchdog did not recover panic')
                    time.sleep(.1)
            reconnect_after_reset(guest, previous, initial_id, path)
            guest.result['reset_boot_ids'][-1]['fault'] = fault
        console = (guest.out / 'console.log').read_text(errors='replace')
        # Linux also prints an "end Kernel panic" delimiter with the same text.
        panics = re.findall(r'(?m)^\[\s*[0-9.]+\] Kernel panic - not syncing: (.+)', console)
        if len(panics) != 1 or panics[0].strip() != 'sysrq triggered crash':
            raise AssertionError('expected exactly the injected SysRq panic')
        if re.search(r'WARNING:|BUG:|Oops:|rcu:.*stall', console):
            raise AssertionError('unexpected kernel diagnostics')
        guest.result['checks'].append('only the deliberate SysRq panic occurred')
        resets = (guest.out / 'qemu.log').read_text().count('x2000-wdt: watchdog reset')
        if resets != 3 + int(args.nowayout): raise AssertionError('reset not attributed to watchdog')
        guest.result['watchdog_reset_diagnostics'] = resets
        guest.result['checks'].append('every reset attributed to emulated watchdog')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        if guest.serial and getattr(guest, 'trial_path', None):
            try:
                path = guest.trial_path
                guest.check('generated trial diagnostics after failure',
                    f'cat {path}/supervisor.log; head -c 4096 {path}/candidate.log; '
                    f'cat {path}/exit 2>/dev/null; '
                    f'pid=$(sed -n "s/^DETACHED=//p" {path}/candidate.log); '
                    'case "$pid" in ""|*[!0-9]*) ;; *) cat /proc/$pid/status 2>/dev/null ;; esac; true', 5)
            except Exception: pass
        raise
    finally:
        guest.close()
        name = ('rescue-' if args.rescue else 'recovery-') + ('nowayout-' if args.nowayout else '') + ('icount' if args.icount else 'normal')
        (WORK / (name + '.json')).write_text(
            json.dumps({'result': str((guest.out / 'result.json').relative_to(ROOT)), **guest.result}, indent=2) + '\n')


if __name__ == '__main__': main()
