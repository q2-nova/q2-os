#!/usr/bin/env python3
"""Real Buildroot kernel handoff with active ALSA/SD and watchdog reset rescue."""
import argparse
import json
import re
import socket
import time
from build import WORK, ROOT, sha
from test_audio import control, expect
from test_boot import Guest
from test_kexec_load import prepare_candidate_disk
from test_recovery import boot_id


def reconnect(guest):
    if guest.serial: guest.serial.close()
    guest.serial = socket.socket(socket.AF_UNIX)
    guest.serial.settimeout(1); guest.serial.connect(str(guest.out / 'shell.sock'))
    guest.serial.sendall(b'\n'); guest.receive(rb'# ')
    guest.check('new kernel serial shell', 'stty -echo')


def wait_rescue(guest):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        text = (guest.out / 'console.log').read_text(errors='replace')
        diagnostics = (guest.out / 'qemu.log').read_text(errors='replace')
        if (diagnostics.count('x2000-wdt: watchdog reset') == 1 and text.count('Linux version') == 3
                and 'Q2_OS_READY' in text.rsplit('Linux version', 1)[-1]):
            return text
        if guest.proc.poll() is not None: raise RuntimeError('emulator exited during recovery')
        time.sleep(.1)
    raise TimeoutError('watchdog reset rescue')


def claim(guest, seconds=120, timeout=5):
    guest.check('claim early watchdog in this kernel',
        f'Q2_EMULATOR_TEST=1 q2-watchdog-guard claim {seconds} {timeout} > /tmp/q2-kexec-owner.log 2>&1 & echo $! > /run/kexec-owner.pid')
    guest.check('watchdog descriptor ownership transferred',
        'timeout 3 sh -c \'until grep -q Q2_WDT_CLAIMED /tmp/q2-kexec-owner.log; do sleep 0.05; done\'')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icount', action='store_true')
    parser.add_argument('--mode', choices=['owned', 'hang', 'no-owner'], default='owned')
    args = parser.parse_args()
    disk, candidate, source = prepare_candidate_disk()
    # Same QEMU controls/capture as audio tests, with an explicitly KEXEC kernel.
    guest = Guest(WORK / 'kernel-recovery/uImage.gz', disk, args.icount)
    path = '/work/' + str(guest.out.relative_to(ROOT))
    guest.command[-1] = guest.command[-1].replace('panic=5', 'panic=0') + ' q2.emulator-wdt=boot'
    guest.command[guest.command.index('-M')+1] = 'q2,audio-wav=' + path + '/capture.wav'
    guest.command += ['-d', 'guest_errors', '-chardev', f'socket,id=q2ctl,path={path}/ctl.sock,server=on,wait=off']
    guest.result.update(test='buildroot-kexec', mode=args.mode, candidate_sha256=sha(candidate),
        candidate_vmlinux_sha256=sha(source), replacement_kernel_executed=False)
    try:
        guest.start(); claim(guest, timeout=15); guest.seed_entropy(); guest.wait_player()
        guest.check('bounded fifteen-second kernel transfer deadline', 'test "$(cat /sys/class/watchdog/watchdog0/timeout)" = 15')
        first = boot_id(guest)
        guest.check('source has SMP and durable known-good app',
            'test "$(cat /sys/devices/system/cpu/online)" = 0-1 && cmp /usr/bin/q2nd /state/app/player && '
            'printf "durable generated kexec canary\\n" > /state/kexec-canary && sync')
        append = 'console=ttyS3,115200 earlycon mem=128M lpj=1000000 panic=0 q2.diagnostic=1 q2.kexec-candidate=1'
        if args.mode != 'no-owner': append += ' q2.emulator-wdt=' + ('hang' if args.mode == 'hang' else 'boot')
        guest.check('load distinct Buildroot rescue ELF and FDT',
            f'kexec -l /state/candidate.elf --append="{append}" > /tmp/q2-kexec-load.log 2>&1 && '
            'test "$(cat /sys/kernel/kexec_loaded)" = 1')
        guest.check('start continuous ALSA and writable SD activity',
            'aplay -q -D hw:Q2,0 -t raw -f S32_LE -r 44100 -c 2 /dev/zero > /tmp/q2-kexec-audio.log 2>&1 & echo $! > /run/audio.pid; '
            '(while :; do dd if=/dev/zero of=/state/kexec-inflight bs=4096 count=32 conv=fsync 2>/dev/null || exit; done) '
            '> /tmp/q2-kexec-io.log 2>&1 & echo $! > /run/io.pid')
        guest.check('both workloads active before handoff',
            'timeout 10 sh -c \'until grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status && test -s /state/kexec-inflight; do sleep 0.05; done\' && '
            'kill -0 "$(cat /run/audio.pid)" && kill -0 "$(cat /run/io.pid)"')
        before = control(guest, 'audio'); guest.result['audio_before'] = before
        expect(guest, 'modeled audio DMA active before jump', 'ch0=run' in before)
        # No stop-services/sync wrapper: exercise kernel shutdown with open
        # PCM and ongoing writes. Only the separate canary is promised durable.
        jump_started = time.monotonic()
        guest.serial.sendall(b'kexec -e\n')
        if args.mode == 'owned':
            end = time.monotonic() + 180
            while time.monotonic() < end:
                progress = (guest.out / 'console.log').read_text(errors='replace')
                if progress.count('Q2_WDT_BOOT_OWNER') >= 2 and 'owner_observed_host_seconds' not in guest.result:
                    guest.result['owner_observed_host_seconds'] = time.monotonic() - jump_started
                if progress.count('Q2_OS_READY') >= 2: break
                if guest.proc.poll() is not None: raise RuntimeError('emulator exited during handoff')
                time.sleep(.1)
            else: raise TimeoutError('replacement readiness')
            reconnect(guest); claim(guest)
            expect(guest, 'handoff did not reset the machine',
                'x2000-wdt: watchdog reset' not in (guest.out / 'qemu.log').read_text())
            second = boot_id(guest)
            expect(guest, 'new kernel boot identity', first != second)
            guest.check('replacement root and FDT command line',
                'grep -q q2.kexec-candidate=1 /proc/cmdline && ! grep -q " / squashfs " /proc/mounts && '
                'test -x /usr/share/q2-recovery/trial && test ! -e /run/audio.pid && test ! -e /run/io.pid')
            after = control(guest, 'audio'); guest.result['audio_after'] = after
            expect(guest, 'old DMA stopped through kernel handoff', 'ch0=stop' in after)
            guest.seed_entropy(); guest.wait_player()
            guest.check('replacement runs SDK userspace on both CPUs past watchdog timeout',
                'test "$(cat /sys/devices/system/cpu/online)" = 0-1 && '
                'taskset 1 q2-rust-probe && taskset 2 q2-rust-probe && sleep 6 && '
                'kill -0 "$(cat /run/kexec-owner.pid)" && test "$(cat /sys/class/watchdog/watchdog0/timeout)" = 5')
            guest.check('durable SD state and writable filesystem survive active-I/O handoff',
                'test "$(cat /state/kexec-canary)" = "durable generated kexec canary" && '
                'echo replacement > /state/kexec-after && sync')
            guest.result.update(replacement_kernel_executed=True, replacement_boot_id=second)
            guest.check('kill replacement watchdog owner to require rescue reset', 'kill -KILL "$(cat /run/kexec-owner.pid)"')
            text = wait_rescue(guest)
        else:
            text = wait_rescue(guest)
            expect(guest, 'replacement kernel entered before injected startup failure', text.count('Linux version') == 3 and 'q2.kexec-candidate=1' in text)
            target_console = text.split('Linux version')[2]
            if args.mode == 'hang':
                expect(guest, 'replacement init hung before readiness', 'Q2_WDT_INJECTED_INIT_HANG' in target_console and
                    'Q2_WDT_BOOT_LEASE_EXPIRED' in target_console and 'Q2_OS_READY' not in target_console)
            else:
                expect(guest, 'replacement init ran without a watchdog owner', 'Run /init as init process' in target_console and
                    'Q2_WDT_BOOT_OWNER' not in target_console)
            guest.result['replacement_kernel_executed'] = True
        reconnect(guest)
        guest.check('disarm fresh rescue boot owner after recovery', 'Q2_EMULATOR_TEST=1 q2-watchdog-guard claim 0')
        guest.seed_entropy(); guest.wait_player()
        recovered = boot_id(guest)
        expect(guest, 'reset returned with new boot identity', recovered != first and recovered != guest.result.get('replacement_boot_id'))
        guest.check('reset restores original disk kernel and durable state',
            '! grep -q q2.kexec-candidate /proc/cmdline && grep -q " / squashfs ro" /proc/mounts && '
            'test "$(cat /sys/kernel/kexec_loaded)" = 0 && test ! -e /run/kexec-owner.pid && '
            'cmp /usr/bin/q2nd /state/app/player && test "$(cat /state/kexec-canary)" = "durable generated kexec canary"')
        expect(guest, 'exactly one watchdog reset after successful handoff',
            (guest.out / 'qemu.log').read_text().count('x2000-wdt: watchdog reset') == 1)
        expect(guest, 'three kernel boots with no panic or shutdown warning', text.count('Linux version') == 3 and
            not re.search(r'Kernel panic|Oops:|BUG:|WARNING:|rcu:.*stall|I/O error|EXT4-fs error', text))
        guest.result['boot_ids'] = {'source':first, 'rescue':recovered}
        guest.check('flush final recovered SD state', 'sync')
        guest.poweroff()
        # Read-only offline check of our disposable state partition after an
        # orderly recovery shutdown; never touches host block devices.
        from image import STATE_START, STATE_SIZE
        from build import container
        state = guest.out / 'state-check.ext4'
        with disk.open('rb') as src, state.open('wb') as dst:
            src.seek(STATE_START); dst.write(src.read(STATE_SIZE))
        container(['e2fsck', '-fn', '/work/' + str(state.relative_to(ROOT))])
        guest.result['checks'].append('offline e2fsck clean after active-I/O handoff and reset')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        if guest.serial:
            try: guest.check('generated handoff failure logs', 'cat /tmp/q2-kexec-load.log /tmp/q2-kexec-owner.log /run/q2nd/launcher.log 2>/dev/null; true', 5)
            except Exception: pass
        raise
    finally:
        guest.close()
        (WORK / f'kexec-{args.mode}-{"icount" if args.icount else "normal"}.json').write_text(
            json.dumps({'artifacts':str(guest.out.relative_to(ROOT)), **guest.result},indent=2)+'\n')

if __name__ == '__main__': main()
