"""The install build admits only the pinned stock Bluetooth firmware files, and
the production kernel carries the device tree and kernel option that go with them."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / 'build/firmware-dl/root/firmware/rtlbt'


def production_kernel():
    """A clean checkout of the production Linux revision prepared under build/os, or None."""
    sys.path.insert(0, str(ROOT / 'os'))
    from build import LOCK
    from fork_checkout import verify
    for name in ('install-src', 'fault-kernel-src'):
        path = ROOT / 'build/os' / name
        if not path.is_dir():
            continue
        try:
            verify(path, LOCK['linux_production']['commit'])
        except (OSError, ValueError, subprocess.CalledProcessError):
            continue
        return path
    return None


KERNEL = production_kernel()


def load_build():
    sys.path.insert(0, str(ROOT / 'os/install'))
    spec = importlib.util.spec_from_file_location('q2_install_build', ROOT / 'os/install/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BluetoothFirmware(unittest.TestCase):
    def setUp(self):
        self.build = load_build()
        (ROOT / 'build/bluetooth-firmware-tests').mkdir(parents=True, exist_ok=True)

    def test_generated_files_are_refused_and_nothing_is_installed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/bluetooth-firmware-tests') as directory:
            source, root = Path(directory) / 'source', Path(directory) / 'root'
            source.mkdir()
            for name in self.build.BLUETOOTH_FIRMWARE:
                (source / name).write_bytes(b'generated, not Realtek firmware')
            with self.assertRaisesRegex(RuntimeError, 'unexpected Bluetooth firmware file'):
                self.build.bluetooth_firmware(source, root)
            self.assertFalse((root / 'lib/firmware/rtlbt').exists())

    @unittest.skipUnless(STOCK.is_dir(), 'the stock V1.32 root is not extracted on this host')
    def test_stock_files_are_installed_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/bluetooth-firmware-tests') as directory:
            root = Path(directory) / 'root'
            hashes = self.build.bluetooth_firmware(STOCK, root)
            self.assertEqual(hashes, self.build.BLUETOOTH_FIRMWARE)
            for name in hashes:
                self.assertEqual((root / 'lib/firmware/rtlbt' / name).read_bytes(), (STOCK / name).read_bytes())
                self.assertEqual((root / 'lib/firmware/rtlbt' / name).stat().st_mode & 0o777, 0o644)

    @unittest.skipUnless(KERNEL, 'no prepared production Linux source (os/install/prepare.py --dest build/os/install-src)')
    def test_production_kernel_carries_the_uart_mux_and_enable_hold(self):
        firmware = self.build.bt_firmware
        dts = (KERNEL / firmware.DEVICE_TREE).read_text()
        self.assertIn('pinctrl-0 = <&uart0_pins>;', dts)
        self.assertIn('groups = "uart0-data", "uart0-hwflow";', dts)
        self.assertIn('bt-power {', dts)
        self.assertIn('compatible = "regulator-output";', dts)
        self.assertIn('gpio = <&gpd 20 GPIO_ACTIVE_HIGH>;', dts)
        config = (KERNEL / firmware.DEFCONFIG).read_text().splitlines()
        self.assertIn('CONFIG_REGULATOR_USERSPACE_CONSUMER=y', config)
        # The host stack is in q2nd: the kernel's Bluetooth subsystem stays out.
        self.assertFalse([line for line in config if line.startswith('CONFIG_BT')])

if __name__ == '__main__':
    unittest.main()
