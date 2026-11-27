#!/usr/bin/env python3
"""Source-built RTC, clock floor and CPU hotplug integration; emulation only."""
import argparse
import json
import re
from build import WORK, ROOT
from test_audio import audio_guest, control, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icount', action='store_true')
    args = parser.parse_args()
    guest = audio_guest(args.icount)
    guest.result['test'] = 'mainline-baseline'
    try:
        guest.start(); guest.seed_entropy(); guest.wait_player()
        guest.check('invalid RTC uses marked build floor without setting RTC',
            'test "$(cat /run/q2/clock-status)" = build-floor-unsynchronized && '
            'test "$(date +%s)" -ge "$(cat /etc/q2-build-epoch)" && ! hwclock -r -u')
        guest.check('generated SDK post2038 RTC and alarm ioctls', 'q2-rtc-probe')
        guest.check('valid future RTC can supply system time',
            'hwclock -s -u && test "$(date +%Y)" = 2040 && /etc/init.d/S02q2-clock start && '
            'test "$(cat /run/q2/clock-status)" = rtc-or-system && test "$(date +%Y)" = 2040')
        guest.check('restore generated system clock for SDK fixtures', 'date -u -s "@$(cat /etc/q2-build-epoch)"')
        guest.check('serial IRQ available', 'irq=$(awk \'/ttyS0/ {sub(":", "", $1); print $1}\' /proc/interrupts); test -n "$irq"')
        for cycle in range(10):
            guest.check(f'CPU1 offline and serial IRQ migration {cycle}',
                'echo 2 > /proc/irq/$irq/smp_affinity && taskset 1 sh -c "echo 0 > /sys/devices/system/cpu/cpu1/online" && '
                'test "$(cat /sys/devices/system/cpu/online)" = 0 && test "$(cat /proc/irq/$irq/effective_affinity)" = 1')
            ccu = json.loads(control(guest, 'ccu'))
            expect(guest, f'CPU1 held in reset without delivered interrupts {cycle}', ccu['reset'] == 2 and all(not (p & 2) for p in ccu['delivered']))
            guest.check(f'CPU1 restart and timed SDK userspace {cycle}',
                'taskset 1 sh -c "echo 1 > /sys/devices/system/cpu/cpu1/online" && '
                'test "$(cat /sys/devices/system/cpu/online)" = 0-1 && taskset 2 sh -c "sleep 0.1; q2-rust-probe"')
        guest.check('player survived repeated CPU1 restart', 'kill -0 "$(cat /run/q2nd/player.pid)"')
        console = (guest.out / 'console.log').read_text(errors='replace')
        expect(guest, 'no CPU or RTC kernel faults', not re.search(r'Kernel panic|Oops:|BUG:|WARNING:|failed to come online', console))
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error); raise
    finally:
        guest.close()
        (WORK / ('baseline-' + ('icount' if args.icount else 'normal') + '.json')).write_text(json.dumps({'artifacts':str(guest.out.relative_to(ROOT)), **guest.result},indent=2)+'\n')

if __name__ == '__main__': main()
