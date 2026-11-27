"""The network helper's first-scan channel hint (initial_freq_list via -I):
`down` records the associated channel, `up` passes it for the first scan
only. Generated fixtures; never reads owner configuration."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'os/install/integration/usr/libexec/q2-network/wifi'


def run(actions, carrier='1', channel='100', last=None, saved='ctrl_interface=/run/wpa_supplicant\n'):
    base = ROOT / 'build/wifi-fast-reassoc-tests'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as d:
        root = Path(d)
        net = root / 'sys/class/net/wlan0'; net.mkdir(parents=True)
        (net / 'carrier').write_text(carrier + '\n')
        proc = root / 'proc/net/rtl8733bs/wlan0'; proc.mkdir(parents=True)
        if channel is not None:
            (proc / 'ap_info').write_text(f'cur_channel={channel}, cur_bwmode=0(20MHz), cur_ch_offset=0\n')
        runp = root / 'run'; runp.mkdir()
        if last is not None: (runp / 'last-freq').write_text(last + '\n')
        state = root / 'state'; state.mkdir()
        (state / 'wpa.conf').write_text(saved)
        script = HELPER.read_text() + '''
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
RUN=$FIXTURE/run
STATE=$FIXTURE/state
WPA=wpa
wpa() { echo "wpa $*" >> "$FIXTURE/events"; }
''' + '\n'.join(actions) + '\n'
        r = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=5,
                           env=dict(os.environ, Q2_NETWORK_SOURCE_ONLY='1', FIXTURE=str(root)))
        read = lambda p: p.read_text() if p.exists() else None
        return r, read(runp / 'last-freq'), read(runp / 'initial.conf'), read(root / 'events'), read(state / 'wpa.conf')


class FastReassoc(unittest.TestCase):
    def test_records_the_associated_channel_as_a_frequency(self):
        for ch, freq in (('1', '2412'), ('6', '2437'), ('13', '2472'), ('14', '2484'), ('36', '5180'), ('100', '5500'), ('165', '5825')):
            with self.subTest(channel=ch):
                r, last, _, _, _ = run(['record_channel'], channel=ch)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(last, freq + '\n')

    def test_records_nothing_without_a_link_or_a_sane_channel(self):
        for carrier, ch in (('0', '6'), ('1', None), ('1', 'x'), ('1', '0'), ('1', '200')):
            with self.subTest(carrier=carrier, channel=ch):
                r, last, _, _, _ = run(['record_channel'], carrier=carrier, channel=ch)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIsNone(last)

    def test_overlay_only_for_a_valid_frequency(self):
        r, _, conf, _, _ = run(['initial_overlay || :'], last='5500')
        self.assertEqual(conf, 'initial_freq_list=5500\n')
        for bad in ('', 'abc', '100', '9000', '5500 2412'):
            with self.subTest(last=bad):
                r, _, conf, _, _ = run(['initial_overlay || :'], last=bad)
                self.assertIsNone(conf)

    def test_supplicant_start_passes_the_overlay_and_drops_a_leaked_line(self):
        start = ['if grep -q "^initial_freq_list=" "$STATE/wpa.conf"; then sed -i "/^initial_freq_list=/d" "$STATE/wpa.conf"; fi',
                 'if initial_overlay; then "$WPA" -c "$STATE/wpa.conf" -I "$RUN/initial.conf"; else "$WPA" -c "$STATE/wpa.conf"; fi']
        # Exercise the helper's own up() lines through the real function below.
        r, _, conf, events, saved = run(['initial_overlay && "$WPA" -I "$RUN/initial.conf" || "$WPA"'], last='2437',
                                        saved='ctrl_interface=/run/wpa_supplicant\ninitial_freq_list=5500\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-I', events)
        self.assertTrue(start)
        src = HELPER.read_text()
        self.assertIn('sed -i \'/^initial_freq_list=/d\' "$STATE/wpa.conf"', src, 'up drops a leaked value before start')
        self.assertIn('-I "$RUN/initial.conf"', src)
        self.assertIn('    record_channel\n    stop_owned "$RUN/dhcp.pid" udhcpc', src, 'down records the channel before stopping the link')


if __name__ == '__main__':
    unittest.main()
