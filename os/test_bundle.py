#!/usr/bin/env python3
"""Paired kernel/rootfs trials with actual kexec and immutable reset fallback."""
import argparse
import json
import shutil
import time
import uuid
from build import ROOT, WORK, container, sha
from bundle import create
from image import STATE_START, STATE_SIZE
from test_faults import disk_copy
from test_audio import audio_guest, expect
from test_appliance import healthy, reset
from test_recovery import boot_id


def inject(disk, files):
    state=disk.with_suffix('.state.ext4')
    with disk.open('rb') as src,state.open('wb') as dst:src.seek(STATE_START);dst.write(src.read(STATE_SIZE))
    commands=['mkdir /bundle-fixture']
    for path in files:commands.append(f'write /work/{path.relative_to(ROOT)} /bundle-fixture/{path.name}')
    batch=state.with_suffix('.debugfs');batch.write_text('\n'.join(commands)+'\n')
    container(['debugfs','-w','-f','/work/'+str(batch.relative_to(ROOT)),'/work/'+str(state.relative_to(ROOT))])
    with state.open('rb') as src,disk.open('r+b') as dst:dst.seek(STATE_START);shutil.copyfileobj(src,dst)
    state.unlink()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',choices=['good','broken-root','corrupt','interrupted'],default='good');p.add_argument('--icount',action='store_true');a=p.parse_args()
    identity='trial-'+uuid.uuid4().hex[:8];bundle=create(identity,a.case=='broken-root')
    disk=disk_copy();inject(disk,[bundle/n for n in ['kernel.elf','rootfs.cpio.gz','manifest.sha256']])
    g=audio_guest(a.icount,disk);i=g.command.index('-append')+1;g.command[i]=g.command[i].replace('q2.diagnostic=1','q2.test-faults=1');g.command+=['-d','guest_errors']
    g.result.update(test='kernel-rootfs-bundle',case=a.case,bundle=identity,manifest_sha256=sha(bundle/'manifest.sha256'),stock_bootstrap_validated=False)
    try:
        g.start();g.seed_entropy();g.wait_player();healthy(g)
        first=boot_id(g);expected=sha(bundle/'manifest.sha256')
        g.check('wrong manifest hash is rejected','! q2-os-update stage /state/bundle-fixture rejected '+('0'*64)+' && test ! -f /state/os/pending')
        if a.case=='interrupted':
            g.check('interrupt real copy before publication',f'Q2_UPDATE_TEST_CUT=kernel-copy q2-os-update stage /state/bundle-fixture {identity} {expected} >/tmp/q2-stage.log 2>&1 & echo $! > /run/stager.pid')
            g.check('stager reached controlled interruption','timeout 20 sh -c \'until grep -q Q2_UPDATE_CUT /tmp/q2-stage.log; do sleep 0.1; done\'; kill -KILL "$(cat /run/stager.pid)"; test ! -f /state/os/pending')
        else:
            g.check('stage paired immutable trial',f'q2-os-update stage /state/bundle-fixture {identity} {expected}',90)
            if a.case=='corrupt':g.check('corrupt published root after staging',f'printf damage >> /state/os/{identity}/rootfs.cpio.gz; sync')
        previous=(g.out/'console.log').read_text().count('Q2_OS_READY')
        g.serial.sendall(b'reboot\n');reset(g,previous);g.wait_player();healthy(g)
        expect(g,'new boot identity',boot_id(g)!=first)
        if a.case=='good':
            g.check('new root and kernel arguments form expected pair',f'test "$(cat /etc/q2-bundle-id)" = {identity} && grep -qw q2.bundle={identity} /proc/cmdline && ! grep -q " / squashfs " /proc/mounts && test ! -e /state/os/pending')
            g.check('trial confirms only after actual worker health',f'timeout 30 sh -c \'until test -s /state/os/confirmed; do sleep 0.1; done\'; test "$(cat /state/os/confirmed)" = {identity} && test ! -f /state/os/trial',40)
            # Confirmation records success; it does not change the immutable boot target.
            previous=(g.out/'console.log').read_text().count('Q2_OS_READY');g.serial.sendall(b'reboot\n');reset(g,previous);g.wait_player();healthy(g)
        else:
            g.check('failed or interrupted trial never became default','test ! -f /state/os/pending && test ! -f /etc/q2-bundle-id && ! grep -q q2.bundle= /proc/cmdline')
            if a.case=='broken-root':g.check('failed trial durably recorded',f'test "$(cat /state/os/failed)" = {identity}')
            if a.case=='interrupted':g.check('abandoned staging cleaned','test ! -d /state/os/staging && test ! -d /state/os/lock')
        g.check('immutable known-good root remains boot target','grep -q " / squashfs ro" /proc/mounts && ! test -e /etc/q2-bundle-id && sync')
        g.poweroff();g.result['passed']=True
    except Exception as e:
        g.result['error']=str(e)
        if g.serial:
            try:g.check('bundle failure diagnostics','cat /run/q2nd/bundle-confirm.log /run/q2nd/supervisor.log; ls -l /state/os; true',5)
            except Exception:pass
        raise
    finally:
        g.close();(WORK/f'bundle-{a.case}-{"icount" if a.icount else "normal"}.json').write_text(json.dumps({'artifacts':str(g.out),**g.result},indent=2)+'\n')
if __name__=='__main__':main()
