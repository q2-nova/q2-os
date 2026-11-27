"""Generated host fixtures for the optional NTP service; never sets host time."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'os/network-time/overlay/usr/libexec/q2-network/time'


class TimeService(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/network-time-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=base))
        for name in ('run', 'log', 'trust', 'proc'):
            (self.root / name).mkdir()
        (self.root / 'state-status').write_text('ready\n')
        (self.root / 'clock-status').write_text('build-floor-unsynchronized\n')

    def tearDown(self):
        shutil.rmtree(self.root)

    def shell(self, text, ok=True):
        source = f'Q2_TIME_SOURCE_ONLY=1\n. "{HELPER}"\n' + '''
RUN=$FIXTURE/run
LOG=$FIXTURE/log
TRUST=$FIXTURE/trust
PROC=$FIXTURE/proc
STATUS=$FIXTURE/clock-status
STATE_STATUS=$FIXTURE/state-status
rtc_saved=0
hwclock() { echo "$*" >> "$FIXTURE/rtc-events"; }
sync() { :; }
'''
        result = subprocess.run(['sh', '-c', source + text], capture_output=True, text=True,
            timeout=10, env=dict(os.environ, FIXTURE=str(self.root)))
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        return result

    def test_completed_sync_records_source_and_rtc_without_epoch_bound(self):
        self.shell('record_sync\nrecord_sync\n')
        self.assertEqual((self.root / 'clock-status').read_text(), 'network-ntp\n')
        self.assertEqual((self.root / 'rtc-events').read_text(), '-w -u\n-r -u\n')
        self.assertEqual((self.root / 'trust/network-ntp-rtc').read_text(), 'time.cloudflare.com basic-ntp\n')
        self.assertFalse((self.root / 'trust/clock-epoch').exists())

    def test_failed_rtc_write_does_not_claim_persistence(self):
        self.shell('hwclock() { return 1; }\nrecord_sync\n')
        self.assertEqual((self.root / 'clock-status').read_text(), 'network-ntp\n')
        self.assertFalse((self.root / 'trust/network-ntp-rtc').exists())

    def test_missing_state_keeps_successful_sync_volatile(self):
        (self.root / 'state-status').write_text('missing\n')
        self.shell('record_sync\n')
        self.assertEqual((self.root / 'clock-status').read_text(), 'network-ntp\n')
        self.assertFalse((self.root / 'trust/network-ntp-rtc').exists())

    def test_marker_is_not_rewritten_after_reboot(self):
        marker = self.root / 'trust/network-ntp-rtc'
        marker.write_text('time.cloudflare.com basic-ntp\n')
        inode = marker.stat().st_ino
        self.shell('record_sync\n')
        self.assertEqual(marker.stat().st_ino, inode)

    def test_foreign_pid_is_never_signalled(self):
        (self.root / 'run/service.pid').write_text('123\n')
        (self.root / 'proc/123').mkdir()
        (self.root / 'proc/123/cmdline').write_bytes(b'unrelated\0process\0')
        self.shell('kill() { [ "$1" = -0 ]; }\nmain stop\n', ok=False)

    def test_config_has_explicit_basic_source_and_standard_polling(self):
        config = (HELPER.parents[3] / 'etc/q2-network/chrony.conf').read_text()
        active = [l for l in config.splitlines() if l and not l.startswith('#')]
        sources = [l for l in active if l.startswith(('server ', 'pool '))]
        self.assertEqual(sources, ['server time.cloudflare.com iburst'])
        self.assertNotIn('nocerttimecheck', config)
        self.assertNotIn('maxchange', config)
        self.assertIn('port 0', active)
        self.assertIn('cmdport 0', active)

    def test_worker_waits_for_sync_and_stops_child_before_returning(self):
        helper = self.root / 'q2-network/time'
        helper.parent.mkdir()
        daemon = self.root / 'chronyd'
        daemon.write_text('#!/usr/bin/python3\nimport os,time\nfrom pathlib import Path\n'
            'p=Path(os.environ["FIXTURE"])/"daemon.pid"\np.write_text(str(os.getpid()))\n'
            'while True: time.sleep(1)\n')
        daemon.chmod(0o755)
        client = self.root / 'chronyc'
        client.write_text('#!/bin/sh\necho x >> "$FIXTURE/chronyc-calls"\n[ -e "$FIXTURE/synchronized" ]\n')
        client.chmod(0o755)
        helper.write_text(f'''#!/bin/sh
Q2_TIME_SOURCE_ONLY=1
. "{HELPER}"
RUN=$FIXTURE/run
LOG=$FIXTURE/log
TRUST=$FIXTURE/trust
STATUS=$FIXTURE/clock-status
STATE_STATUS=$FIXTURE/state-status
SELF=$FIXTURE/q2-network/time
CHRONYD=$FIXTURE/chronyd
CHRONYC=$FIXTURE/chronyc
hwclock() {{ echo "$*" >> "$FIXTURE/rtc-events"; }}
sync() {{ :; }}
sleep() {{ command sleep 0.05; }}
main "$@"
''')
        helper.chmod(0o755)
        env = dict(os.environ, FIXTURE=str(self.root))
        subprocess.run([str(helper), 'start'], env=env, check=True, timeout=5)
        deadline = time.monotonic() + 5
        try:
            while not (self.root / 'daemon.pid').exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertFalse((self.root / 'rtc-events').exists())
            self.assertEqual((self.root / 'clock-status').read_text(), 'build-floor-unsynchronized\n')
            (self.root / 'synchronized').touch()
            while not (self.root / 'trust/network-ntp-rtc').exists():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            # Synchronised with the RTC written: the worker waits on the
            # daemon and polls no more (20 sleeps of the fixture's 0.05 s).
            calls = (self.root / 'chronyc-calls').read_text()
            time.sleep(1)
            self.assertEqual((self.root / 'chronyc-calls').read_text(), calls)
            self.assertEqual((self.root / 'rtc-events').read_text(), '-w -u\n-r -u\n')
        finally:
            subprocess.run([str(helper), 'stop'], env=env, check=True, timeout=5)
        self.assertFalse((self.root / 'run/service.pid').exists())
        self.assertFalse((self.root / 'run/service.lock').exists())
        child = int((self.root / 'daemon.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)


if __name__ == '__main__':
    unittest.main()
