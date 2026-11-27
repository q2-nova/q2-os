"""Host-only USB ownership tests; every OS I/O endpoint is a generated fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/hardware/usb-storage'

OPS = r'''
import os,sys,json
from pathlib import Path
root=Path(os.environ['FIXTURE']); op,*args=sys.argv[1:]
with (root/'events').open('a') as f:f.write(op+' '+ ' '.join(args)+'\n')
if os.environ.get('FAIL_OP')==op:sys.exit(1)
mounts=root/'proc/mounts'; lines=[x for x in mounts.read_text().splitlines() if x.strip()]
def save(lines):
 mounts.write_text('\n'.join(lines)+'\n')
 rows=[]
 for i,line in enumerate(lines):
  dev,path,fs,*_=line.split()
  major='7:0' if dev=='/dev/loop0' else '0:25' if fs=='ubifs' else '179:1'
  mountroot='/q2-mainline' if fs=='ubifs' else '/'
  rows.append(f'{i+10} 1 {major} {mountroot} {path} rw - {fs} {dev} rw')
 (root/'proc/1/mountinfo').write_text('\n'.join(rows)+'\n')
if op=='umount':
 save([x for x in lines if x.split()[1]!=args[0]])
 if os.environ.get('AUTO_CLEAR')=='1' and args[0]==str(root/'state'):
  (root/'sys/class/block/loop0/loop/backing_file').unlink()
elif op=='detach':(root/'sys/class/block/loop0/loop/backing_file').unlink()
elif op=='attach':
 p=root/'sys/class/block/loop0/loop/backing_file';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(args[1]+'\n')
elif op=='close-lun':
 assert not (root/'sys/kernel/config/usb_gadget/q2diag/UDC').read_text().strip(), 'LUN closed before gadget quiesced'
elif op=='remove-lun':
 assert not (root/'sys/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card/lun.0/file').read_text().strip(), 'owned backing still open'
elif op=='mount':
 assert not (root/'sys/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card').exists(), 'mount overlaps owned LUN'
 dev,path=args[-2:]; fs=args[1]
 save(lines+[f'{dev} {path} {fs} rw 0 0'])
'''

DRIVER = r'''
Q2_STORAGE_SOURCE_ONLY=1
. "$HELPER"
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
RUN=$FIXTURE/run/q2
SERVICE_RUN=$FIXTURE/run/q2nd
SERVICES=$FIXTURE/services
INSTALLED_SERVICE=$FIXTURE/installed-service
CARD_MOUNT=$FIXTURE/card
STATE_MOUNT=$FIXTURE/state
STATE_FILE=$CARD_MOUNT/.q2-mainline-dev-20261102/state.ext4
SESSION=$RUN/usb-storage
LOCK=$RUN/usb-storage.lock
block_device() { return 0; }
id() { echo 0; }
log_op() { "$PYTHON" "$FIXTURE/ops.py" "$@"; }
sync() { log_op sync; }
umount() { log_op umount "$@"; }
losetup() {
 if [ "$1" = -d ]; then log_op detach "$2"; else log_op attach "$@"; fi
}
mount() { log_op mount "$@"; }
mkdir() {
 command mkdir "$@" || return $?
 case "$*" in "$SYS/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card")
  command mkdir "$SYS/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card/lun.0"
  for attr in file removable ro nofua cdrom; do
   : > "$SYS/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card/lun.0/$attr"
  done ;;
 esac
}
rmdir() {
 case "$1" in "$SYS/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card") log_op remove-lun || return; command rm -r "$1" ;;
 *) command rmdir "$@" ;;
 esac
}
readlink() {
 case "${RACE_KIND:-}:$1" in
  kernel:*/8/exe|active:*/8/exe) return 1 ;;
  process:*/8/exe) command rm -r "$PROC/8"; return 1 ;;
  fd:*/8/fd/4) command rm "$PROC/8/fd/4"; return 1 ;;
  active-fd:*/8/fd/4) return 1 ;;
  reused-safe:*/8/fd/4|reused-card:*/8/fd/4)
   if [ ! -e "$FIXTURE/fd-reused" ]; then
    command rm "$PROC/8/fd/4"
    case "$RACE_KIND" in reused-safe) target=/dev/null;; *) target=/dev/mmcblk0;; esac
    command ln -s "$target" "$PROC/8/fd/4"
    : > "$FIXTURE/fd-reused"
    return 1
   fi ;;
 esac
 command readlink "$@"
}
awk() {
 for arg do
  case "${RACE_KIND:-}:$arg" in
   mount-gone:*/8/mountinfo) command rm -r "$PROC/8"; return 1 ;;
   mount-active:*/8/mountinfo|mount-zombie:*/8/mountinfo) return 1 ;;
  esac
 done
 command awk "$@"
}
for directory in "$PROC"/[0-9]*; do
 if [ ! -e "$directory/status" ]; then
  if [ -L "$directory/exe" ]; then echo 'Kthread: 0'; else echo 'Kthread: 1'; fi > "$directory/status"
 fi
done
vbus_online() {
 if [ -f "$FIXTURE/vbus-trace" ]; then
  head -n 1 "$FIXTURE/vbus-trace"
  sed '1d' "$FIXTURE/vbus-trace" > "$FIXTURE/vbus-next"
  mv "$FIXTURE/vbus-next" "$FIXTURE/vbus-trace"
 else cat "$SYS/class/power_supply/axp20x-usb/online" 2>/dev/null || :; fi
}
vbus_sample_pause() { :; }
unbind_gadget() {
 log_op unbind || return
 if [ "${REPLUG_AT_UNBIND:-0}" = 1 ]; then echo 1 > "$SYS/class/power_supply/axp20x-usb/online"; fi
 printf '\n' > "$gadget/UDC"
}
close_lun() { log_op close-lun || return; printf '\n' > "$lun/file"; }
blockdev() { log_op blockdev "$@"; }
card_flags() { cat "$FIXTURE/volume-flags" 2>/dev/null || echo 0; }
have_card_fsck() { [ "${CARD_FSCK:-absent}" != absent ]; }
card_fsck() { log_op fsck; [ "$CARD_FSCK" = clean ]; }
status_start() { :; }
status_stop() { :; }
if [ "${DEFER_TEST:-0}" = 1 ]; then main "$ACTION" --defer-restart; else main "$ACTION"; fi
'''

class StorageTest(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/usb-storage-tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='case-',dir=base))
        self.card=self.root/'card';self.state=self.root/'state'
        backing=self.card/'.q2-mainline-dev-20261102/state.ext4'
        backing.parent.mkdir(parents=True);backing.write_bytes(b'generated fixture')
        self.state.mkdir()
        self.sys=self.root/'sys';self.proc=self.root/'proc'
        device=self.sys/'devices/platform/13490000.mmc/mmc_host/mmc0/mmc0:0001'
        device.mkdir(parents=True)
        (device/'type').write_text('SD\n');(device/'cid').write_text('0123456789abcdef0123456789abcdef\n')
        for name,dev in [('mmcblk0','179:0'),('mmcblk0p1','179:1')]:
            p=self.sys/'class/block'/name;p.mkdir(parents=True)
            (p/'dev').write_text(dev+'\n');(p/'holders').mkdir()
        (self.sys/'class/block/mmcblk0/device').symlink_to(device)
        p=self.sys/'class/block/loop0/loop';p.mkdir(parents=True)
        (p/'backing_file').write_text(str(backing)+'\n')
        (self.sys/'class/udc/13500000.otg').mkdir(parents=True)
        (self.sys/'class/power_supply/axp20x-usb').mkdir(parents=True)
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('1\n')
        self.gadget=self.sys/'kernel/config/usb_gadget/q2diag'
        (self.gadget/'functions/rndis.usb0').mkdir(parents=True)
        (self.gadget/'configs/c.1').mkdir(parents=True)
        (self.gadget/'configs/c.1/rndis.usb0').symlink_to(self.gadget/'functions/rndis.usb0')
        (self.gadget/'UDC').write_text('13500000.otg\n')
        self.lun=self.gadget/'functions/mass_storage.q2card/lun.0'
        (self.proc/'1').mkdir(parents=True)
        (self.proc/'self').symlink_to('1')
        (self.proc/'mounts').write_text(f'/dev/mmcblk0p1 {self.card} exfat rw 0 0\n/dev/loop0 {self.state} ext4 rw 0 0\n')
        (self.proc/'1/mountinfo').write_text(f'10 1 179:1 / {self.card} rw - exfat /dev/mmcblk0p1 rw\n11 1 7:0 / {self.state} rw - ext4 /dev/loop0 rw\n')
        (self.proc/'swaps').write_text('Filename Type Size Used Priority\n')
        (self.root/'run/q2').mkdir(parents=True)
        (self.root/'run/q2/state-status').write_text('ready\n')
        (self.root/'ops.py').write_text(OPS)
        (self.root/'driver.sh').write_text(DRIVER)
        self.env=dict(os.environ,FIXTURE=str(self.root),HELPER=str(HELPER),PYTHON=shutil.which('python3'))

    def tearDown(self):shutil.rmtree(self.root)

    def command(self,action,success=True,**env):
        result=subprocess.run(['sh',str(self.root/'driver.sh')],env=dict(self.env,ACTION=action,**env),capture_output=True,text=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result.stdout+result.stderr

    def eject(self):self.lun.joinpath('file').write_text('\n')
    def not_exported(self):
        self.assertFalse(self.lun.joinpath('file').exists() and self.lun.joinpath('file').read_text().strip())

    def test_export_and_genuine_eject_restore_in_order(self):
        self.command('start')
        self.assertEqual(self.lun.joinpath('file').read_text().strip(),'/dev/mmcblk0')
        self.assertEqual(self.lun.joinpath('nofua').read_text().strip(),'0')
        self.assertEqual(self.lun.joinpath('removable').read_text().strip(),'1')
        self.assertFalse((self.proc/'mounts').read_text().strip())
        self.assertFalse((self.sys/'class/block/loop0/loop/backing_file').exists())
        events=(self.root/'events').read_text().splitlines()
        self.assertEqual([x.split()[0] for x in events],['sync','umount','detach','umount','blockdev','blockdev'])
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')
        self.command('stop',False)
        self.assertFalse((self.proc/'mounts').read_text().strip())
        self.eject();self.command('stop')
        self.assertIn('/dev/loop0', (self.proc/'mounts').read_text())
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')
        self.assertTrue((self.gadget/'configs/c.1/rndis.usb0').is_symlink())

    def test_numeric_stage_boundaries_follow_real_ownership_and_failed_sync(self):
        self.internal_profile()
        (self.proc/'uptime').write_text('12.34 0.00\n')
        self.command('start')
        output=self.root/'run/q2/usb-stage-times'
        rows=[line.split() for line in output.read_text().splitlines()]
        self.assertTrue(all(row[0]=='12.34' for row in rows))
        names=[row[1] for row in rows]
        ordered=['usb-phase-preparing','usb-player-stop-begin','usb-player-stop-returned',
                 'usb-status-start-begin','usb-status-start-returned','usb-sync-begin','usb-sync-returned',
                 'usb-card-unmount-begin','usb-card-unmount-returned','usb-ownership-first-begin',
                 'usb-ownership-first-returned','usb-phase-detached','usb-gadget-reconfigure-begin',
                 'usb-ownership-second-begin','usb-ownership-second-returned','usb-phase-armed','usb-phase-exported']
        self.assertEqual([name for name in names if name in ordered],ordered)
        self.eject();self.command('stop')
        names=[line.split()[1] for line in output.read_text().splitlines()]
        self.assertIn('usb-gadget-unbind-returned',names)
        self.assertIn('usb-restore-mounts-returned',names)
        output.unlink()
        self.command('start',False,FAIL_OP='sync')
        names=[line.split()[1] for line in output.read_text().splitlines()]
        self.assertIn('usb-sync-begin',names)
        self.assertNotIn('usb-sync-returned',names)
        self.assertNotIn('usb-phase-exported',names)
        self.not_exported()

    def internal_profile(self):
        (self.sys/'class/block/loop0/loop/backing_file').unlink()
        (self.proc/'mounts').write_text(f'/dev/mmcblk0p1 {self.card} exfat rw 0 0\n/dev/ubi0_0 {self.state} ubifs rw 0 0\n')
        (self.proc/'1/mountinfo').write_text(f'10 1 179:1 / {self.card} rw - exfat /dev/mmcblk0p1 rw\n11 1 0:25 /q2-mainline {self.state} rw - ubifs /dev/ubi0_0 rw\n')
        for name, contents in [('class/ubi/ubi0_0/name','user_data'),('class/ubi/ubi0/mtd_num','4'),('class/mtd/mtd4/name','userdata')]:
            p=self.sys/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(contents+'\n')
        script=self.root/'installed-service'
        script.write_text('#!/bin/sh\necho "installed-$1" >> "$FIXTURE/events"\n')
        script.chmod(0o755)

    def test_internal_state_remains_mounted_and_config_restarts_after_eject(self):
        self.internal_profile()
        p=self.proc/'8/fd';p.mkdir(parents=True);(p/'4').symlink_to(self.state/'network/tailscale/tailscaled.state')
        self.command('start')
        self.assertIn('ubifs', (self.proc/'mounts').read_text())
        self.assertNotIn('exfat', (self.proc/'mounts').read_text())
        events=(self.root/'events').read_text().splitlines()
        self.assertEqual([x.strip() for x in events],['installed-stop','sync',f'umount {self.card}',
            'blockdev --flushbufs /dev/mmcblk0p1','blockdev --flushbufs /dev/mmcblk0'])
        self.command('stop',False)
        self.eject();self.command('stop')
        events=(self.root/'events').read_text().splitlines()
        self.assertEqual(events[-1],'installed-start')
        self.assertIn('exfat', (self.proc/'mounts').read_text())
        self.assertIn('ubifs', (self.proc/'mounts').read_text())

    def test_deferred_installed_return_holds_player_without_restarting(self):
        self.internal_profile()
        self.command('start');self.eject()
        text=self.command('stop',DEFER_TEST='1')
        self.assertIn('Player held for setup',text)
        events=(self.root/'events').read_text().splitlines()
        self.assertIn('installed-hold',events)
        self.assertNotIn('installed-start',events)
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')
        self.assertIn('exfat',(self.proc/'mounts').read_text())
        self.assertFalse((self.root/'run/q2/usb-storage').exists())

    def test_internal_profile_rejects_unknown_bind_root_before_player_stop(self):
        self.internal_profile()
        p=self.proc/'1/mountinfo';p.write_text(p.read_text().replace('/q2-mainline','/private'))
        self.assertIn('internal state mount',self.command('start',False))
        self.assertFalse((self.root/'events').exists())

    def test_internal_state_change_during_host_ownership_blocks_restart(self):
        self.internal_profile();self.command('start');self.eject()
        (self.sys/'class/ubi/ubi0_0/name').write_text('unknown\n')
        self.assertIn('internal state volume',self.command('stop',False))
        self.assertNotIn('installed-start',(self.root/'events').read_text())

    def test_own_udc_unbind_is_not_physical_unplug(self):
        self.command('start');(self.gadget/'UDC').write_text('\n')
        self.assertIn('not ejected',self.command('stop',False))
        self.command('retry-export')
        self.assertEqual((self.gadget/'UDC').read_text().strip(),'13500000.otg')

    def test_physical_unplug_quiesces_owned_lun_before_local_mounts(self):
        self.command('start')
        online=self.sys/'class/power_supply/axp20x-usb/online'
        online.write_text('0\n')
        self.command('stop')
        operations=[line.split()[0] for line in (self.root/'events').read_text().splitlines()]
        for earlier,later in [('unbind','close-lun'),('close-lun','remove-lun'),('remove-lun','mount')]:
            self.assertLess(operations.index(earlier),operations.index(later))
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')
        self.assertFalse(self.lun.exists())
        self.assertTrue((self.gadget/'configs/c.1/rndis.usb0').is_symlink())

    def test_disconnect_confirmation_exhausts_three_sample_fault_matrix(self):
        import itertools
        self.command('start')
        for trace in itertools.product(['0','1','','unavailable'],repeat=3):
            if trace==('0','0','0'):continue
            with self.subTest(trace=trace):
                (self.root/'vbus-trace').write_text('\n'.join(trace)+'\n')
                self.command('stop',False)
                self.assertEqual((self.lun/'file').read_text().strip(),'/dev/mmcblk0')
                self.assertFalse((self.proc/'mounts').read_text().strip())
                self.assertEqual((self.gadget/'UDC').read_text().strip(),'13500000.otg')
        (self.root/'vbus-trace').write_text('0\n0\n0\n')
        self.command('stop')

    def test_reconnect_after_confirmed_loss_cannot_race_local_mount(self):
        self.internal_profile();self.command('start')
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('0\n')
        self.command('stop',REPLUG_AT_UNBIND='1')
        self.assertFalse(self.lun.exists())
        self.assertEqual((self.sys/'class/power_supply/axp20x-usb/online').read_text().strip(),'1')
        self.assertIn('installed-start',(self.root/'events').read_text())

    def test_unplug_return_faults_preserve_exclusive_ownership_and_allow_retry(self):
        self.command('start')
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('0\n')
        for failure in ['unbind','close-lun','sync','remove-lun','mount']:
            with self.subTest(failure=failure):
                self.command('stop',False,FAIL_OP=failure)
                self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')
                self.assertNotIn(' exfat ',(self.proc/'mounts').read_text())
        self.command('stop')
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')

    def events(self):return (self.root/'events').read_text().splitlines()
    def card_mount(self):return next(line for line in self.events() if line.startswith('mount -t exfat'))

    def test_return_flushes_both_card_caches_after_the_lun_is_gone_and_before_mount(self):
        self.command('start');self.eject();(self.root/'events').write_text('')
        self.command('stop')
        events=self.events();ops=[line.split()[0] for line in events]
        flushes=[i for i,line in enumerate(events) if line.startswith('blockdev --flushbufs')]
        self.assertEqual([events[i] for i in flushes],['blockdev --flushbufs /dev/mmcblk0p1','blockdev --flushbufs /dev/mmcblk0'])
        self.assertLess(ops.index('remove-lun'),flushes[0])
        self.assertLess(flushes[-1],ops.index('mount'))
        self.assertEqual(self.card_mount().split()[4],'nosuid,nodev,noexec')
        self.assertNotIn('fsck',ops,'a clean volume is not checked')

    def test_failed_cache_flush_keeps_the_card_detached(self):
        self.command('start');self.eject()
        self.assertIn('caches could not be flushed',self.command('stop',False,FAIL_OP='blockdev'))
        self.assertNotIn(' exfat ',(self.proc/'mounts').read_text())
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')
        self.assertFalse((self.proc/'mounts').read_text().strip())

    def test_export_refuses_when_the_card_caches_cannot_be_flushed(self):
        self.command('start',False,FAIL_OP='blockdev')
        self.not_exported()

    def test_dirty_card_failing_its_check_returns_read_only(self):
        self.internal_profile();self.command('start');self.eject()
        (self.root/'volume-flags').write_text('2\n')
        text=self.command('stop',CARD_FSCK='errors')
        self.assertIn('mounted read-only',text)
        self.assertEqual(self.card_mount().split()[4],'ro,nosuid,nodev,noexec')
        self.assertTrue((self.root/'run/q2/card-readonly').exists())
        self.assertIn('fsck',[line.split()[0] for line in self.events()])
        self.assertEqual(self.events()[-1],'installed-start')

    def test_dirty_card_passing_its_check_or_without_a_checker_returns_writable(self):
        for checker in ['clean','absent']:
            with self.subTest(checker=checker):
                (self.root/'events').write_text('')
                self.command('start');self.eject()
                (self.root/'volume-flags').write_text('3\n')
                (self.root/'run/q2/card-readonly').write_text('')
                self.command('stop',CARD_FSCK=checker)
                self.assertEqual(self.card_mount().split()[4],'nosuid,nodev,noexec')
                self.assertFalse((self.root/'run/q2/card-readonly').exists(),'an earlier read-only marker is cleared')
                self.assertEqual('fsck' in [line.split()[0] for line in self.events()],checker=='clean')

    def policy_step(self):
        (self.proc/'cmdline').write_text('q2.usb-auto=off\n')
        (self.root/'run/q2/usb-auto.inhibit').touch()
        manager=ROOT/'os/platform/usb-auto'
        script=f'Q2_USB_AUTO_SOURCE_ONLY=1\n. "{manager}"\n'+'''
RUN=$FIXTURE/run/q2
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
storage() { ACTION="$1" sh "$FIXTURE/driver.sh"; }
STORAGE=storage
step
'''
        result=subprocess.run(['sh','-c',script],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_actual_policy_and_storage_return_manual_unplug_when_autostart_disabled(self):
        self.command('start')
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('0\n')
        self.policy_step()
        self.assertFalse((self.root/'run/q2/usb-storage').exists())
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')
        self.assertTrue((self.root/'run/q2/usb-auto.inhibit').exists())
        self.policy_step()
        self.assertFalse((self.root/'run/q2/usb-storage').exists())

    def test_transient_reconnect_between_probe_and_return_does_not_latch_failure(self):
        self.command('start')
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('0\n')
        (self.root/'vbus-trace').write_text('0\n0\n0\n0\n1\n')
        self.policy_step()
        self.assertFalse((self.root/'run/q2/usb-auto.return-attempted').exists())
        self.assertEqual((self.lun/'file').read_text().strip(),'/dev/mmcblk0')
        self.assertFalse((self.proc/'mounts').read_text().strip())
        (self.root/'vbus-trace').write_text('0\n'*6)
        self.policy_step()
        self.assertFalse((self.root/'run/q2/usb-storage').exists())

    def test_foreign_lun_and_missing_vbus_never_clear_backing(self):
        self.command('start')
        (self.lun/'file').write_text('/dev/foreign\n')
        (self.sys/'class/power_supply/axp20x-usb/online').write_text('0\n')
        self.assertIn('backing changed',self.command('stop',False))
        self.assertEqual((self.lun/'file').read_text().strip(),'/dev/foreign')
        (self.lun/'file').write_text('/dev/mmcblk0\n')
        (self.sys/'class/power_supply/axp20x-usb/online').unlink()
        self.command('stop',False)
        self.assertEqual((self.lun/'file').read_text().strip(),'/dev/mmcblk0')

    def test_busy_unmount_never_exports_and_can_restore(self):
        self.command('start',False,FAIL_OP='umount');self.not_exported()
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')
        self.command('stop')
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'ready')

    def test_loop_detach_failure_never_exports(self):
        self.command('start',False,FAIL_OP='detach');self.not_exported()
        self.assertIn('/dev/mmcblk0p1',(self.proc/'mounts').read_text())
        self.command('stop')
        self.assertIn('/dev/loop0',(self.proc/'mounts').read_text())

    def test_autoclear_detach_after_unmount_is_already_safe(self):
        self.command('start',AUTO_CLEAR='1')
        self.assertNotIn('detach', (self.root/'events').read_text())
        self.assertEqual(self.lun.joinpath('file').read_text().strip(),'/dev/mmcblk0')
        self.eject();self.command('stop')

    def test_foreign_namespace_mount_blocks_export(self):
        p=self.proc/'2';p.mkdir();(p/'mountinfo').write_text('20 1 179:1 / /other rw - exfat /dev/mmcblk0p1 rw\n')
        self.assertIn('namespace',self.command('start',False));self.not_exported()

    def test_mountinfo_exit_one_is_not_positive_mount_evidence(self):
        p=self.proc/'8';p.mkdir();(p/'mountinfo').write_text('')
        (p/'exe').symlink_to('/usr/bin/generated-process')
        (p/'status').write_text('Kthread: 0\nState:\tS (sleeping)\n')
        output=self.command('start',False,RACE_KIND='mount-active')
        self.assertIn('cannot inspect active mount namespace',output)
        self.assertNotIn('card remains mounted',output)
        self.not_exported()

    def test_vanished_mountinfo_and_dead_process_have_no_remaining_ownership(self):
        for kind in ['mount-gone','mount-zombie']:
            with self.subTest(kind=kind):
                p=self.proc/'8';p.mkdir();(p/'mountinfo').write_text('')
                (p/'exe').symlink_to('/usr/bin/generated-process')
                (p/'status').write_text('Kthread: 0\nState:\tZ (zombie)\n' if kind=='mount-zombie' else 'Kthread: 0\nState:\tS (sleeping)\n')
                self.command('start',RACE_KIND=kind)
                self.assertEqual((self.lun/'file').read_text().strip(),'/dev/mmcblk0')
                self.eject();self.command('stop',RACE_KIND=kind)
                if p.exists():shutil.rmtree(p)

    def test_additional_loop_blocks_export(self):
        p=self.sys/'class/block/loop1/loop';p.mkdir(parents=True)
        (p/'backing_file').write_text(str(self.card/'another.img')+'\n')
        self.assertIn('loop remains',self.command('start',False));self.not_exported()

    def test_swap_and_block_holders_block_export(self):
        (self.proc/'swaps').write_text('Filename Type Size Used Priority\n/dev/mmcblk0p2 partition 1 0 -2\n')
        self.assertIn('swap',self.command('start',False));self.not_exported()

    def test_block_holder_blocks_export(self):
        (self.sys/'class/block/mmcblk0p1/holders/dm-0').mkdir()
        self.assertIn('block-layer holder',self.command('start',False));self.not_exported()

    def test_unknown_state_layout_refused_before_stop(self):
        (self.sys/'class/block/loop0/loop/backing_file').write_text('/some/other/state.ext4\n')
        self.assertIn('unknown state',self.command('start',False));self.not_exported()
        self.assertFalse((self.root/'events').exists())

    def test_state_mount_failure_keeps_player_blocked(self):
        self.command('start');self.eject()
        self.command('stop',False,FAIL_OP='mount')
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')
        self.not_exported()
        self.command('stop')

    def test_open_card_descriptor_blocks_export(self):
        p=self.proc/'2/fd';p.mkdir(parents=True);(p/'4').symlink_to('/dev/mmcblk0')
        self.assertIn('descriptor',self.command('start',False));self.not_exported()

    def test_unknown_slot_or_layout_refused_before_stop(self):
        p=self.sys/'class/block/mmcblk0/device';p.unlink();p.symlink_to(self.sys/'wrong-controller')
        self.command('start',False);self.not_exported()
        self.assertFalse((self.root/'events').exists())

    def test_changed_card_refused_on_return(self):
        self.command('start');self.eject()
        (self.sys/'class/block/mmcblk0/device/cid').write_text('f'*32+'\n')
        self.assertIn('card changed',self.command('stop',False))
        self.assertFalse((self.proc/'mounts').read_text().strip())

    def test_missing_state_image_never_formats_or_starts_player(self):
        self.command('start');self.eject()
        (self.card/'.q2-mainline-dev-20261102/state.ext4').unlink()
        self.assertIn('state image',self.command('stop',False))
        self.assertNotIn('/dev/loop0',(self.proc/'mounts').read_text())
        self.assertEqual((self.root/'run/q2/state-status').read_text().strip(),'usb-storage')

    def test_second_start_and_lock_fail_closed(self):
        self.command('start');self.command('start',False)
        lock=self.root/'run/q2/usb-storage.lock';lock.mkdir()
        self.assertIn('lock',self.command('stop',False))

    def test_player_process_without_pid_marker_blocks_export(self):
        p=self.proc/'8';p.mkdir();(p/'exe').symlink_to('/run/q2nd-test')
        self.assertIn('player process remains',self.command('start',False));self.not_exported()

    def test_managed_service_stops_before_detach_and_restarts_after_mount(self):
        run=self.root/'run/q2nd';run.mkdir();(run/'launcher.pid').write_text('99\n')
        services=self.root/'services';services.mkdir()
        script=services/'S80q2-player'
        script.write_text('#!/bin/sh\necho "service-$1" >> "$FIXTURE/events"\n')
        script.chmod(0o755)
        self.command('start');self.eject();self.command('stop')
        events=(self.root/'events').read_text().splitlines()
        self.assertEqual(events[0],'service-stop')
        self.assertEqual(events[-1],'service-start')
        self.assertTrue(events[-2].startswith('mount '))

    def test_failed_service_stop_never_unmounts_or_exports(self):
        run=self.root/'run/q2nd';run.mkdir();(run/'supervisor.pid').write_text('99\n')
        services=self.root/'services';services.mkdir()
        script=services/'S80q2-player';script.write_text('#!/bin/sh\nexit 1\n');script.chmod(0o755)
        self.assertIn('stop failed',self.command('start',False))
        self.assertFalse((self.root/'events').exists());self.not_exported()

    def process(self,status):
        p=self.proc/'8';p.mkdir();(p/'exe').symlink_to('/bin/generated-process')
        (p/'status').write_text(status)
        return p

    def test_kernel_thread_unreadable_exe_does_not_block_export(self):
        self.process('State:\tS (sleeping)\nKthread:\t1\n')
        self.command('start',RACE_KIND='kernel')

    def test_zombie_and_disappearing_process_are_not_active_owners(self):
        self.process('State:\tZ (zombie)\nKthread:\t0\n')
        self.command('start',RACE_KIND='active')
        self.eject();self.command('stop')
        (self.proc/'8/status').write_text('State:\tS (sleeping)\nKthread:\t0\n')
        self.command('start',RACE_KIND='process')

    def test_active_unreadable_process_still_blocks_export(self):
        self.process('State:\tS (sleeping)\nKthread:\t0\n')
        self.assertIn('active process executable',self.command('start',False,RACE_KIND='active'))
        self.not_exported()

    def test_fd_disappearing_during_read_is_distinguished_from_active_failure(self):
        p=self.process('State:\tS (sleeping)\nKthread:\t0\n')
        (p/'fd').mkdir();(p/'fd/4').symlink_to('/dev/mmcblk0')
        self.assertIn('active open descriptor',self.command('start',False,RACE_KIND='active-fd'))
        self.not_exported();self.command('stop')
        self.command('start',RACE_KIND='fd')

    def test_closed_card_fd_reused_for_noncard_target_is_reinspected(self):
        p=self.process('State:\tS (sleeping)\nKthread:\t0\n')
        (p/'fd').mkdir();(p/'fd/4').symlink_to('/dev/mmcblk0')
        self.command('start',RACE_KIND='reused-safe')
        self.assertTrue((self.root/'fd-reused').exists())

    def test_fd_reused_for_card_target_still_blocks_export(self):
        p=self.process('State:\tS (sleeping)\nKthread:\t0\n')
        (p/'fd').mkdir();(p/'fd/4').symlink_to('/dev/null')
        output=self.command('start',False,RACE_KIND='reused-card')
        self.assertIn('card still has an open descriptor',output)
        self.not_exported()

if __name__=='__main__':unittest.main()
