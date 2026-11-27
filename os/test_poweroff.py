"""Installed shutdown ordering and refusal paths, with generated mount/process mocks."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/sbin/q2-safe-poweroff'


class PoweroffTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/poweroff-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.fixture = Path(tempfile.mkdtemp(prefix='case-', dir=base))
        for name in ('run', 'proc/self'):
            (self.fixture / name).mkdir(parents=True)
        self.mounts = self.fixture / 'proc/mounts'
        self.mounts.write_text('/dev/mmcblk0p1 /mnt/mmc exfat rw 0 0\n'
                               '/dev/ubi0_0 /state ubifs rw 0 0\n'
                               '/dev/ubi0_0 /mnt/userdata ubifs rw 0 0\n')
        (self.fixture / 'proc/self/mountinfo').write_text(
            '40 20 0:20 /q2-mainline /state rw - ubifs /dev/ubi0_0 rw\n')
        self.events = self.fixture / 'events'
        for name in ('services', 'wifi', 'time', 'poweroff'):
            path = self.fixture / name
            path.write_text('#!/bin/sh\nname=${0##*/}\n'
                            'echo "$name $*" >> "$FIXTURE/events"\n'
                            '[ "${FAIL:-}" != "$name" ]\n')
            path.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.fixture)

    def run_helper(self, fail='', main=False, fail_mount=False):
        script = f'Q2_POWEROFF_SOURCE_ONLY=1\n. "{HELPER}"\n' + '''
RUN=$FIXTURE/run
PROC=$FIXTURE/proc
SERVICES=$FIXTURE/services
WIFI=$FIXTURE/wifi
TIME=$FIXTURE/time
POWEROFF=$FIXTURE/poweroff
USB_AUTO=$FIXTURE/usb-auto
WIFI_RUN=$FIXTURE/wifi-run
TIME_RUN=$FIXTURE/time-run
HISTORY=$FIXTURE/history
sync() { echo sync >> "$FIXTURE/events"; [ "$FAIL" != sync ]; }
umount() { echo "umount $1" >> "$FIXTURE/events"; [ "$FAIL" != "$1" ]; }
mount() { echo "mount $*" >> "$FIXTURE/events"; [ "$FAIL_MOUNT" != 1 ]; }
id() { echo 0; }
'''
        script += 'main\n' if main else 'shutdown_installed\n'
        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                                env=dict(os.environ, FIXTURE=str(self.fixture), FAIL=fail, FAIL_MOUNT=str(int(fail_mount))), timeout=10)
        events = self.events.read_text().splitlines() if self.events.exists() else []
        return result, events

    def test_orderly_shutdown_flushes_and_unmounts_before_force_poweroff(self):
        result, events = self.run_helper(main=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['services stop', 'time stop', 'wifi down', 'sync',
                                 'umount /mnt/mmc', 'umount /state', 'umount /mnt/userdata',
                                 'sync', 'poweroff -f'])
        self.assertFalse((self.fixture / 'run/usb-storage.lock').exists())

    def test_orderly_shutdown_appends_one_bounded_history_line_first(self):
        history = self.fixture / 'history'
        history.write_text(''.join(f'boot {n}\n' for n in range(70)))
        result, events = self.run_helper(main=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = history.read_text().splitlines()
        self.assertEqual(len(lines), 64)
        self.assertEqual(lines[0], 'boot 7')
        self.assertTrue(lines[-1].startswith('safe-poweroff up='), lines[-1])
        self.assertFalse((self.fixture / 'history.new').exists())

    def test_unwritable_history_never_blocks_shutdown(self):
        (self.fixture / 'history').mkdir()
        (self.fixture / 'history.new').mkdir()
        result, events = self.run_helper(main=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1], 'poweroff -f')

    def test_every_stop_sync_and_unmount_failure_prevents_kernel_shutdown(self):
        for failure in ('services', 'time', 'wifi', 'sync', '/mnt/mmc', '/state', '/mnt/userdata'):
            with self.subTest(failure=failure):
                self.events.unlink(missing_ok=True)
                result, events = self.run_helper(failure, main=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('poweroff -f', events)
                self.assertFalse((self.fixture / 'run/usb-storage.lock').exists())
                self.assertEqual((self.fixture / 'run/poweroff-status').read_text(), 'failed\n')

    def test_failed_poweroff_restores_unmounted_storage_before_player(self):
        result, events = self.run_helper('poweroff', main=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-4:], [
            'mount -t ubifs /dev/ubi0_0 /mnt/userdata',
            'mount --bind /mnt/userdata/q2-mainline /state',
            'mount -t exfat -o nosuid,nodev,noexec /dev/mmcblk0p1 /mnt/mmc',
            'services start'])
        self.assertEqual((self.fixture / 'run/poweroff-status').read_text(), 'failed\n')

    def test_failed_storage_recovery_withholds_player_restart(self):
        result, events = self.run_helper('poweroff', main=True, fail_mount=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mount -t ubifs /dev/ubi0_0 /mnt/userdata', events)
        self.assertNotIn('services start', events)
        self.assertIn('recovery incomplete', result.stderr)

    def test_kernel_failure_is_reported(self):
        result, events = self.run_helper('poweroff')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-1], 'poweroff -f')
        self.assertIn('kernel poweroff request failed', result.stderr)

    def test_optional_usb_manager_stops_before_services_and_failure_blocks_shutdown(self):
        manager=self.fixture/'usb-auto'
        manager.write_text('#!/bin/sh\necho "usb-auto $*" >> "$FIXTURE/events"\n[ "$FAIL" != usb-auto ]\n')
        manager.chmod(0o755)
        result,events=self.run_helper()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(events[:2],['usb-auto stop','services stop'])
        self.events.unlink()
        result,events=self.run_helper('usb-auto')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(events,['usb-auto stop'])

    def test_host_owned_card_is_refused_before_stopping_player(self):
        session = self.fixture / 'run/usb-storage'
        session.mkdir()
        (session / 'phase').write_text('exported\n')
        result, events = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(events)

    def test_unknown_or_nested_mounts_are_refused_before_stopping(self):
        original = self.mounts.read_text()
        for mounts in (original.replace('/dev/mmcblk0p1', '/dev/foreign'),
                       original.replace('/dev/ubi0_0 /state ubifs', '/dev/loop0 /state ext4'),
                       original + 'tmpfs /state/nested tmpfs rw 0 0\n'):
            with self.subTest(mounts=mounts):
                self.mounts.write_text(mounts)
                result, events = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(events)

    def test_foreign_state_bind_is_refused(self):
        (self.fixture / 'proc/self/mountinfo').write_text(
            '40 20 0:20 /stock /state rw - ubifs /dev/ubi0_0 rw\n')
        result, events = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(events)

    def test_storage_lock_refuses_parallel_operation(self):
        (self.fixture / 'run/usb-storage.lock').mkdir()
        result, events = self.run_helper(main=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(events)
        self.assertTrue((self.fixture / 'run/usb-storage.lock').exists())
        self.assertEqual((self.fixture / 'run/poweroff-status').read_text(), 'failed\n')

    def test_absent_storage_and_optional_clock_need_no_unmount(self):
        self.mounts.write_text('')
        (self.fixture / 'time').unlink()
        result, events = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['services stop', 'wifi down', 'sync', 'sync', 'poweroff -f'])


if __name__ == '__main__':
    unittest.main()
