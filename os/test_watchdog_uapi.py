#!/usr/bin/env python3
"""Standard watchdog UAPI of the production kernel's X2000 driver; emulation only.

Runs the fault kernel (os/kernel_faults.py). The emulator logs each watchdog
reset ("x2000-wdt: watchdog reset" with -d guest_errors), so every reset the
suite expects is attributed to the watchdog and an unexpected one fails it.
os/test_boot_watchdog.py covers early ownership and descriptor transfer on
the rescue image; docs/kernel-faults.md lists every check here.

  python3 os/test_watchdog_uapi.py [--case uapi|nowayout|all] [--container IMAGE]
"""
import argparse
import re

import kernel_faults
from kernel_faults import Guest

SYSFS = '/sys/class/watchdog/watchdog0'
IDENTITY = 'Ingenic X2000 watchdog'
LOSSES = ('kill', 'stop', 'panic')


def check(guest, label, condition):
    if not condition:
        raise AssertionError(label)
    guest.result['checks'].append(label)


def read(guest, label, path):
    return guest.run(label, 'cat ' + path).strip().splitlines()[-1].strip()


def expect_reset(guest, boot, label, panic=False):
    """Wait for boot `boot` and require one more watchdog reset than before."""
    resets = guest.resets()
    guest.wait_boot(boot, panic=panic)
    check(guest, label, guest.resets() == resets + 1)
    guest.shell_ready()


def holder(guest):
    """Start a feeding holder in the background; returns its PID."""
    guest.send('q2-wdtcheck hold &')
    reply = guest.receive(rb'Q2_WDT_ARMED pid=(\d+)\r?\n', 30)
    return int(re.search(rb'Q2_WDT_ARMED pid=(\d+)', reply).group(1))


def uapi(args):
    guest = Guest('watchdog-uapi', kernel_dir=args.kernel_dir, qemu_dir=args.qemu_dir,
                  container=args.container, exclusive_clock=True)
    try:
        guest.start()
        check(guest, 'driver identity', read(guest, 'identity', SYSFS + '/identity') == IDENTITY)
        check(guest, 'cold boot leaves the watchdog inactive', read(guest, 'state', SYSFS + '/state') == 'inactive')
        maximum = int(read(guest, 'maximum timeout', SYSFS + '/max_timeout'))
        guest.result['max_timeout'] = maximum
        guest.probe('exclusive open, timeout bounds, disable and enable, magic close',
                    'wdtcheck', 'magic', str(maximum), timeout=60)
        check(guest, 'no reset across disable, magic close and the timeouts after them', guest.resets() == 0)
        boot = 1
        for loss in LOSSES:
            pid = holder(guest)
            guest.run(f'{loss}: a feeding holder outlives one timeout', 'sleep 3', 30)
            check(guest, f'{loss}: no reset while the holder feeds', guest.resets() == boot - 1)
            if loss == 'panic':
                guest.send('echo 0 > /proc/sys/kernel/panic; echo c > /proc/sysrq-trigger')
            else:
                guest.send(f'kill -{"KILL" if loss == "kill" else "STOP"} {pid}')
            boot += 1
            expect_reset(guest, boot, f'{loss}: the watchdog resets the machine', panic=loss == 'panic')
            check(guest, f'{loss}: the new boot starts with the watchdog inactive',
                  read(guest, 'state', SYSFS + '/state') == 'inactive')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()


def nowayout(args):
    guest = Guest('watchdog-nowayout', kernel_dir=args.kernel_dir, qemu_dir=args.qemu_dir,
                  container=args.container, exclusive_clock=True, append='ingenic_x2000_wdt.nowayout=1')
    try:
        guest.start()
        check(guest, 'nowayout parameter set', read(guest, 'nowayout', SYSFS + '/nowayout') == '1')
        guest.send('q2-wdtcheck nowayout')
        guest.receive(rb'Q2_WDT_NOWAYOUT_ARMED\r?\n', 30)
        guest.result['checks'].append('nowayout refuses disable and accepts magic close')
        expect_reset(guest, 2, 'nowayout: the watchdog resets after magic close')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--case', choices=('uapi', 'nowayout', 'all'), default='all')
    kernel_faults.guest_arguments(parser)
    args = parser.parse_args()
    if args.case in ('uapi', 'all'):
        uapi(args)
    if args.case in ('nowayout', 'all'):
        nowayout(args)


if __name__ == '__main__':
    main()
