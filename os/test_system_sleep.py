"""Generated public-state fixtures for installed suspend ordering and unwind."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/sbin/q2-system-sleep'
SUPERVISOR = ROOT / 'os/overlay/etc/init.d/S79q2-supervisor'


def proof(**changes):
    values=dict(attempts=3,core_returns=2,core_failures=1,valid=0,stage=0,error=0,
                entry_ddrc='0x00000000',entry_phy='0x00000000',saved_status='0x14001c00',
                resume_status='0x14001c00',rtc_enter=100,rtc_exit=105,roundtrip=0,
                roundtrip_returns=0,roundtrip_failures=0,cpu_only=0,cpu_only_returns=0,
                cpu_only_failures=0,entry_opcr='0x40f01514',entry_lcr='0xff001f01',
                saved_epc='0xc025303c',epc_restore=0)
    values.update(changes)
    return ' '.join(f'{k}={v}' for k,v in values.items())


def returned(**changes):
    return proof(**dict(dict(attempts=4,core_returns=3,valid=1,stage=7,
                            entry_ddrc='0x00000104',entry_phy='0xa0000002'),**changes))


class SleepTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/sleep-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=base))
        for name in ('run', 'app', 'network', 'ctrl', 'sys/power/suspend_stats',
                     'sys/class/power_supply/axp20x-battery', 'proc/sys/kernel/random', 'sys/kernel/q2_suspend'):
            (self.root / name).mkdir(parents=True)
        for path, value in {'state-status': 'ready', 'app/player.pid': '123', 'app/ready': '123',
                            'sys/power/state': 'freeze mem', 'sys/power/wakeup_count': '10',
                            'sys/power/suspend_stats/success': '4', 'sys/power/suspend_stats/fail': '0',
                            'sys/power/suspend_stats/failed_resume': '0',
                            'sys/power/suspend_stats/failed_resume_early': '0',
                            'sys/power/suspend_stats/failed_resume_noirq': '0',
                            'sys/class/power_supply/axp20x-battery/charge_behaviour': 'auto [inhibit-charge]',
                            'proc/sys/kernel/random/boot_id': 'generated-boot',
                            'sys/kernel/q2_suspend/entry_policy': 'version=1 normal_retained=1',
                            'sys/kernel/q2_suspend/proof': proof()}.items():
            (self.root / path).write_text(value + '\n')
        supervisor = self.root / 'supervisor'
        supervisor.write_text("""#!/bin/sh
echo "supervisor $1" >> "$FIXTURE/events"
drm_resume="$FIXTURE/sys/class/drm/card0/device/q2_async_resume"
if [ "$1" = start ] && [ -f "$drm_resume" ] && [ "$(cat "$drm_resume")" = 1 ]; then
    echo "display joined after supervisor" >> "$FIXTURE/events"
    if [ "$FAIL" = display ]; then
        echo error > "$drm_resume"
    else
        echo ready > "$drm_resume"
    fi
fi
[ "$FAIL" != "$1" ]
""")
        supervisor.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_helper(self, fault='', proof_after=None, retained_drm=False, notice=False):
        if proof_after is None: proof_after=returned()
        script = f'Q2_SLEEP_SOURCE_ONLY=1\n. "{HELPER}"\n' + '''
RUN=$FIXTURE/run
APP=$FIXTURE/app
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
STATE_STATUS=$FIXTURE/state-status
SUPERVISOR=$FIXTURE/supervisor
NETWORK=$FIXTURE/network
CTRL=$FIXTURE/ctrl
id() { echo 0; }
sync() { echo sync >> "$FIXTURE/events"; [ "$FAIL" != sync ]; }
wpa_command() {
    echo "wpa $1" >> "$FIXTURE/events"
    case "$FAIL:$1" in
        notify-suspend:suspend|notify-resume:resume) echo FAIL; return 1 ;;
        notify-output:suspend) echo 'OK extra'; return 0 ;;
        replace-suspend:suspend|replace-resume:resume)
            echo '1 (wpa_supplicant) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 9999' > "$PROC/$(cat "$NETWORK/wpa.pid")/stat" ;;
    esac
    echo OK
}
kernel_suspend() {
    echo mem >> "$FIXTURE/events"
    if [ -f "$drm_resume" ] && [ "$(cat "$drm_resume")" = 1 ]; then
        echo armed >> "$FIXTURE/events"
    fi
    [ "$FAIL" != mem ] || return 1
    [ "$FAIL" = no-cycle ] || echo 5 > "$SYS/power/suspend_stats/success"
    case "$FAIL" in
        resume-fail) echo 1 > "$SYS/power/suspend_stats/fail" ;;
        resume-missing) rm "$SYS/power/suspend_stats/failed_resume" ;;
        resume-extra-line) printf '0\n1\n' > "$SYS/power/suspend_stats/fail" ;;
    esac
    case "$FAIL" in failed_resume*) echo 1 > "$SYS/power/suspend_stats/$FAIL" ;; esac
    [ "$FAIL" != reboot ] || echo other-boot > "$PROC/sys/kernel/random/boot_id"
    [ "$FAIL" != charge ] || echo '[auto] inhibit-charge' > "$SYS/class/power_supply/axp20x-battery/charge_behaviour"
    [ "$FAIL" != storage ] || echo exported > "$RUN/usb-storage/phase"
    if [ -f "$SYS/kernel/q2_suspend/proof" ]; then printf '%s\n' "$PROOF_AFTER" > "$SYS/kernel/q2_suspend/proof"; fi
    return 0
}
main
'''
        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                                env=dict(os.environ, FIXTURE=str(self.root), FAIL=fault, PROOF_AFTER=proof_after,
                                         Q2_RETAIN_DRM_FOR_PM=str(int(retained_drm)),
                                         Q2_NOTIFY_KERNEL_RETURN=str(int(notice))), timeout=5)
        events = self.root / 'events'
        return result, events.read_text().splitlines() if events.exists() else []

    def test_success_orders_sync_kernel_and_supervisor_restart_preserving_inhibit(self):
        result, events = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['supervisor stop', 'sync', 'mem', 'supervisor start'])
        self.assertEqual((self.root / 'run/sleep-status').read_text(), 'resumed\n')
        self.assertFalse((self.root / 'run/usb-storage.lock').exists())
        self.assertFalse((self.root / 'app/control.lock').exists())

    def test_retained_drm_joins_after_supervision_and_disarms_on_errors(self):
        for fault in ('', 'display', 'mem', 'start'):
            with self.subTest(fault=fault):
                self.tearDown()
                self.setUp()
                drm_resume = self.root / 'sys/class/drm/card0/device/q2_async_resume'
                drm_resume.parent.mkdir(parents=True)
                drm_resume.write_text('ready\n')
                result, events = self.run_helper(fault, retained_drm=True, notice=True)
                self.assertEqual(events, ['supervisor stop', 'sync', 'mem', 'armed',
                                          'supervisor start', 'display joined after supervisor'])
                self.assertEqual(drm_resume.read_text(), '0\n')
                self.assertEqual(result.returncode == 0, not fault, result.stderr)
                expected = 'failed\n' if fault else 'resumed\n'
                self.assertEqual((self.root / 'run/sleep-status').read_text(), expected)
                self.assertEqual(result.stdout, '' if fault == 'mem' else 'Q2_KERNEL_RETURN\n')
                self.assertFalse((self.root / 'app/control.lock').exists())

    def test_retained_drm_with_no_kernel_support_uses_original_sleep(self):
        result, events = self.run_helper(retained_drm=True, notice=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Q2_KERNEL_RETURN\n')
        self.assertEqual(events, ['supervisor stop', 'sync', 'mem', 'supervisor start'])

    def test_every_kernel_resume_and_preparation_fault_unwinds_supervision(self):
        for fault in ('stop', 'sync', 'mem', 'no-cycle', 'resume-fail', 'resume-missing', 'resume-extra-line', 'reboot', 'charge', 'start', 'failed_resume', 'failed_resume_early', 'failed_resume_noirq'):
            with self.subTest(fault=fault):
                self.tearDown()
                self.setUp()
                result, events = self.run_helper(fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-1], 'supervisor start')
                self.assertEqual((self.root / 'run/sleep-status').read_text(), 'failed\n')
                self.assertFalse((self.root / 'run/usb-storage.lock').exists())
                self.assertFalse((self.root / 'app/control.lock').exists())

    def test_host_owned_card_refused_without_touching_supervision(self):
        session = self.root / 'run/usb-storage'
        session.mkdir()
        (session / 'phase').write_text('exported\n')
        result, events = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])

    def test_pm_dry_runs_and_shallow_mem_selection_cannot_claim_sleep(self):
        for path, modes in {
            'pm_test': ('[none] core processors platform devices freezer',
                        'none core processors platform [devices] freezer'),
            'mem_sleep': ('s2idle [deep]', '[s2idle] deep'),
        }.items():
            with self.subTest(path=path):
                target = self.root / 'sys/power' / path
                target.write_text(modes[1] + '\n')
                result, events = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events, [])
                target.write_text(modes[0] + '\n')
                result, events = self.run_helper()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('mem', events)
                target.unlink()
                (self.root / 'events').unlink()
                (self.root / 'sys/power/suspend_stats/success').write_text('4\n')
                (self.root / 'sys/kernel/q2_suspend/proof').write_text(proof()+'\n')

    def test_core_proof_requires_one_real_return_and_both_ddr_acknowledgements(self):
        before = proof()
        after = returned()
        path = self.root / 'sys/kernel/q2_suspend/proof'
        faults = [None, ('attempts=4', 'attempts=3'), ('core_returns=3', 'core_returns=2'),
                  ('core_failures=1', 'core_failures=2'), ('valid=1', 'valid=0'),
                  ('stage=7', 'stage=6'), ('error=0', 'error=1'),
                  ('0x00000104', '0x00000100'), ('0xa0000002', '0xa0000000'),
                  ('attempts=4', 'attempts=invalid')]
        for fault in faults:
            with self.subTest(fault=fault):
                path.write_text(before + '\n')
                (self.root / 'sys/power/suspend_stats/success').write_text('4\n')
                result, events = self.run_helper(proof_after=after if fault is None else after.replace(*fault))
                self.assertEqual(result.returncode == 0, fault is None, result.stderr)
                self.assertEqual(events[-1], 'supervisor start')
                (self.root / 'events').unlink()

    def test_retained_mode_rejects_cpu_reset_exception_roundtrip_and_wrong_clocks(self):
        path = self.root / 'sys/kernel/q2_suspend/proof'
        mutants=[dict(roundtrip=1),dict(roundtrip_returns=1),dict(roundtrip_failures=1),
                 dict(cpu_only=1),dict(cpu_only_returns=1),dict(cpu_only_failures=1),
                 dict(epc_restore=1),dict(rtc_exit=99)]
        mutants += [dict(resume_status=f'0x{0x14001c00|n:08x}') for n in range(1,8)]
        mutants += [dict(entry_lcr=f'0x{0xff001f00|n:08x}') for n in (0,2,3)]
        mutants += [dict(entry_opcr=f'0x{0x40f01514^(1<<bit):08x}') for bit in (31,30,26,22,21,20,4,3,2)]
        for changes in mutants:
            with self.subTest(changes=changes):
                path.write_text(proof()+'\n');(self.root/'sys/power/suspend_stats/success').write_text('4\n')
                result,events=self.run_helper(proof_after=returned(**changes))
                self.assertNotEqual(result.returncode,0);self.assertEqual(events[-1],'supervisor start')
                self.assertEqual((self.root/'run/sleep-status').read_text(),'failed\n');(self.root/'events').unlink()

    def test_proof_counter_offsets_and_numeric_wire_properties(self):
        path = self.root / 'sys/kernel/q2_suspend/proof'
        for count in (0,1,17,2**32-2):
            with self.subTest(count=count):
                path.write_text(proof(attempts=count,core_returns=count,error=-5)+'\n')
                (self.root/'sys/power/suspend_stats/success').write_text('4\n')
                result,events=self.run_helper(proof_after=returned(attempts=count+1,core_returns=count+1))
                self.assertEqual(result.returncode,0,result.stderr);(self.root/'events').unlink()
        for raw in (proof()+' attempts=3',proof().replace('attempts=3','attempts=03'),
                    proof().replace('attempts=3','attempts=4294967296'),
                    proof().replace('error=0','error=-4096'),
                    proof().replace('entry_opcr=0x40f01514','entry_opcr=0x400000000'),
                    proof().replace(' epc_restore=0','')):
            with self.subTest(raw=raw):
                path.write_text(raw+'\n');result,events=self.run_helper(proof_after=returned())
                self.assertNotEqual(result.returncode,0);self.assertEqual(events,[])

    def test_malformed_core_proof_refuses_before_disarming_supervision(self):
        path = self.root / 'sys/kernel/q2_suspend/proof'
        for value in ('', 'attempts=1', 'attempts=1 core_returns=1 core_failures=0 valid=1 stage=7 error=0 entry_ddrc=0xnothex00 entry_phy=0x00000002'):
            with self.subTest(value=value):
                path.write_text(value + '\n')
                result, events = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events, [])

    def test_busy_storage_or_service_lock_is_preserved(self):
        for name in ('run/usb-storage.lock', 'app/control.lock'):
            with self.subTest(name=name):
                lock = self.root / name
                lock.mkdir()
                result, events = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(lock.exists())
                self.assertEqual(events, [])
                lock.rmdir()

    def wifi(self):
        pid=str(os.getpid());proc=self.root/'proc'/pid;proc.mkdir()
        (proc/'comm').write_text('wpa_supplicant\n')
        (proc/'stat').write_text(pid+' (wpa_supplicant) S '+'0 '*18+'1234\n')
        (self.root/'network/wpa.pid').write_text(pid+'\n')
        endpoint=socket.socket(socket.AF_UNIX);self.addCleanup(endpoint.close)
        # Worktree prefixes may exceed sockaddr_un; bind the same actual inode
        # through an owned directory FD, without a global alias or cwd mutation.
        directory=os.open(self.root/'ctrl',os.O_RDONLY|os.O_DIRECTORY)
        try:endpoint.bind(f'/proc/self/fd/{directory}/wlan0')
        finally:os.close(directory)

    def test_normal_capability_and_proof_are_required_before_any_pm_action(self):
        path=self.root/'sys/kernel/q2_suspend/entry_policy'
        for value in ('version=1 normal_retained=0','version=2 normal_retained=1',
                      'version=1 normal_retained=1 extra=1','',None):
            with self.subTest(value=value):
                if value is None:path.unlink()
                else:path.write_text(value+'\n')
                result,events=self.run_helper()
                self.assertNotEqual(result.returncode,0);self.assertEqual(events,[])
        path.write_text('version=1 normal_retained=1\n')
        (self.root/'sys/kernel/q2_suspend/proof').unlink()
        result,events=self.run_helper();self.assertNotEqual(result.returncode,0);self.assertEqual(events,[])

    def test_each_real_sleep_has_one_owned_network_event_pair(self):
        self.wifi();result,events=self.run_helper()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(events,['supervisor stop','sync','wpa suspend','mem','wpa resume','supervisor start'])
        self.assertFalse((self.root/'network/control.lock').exists())

    def test_notify_and_mem_failures_resume_same_owner_and_restore_all_locks(self):
        for fault in ('notify-suspend','notify-output','notify-resume','mem','replace-suspend','replace-resume'):
            with self.subTest(fault=fault):
                self.tearDown();self.setUp();self.wifi();result,events=self.run_helper(fault)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(events[-1],'supervisor start')
                if fault=='replace-suspend':self.assertNotIn('wpa resume',events)
                elif fault=='replace-resume':self.assertEqual(events.count('wpa resume'),1)
                else:self.assertIn('wpa resume',events)
                for lock in ('network/control.lock','run/usb-storage.lock','app/control.lock'):
                    self.assertFalse((self.root/lock).exists())

    def test_unowned_network_and_busy_network_lock_refuse_without_stimulus(self):
        for condition in ('bad-pid','missing-pid','network-lock'):
            with self.subTest(condition=condition):
                self.tearDown();self.setUp();self.wifi()
                if condition=='bad-pid':(self.root/'network/wpa.pid').write_text('1\n')
                elif condition=='missing-pid':(self.root/'network/wpa.pid').unlink()
                else:(self.root/'network/control.lock').mkdir()
                result,events=self.run_helper();self.assertNotEqual(result.returncode,0);self.assertEqual(events,[])
                self.assertEqual((self.root/'network/control.lock').exists(),condition=='network-lock')

    def test_malformed_or_missing_kernel_evidence_refuses_suspend(self):
        for path in ('sys/power/wakeup_count', 'sys/power/suspend_stats/success', 'sys/power/suspend_stats/fail'):
            with self.subTest(path=path):
                target = self.root / path
                old = target.read_text()
                target.write_text('invalid\n')
                result, events = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('mem', events)
                target.write_text(old)
                (self.root / 'events').unlink(missing_ok=True)


class SupervisorStartupTest(unittest.TestCase):
    def test_prompt_health_rescue_and_bounded_slow_start(self):
        source = SUPERVISOR.read_text().replace('/run/q2nd', '"$FIXTURE/run"')
        source = source.replace('/state/app', '"$FIXTURE/state"')
        source = source.replace('/proc/cmdline', '"$FIXTURE/cmdline"')
        source = source.replace('/usr/sbin/q2-player-supervisor', ':')
        base = ROOT / 'build/sleep-tests'
        base.mkdir(parents=True, exist_ok=True)
        for ready in ('health', 'rescue', 'never'):
            with self.subTest(ready=ready), tempfile.TemporaryDirectory(dir=base, prefix='supervisor-') as directory:
                root = Path(directory)
                (root / 'run').mkdir()
                (root / 'cmdline').write_text('generated boot\n')
                with socket.socket(socket.AF_UNIX) as endpoint:
                    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        endpoint.bind(f'/proc/self/fd/{fd}/staged.sock')
                    finally:
                        os.close(fd)
                    # Supply readiness during the first poll without relying on
                    # host scheduler timing. The real script tests the socket.
                    script = """
sleep() {
    echo "$1" >> "$FIXTURE/sleeps"
    case "$READY" in
        health) mv "$FIXTURE/staged.sock" "$FIXTURE/run/health.sock" ;;
        rescue) touch "$FIXTURE/run/rescue" ;;
    esac
}
""" + source
                    result = subprocess.run(['sh', '-c', script, 'supervisor', 'start'],
                                            env=dict(os.environ, FIXTURE=directory, READY=ready),
                                            capture_output=True, text=True, timeout=5)
                delays = [float(seconds) for seconds in (root / 'sleeps').read_text().splitlines()]
                if ready == 'never':
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertAlmostEqual(sum(delays), 4.9)
                    self.assertLess(len(delays), 60)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(delays, [0.01])


if __name__ == '__main__':
    unittest.main()
