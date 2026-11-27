import hashlib
import os
import signal
import shlex
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'card/launcher.sh'
GOOD = b'#!/bin/sh\necho $$ > "$Q2ND_RUNTIME/ready"\ntrap "exit 0" TERM\nwhile :; do sleep 1; done\n'

class LauncherTests(unittest.TestCase):
    def setUp(self):
        build = LAUNCHER.parents[2] / 'build/power/host-tests'
        build.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='q2-launcher-', dir=build)
        self.root = Path(self.temp.name)
        self.app = self.root / 'app'
        self.run = self.root / 'run'
        self.app.mkdir()
        self.proc = None
        self.log = (self.root / 'log').open('wb')
    def tearDown(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=15)
        self.log.close()
        self.temp.cleanup()
    def put(self, name, data):
        p = self.app / name
        p.write_bytes(data)
        p.chmod(0o755)
    def start(self):
        env = dict(os.environ, Q2ND_APP=str(self.app), Q2ND_RUNTIME=str(self.run))
        shell = shlex.split(os.environ.get('Q2_TEST_SHELL', 'sh'))
        self.proc = subprocess.Popen(shell + [str(LAUNCHER)], env=env, stdout=self.log, stderr=self.log)
    def until(self, condition):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if condition(): return
            time.sleep(.1)
        self.fail((self.root / 'log').read_text())
    def request_restart(self):
        # As deploy.py does: touch restart, then USR1 if the launcher asks for it.
        (self.run / 'restart').touch()
        if (self.run / 'launcher.wake').exists():
            os.kill(int((self.run / 'launcher.pid').read_text()), signal.SIGUSR1)
    def children(self):
        out = subprocess.run(['ps', '-o', 'pid=,comm=', '--ppid', str(self.proc.pid)], capture_output=True, text=True).stdout
        return [l.split() for l in out.splitlines() if l.strip()]
    def stage(self, image, digest=None):
        self.put('player.next', image)
        digest = digest or hashlib.sha256(image).hexdigest()
        self.put('player.next.sha256', f'{digest}  player.next\n'.encode())
    def test_bad_candidate_restores_previous(self):
        self.put('player', GOOD)
        self.stage(b'#!/bin/sh\nexit 1\n')
        self.start()
        self.until(lambda: b'Restoring previous' in (self.root / 'log').read_bytes())
        self.until(lambda: (self.run / 'ready').exists())
        self.assertEqual((self.app / 'player').read_bytes(), GOOD)
        self.assertFalse((self.app / 'player.trial').exists())
    def test_hash_mismatch_does_not_replace_player(self):
        self.put('player', GOOD)
        self.stage(b'broken', '0' * 64)
        self.start()
        self.until(lambda: (self.run / 'ready').exists())
        self.assertEqual((self.app / 'player').read_bytes(), GOOD)
        self.assertFalse((self.app / 'player.previous').exists())
    def test_power_loss_restores_unconfirmed_player(self):
        self.put('player', b'broken')
        self.put('player.previous', GOOD)
        self.put('player.trial', b'')
        self.start()
        self.until(lambda: (self.run / 'ready').exists())
        self.assertEqual((self.app / 'player').read_bytes(), GOOD)
    def test_live_restart_confirms_ready_candidate(self):
        self.put('player', GOOD)
        self.start()
        self.until(lambda: (self.run / 'ready').exists())
        old_pid = (self.run / 'ready').read_text()
        candidate = GOOD + b'# candidate\n'
        self.stage(candidate)
        self.request_restart()
        self.until(lambda: b'update confirmed' in (self.root / 'log').read_bytes())
        self.assertNotEqual((self.run / 'ready').read_text(), old_pid)
        self.assertEqual((self.app / 'player').read_bytes(), candidate)
        self.assertEqual((self.app / 'player.previous').read_bytes(), GOOD)
    def test_confirmed_launcher_blocks_without_polling(self):
        self.put('player', GOOD)
        self.start()
        self.until(lambda: (self.run / 'ready').exists())
        player = int((self.run / 'ready').read_text())
        # No `sleep` children over several seconds: only the player (and
        # the optional SSH script, absent here).
        for _ in range(6):
            self.assertEqual([int(p) for p, _ in self.children()], [player])
            time.sleep(.5)
        started = time.monotonic()
        self.request_restart()
        self.until(lambda: (self.run / 'ready').exists() and (self.run / 'ready').read_text() != str(player))
        self.assertLess(time.monotonic() - started, 5)
    def test_stock_exit_passes_through(self):
        self.put('player', b'#!/bin/sh\nexit 81\n')
        self.start()
        self.assertEqual(self.proc.wait(timeout=5), 81)
        self.assertFalse((self.run / 'player.pid').exists())

if __name__ == '__main__': unittest.main()
