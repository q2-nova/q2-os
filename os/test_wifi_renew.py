"""Generated fixtures for the Wi-Fi lease replacement; never reads owner configuration.

`renew` runs when the supplicant has joined a different saved network. These
tests execute the actual helper against real processes named wpa_supplicant and
udhcpc (copies of sleep), so its PID ownership checks and TERM path are real.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/usr/libexec/q2-network/wifi'


class WifiRenew(unittest.TestCase):
    def setUp(self):
        base = ROOT / 'build/wifi-renew-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.dir = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.dir.name)
        self.children = []
        for name in ('wpa_supplicant', 'udhcpc'):
            shutil.copy(shutil.which('sleep'), self.root / name)

    def tearDown(self):
        for pid in self.children:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        self.dir.cleanup()

    def spawn(self, name, pidfile):
        # Detach through a short-lived shell, as udhcpc -b and wpa_supplicant -B
        # do on the Q2, so the killed process is reaped by init rather than left
        # a zombie that `kill -0` would still find.
        pid = int(subprocess.run(['sh', '-c', f'"{self.root / name}" 60 >/dev/null 2>&1 & echo $!'],
                                 capture_output=True, text=True, check=True).stdout)
        self.children.append(pid)
        deadline = time.monotonic() + 2
        while Path(f'/proc/{pid}/comm').read_text().strip() != name:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        pidfile.write_text(f'{pid}\n')
        return pid

    @staticmethod
    def alive(pid):
        try:
            return Path(f'/proc/{pid}/stat').read_text().split(') ')[1][0] != 'Z'
        except FileNotFoundError:
            return False

    def helper(self, action, interface=True):
        run = self.root / 'run'
        run.mkdir(exist_ok=True)
        if interface:
            (self.root / 'sys/class/net/wlan0').mkdir(parents=True, exist_ok=True)
        script = HELPER.read_text() + '''
RUN=$FIXTURE/run
SYS=$FIXTURE/sys
LOG=$FIXTURE
CTRL=$FIXTURE/ctrl
ip() { echo "ip:$*" >> "$FIXTURE/events"; }
udhcpc() { echo "udhcpc:$*" >> "$FIXTURE/events"; }
sleep() { command sleep 0.05; }
$ACTION
'''
        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, Q2_NETWORK_SOURCE_ONLY='1', FIXTURE=str(self.root), ACTION=action))
        events = (self.root / 'events').read_text().splitlines() if (self.root / 'events').exists() else []
        return result, events

    def test_renew_replaces_a_running_client_and_its_address(self):
        run = self.root / 'run'
        run.mkdir()
        self.spawn('wpa_supplicant', run / 'wpa.pid')
        old = self.spawn('udhcpc', run / 'dhcp.pid')
        result, events = self.helper('renew')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(old))
        self.assertFalse((run / 'dhcp.pid').exists())
        self.assertEqual(events[0], 'ip:-4 addr flush dev wlan0')
        self.assertEqual(events[1].split()[:6], ['udhcpc:-i', 'wlan0', '-b', '-p', str(run / 'dhcp.pid'), '-s'])
        self.assertEqual(len(events), 2)

    def test_dhcp_keeps_a_running_client(self):
        run = self.root / 'run'
        run.mkdir()
        self.spawn('wpa_supplicant', run / 'wpa.pid')
        client = self.spawn('udhcpc', run / 'dhcp.pid')
        result, events = self.helper('dhcp')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.alive(client))
        self.assertEqual(events, [])

    def test_renew_without_a_client_starts_one(self):
        run = self.root / 'run'
        run.mkdir()
        self.spawn('wpa_supplicant', run / 'wpa.pid')
        result, events = self.helper('renew')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e.split()[0] for e in events], ['ip:-4', 'udhcpc:-i'])

    def test_renew_refuses_without_the_owned_supplicant(self):
        run = self.root / 'run'
        run.mkdir()
        client = self.spawn('udhcpc', run / 'dhcp.pid')
        result, events = self.helper('renew')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owned supplicant is not running', result.stderr)
        self.assertTrue(self.alive(client))
        self.assertEqual(events, [])

    def test_renew_refuses_a_foreign_dhcp_pid(self):
        run = self.root / 'run'
        run.mkdir()
        self.spawn('wpa_supplicant', run / 'wpa.pid')
        foreign = self.spawn('wpa_supplicant', run / 'dhcp.pid')
        result, events = self.helper('renew')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not the expected network process', result.stderr)
        self.assertTrue(self.alive(foreign))
        self.assertEqual(events, [])

    def test_main_accepts_renew_and_refuses_unknown_actions(self):
        run = self.root / 'run'
        run.mkdir()
        self.spawn('wpa_supplicant', run / 'wpa.pid')
        for action, ok in (('main renew', True), ('main bogus', False)):
            with self.subTest(action=action):
                result, _ = self.helper(action)
                self.assertEqual(result.returncode == 0, ok, result.stderr)
                if not ok:
                    self.assertIn('unknown action', result.stderr)
                self.assertFalse((run / 'control.lock').exists())


if __name__ == '__main__':
    unittest.main()
