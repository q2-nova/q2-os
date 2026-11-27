#!/usr/bin/env python3
"""Qualify one-shot recovery using the sibling X2000 emulator.

Uses newly generated SD/NAND images, -snapshot and no network. Never touches
the sibling's source, default disk images, or other emulator sessions.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from fixture import build

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('qemu_worktree', type=Path)
    p.add_argument('stock_root', type=Path)
    p.add_argument('--update', type=Path, default=ROOT / 'build/recovery/firmware/update.tar')
    args = p.parse_args()
    qemu = args.qemu_worktree.resolve()
    name = 'recovery-' + uuid.uuid4().hex[:10]
    shared = qemu / 'build/qemu-x2000'
    work = shared / name
    work.mkdir()
    card = work / 'card'
    (card / '.q2nd').mkdir(parents=True)
    shutil.copyfile(qemu / 'qemu/harness/q2nd', card / '.q2nd/q2nd')
    shutil.copyfile(ROOT / 'build/recovery/trial', card / 'trial')
    for case in ('success', 'hang'):
        build(args.stock_root, card / f'{case}.tar', case)
    def run(argv, **kw):
        return subprocess.run([str(a) for a in argv], check=True, cwd=qemu, **kw)
    run(['python3', 'qemu/mknand.py', args.update.resolve(), work / 'nand.img'])
    run(['sh', 'qemu/mksd.sh', work / 'sd.img', '64', card])
    socket = shared / f'{name}.shell.sock'
    monitor = shared / f'{name}.mon.sock'
    transcript = work / 'shell.log'
    qemu_binary = shared / 'qemu-build/qemu-system-mipsel'
    qemu_hash = hashlib.sha256(qemu_binary.read_bytes()).hexdigest()
    pinned_qemu = work / 'qemu-system-mipsel'
    shutil.copyfile(qemu_binary, pinned_qemu)
    pinned_qemu.chmod(0o755)
    if hashlib.sha256(pinned_qemu.read_bytes()).hexdigest() != qemu_hash:
        raise RuntimeError('QEMU changed while making the private executable copy')
    def console(*commands, timeout=55):
        result = run(['python3', 'qemu/tools/console.py', socket, transcript,
                      '--timeout', str(timeout), *commands], capture_output=True, text=True, timeout=timeout + 10)
        return result.stdout
    def boot_id(output):
        ids = re.findall(r'(?m)^([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\s*$', output)
        if not ids:
            raise RuntimeError('no boot identity from guest')
        return ids[-1]
    with (work / 'qemu.log').open('wb') as log:
        proc = subprocess.Popen(['timeout', '240', 'podman', 'run', '--rm', '--network', 'none',
            '--security-opt', 'label=disable', '-v', f'{shared}:/w', '-w', '/w', 'q2-qemu-build',
            f'{name}/qemu-system-mipsel', '-M', 'q2', '-nographic',
            '-kernel', 'fw/recovery-update/xImage', '-snapshot', '-nic', 'none',
            '-drive', f'if=mtd,format=raw,file={name}/nand.img',
            '-drive', f'if=sd,format=raw,file={name}/sd.img',
            '-serial', f'file:/w/{name}/console.log',
            '-serial', f'unix:/w/{name}.shell.sock,server,nowait',
            '-monitor', f'unix:/w/{name}.mon.sock,server,nowait'], cwd=qemu, stdout=log, stderr=log)
        try:
            end = time.monotonic() + 10
            while not socket.exists():
                if proc.poll() is not None or time.monotonic() > end:
                    raise RuntimeError('QEMU did not create the console')
                time.sleep(.1)
            first = console('cat /proc/sys/kernel/random/boot_id; cat /run/q2-boot-stage')
            initial = boot_id(first)
            if 'card_wait_steps=' not in first:
                raise RuntimeError('recovery firmware boot marker missing')
            result = console(
                'mkdir -p /run/recovery/root; tar xf /mnt/mmc/success.tar -C /run/recovery/root',
                '(while echo P; do sleep 1; done) | /mnt/mmc/trial /run/recovery/root 10 4 --jz-watchdog; cat /run/recovery/candidate.log')
            if 'END reason=candidate_exit clean=1 result=0' not in result or 'Q2_TRIAL_COMPLETE' not in result:
                raise RuntimeError('successful pivot/watchdog stop failed')
            # Reuse only our RAM staging tree. Candidate selection is never persistent.
            console('rm /run/recovery/candidate.log; tar xf /mnt/mmc/hang.tar -C /run/recovery/root; mkfifo /run/recovery/pulse; '
                    '(while echo P; do sleep 1; done) > /run/recovery/pulse & '
                    '/mnt/mmc/trial /run/recovery/root 120 4 --jz-watchdog < /run/recovery/pulse > /run/recovery/supervisor.log 2>&1 & '
                    'echo $! > /run/recovery/pid')
            console_log = work / 'console.log'
            boots = console_log.read_bytes().count(b'Linux version ')
            started = console('sleep 2; cat /run/recovery/supervisor.log; kill -KILL $(cat /run/recovery/pid)')
            if 'hardware_watchdog=1' not in started:
                raise RuntimeError('watchdog trial did not start')
            # The vendor's RTC source assumptions and emulator clock differ.
            # Observe reset, rather than racing a guessed wall-clock delay.
            end = time.monotonic() + 30
            while console_log.read_bytes().count(b'Linux version ') <= boots:
                if proc.poll() is not None or time.monotonic() >= end:
                    raise RuntimeError('watchdog did not start another kernel boot')
                time.sleep(.2)
            final = console('cat /proc/sys/kernel/random/boot_id; cat /run/q2-boot-stage; '
                            'test ! -e /run/recovery && echo NO_TRIAL_AFTER_REBOOT')
            recovered = boot_id(final)
            if recovered == initial or 'NO_TRIAL_AFTER_REBOOT' not in final:
                raise RuntimeError('watchdog failed to return to a fresh known-good boot')
            if hashlib.sha256(pinned_qemu.read_bytes()).hexdigest() != qemu_hash:
                raise RuntimeError('private QEMU executable changed during qualification')
            result = {'passed': True, 'initial_boot_id': initial, 'recovered_boot_id': recovered,
                      'trial_sha256': hashlib.sha256((card / 'trial').read_bytes()).hexdigest(),
                      'update_sha256': hashlib.sha256(args.update.read_bytes()).hexdigest(),
                      'qemu_sha256': qemu_hash,
                      'physical_hardware': False, 'raw_directory': str(work)}
            (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            local = ROOT / 'build/recovery' / name
            local.mkdir()
            shutil.copyfile(work / 'result.json', local / 'result.json')
            shutil.copyfile(transcript, local / 'shell.log')
            print(json.dumps(result, indent=2))
        finally:
            if proc.poll() is None:
                try:
                    run(['python3', 'qemu/tools/hmp.py', monitor, 'quit'], capture_output=True, timeout=10)
                finally:
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.terminate()


if __name__ == '__main__':
    main()
