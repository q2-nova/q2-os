#!/usr/bin/env python3
"""Generated inputs at the installed DHCP hook boundary."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).resolve().parent / 'integration/usr/libexec/q2-network/dhcp'
STATUS = 'wpa_state=COMPLETED\nssid=Generated Guest \nbssid=02:00:00:00:00:01\n'
OPTION = 'https://venue.test/api'.encode().hex()

SCRIPT = r'''
. "$1"
RUN=$2
RESOLV=$RUN/resolv.conf
BOOT_TIMES=$RUN/boot-times
wifi_status() { printf 'wpa\n' >> "$RUN/calls"; printf '%s' "$WPA_FIXTURE"; }
ifconfig() {
    printf 'ifconfig %s\n' "$*" >> "$RUN/calls"
    : > "$RUN/address-visible"
    if [ -f "$RESOLV" ]; then cp "$RESOLV" "$RUN/dns-at-address"; fi
    if [ -f "$RUN/portal-lease" ]; then cp "$RUN/portal-lease" "$RUN/option-at-address"; fi
}
ip() { printf 'ip %s\n' "$*" >> "$RUN/calls"; }
mktemp() {
    printf 'mktemp\n' >> "$RUN/calls"
    [ "$FAULT" != mktemp ] || return 1
    command mktemp "$@"
}
mv() {
    [ "$FAULT" != rename ] || [ "${3:-}" != "$RUN/portal-lease" ] || return 1
    [ "$FAULT" != early_dns ] || [ "${2:-}" != "$RESOLV" ] || [ -f "$RUN/address-visible" ] || return 1
    command mv "$@"
}
rm() {
    [ "$FAULT" != unlink ] || [ "${2:-}" != "$RUN/portal-lease" ] || return 1
    command rm "$@"
}
main "$3"
'''


class CapportHook(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lease = self.root / 'portal-lease'
        self.resolver = self.root / 'resolv.conf'
        self.calls = self.root / 'calls'

    def run_hook(self, action='bound', option=OPTION, status=STATUS, fault=''):
        self.calls.write_text('')
        for name in ('address-visible', 'dns-at-address', 'option-at-address'):
            (self.root / name).unlink(missing_ok=True)
        result = subprocess.run(
            ['/bin/sh', '-c', SCRIPT, 'hook-fixture', HOOK, self.root, action],
            check=True, capture_output=True, text=True, timeout=5,
            env={**os.environ, 'Q2_DHCP_SOURCE_ONLY': '1', 'interface': 'wlan0',
                 'ip': '192.0.2.10', 'subnet': '255.255.255.0', 'router': '192.0.2.1',
                 'dns': '192.0.2.53', 'opt114': option, 'WPA_FIXTURE': status, 'FAULT': fault},
        )
        self.assertEqual(result.stderr, '')
        return self.calls.read_text().splitlines()

    def assert_standard_configuration(self, calls):
        self.assertIn('ifconfig wlan0 192.0.2.10 netmask 255.255.255.0', calls)
        self.assertIn('ip route replace default via 192.0.2.1 dev wlan0', calls)
        self.assertEqual(self.resolver.read_text(), 'nameserver 192.0.2.53\n')

    def test_records_wpa_identity_and_resolver_generation(self):
        calls = self.run_hook()
        self.assert_standard_configuration(calls)
        self.assertEqual(calls[0], 'wpa')
        self.assertEqual(self.lease.read_text().splitlines(),
                         ['q2-capport-v2', '192.0.2.10', str(self.resolver.stat().st_ino),
                          '02:00:00:00:00:01', 'Generated Guest ', OPTION])
        self.assertEqual(self.lease.stat().st_mode & 0o777, 0o600)

    def test_option_and_matching_dns_are_available_at_first_address(self):
        self.assert_standard_configuration(self.run_hook())
        self.assertEqual((self.root / 'option-at-address').read_bytes(), self.lease.read_bytes())
        self.assertEqual((self.root / 'dns-at-address').read_bytes(), self.resolver.read_bytes())

    def test_early_resolver_failure_does_not_prevent_address_and_route(self):
        self.assert_standard_configuration(self.run_hook(fault='early_dns'))
        self.assertFalse((self.root / 'dns-at-address').exists())
        self.assertFalse((self.root / 'option-at-address').exists())
        self.assertEqual(self.lease.read_text().splitlines()[2], str(self.resolver.stat().st_ino))

    def test_no_option_removes_legacy_record_without_querying_wpa_or_allocating_file(self):
        self.lease.write_text(f'192.0.2.10\n{OPTION}\n')
        calls = self.run_hook(action='renew', option='')
        self.assert_standard_configuration(calls)
        self.assertNotIn('wpa', calls)
        self.assertNotIn('mktemp', calls)
        self.assertFalse(self.lease.exists())

    def test_optional_temporary_file_or_rename_failure_preserves_network(self):
        for fault in ('mktemp', 'rename'):
            with self.subTest(fault=fault):
                self.lease.write_text(f'192.0.2.10\n{OPTION}\n')
                calls = self.run_hook(action='renew', fault=fault)
                self.assert_standard_configuration(calls)
                self.assertIn('mktemp', calls)
                self.assertFalse(self.lease.exists())
                self.assertEqual(list(self.root.glob('portal-lease.*')), [])

    def test_undeletable_old_record_does_not_own_new_resolver_generation(self):
        self.run_hook()
        old = self.lease.read_text()
        calls = self.run_hook(action='renew', option='', fault='unlink')
        self.assert_standard_configuration(calls)
        self.assertEqual(self.lease.read_text(), old)
        self.assertNotEqual(str(self.resolver.stat().st_ino), old.splitlines()[2])
        self.assertEqual(self.run_hook(action='deconfig', fault='unlink'), ['ip -4 addr flush dev wlan0'])

    def test_ambiguous_association_or_malformed_option_falls_back(self):
        for status, option in (
            (STATUS + 'ssid=other\n', OPTION),
            (STATUS.replace('COMPLETED', 'ASSOCIATING'), OPTION),
            (STATUS.replace(':01', ':GG'), OPTION),
            (STATUS, 'dead0'),
            (STATUS, 'x0'),
        ):
            with self.subTest(status=status, option=option):
                calls = self.run_hook(status=status, option=option)
                self.assert_standard_configuration(calls)
                self.assertNotIn('mktemp', calls)
                self.assertFalse(self.lease.exists())


if __name__ == '__main__':
    unittest.main()
