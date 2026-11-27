#!/usr/bin/env python3
"""One controlled watchdog-owner death and verified recovery on stock Q2.

Requires explicit --expire to perform the reset; otherwise qualifies start/feed/
stop only. Never flashes firmware or changes persistent boot selection. The
installed player must be ready and paused. Retains failed evidence; never retries
an expiry automatically. Kernel replacement and hang recovery remain separate.
"""
import argparse, hashlib, json, subprocess, sys, time, uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/power'))
from run import ssh, KEYS
from restore import wait_ready

MODULE = 'cebe7c8c0866a82531fcd918a018fdf95809a787fb6904ce8445286f0d64d0c0'
PREFLIGHT = '''set -eu
test ! -d /run/q2power/measurement.lock
test ! -d /run/q2nd/deploy.lock
test ! -f /run/q2nd/restart
test ! -f /mnt/mmc/.q2nd/player.next
test ! -f /mnt/mmc/.q2nd/player.trial
test "$(cat /proc/asound/card0/pcm0p/sub0/status)" = closed
test "$(cat /run/q2nd/ready)" = "$(cat /run/q2nd/player.pid)"
kill -0 "$(cat /run/q2nd/player.pid)"
test "$(cat /sys/class/power_supply/battery/capacity)" -ge 50
cat /proc/sys/kernel/random/boot_id
sha256sum /module_driver/soc_watchdog.ko /mnt/mmc/.q2nd/player /mnt/mmc/.q2nd/q2nd /etc/init.d/S90play
cat /sys/class/power_supply/battery/status /sys/class/power_supply/battery/capacity
'''

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True)
    p.add_argument('--expire',action='store_true')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--binary',type=Path,default=ROOT/'build/recovery/watchdog-probe')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    conn=ssh(a.host,KEYS);base='/run/q2-watchdog-'+uuid.uuid4().hex[:12]
    result={'passed':False,'physical_hardware':True,'expiry_requested':a.expire,
            'replacement_kernel':False,'persistent_boot_selection':False,'base':base}
    def remote(cmd,check=True,**kw):
        return subprocess.run(conn+[cmd],capture_output=True,check=check,timeout=20,**kw)
    boot=None; locked=False
    try:
        initial=remote(PREFLIGHT).stdout.decode();(a.out/'before.txt').write_text(initial)
        lines=initial.splitlines();boot=lines[0];hashes=lines[1:5]
        if hashes[0].split()[0]!=MODULE:raise RuntimeError('unqualified watchdog module')
        result.update(initial_boot_id=boot,hashes_before=hashes,
                      helper_sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest())
        remote(f'mkdir -p /run/q2power && mkdir /run/q2power/measurement.lock && echo {base} > /run/q2power/measurement.lock/owner')
        locked=True
        remote(f'mkdir {base}')
        remote(f'cat > {base}/probe',input=a.binary.read_bytes())
        remote(f'echo "{result["helper_sha256"]}  {base}/probe" | sha256sum -c - && chmod 700 {base}/probe')
        stopped=remote(f'{base}/probe --stop-test').stdout.decode()
        (a.out/'stop-test.txt').write_text(stopped)
        if 'Q2_WATCHDOG_STOPPED' not in stopped:raise RuntimeError('stop test failed')
        time.sleep(12)
        if remote('cat /proc/sys/kernel/random/boot_id').stdout.decode().strip()!=boot:
            raise RuntimeError('unexpected reboot after stop')
        result['stop_test_passed']=True
        if a.expire:
            remote('sync')
            armed=remote(f'{base}/probe --expire',check=False)
            (a.out/'expiry.txt').write_bytes(armed.stdout+armed.stderr)
            if b'Q2_WATCHDOG_ARMED' not in armed.stdout:raise RuntimeError('watchdog did not arm')
            end=time.monotonic()+150;observations=[];changed=False
            while time.monotonic()<end:
                time.sleep(3)
                try:
                    r=remote('cat /proc/sys/kernel/random/boot_id',check=False)
                    value=r.stdout.decode().strip();observations.append({'exit':r.returncode,'boot_id':value})
                    if r.returncode==0 and value and value!=boot:
                        result['recovered_boot_id']=value;changed=True;break
                except subprocess.TimeoutExpired:observations.append({'timeout':True})
                (a.out/'observations.json').write_text(json.dumps(observations,indent=2)+'\n')
            (a.out/'observations.json').write_text(json.dumps(observations,indent=2)+'\n')
            if not changed:
                # Disarm only if the original boot is still reachable; no blind
                # stop/reset operation against a new or unidentified system.
                r=remote('cat /proc/sys/kernel/random/boot_id',check=False)
                if r.stdout.decode().strip()==boot:
                    disarm=remote(f'test "$(cat /run/q2power/measurement.lock/owner 2>/dev/null)" = "{base}" && {base}/probe --disarm',check=False)
                    result['disarm_exit']=disarm.returncode
                raise RuntimeError('no verified new boot; do not repeat expiry automatically')
            ready=wait_ready(conn);(a.out/'ready.txt').write_text(ready)
            after=remote('sha256sum /module_driver/soc_watchdog.ko /mnt/mmc/.q2nd/player /mnt/mmc/.q2nd/q2nd /etc/init.d/S90play').stdout.decode().splitlines()
            if after!=hashes:raise RuntimeError('persistent artifact identity changed')
            remote(f'test ! -e {base} && test ! -d /run/q2power/measurement.lock')
            result['expiry_recovery_passed']=True
        result['passed']=True
    except Exception as error:
        result['error']=repr(error)
        raise
    finally:
        if locked:
            # Cleanup only our lock in the original boot. A failed start may
            # mean another owner has armed the device: never disarm blindly.
            try:
                cleanup=remote(f'''test "$(cat /proc/sys/kernel/random/boot_id)" = "{boot}" &&
                    test "$(cat /run/q2power/measurement.lock/owner 2>/dev/null)" = "{base}" &&
                    rm -f {base}/probe && rmdir {base} &&
                    rm /run/q2power/measurement.lock/owner && rmdir /run/q2power/measurement.lock''',check=False)
                result['original_boot_cleanup_exit']=cleanup.returncode
                if cleanup.returncode and result['passed'] and not result.get('expiry_recovery_passed'):
                    result.update(passed=False,error='original-boot cleanup failed')
            except Exception as error:
                result['cleanup_error']=repr(error)
                if result['passed'] and not result.get('expiry_recovery_passed'):
                    result.update(passed=False,error='original-boot cleanup failed')
        (a.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        (a.out/'runner.py').write_bytes(Path(__file__).read_bytes())
        print(a.out,flush=True)
    if not result['passed']:
        raise RuntimeError(result['error'])

if __name__=='__main__':main()
