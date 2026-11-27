"""Generated cold-boot interface fixtures; never read owner configuration."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/usr/libexec/q2-network/wifi'


class ColdBootWifi(unittest.TestCase):
    def run_fixture(self, driver, ready=False, appears=True, old=False, managed=False):
        base = ROOT / 'build/wifi-cold-tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            target = root / 'sys/bus/platform/drivers' / driver
            target.mkdir(parents=True)
            for name in ['bind', 'unbind']:
                (target / name).write_text('')
            device = root / 'sys/bus/platform/devices/13460000.mmc'
            device.mkdir(parents=True)
            (device / 'driver').symlink_to(target)
            if managed:
                (device / 'of_node').mkdir()
                (device / 'of_node/mmc-pwrseq').write_bytes(b'\0\0\0\1')
            if ready:
                (root / 'sys/class/net/wlan0').mkdir(parents=True)
            source = HELPER.read_text()
            if old:
                source = source.replace('sdhci-ingenic|sdhci-ingenic-x2000)', 'sdhci-ingenic)')
            script = source + '''
SYS=$FIXTURE/sys
modprobe() { echo "module:$*" >> "$FIXTURE/events"; }
ip() { echo "link:$*" >> "$FIXTURE/events"; }
sleep() {
    echo wait >> "$FIXTURE/events"
    if [ "$APPEARS" = 1 ]; then mkdir -p "$SYS/class/net/wlan0"; fi
}
prepare_interface
'''
            result = subprocess.run(['sh', '-c', script], capture_output=True, text=True,
                timeout=5, env=dict(os.environ, Q2_NETWORK_SOURCE_ONLY='1',
                                  FIXTURE=str(root), APPEARS=str(int(appears))))
            events = (root / 'events').read_text().splitlines() if (root / 'events').exists() else []
            return result, events, (target / 'bind').read_text(), (target / 'unbind').read_text()

    def test_actual_driver_reprobes_only_msc1_and_old_policy_fails(self):
        old, _, binding, unbinding = self.run_fixture('sdhci-ingenic-x2000', old=True)
        self.assertNotEqual(old.returncode, 0)
        self.assertIn('unexpected Wi-Fi host driver', old.stderr)
        self.assertEqual((binding, unbinding), ('', ''))
        for name in ['sdhci-ingenic-x2000', 'sdhci-ingenic']:
            with self.subTest(driver=name):
                result, events, binding, unbinding = self.run_fixture(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((binding, unbinding), ('13460000.mmc\n',) * 2)
                self.assertEqual(events, ['module:8733bs', 'wait', 'link:link set wlan0 up'])

    def test_ready_fast_path_has_no_reprobe_or_delay(self):
        result, events, binding, unbinding = self.run_fixture('sdhci-ingenic-x2000', ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['link:link set wlan0 up'])
        self.assertEqual((binding, unbinding), ('', ''))

    def test_kernel_managed_sequence_is_never_rebound(self):
        for appears in [False, True]:
            with self.subTest(appears=appears):
                result, events, binding, unbinding = self.run_fixture(
                    'sdhci-ingenic-x2000', appears=appears, managed=True)
                self.assertEqual(result.returncode == 0, appears, result.stderr)
                self.assertEqual((binding, unbinding), ('', ''))
                self.assertEqual(events.count('wait'), 1 if appears else 10)

    def test_unknown_driver_is_not_touched(self):
        for name in ['unrelated', 'sdhci-ingenic-x2000-extra']:
            with self.subTest(driver=name):
                result, events, binding, unbinding = self.run_fixture(name)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((binding, unbinding), ('', ''))
                self.assertNotIn('wait', events)

    def test_failure_is_bounded_with_one_reprobe(self):
        result, events, binding, unbinding = self.run_fixture('sdhci-ingenic-x2000', appears=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not enumerate', result.stderr)
        self.assertEqual(events.count('wait'), 10)
        self.assertEqual((binding, unbinding), ('13460000.mmc\n',) * 2)


if __name__ == '__main__':
    unittest.main()
