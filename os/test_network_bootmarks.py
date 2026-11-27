"""No owner data: exercise actual shell telemetry writers and rejection paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NetworkBootmarkTests(unittest.TestCase):
    def test_generated_uptime_and_unsafe_destinations(self):
        base = ROOT / 'build/perf'
        base.mkdir(parents=True, exist_ok=True)
        paths = dict(wifi='os/install/integration/usr/libexec/q2-network/wifi',
                     dhcp='os/install/integration/usr/libexec/q2-network/dhcp',
                     services='os/install/integration/sbin/q2-installed-services',
                     root_init='os/install/root-init', bootstrap='os/install/bootstrap',
                     usb='os/hardware/usb-storage')
        for helper, path in paths.items():
            text = (ROOT / path).read_text()
            start = text.index('bootmark() {')
            source = text[start:text.index('\n}', start) + 2]
            source = source.replace('/run/q2/boot-times', '$FIXTURE/boot-times').replace('/proc/uptime', '$FIXTURE/uptime')
            for kind in ('regular', 'full', 'symlink', 'fifo', 'directory'):
                for uptime in ('12.34', '', 'bad', '1..2', '-1.0', '1.0-extra', '.2', '2.'):
                    with self.subTest(helper=helper, kind=kind, uptime=uptime), tempfile.TemporaryDirectory(dir=base) as directory:
                        root = Path(directory)
                        (root / 'uptime').write_text(uptime + (' 0.00\n' if uptime else '\n'))
                        output = root / ('service-boot-times' if helper == 'services' else 'usb-stage-times' if helper == 'usb' else 'boot-times')
                        other = root / 'sentinel'
                        other.write_text('unchanged')
                        if kind == 'full':
                            output.write_bytes(b'x' * 65536)
                        elif kind == 'symlink':
                            output.symlink_to(other)
                        elif kind == 'fifo':
                            os.mkfifo(output)
                        elif kind == 'directory':
                            output.mkdir()
                        script = source + '\nRUN=$FIXTURE\nPROC=$FIXTURE\nUPTIME=$FIXTURE/uptime\nBOOT_TIMES=$FIXTURE/boot-times\nbootmark generated-stage\necho FINISHED\n'
                        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=2,
                            env=dict(os.environ, FIXTURE=str(root), Q2_NETWORK_SOURCE_ONLY='1',
                                     Q2_DHCP_SOURCE_ONLY='1', Q2_SERVICES_SOURCE_ONLY='1'))
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, 'FINISHED\n')
                        self.assertEqual(other.read_text(), 'unchanged')
                        if kind == 'regular':
                            self.assertEqual(output.read_text() if output.exists() else '',
                                             '12.34 generated-stage\n' if uptime == '12.34' else '')
                        if kind == 'full':
                            self.assertEqual(output.stat().st_size, 65536)


if __name__ == '__main__':
    unittest.main()
