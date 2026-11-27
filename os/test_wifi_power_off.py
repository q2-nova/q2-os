"""The network helper powers the Wi-Fi chip off on `down` (MSC1 unbind) and back
on before the interface is needed. Generated sysfs fixtures only."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/usr/libexec/q2-network/wifi'


class WifiPowerOff(unittest.TestCase):
    def run_fixture(self, actions, driver='sdhci-ingenic-x2000', bound=True, iface=True, pwrseq=True):
        base = ROOT / 'build/wifi-power-off-tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            target = root / 'sys/bus/platform/drivers' / driver
            target.mkdir(parents=True)
            for name in ['bind', 'unbind']:
                (target / name).write_text('')
            device = root / 'sys/bus/platform/devices/13460000.mmc'
            device.mkdir(parents=True)
            (device / 'of_node').mkdir()
            if pwrseq: (device / 'of_node/mmc-pwrseq').write_bytes(b'\0\0\0\1')
            msc2 = root / 'sys/bus/platform/devices/13470000.mmc'
            msc2.mkdir(parents=True)
            (msc2 / 'driver').symlink_to(target)
            if bound: (device / 'driver').symlink_to(target)
            if iface: (root / 'sys/class/net/wlan0').mkdir(parents=True)
            script = HELPER.read_text() + '''
SYS=$FIXTURE/sys
RUN=$FIXTURE/run
modprobe() { echo "module:$*" >> "$FIXTURE/events"; }
ip() { echo "ip:$*" >> "$FIXTURE/events"; }
sleep() { kernel; echo wait >> "$FIXTURE/events"; mkdir -p "$SYS/class/net/wlan0"; }
stop_owned() { :; }
# The kernel's side of a bind/unbind, after the helper's write.
kernel() {
    if [ -s "$DRV/unbind" ]; then rm -f "$SYS/bus/platform/devices/13460000.mmc/driver"; rm -rf "$SYS/class/net/wlan0"; : > "$DRV/unbind"; echo unbound >> "$FIXTURE/events"; fi
    if [ -s "$DRV/bind" ]; then ln -s "$DRV" "$SYS/bus/platform/devices/13460000.mmc/driver"; : > "$DRV/bind"; echo bound >> "$FIXTURE/events"; fi
}
''' + '\n'.join(f'{a}; kernel' for a in actions) + '\n'
            result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=5,
                                    env=dict(os.environ, Q2_NETWORK_SOURCE_ONLY='1', FIXTURE=str(root), DRV=str(target)))
            events = (root / 'events').read_text().splitlines() if (root / 'events').exists() else []
            return result, events, (msc2 / 'driver').exists()

    def test_down_powers_the_chip_off_and_never_touches_msc2(self):
        for driver in ['sdhci-ingenic-x2000', 'sdhci-ingenic']:
            with self.subTest(driver=driver):
                result, events, msc2 = self.run_fixture(['down'], driver=driver)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events, ['ip:-4 addr flush dev wlan0', 'ip:link set wlan0 down', 'unbound'])
                self.assertTrue(msc2, 'MSC2 (microSD) stays bound')

    def test_down_twice_and_down_while_off_are_harmless(self):
        result, events, _ = self.run_fixture(['down', 'down'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events.count('unbound'), 1)

    def test_prepare_powers_on_then_waits_for_the_interface(self):
        result, events, _ = self.run_fixture(['down', 'prepare_interface'])
        self.assertEqual(result.returncode, 0, result.stderr)
        # The module is already loaded on the device, so modprobe is a no-op.
        self.assertEqual(events[events.index('unbound') + 1:], ['module:8733bs', 'bound', 'wait', 'ip:link set wlan0 up'])

    def test_power_on_skips_the_compat_reprobe_without_pwrseq(self):
        # Kernels without the power-sequence provider reprobe MSC1 once when the
        # interface is missing; right after power_on that would undo the bind.
        result, events, _ = self.run_fixture(['down', 'prepare_interface'], pwrseq=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events.count('bound'), 1)
        self.assertEqual(events.count('unbound'), 1, 'only the power-off unbind')

    def test_unknown_host_driver_is_left_powered(self):
        result, events, _ = self.run_fixture(['down'], driver='unrelated')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('unbound', events)

    def test_ready_interface_is_not_rebound(self):
        result, events, _ = self.run_fixture(['prepare_interface'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events, ['ip:link set wlan0 up'])


if __name__ == '__main__':
    unittest.main()
