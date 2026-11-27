#!/usr/bin/env python3
"""Load-only real rescue ELF qualification. Never executes a replacement kernel."""
import json
import shutil
import uuid
from build import WORK, ROOT, container, run, sha
from image import STATE_START, STATE_SIZE
from test_boot import Guest
from test_faults import disk_copy


def prepare_candidate_disk():
    candidate = WORK / 'tests' / ('candidate-' + uuid.uuid4().hex[:10] + '.elf')
    source = WORK / 'kernel-rescue/vmlinux'
    run([WORK / 'out/host/bin/mipsel-buildroot-linux-gnu-strip', '--strip-debug', '-o', candidate, source])
    disk = disk_copy(); state = disk.with_suffix('.state.ext4')
    with disk.open('rb') as src, state.open('wb') as dst:
        src.seek(STATE_START); dst.write(src.read(STATE_SIZE))
    container(['debugfs', '-w', '-R', f'write /work/{candidate.relative_to(ROOT)} /candidate.elf',
               '/work/' + str(state.relative_to(ROOT))])
    with state.open('rb') as src, disk.open('r+b') as dst:
        dst.seek(STATE_START); shutil.copyfileobj(src, dst)
    state.unlink()
    return disk, candidate, source


def main():
    disk, candidate, source = prepare_candidate_disk()
    guest = Guest(WORK / 'kernel-recovery/uImage.gz', disk)
    guest.result.update(test='real-kexec-load-only', candidate_sha256=sha(candidate),
        candidate_vmlinux_sha256=sha(source), replacement_kernel_executed=False)
    try:
        guest.start(); guest.seed_entropy(); guest.wait_player()
        guest.check('source-built kexec utility', 'kexec --version')
        guest.check('generated rescue ELF integrity',
            f'test "$(sha256sum /state/candidate.elf | cut -d " " -f 1)" = {sha(candidate)}')
        response = guest.check('load real rescue ELF without executing',
            'kexec -l /state/candidate.elf --append="console=ttyS3,115200 earlycon mem=128M lpj=1000000 panic=0 q2.kexec-candidate=1" '
            '> /tmp/q2-kexec.log 2>&1; echo "KEXEC_RC=$?"; cat /tmp/q2-kexec.log; '
            'echo "KEXEC_LOADED=$(cat /sys/kernel/kexec_loaded)"; true')
        if b'KEXEC_RC=0' in response and b'KEXEC_LOADED=1' in response:
            guest.result['load_status'] = 'accepted-and-unloaded'
            guest.check('unload without jumping', 'kexec -u && test "$(cat /sys/kernel/kexec_loaded)" = 0')
        elif b'Invalid argument' in response and b'KEXEC_LOADED=0' in response:
            guest.result['load_status'] = 'blocked-kernel-EINVAL'
            raise AssertionError('qualified SMP baseline rejected the real FDT/kernel load')
        else:
            raise AssertionError('unexpected real-kernel loader failure; inspect generated log')
        guest.fetch('/tmp/q2-kexec.log', 'kexec-load.log')
        guest.check('known-good userspace survives load test', 'q2-rust-probe && kill -0 "$(cat /run/q2nd/player.pid)"')
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error); raise
    finally:
        guest.close()
        (WORK / 'kexec-load.json').write_text(json.dumps({'artifacts':str(guest.out.relative_to(ROOT)), **guest.result},indent=2)+'\n')

if __name__ == '__main__': main()
