"""The install build admits only the pinned stock Bluetooth firmware files and
carries the device-tree patch and kernel option that go with them."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / 'build/firmware-dl/root/firmware/rtlbt'


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

    def test_patch_and_configuration_fragment_exist_and_agree(self):
        patch = (ROOT / self.build.BLUETOOTH_PATCH).read_text()
        self.assertIn('+	pinctrl-0 = <&uart0_pins>;', patch)
        self.assertIn('+		groups = "uart0-data", "uart0-hwflow";', patch)
        self.assertIn('+		compatible = "regulator-output";', patch)
        self.assertIn('+		gpio = <&gpd 20 GPIO_ACTIVE_HIGH>;', patch)
        fragment = (ROOT / 'kernel/bluetooth.config').read_text()
        self.assertIn('CONFIG_REGULATOR_USERSPACE_CONSUMER=y', fragment)
        # The host stack is in q2nd: the kernel's Bluetooth subsystem stays out.
        self.assertFalse([line for line in fragment.splitlines() if line.startswith('CONFIG_BT')])
        # The patch applies to a tree that has exactly the hunk's context.
        with tempfile.TemporaryDirectory(dir=ROOT / 'build/bluetooth-firmware-tests') as directory:
            dts = Path(directory) / 'arch/mips/boot/dts/ingenic/shanling-q2.dts'
            dts.parent.mkdir(parents=True)
            # The end of the root node as patch 0034 leaves it, then the UART nodes of patch 0005.
            dts.write_text('/ {\n\twlan_en: regulator-wlan {\n\t\tvin-supply = <&wlan_bt_en>;\n\t\toff-on-delay-us = <100000>;\n\t};\n};\n\n'
                           '&uart0 {\n\tstatus = "okay";\n};\n\n&uart3 {\n\tstatus = "okay";\n};\n\n&pinctrl {\n};\n')
            subprocess.run(['patch', '-p1', '--fuzz=0', '-s', '-d', directory, '-i', str(ROOT / self.build.BLUETOOTH_PATCH)], check=True)
            self.assertIn('bt-power {', dts.read_text())


if __name__ == '__main__':
    unittest.main()
