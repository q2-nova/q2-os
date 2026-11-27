"""Barrier-based startup/USB ordering simulations; all processes/data generated."""
import os
from pathlib import Path
import select
import subprocess
import unittest

import test_installed_services as integration

ROOT = Path(__file__).resolve().parents[1]


class UsbStartupDST(unittest.TestCase):
    def setUp(self):
        self.case = integration.IntegrationTest()
        self.case.setUp()
        self.root = self.case.root
        self.enter = self.root / 'entered'
        self.release = self.root / 'release'
        os.mkfifo(self.enter)
        os.mkfifo(self.release)
        self.reader = os.open(self.enter, os.O_RDWR | os.O_NONBLOCK)
        self.writer = os.open(self.release, os.O_RDWR | os.O_NONBLOCK)
        self.processes = []

    def tearDown(self):
        os.write(self.writer, b'release\nrelease\n')
        for child in self.processes:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=25)
        os.close(self.reader)
        os.close(self.writer)
        self.case.tearDown()

    def entered(self):
        self.assertTrue(select.select([self.reader], [], [], 4)[0], 'startup never reached generated barrier')
        os.read(self.reader, 64)

    def spawn(self, *command, **extra):
        child = subprocess.Popen(command, env=dict(self.case.env, **extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(child)
        return child

    def test_usb_stop_waits_for_the_complete_inflight_start_admission(self):
        driver = self.case.service_script(fail=False)
        content = driver.read_text().rsplit('main "$@"', 1)[0]
        driver.write_text(content + '''
if [ "${BYPASS_ADMISSION:-0}" = 1 ]; then control_lock() { :; }; fi
media_setup() { echo media-setup >> "$FIXTURE/events"; export Q2ND_SD_READY=0; }
if [ "$1" = run ]; then
    echo reached > "$FIXTURE/entered"
    IFS= read -r release < "$FIXTURE/release"
fi
main "$@"
''')
        for bypass in (False, True):
            with self.subTest(negative_control=bypass):
                (self.root / 'state-status').write_text('ready\n')
                session = self.root / 'usb-storage'
                if session.exists():
                    (session / 'phase').unlink()
                    session.rmdir()
                start = self.spawn(str(driver), 'start', BYPASS_ADMISSION=str(int(bypass)))
                self.entered()  # run exists but has not published launcher.pid.
                session.mkdir()
                (session / 'phase').write_text('preparing\n')
                (self.root / 'state-status').write_text('usb-storage\n')
                stop = self.spawn(str(driver), 'stop', BYPASS_ADMISSION=str(int(bypass)))
                if bypass:
                    self.assertEqual(stop.wait(timeout=2), 0, 'old uncoordinated stop no longer reproduces')
                    self.assertIsNone(start.poll(), 'start is still in its admission interval')
                else:
                    with self.assertRaises(subprocess.TimeoutExpired):
                        stop.wait(timeout=.2)
                os.write(self.writer, b'release\n')
                self.assertNotEqual(start.wait(timeout=4), 0, 'late start must respect USB storage gate')
                self.assertEqual(stop.wait(timeout=4), 0)
                self.assertFalse((self.root / 'run/control.lock').exists())
                self.assertFalse((self.root / 'run/launcher.pid').exists())
                self.assertNotIn('media-setup', self.case.events.read_text())
                # The negative proves unordered control completion, not an
                # unsafe mount: even old late-start state gates reject setup.

    def test_root_init_waits_for_service_admission_before_usb_watcher(self):
        source = (ROOT / 'os/install/root-init').read_text()
        begin = source.index('if [ "$peripherals_ready" = 1 ]; then')
        stage = source[begin:source.index('echo Q2_INSTALLED_ROOT_READY', begin)]
        service = self.root / 'installed-services'
        service.write_text('''#!/bin/sh
 echo service-enter >> "$FIXTURE/events"
 echo reached > "$FIXTURE/entered"
 IFS= read -r release < "$FIXTURE/release"
 echo service-admitted >> "$FIXTURE/events"
''')
        service.chmod(0o755)
        auto = self.root / 'usb-auto'
        auto.write_text('#!/bin/sh\necho auto-start >> "$FIXTURE/events"\n')
        auto.chmod(0o755)
        stage = stage.replace('/sbin/q2-player-bringup', 'true').replace('/sbin/q2-installed-services', str(service)).replace('/usr/sbin/q2-usb-auto', str(auto)).replace('/run/log', str(self.root / 'log'))
        driver = self.root / 'init-stage'
        driver.write_text('#!/bin/sh\nperipherals_ready=1\nbootmark() { :; }\n' + stage)
        init = self.spawn('sh', str(driver))
        self.entered()
        self.assertNotIn('auto-start', self.case.events.read_text())
        os.write(self.writer, b'release\n')
        self.assertEqual(init.wait(timeout=3), 0)
        self.assertEqual(self.case.events.read_text().splitlines(), ['service-enter', 'service-admitted', 'auto-start'])


if __name__ == '__main__':
    unittest.main()
