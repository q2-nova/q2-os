import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import compose_stock as stock
from test_bluetooth_firmware import load_build


class StockBluetoothTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.kit = self.root / 'kit'
        self.options = SimpleNamespace(without_bluetooth_firmware=False,
                                       bluetooth_source=None, bluetooth_cache=self.root / 'cache',
                                       bluetooth_offline=False, bluetooth_zip=None)
        self.assets = self.root / 'release'
        self.platform = {'sdk': 'generated'}
        self.data = {name: name.encode() for name in stock.fetch_firmware.BLUETOOTH_FIRMWARE}
        self.pins = {name: hashlib.sha256(data).hexdigest() for name, data in self.data.items()}
        change = patch.dict(stock.fetch_firmware.BLUETOOTH_FIRMWARE, self.pins, clear=True)
        change.start()
        self.addCleanup(change.stop)

    def fetch(self, source, out, cache, offline, local_zip):
        self.assertEqual((source, cache, offline, local_zip),
                         (stock.fetch_firmware.DEFAULT_SOURCE, self.options.bluetooth_cache, False, None))
        out.mkdir()
        for name, data in self.data.items():
            (out / name).write_bytes(data)
        (out / 'provenance.json').write_text(json.dumps(stock.fetch_firmware.provenance(source)))

    def radio_kernel(self, assets, out, platform):
        self.assertEqual((assets, platform), (self.assets, self.platform))
        (out / 'kernel').mkdir()
        manifest = out / 'kernel/kernel-base.json'
        manifest.write_text('{}')
        record = {'profile': 'keyless-stock-wifi', 'platform': platform, 'wifi': 'generated',
                  'kernel_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
                  'bluetooth_firmware': stock.bluetooth_inputs(out / 'bluetooth')}
        (out / stock.STOCK_MANIFEST).write_text(json.dumps(record))

    def prepare(self):
        with patch.object(stock.fetch_firmware, 'run', side_effect=self.fetch) as fetch, \
                patch.object(stock, 'prepare_radio_kernel', side_effect=self.radio_kernel):
            stock.prepare(self.assets, self.kit, self.platform, self.options)
        return fetch

    def compose(self):
        opts = SimpleNamespace(stock_inputs=self.kit, player_source=self.root, tsrs_source=self.root,
                               tsrsd=self.root, app=self.root / 'q2nd', qemu_user=None, release_tag='V0.01')
        with patch.object(stock, 'wifi_inputs', return_value='generated'), \
                patch.object(stock, 'verify_base'), patch.object(stock, 'restore', return_value=self.root), \
                patch.object(stock, 'WORK', self.root), patch.object(stock, 'run') as run:
            stock.compose(opts, self.assets, self.root / 'image', self.platform)
        return run.call_args.args[0]

    def test_prepared_pair_is_bound_and_passed_to_normal_image(self):
        self.prepare().assert_called_once()
        command = self.compose()
        self.assertEqual(command[command.index('--bluetooth-firmware') + 1], self.kit / 'bluetooth')
        (self.kit / 'bluetooth/rtl8733bs_fw').write_bytes(b'wrong')
        with self.assertRaises(stock.fetch_firmware.FetchError):
            self.compose()

    def test_explicit_omission_does_not_fetch_or_install_firmware(self):
        self.options.without_bluetooth_firmware = True
        self.prepare().assert_not_called()
        self.assertNotIn('--bluetooth-firmware', self.compose())

    def test_failure_does_not_publish_a_partial_kit(self):
        with patch.object(stock.fetch_firmware, 'run', side_effect=self.fetch), \
                patch.object(stock, 'prepare_radio_kernel', side_effect=ValueError('failed')):
            with self.assertRaisesRegex(ValueError, 'failed'):
                stock.prepare(self.assets, self.kit, self.platform, self.options)
        self.assertFalse(self.kit.exists())
        self.assertEqual(list(self.root.iterdir()), [])


@unittest.skipUnless(os.environ.get('Q2_BT_FETCHED'), 'set Q2_BT_FETCHED to a checked firmware extraction')
class RealStockBluetoothTest(unittest.TestCase):
    def test_exact_pair_and_source_notice_are_installed(self):
        source = Path(os.environ['Q2_BT_FETCHED'])
        build = load_build()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes, provenance = build.stock_bluetooth_firmware(source, root)
            self.assertEqual(hashes, build.BLUETOOTH_FIRMWARE)
            for name, expected in hashes.items():
                payload = root / 'lib/firmware/rtlbt' / name
                self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(), expected)
            notice = root / 'usr/share/doc/q2-os/rtl8733bs-provenance.json'
            self.assertEqual(json.loads(notice.read_text()), provenance)
