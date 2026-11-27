"""Installed-loopback startup contract; generated shell fixtures only."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / 'os/install/root-init'


class RootInitTest(unittest.TestCase):
    def test_boot_metadata_failure_does_not_block_init(self):
        text = INIT.read_text()
        start = text.index('bootmark() {')
        function = text[start:text.index('\n}', start) + 2]
        base = ROOT / 'build/perf/root-boot-times'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            path = Path(directory) / 'boot-times'
            function = function.replace('/run/q2/boot-times', '"$checkpoint"')
            result = subprocess.run(['sh', '-c', function + '''
checkpoint=$1
bootmark state-complete || exit 1
checkpoint=$1/missing/file
bootmark peripherals-complete || exit 2
read() { return 1; }
bootmark services-requested
''', 'sh', str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            fields = path.read_text().split()
            self.assertEqual(fields[1:], ['state-complete'])
            self.assertGreaterEqual(float(fields[0]), 0)

    def test_actual_loopback_function_is_repeatable_and_propagates_failure(self):
        text = INIT.read_text()
        function = text[text.index('loopback_setup() {'):text.index('\n}', text.index('loopback_setup() {')) + 2]
        result = subprocess.run(['sh', '-c', function + '''
ifconfig() {
    [ "$*" = 'lo 127.0.0.1 netmask 255.0.0.0 up' ] || return 42
    [ "${failed:-0}" = 0 ] || return 43
    address=127.0.0.1; mask=255.0.0.0; online=1
}
loopback_setup && loopback_setup || exit 1
[ "$address/$mask/$online" = '127.0.0.1/255.0.0.0/1' ] || exit 2
failed=1
loopback_setup
[ "$?" = 43 ]
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_loopback_precedes_services_and_failure_withholds_app(self):
        text = INIT.read_text()
        configured = text.index('if loopback_setup; then')
        for command in ['/sbin/q2-state >', '/sbin/q2-diagnostic-usb &&',
                        '/etc/init.d/S02q2-clock start', '/sbin/q2-player-bringup >']:
            self.assertLess(configured, text.index(command))
        self.assertIn('if [ "$loopback_ready" = 1 ]; then', text)
        self.assertLess(text.index('peripherals_pid=$!'), text.index('wait "$peripherals_pid"'))
        self.assertLess(text.index('wait "$peripherals_pid"'), text.index('if [ "$peripherals_ready" = 1 ]; then'))
        self.assertIn('diagnostics retained', text)


if __name__ == '__main__': unittest.main()
