#!/usr/bin/env python3
"""Bounded early watchdog ownership and SCM_RIGHTS transfer; emulation only."""
import argparse
import json
import re
import socket
import subprocess
import time
from build import WORK, ROOT
from test_boot import Guest
from test_recovery import boot_id, stage_tools, start_trial, finish_trial


def connect(guest, name):
    end = time.monotonic() + 30
    while time.monotonic() < end:
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(str(guest.out / name)); sock.settimeout(10)
            return sock
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close(); time.sleep(.05)
    raise TimeoutError(name)


def start_inherited(guest, hang=False, inherited_seconds=30):
    guest.command[-1] += ' q2.emulator-wdt=' + ('hang' if hang else 'boot')
    path = '/work/' + str(guest.out.relative_to(ROOT))
    guest.command += ['-S', '-d', 'guest_errors', '-qtest', f'unix:{path}/qtest.sock,server=on,wait=off',
                      '-qmp', f'unix:{path}/qmp.sock,server=on,wait=off']
    guest.log = (guest.out / 'qemu.log').open('w')
    guest.proc = subprocess.Popen(guest.command, stdout=guest.log, stderr=subprocess.STDOUT)
    with connect(guest, 'qtest.sock') as sock, sock.makefile('rwb', buffering=0) as qt:
        full, control = (40960, 10) if inherited_seconds == 5 else (61440, 18)
        for command in ['writel 0x1000203c 0x10000', f'writel 0x10002000 {full}',
                        'writel 0x10002008 0', f'writel 0x1000200c {control}', 'writeb 0x10002004 1',
                        'readb 0x10002004']:
            qt.write((command + '\n').encode()); response = qt.readline().decode().strip()
            if not response.startswith('OK'): raise AssertionError((command, response))
        if int(response[3:], 0) != 1: raise AssertionError('inherited counter not enabled')
    guest.result['inherited_watchdog'] = {'source': 'host qtest before first guest instruction', 'timeout_seconds': inherited_seconds}
    with connect(guest, 'qmp.sock') as sock, sock.makefile('rwb', buffering=0) as qm:
        qm.readline()
        for command in ['qmp_capabilities', 'cont']:
            qm.write((json.dumps({'execute': command}) + '\n').encode())
            while True:
                response = json.loads(qm.readline())
                if 'event' in response: continue
                if 'return' not in response: raise AssertionError(response)
                break
    if hang: return
    guest.wait_boot()
    guest.serial = connect(guest, 'shell.sock'); guest.serial.settimeout(1)
    guest.serial.sendall(b'\n'); guest.receive(rb'# ')
    guest.check('early owner reached serial shell', 'stty -echo; test -S /run/q2-wdt/owner.sock; '
                'kill -0 "$(cat /run/q2-wdt/owner.pid)"; rm -f /dev/mem')


def wait_reset(guest):
    end = time.monotonic() + 90
    while time.monotonic() < end:
        text = (guest.out / 'qemu.log').read_text()
        if 'x2000-wdt: watchdog reset' in text:
            guest.result['checks'].append('fault attributed to emulated watchdog reset')
            return
        if guest.proc.poll() is not None: raise RuntimeError('emulator exited before reset')
        time.sleep(.1)
    raise TimeoutError('watchdog reset')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['transfer', 'kill-owner', 'stop-owner', 'kill-recipient', 'no-ack', 'hang'], required=True)
    parser.add_argument('--icount', action='store_true')
    parser.add_argument('--inherited-seconds', type=int, choices=[5, 30], default=30)
    args = parser.parse_args()
    guest = Guest(WORK / 'kernel-rescue/uImage.gz', None, args.icount)
    guest.rescue = True; guest.fixture_dir = '/usr/share/q2-recovery'
    guest.result.update(test='early-watchdog', fault=args.case)
    try:
        start_inherited(guest, args.case == 'hang', args.inherited_seconds)
        if args.case == 'hang':
            wait_reset(guest)
            console = (guest.out / 'console.log').read_text()
            assert 'Q2_WDT_INJECTED_INIT_HANG' in console and 'Q2_WDT_BOOT_LEASE_EXPIRED' in console
            assert 'Q2_OS_READY' not in console
            guest.result['checks'].append('hung init never becomes ready; bounded boot lease expires')
        else:
            first = boot_id(guest)
            if args.case == 'transfer':
                # Transfer the same open file to the unchanged RAM-trial supervisor.
                manifest = json.loads((WORK / 'recovery/manifest.json').read_text())
                stage_tools(guest, manifest)
                guest.check('request inherited descriptor instead of reopening',
                    'export Q2_WATCHDOG_SOCKET=/run/q2-wdt/owner.sock')
                path = start_trial(guest, 'inherited', 'sleep 7\necho Q2_TRIAL_COMPLETE\n', watchdog=True, deadline=15, lease=5)
                guest.check('boot owner relinquished descriptor',
                    'timeout 3 sh -c \'while test -S /run/q2-wdt/owner.sock; do sleep 0.05; done\'')
                finish_trial(guest, path, 'candidate_exit', 0)
                guest.check('explicit disarm survives timeout', 'sleep 6; test "$(cat /sys/class/watchdog/watchdog0/state)" = inactive')
                assert boot_id(guest) == first
                assert 'Q2_WDT_TRANSFER ack=1' in (guest.out / 'console.log').read_text()
                assert 'x2000-wdt: watchdog reset' not in (guest.out / 'qemu.log').read_text()
            elif args.case == 'kill-recipient':
                guest.check('claim inherited descriptor',
                    'Q2_EMULATOR_TEST=1 q2-watchdog-guard claim 120 > /tmp/q2-owner.log 2>&1 & echo $! > /run/recipient.pid')
                guest.check('recipient feeds beyond timeout',
                    'sleep 6; grep -q Q2_WDT_CLAIMED /tmp/q2-owner.log && test ! -S /run/q2-wdt/owner.sock')
                guest.check('kill recipient', 'kill -KILL "$(cat /run/recipient.pid)"')
                wait_reset(guest)
            elif args.case == 'no-ack':
                guest.check('abandon offered watchdog without ACK', 'Q2_EMULATOR_TEST=1 q2-watchdog-guard abandon')
                wait_reset(guest)
                assert 'Q2_WDT_TRANSFER ack=0' in (guest.out / 'console.log').read_text()
            else:
                signal = 'KILL' if args.case == 'kill-owner' else 'STOP'
                guest.check('stop early keepalive owner', f'kill -{signal} "$(cat /run/q2-wdt/owner.pid)"')
                wait_reset(guest)
            if args.case != 'transfer':
                guest.serial.close(); guest.serial = None
                guest.wait_boot(1)
                guest.serial = connect(guest, 'shell.sock'); guest.serial.settimeout(1)
                guest.serial.sendall(b'\n'); guest.receive(rb'# '); guest.check('reset shell', 'stty -echo')
                assert boot_id(guest) != first
                guest.check('new boot volatile state and explicit disarm',
                    'test ! -e /run/recipient.pid; Q2_EMULATOR_TEST=1 q2-watchdog-guard claim 0')
                guest.result['checks'].append('reset returned to no-card rescue with new boot identity')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()
        suffix = '-5s' if args.inherited_seconds == 5 else ''
        report = WORK / f'boot-watchdog-{args.case}-{"icount" if args.icount else "normal"}{suffix}.json'
        report.write_text(json.dumps({'artifacts': str(guest.out.relative_to(ROOT)), **guest.result}, indent=2) + '\n')

if __name__ == '__main__': main()
