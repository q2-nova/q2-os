#!/usr/bin/env python3
"""Verify diagnostic behavior for missing state and a bad init on disposable disks."""
import json
import shutil
import uuid
from build import WORK, sha
from test_boot import Guest
from image import STATE_START

base = WORK / 'out/images/q2-sd.img'
kernel = WORK / 'kernel-disk/uImage.gz'


def disk_copy():
    disk = WORK / 'tests' / ('fault-' + uuid.uuid4().hex[:10] + '.img')
    disk.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base, disk)
    return disk


def state_hash(disk):
    import hashlib
    with disk.open('rb') as f:
        f.seek(STATE_START)
        return hashlib.sha256(f.read()).hexdigest()


def main():
    disk = disk_copy()
    with disk.open('r+b') as f:
        f.seek(446 + 16)
        f.write(b'\0' * 16)
    before = state_hash(disk)
    guest = Guest(kernel, disk)
    try:
        guest.start()
        guest.check('missing state is explicit', 'test "$(cat /run/q2/state-status)" = missing')
        guest.check('player stays stopped', '! test -e /run/q2nd/player.pid')
        guest.check('diagnostic shell remains usable', 'test -r /etc/q2-os-release.json && test -x /usr/bin/q2-abi-probe')
        guest.result['passed'] = True
    except Exception as e:
        guest.result['error'] = str(e)
        raise
    finally: guest.close()
    if state_hash(disk) != before:
        raise AssertionError('missing partition caused state modification')

    guest = Guest(kernel, disk_copy())
    guest.command[-1] += ' init=/q2-intentionally-missing-init'
    try:
        try:
            guest.start()
        except RuntimeError:
            console = (guest.out / 'console.log').read_text(errors='replace')
            if 'Requested init /q2-intentionally-missing-init failed' not in console:
                raise
            guest.result['checks'].append('missing init detected as boot failure')
            guest.result['passed'] = True
        else:
            raise AssertionError('harness accepted a broken init')
    except Exception as e:
        guest.result['error'] = str(e)
        raise
    finally: guest.close()

if __name__ == '__main__': main()
