#!/usr/bin/env python3
"""Host regression: SIGTERM must wake a dark, idle UI without a health timer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary', type=Path, default=ROOT / 'target/debug/q2nd')
a = p.parse_args()
out = ROOT / 'build/os/appliance' / ('shutdown-' + uuid.uuid4().hex[:8])
out.mkdir(parents=True)
(out / 'config.toml').write_text('''# Generated fixture; no owner configuration.
[server]
url = "http://127.0.0.1:1"
user = "shutdown-fixture"
password = "synthetic-only"
[wifi]
enabled = false
[display]
timezone = "UTC"
backlight_seconds = 1
[power]
sleep_minutes = 0
''')
runtime = out / 'run'
runtime.mkdir()
env = {k: v for k, v in os.environ.items() if not k.startswith('Q2ND_')}
env['Q2ND_RUNTIME'] = str(runtime)
report = {'passed': False, 'host_only': True, 'artifacts': str(out.relative_to(ROOT))}
with (out / 'player.log').open('w') as log:
    proc = subprocess.Popen([str(a.binary.resolve()), '--headless', '--data', str(out)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 15
        while not (runtime / 'ready').exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('player did not become ready')
            time.sleep(.05)
        # Backlight expires after one second. The resulting idle UI normally
        # waits up to 30 seconds; no mainline heartbeat shortens that wait.
        time.sleep(4)
        started = time.monotonic()
        proc.terminate()
        assert proc.wait(timeout=2) == 0, 'shutdown exit code'
        report.update(passed=True, shutdown_seconds=time.monotonic()-started)
        assert 'stopping (0)' in (out / 'player.log').read_text()
    except BaseException as exc:
        report.update(passed=False, error=str(exc))
        raise
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        (ROOT / 'build/os/shutdown-test.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
