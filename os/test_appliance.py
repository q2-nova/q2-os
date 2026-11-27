#!/usr/bin/env python3
"""Default mainline boot and real-worker watchdog faults. Disposable emulation."""
import argparse
import json
import time
from build import WORK
from test_audio import audio_guest, control, expect
from test_recovery import boot_id
from test_kexec import reconnect


def healthy(g):
    g.check('both workers healthy', "timeout 25 sh -c 'while :; do pid=$(cat /run/q2nd/player.pid 2>/dev/null); if test -n \"$pid\" && test \"$(cat /run/q2nd/healthy 2>/dev/null)\" = \"$pid\" && kill -0 \"$pid\"; then exit 0; fi; sleep 0.1; done'",35)


def reset(g, previous):
    end=time.monotonic()+150
    while time.monotonic()<end:
        text=(g.out/'console.log').read_text(errors='replace')
        if text.count('Q2_OS_READY')>previous: break
        if g.proc.poll() is not None: raise RuntimeError('emulator stopped')
        time.sleep(.1)
    else: raise TimeoutError('watchdog reset')
    reconnect(g); g.seed_entropy()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fault',choices=['none','ui','audio','process','spoof','repeat','missing-state','full-state'],default='none');p.add_argument('--icount',action='store_true');a=p.parse_args()
    g=audio_guest(a.icount)
    if a.fault=='missing-state':
        with g.disk.open('r+b') as disk: disk.seek(462);disk.write(bytes(16))
        from test_boot import immutable_hash
        from build import sha
        g.result['disk_sha256_before']=sha(g.disk);g.result['immutable_sha256_before']=immutable_hash(g.disk)
    i=g.command.index('-append')+1;g.command[i]=g.command[i].replace('q2.diagnostic=1','q2.test-faults=1')
    g.command += ['-d','guest_errors']
    g.result.update(test='mainline-appliance',fault=a.fault)
    try:
        g.start();g.seed_entropy()
        if a.fault=='missing-state':
            g.check('missing state enters stable rescue','test -f /run/q2nd/rescue && test ! -s /run/q2nd/player.pid; sleep 8; test -f /run/q2nd/rescue')
            g.poweroff();g.result['passed']=True;return
        g.wait_player();healthy(g)
        if a.fault=='full-state':
            g.check('fill disposable state before reboot','/etc/init.d/S80q2-player stop && ! dd if=/dev/zero of=/state/fill bs=1M 2>/tmp/fill.log; sync',60)
            count=(g.out/'console.log').read_text().count('Q2_OS_READY');g.serial.sendall(b'reboot\n');reset(g,count)
            g.check('unwritable state counter selects stable rescue','test -f /run/q2nd/rescue && test ! -s /run/q2nd/player.pid; sleep 8; test -f /run/q2nd/rescue')
            g.check('release fixture','rm /state/fill; sync');g.poweroff();g.result['passed']=True;return
        if a.fault=='none':
            g.check('repeated shell exec preserves userspace context','n=0; while test "$n" -lt 200; do sh -c "/bin/true" || exit 1; n=$((n+1)); done',90)
        first=boot_id(g)
        g.check('normal boot selects mainline interfaces', 'grep -q "input=evdev; audio=ALSA" /run/q2nd/launcher.log && test -c /dev/dri/card0 && test -d /sys/class/net/usb0 && ! wget -q -T 2 -O /tmp/q2-no-loop http://127.0.0.1:4533/rest/ping')
        g.check('idle and absent server do not expire workers', 'sleep 35; test -s /run/q2nd/healthy && test -s /run/q2nd/player.pid && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)"',45)
        expect(g,'idle did not reboot',boot_id(g)==first)
        if a.fault=='none':
            g.check('restart crashed mainline player without resetting', 'old=$(cat /run/q2nd/player.pid); kill -KILL "$old"; timeout 20 sh -c \'until test -s /run/q2nd/healthy && test "$(cat /run/q2nd/player.pid)" != "$1"; do sleep 0.1; done\' sh "$old"',30)
            healthy(g);expect(g,'launcher recovered within same boot',boot_id(g)==first)
            g.check('stage failing mainline app trial', "printf '#!/bin/sh\\nexit 1\\n' > /tmp/q2-bad-player; hash=$(sha256sum /tmp/q2-bad-player); q2-app-update /tmp/q2-bad-player \"${hash%% *}\"")
            g.check('bad mainline app restores known-good executable', 'timeout 25 sh -c \'until grep -q "Restoring previous player" /run/q2nd/launcher.log && test ! -f /state/app/player.trial; do sleep 0.1; done\' && cmp /state/app/player /usr/bin/q2nd',35)
            healthy(g);expect(g,'app rollback stayed in same boot',boot_id(g)==first)
        if a.fault!='none':
            cycles=3 if a.fault=='repeat' else 1
            for n in range(cycles):
                if a.fault=='process' or a.fault=='repeat': command='kill -STOP "$(cat /run/q2nd/player.pid)"'
                elif a.fault=='spoof':
                    # An unregistered sender cannot impersonate the frozen player's PID.
                    command='kill -STOP "$(cat /run/q2nd/player.pid)"; Q2ND_HEALTH_SOCKET=/run/q2nd/health.sock q2nd --headless --data /tmp/q2-spoofer >/tmp/spoof.log 2>&1 & true'
                else: command=f'touch /run/q2nd/freeze-{a.fault}'
                count=(g.out/'console.log').read_text().count('Q2_OS_READY');g.check('inject '+a.fault,command);reset(g,count)
                if a.fault=='repeat' and n==2:
                    g.check('three failed boots stop at bounded rescue', 'test -f /run/q2nd/rescue && test ! -s /run/q2nd/player.pid && test "$(cat /state/app/boot-failures)" = 3')
                else: healthy(g)
            expect(g,'reset attributed to watchdog', 'x2000-wdt: watchdog reset' in (g.out/'qemu.log').read_text())
        g.check('flush state','sync');g.poweroff();g.result['passed']=True
    except Exception as e:
        g.result['error']=str(e)
        if g.serial:
            try:g.check('diagnostic logs','cat /run/q2nd/supervisor.log /run/q2nd/launcher.log; true',5)
            except Exception:pass
        raise
    finally:
        g.close();(WORK/f'appliance-{a.fault}-{"icount" if a.icount else "normal"}.json').write_text(json.dumps({'artifacts':str(g.out),**g.result},indent=2)+'\n')
if __name__=='__main__':main()
