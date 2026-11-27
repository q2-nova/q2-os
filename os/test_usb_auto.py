"""Automatic USB ownership policy with generated sysfs and fake storage commands."""
import os
from pathlib import Path
import shutil
import subprocess
import select
import signal
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
MANAGER=ROOT/'os/platform/usb-auto'
class UsbAutoTest(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/usb-auto-tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='case-',dir=base));self.run=self.root/'run';self.run.mkdir()
        self.sys=self.root/'sys';self.online=self.sys/'class/power_supply/axp20x-usb/online';self.online.parent.mkdir(parents=True);self.online.write_text('1\n')
        self.state=self.sys/'class/udc/q2/state';self.state.parent.mkdir(parents=True);self.state.write_text('configured\n')
        (self.root/'proc').mkdir();(self.root/'proc/cmdline').write_text('q2.hardware=1\n')
        self.gadget=self.sys/'kernel/config/usb_gadget/q2diag'
        self.lun=self.gadget/'functions/mass_storage.q2card/lun.0/file';self.lun.parent.mkdir(parents=True)
        self.events=self.root/'events'
    def tearDown(self):shutil.rmtree(self.root)
    def session(self,backing='/dev/mmcblk0',phase='exported'):
        directory=self.run/'usb-storage';directory.mkdir(exist_ok=True)
        (directory/'phase').write_text(phase+'\n');(directory/'gadget').write_text(str(self.gadget)+'\n');self.lun.write_text(backing)
    def step(self,fail='',rebind=False):
        script=f'Q2_USB_AUTO_SOURCE_ONLY=1\n. "{MANAGER}"\n'+'''
RUN=$FIXTURE/run
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
storage() {
 if [ "$1" = disconnected ]; then
  [ "$(cat "$SYS/class/power_supply/axp20x-usb/online" 2>/dev/null)" = 0 ]; return
 fi
 echo "$1" >> "$FIXTURE/events"
 [ "$FAIL" != "$1" ] || return 1
 if [ "$1" = start ]; then
  mkdir -p "$RUN/usb-storage"
  echo exported > "$RUN/usb-storage/phase"
  echo "$SYS/kernel/config/usb_gadget/q2diag" > "$RUN/usb-storage/gadget"
  echo /dev/mmcblk0 > "$SYS/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card/lun.0/file"
  if [ "$REBIND" = 1 ]; then echo 'not attached' > "$SYS/class/udc/q2/state"; fi
 else
  rm -r "$RUN/usb-storage"
 fi
}
STORAGE=storage
step
'''
        result=subprocess.run(['sh','-c',script],env=dict(os.environ,FIXTURE=str(self.root),FAIL=fail,REBIND=str(int(rebind))),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        return self.events.read_text().splitlines() if self.events.exists() else []
    def test_normal_host_attach_exports_once(self):
        self.assertEqual(self.step(),['start']);self.assertEqual(self.step(),['start'])
    def test_charger_suspend_and_unconfigured_states_do_not_stop_player(self):
        for state in ['not attached','attached','powered','default','addressed','suspended']:
            self.state.write_text(state+'\n');self.assertEqual(self.step(),[])
    def test_missing_or_off_vbus_prevents_export(self):
        self.online.unlink();self.assertEqual(self.step(),[])
        self.online.write_text('0\n');self.assertEqual(self.step(),[])
    def test_own_rebind_does_not_start_another_session(self):
        self.assertEqual(self.step(rebind=True),['start']);self.assertEqual(self.step(),['start'])
        self.state.write_text('configured\n');self.assertEqual(self.step(),['start'])
    def test_eject_returns_player_but_does_not_reexport_until_replug(self):
        self.step();self.lun.write_text('')
        self.assertEqual(self.step(),['start','stop']);self.assertEqual(self.step(),['start','stop'])
        self.online.write_text('0\n');self.step();self.online.write_text('1\n')
        self.assertEqual(self.step(),['start','stop','start'])
    def test_eject_then_immediate_unplug_still_returns(self):
        self.step();self.lun.write_text('');self.online.write_text('0\n')
        self.assertEqual(self.step(),['start','stop'])
    def test_confirmed_unplug_returns_without_eject_then_permits_new_attach(self):
        self.step();self.online.write_text('0\n');self.assertEqual(self.step(),['start','stop'])
        self.online.write_text('1\n');self.assertEqual(self.step(),['start','stop','start'])
    def test_busy_storage_waits_without_consuming_attach(self):
        (self.run/'usb-storage.lock').mkdir();self.assertEqual(self.step(),[])
        (self.run/'usb-storage.lock').rmdir();self.assertEqual(self.step(),['start'])
    def test_start_failure_is_one_attempt_per_attach(self):
        self.assertEqual(self.step(fail='start'),['start']);self.assertEqual(self.step(),['start'])
        self.online.write_text('0\n');self.step();self.online.write_text('1\n');self.assertEqual(self.step(),['start','start'])
    def test_return_failure_does_not_loop_or_force_backing(self):
        self.session('');self.assertEqual(self.step(fail='stop'),['stop']);self.assertEqual(self.step(),['stop'])
        self.assertEqual(self.lun.read_text(),'')
    def test_manual_session_is_consumed_even_while_inhibited(self):
        (self.run/'usb-auto.inhibit').touch();self.session();self.assertEqual(self.step(),[])
        shutil.rmtree(self.run/'usb-storage');(self.run/'usb-auto.inhibit').unlink()
        self.assertEqual(self.step(),[])
    def test_development_boot_override_and_inhibit_disable_new_exports_only(self):
        (self.root/'proc/cmdline').write_text('q2.usb-auto=off\n');self.assertEqual(self.step(),[])
        (self.root/'proc/cmdline').write_text('');(self.run/'usb-auto.inhibit').touch();self.assertEqual(self.step(),[])
        self.session('');self.assertEqual(self.step(),['stop'])
        self.assertEqual(self.step(),['stop'], 'disabled autostart must not export after manual return')

    def test_manual_eject_return_with_disabled_autostart_generated_state_matrix(self):
        # Deterministic policy simulation: no real devices or host configuration.
        for disabled in ['boot', 'inhibit', 'both']:
            (self.root/'proc/cmdline').write_text('q2.usb-auto=off\n' if disabled in ['boot', 'both'] else '')
            (self.run/'usb-auto.inhibit').unlink(missing_ok=True)
            if disabled in ['inhibit', 'both']:(self.run/'usb-auto.inhibit').touch()
            for phase in ['armed', 'exported']:
                for online in ['0\n', '1\n', None]:
                    for backing in ['/dev/mmcblk0', '', None]:
                        with self.subTest(disabled=disabled, phase=phase, online=online, backing=backing):
                            shutil.rmtree(self.run/'usb-storage',ignore_errors=True)
                            (self.run/'usb-auto.return-attempted').unlink(missing_ok=True)
                            self.events.unlink(missing_ok=True)
                            if online is None:self.online.unlink(missing_ok=True)
                            else:self.online.write_text(online)
                            self.session(backing or '',phase)
                            if backing is None:self.lun.unlink()
                            expected=['stop'] if backing == '' or (online == '0\n' and backing == '/dev/mmcblk0') else []
                            self.assertEqual(self.step(),expected)
                            self.assertEqual(self.step(),expected, 'no retry/reexport after one return')
                            if backing is not None:self.assertEqual(self.lun.read_text(),backing)

    def test_disabled_autostart_manual_return_remains_fail_closed_and_bounded(self):
        (self.root/'proc/cmdline').write_text('q2.usb-auto=off\n')
        (self.run/'usb-auto.inhibit').touch()
        self.session('/dev/mmcblk0')
        self.online.write_text('0\n')
        self.online.write_text('1\n')
        self.state.write_text('not attached\n')
        self.assertEqual(self.step(),[], 'UDC disconnect is not physical unplug')
        self.assertEqual(self.lun.read_text(),'/dev/mmcblk0')
        self.lun.write_text('')
        (self.run/'usb-storage.lock').mkdir()
        self.assertEqual(self.step(),[], 'ownership helper already active')
        (self.run/'usb-storage.lock').rmdir()
        (self.run/'usb-storage/gadget').write_text('/foreign/gadget\n')
        self.assertEqual(self.step(),[], 'never guess a foreign gadget')
        (self.run/'usb-storage/gadget').write_text(str(self.gadget)+'\n')
        self.assertEqual(self.step(fail='stop'),['stop'])
        self.assertEqual(self.step(),['stop'], 'failed return needs explicit recovery')
        self.assertTrue((self.run/'usb-storage').exists())
        self.assertEqual(self.lun.read_text(),'')

    def test_deferred_return_and_unknown_gadget_are_not_guessed(self):
        self.session('',phase='local-ready');self.assertEqual(self.step(),[])
        (self.run/'usb-storage/phase').write_text('exported\n')
        (self.run/'usb-storage/gadget').write_text('/unowned/gadget\n');self.assertEqual(self.step(),[])

    def test_manager_pid_requires_exact_shell_script_action_argv(self):
        with subprocess.Popen(['sleep','20']) as child:
            directory=self.root/'proc'/str(child.pid);directory.mkdir()
            (directory/'status').write_text('State:\tS (sleeping)\n')
            (self.run/'usb-auto.pid').write_text(str(child.pid)+'\n')
            for argv,accepted in [(['/bin/sh','/usr/sbin/q2-usb-auto','run'],True),
                                  (['foreign','/usr/sbin/q2-usb-auto run'],False),
                                  (['/bin/sh','/usr/sbin/q2-usb-auto','run','extra'],False),
                                  (['/bin/sh','/usr/sbin/q2-usb-auto-other','run'],False)]:
                (directory/'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv)+b'\0')
                script=f'Q2_USB_AUTO_SOURCE_ONLY=1\n. "{MANAGER}"\nRUN="$FIXTURE/run"\nPROC="$FIXTURE/proc"\nmanager_owned\n'
                result=subprocess.run(['sh','-c',script],env=dict(os.environ,FIXTURE=str(self.root)),capture_output=True)
                self.assertEqual(result.returncode==0,accepted)
            child.terminate()

    def test_manager_stop_reaps_event_child_and_removes_fifo(self):
        helper=self.root/'event-helper'
        helper.write_text('#!/bin/sh\ntrap "" USR1\necho $$ > "$FIXTURE/helper.pid"\necho ready\nexec sleep 60\n');helper.chmod(0o755)
        driver=self.root/'manager'
        # Source policy disables new exports; it must still start its persistent
        # event child so a later menu-created session can be returned.
        (self.root/'proc/cmdline').write_text('q2.usb-auto=off\n')
        (self.run/'usb-auto.inhibit').touch()
        driver.write_text(f'#!/bin/sh\nQ2_USB_AUTO_SOURCE_ONLY=1\n. "{MANAGER}"\nRUN="$FIXTURE/run"\nSYS="$FIXTURE/sys"\ncase "$1" in run) PROC="$FIXTURE/proc" ;; *) PROC=/proc ;; esac\nSELF="$FIXTURE/manager"\nEVENTS="$FIXTURE/event-helper"\nLOG="$FIXTURE/log"\nSTORAGE=true\nid() {{ echo 0; }}\nmain "$@"\n');driver.chmod(0o755)
        env=dict(os.environ,FIXTURE=str(self.root))
        subprocess.run([str(driver),'start'],env=env,check=True,timeout=5)
        try:
            helper_pid=int((self.root/'helper.pid').read_text())
            subprocess.run([str(driver),'stop'],env=env,check=True,timeout=5)
            self.assertFalse((self.run/'usb-auto.lock').exists())
            self.assertFalse((self.run/'usb-auto.pid').exists())
            with self.assertRaises(ProcessLookupError):os.kill(helper_pid,0)
        finally:
            subprocess.run([str(driver),'stop'],env=env,timeout=5)

    def test_running_disabled_manager_returns_a_later_manual_session_on_eject(self):
        # Exercise the actual persistent manager, not just isolated step calls.
        # A generated watcher pulse synchronizes a manual export/eject after
        # startup; neither off nor inhibit may prevent watching this session.
        (self.root/'proc/cmdline').write_text('q2.usb-auto=off\n')
        (self.run/'usb-auto.inhibit').touch()
        pulses=self.root/'pulses';os.mkfifo(pulses)
        completed=self.root/'completed';os.mkfifo(completed)
        reader=os.open(completed,os.O_RDWR|os.O_NONBLOCK)
        helper=self.root/'event-helper'
        helper.write_text('#!/bin/sh\ntrap "" USR1\necho ready\nexec cat "$FIXTURE/pulses"\n');helper.chmod(0o755)
        storage=self.root/'storage'
        storage.write_text('#!/bin/sh\n[ "$1" = stop ] || exit 9\n[ ! -s "$FIXTURE/sys/kernel/config/usb_gadget/q2diag/functions/mass_storage.q2card/lun.0/file" ] || exit 10\nrm -r "$FIXTURE/run/usb-storage"\nprintf "stop\\n" > "$FIXTURE/completed"\n');storage.chmod(0o755)
        driver=self.root/'manager'
        driver.write_text(f'#!/bin/sh\nQ2_USB_AUTO_SOURCE_ONLY=1\n. "{MANAGER}"\nRUN="$FIXTURE/run"\nSYS="$FIXTURE/sys"\ncase "$1" in run) PROC="$FIXTURE/proc" ;; *) PROC=/proc ;; esac\nSELF="$FIXTURE/manager"\nEVENTS="$FIXTURE/event-helper"\nLOG="$FIXTURE/log"\nSTORAGE="$FIXTURE/storage"\nid() {{ echo 0; }}\nmain "$@"\n');driver.chmod(0o755)
        env=dict(os.environ,FIXTURE=str(self.root))
        subprocess.run([str(driver),'start'],env=env,check=True,timeout=5)
        try:
            self.session('')
            writer=os.open(pulses,os.O_RDWR|os.O_NONBLOCK)
            os.write(writer,b'change\n')
            self.assertTrue(select.select([reader],[],[],3)[0], 'disabled manager never returned the ejected manual card')
            self.assertEqual(os.read(reader,64),b'stop\n')
            self.assertFalse((self.run/'usb-storage').exists())
            self.assertEqual(self.lun.read_text(),'')
            self.assertTrue((self.run/'usb-auto.inhibit').exists())
        finally:
            os.close(reader)
            subprocess.run([str(driver),'stop'],env=env,timeout=5)
            if 'writer' in locals():os.close(writer)

class UsbAutoEventsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary=ROOT/'build/usb-auto/q2-usb-auto-events-test';cls.binary.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['cc','-DUSB_AUTO_TEST','-Wall','-Wextra','-Werror','-O2',str(ROOT/'os/platform/usb-auto-events.c'),'-o',str(cls.binary)],check=True)
    def setUp(self):
        base=ROOT/'build/usb-auto-tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='events-',dir=base))
        self.run=self.root/'run';self.run.mkdir();self.sys=self.root/'sys';self.sys.mkdir()
        self.events=self.root/'events';os.mkfifo(self.events)
        self.child=subprocess.Popen([str(self.binary),str(self.sys),str(self.run),str(self.events),'700','60'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(self.pulse(2),b'ready\n')
    def tearDown(self):
        self.child.terminate();self.child.communicate(timeout=2);shutil.rmtree(self.root)
    def pulse(self,timeout):
        return os.read(self.child.stdout.fileno(),4096) if select.select([self.child.stdout],[],[],timeout)[0] else b''
    def event(self,payload):
        fd=os.open(self.events,os.O_WRONLY);os.write(fd,payload);os.close(fd)
    def test_idle_sleeps_and_only_relevant_complete_events_wake_policy(self):
        self.assertEqual(self.pulse(.15),b'')
        self.event(b'change@/generated\0SUBSYSTEM=net\0');self.assertEqual(self.pulse(.15),b'')
        self.event(b'change@/generated\0SUBSYSTEM=udc\0');self.assertEqual(self.pulse(.2),b'change\n')
        self.event(b'SUBSYSTEM=power_supply');self.assertEqual(self.pulse(.15),b'')
        self.event(b'SUBSYSTEM=power_supply\0');self.assertEqual(self.pulse(.2),b'change\n')
    def test_active_export_has_bounded_eject_recheck_then_returns_to_idle(self):
        directory=self.run/'usb-storage';directory.mkdir();(directory/'phase').write_text('exported\n')
        self.child.send_signal(signal.SIGUSR1)
        self.assertEqual(self.pulse(.3),b'change\n')
        self.assertEqual(self.pulse(.3),b'change\n')
        (directory/'phase').write_text('local-ready\n')
        self.pulse(.3) # one interval may already have been armed before phase update
        self.assertEqual(self.pulse(.15),b'')
        self.assertEqual(self.pulse(1),b'change\n')

if __name__=='__main__':unittest.main()
