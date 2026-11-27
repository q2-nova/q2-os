#!/usr/bin/env python3
"""Validate generated WPA2/transition/SAE profiles on native wpa_supplicant.

Uses the none driver, so it never associates or touches a real Wi-Fi interface.
Actual association/DHCP/transfer/reconnect require a generated AP on hardware.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--supplicant', type=Path, required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/wifi-security-test')
    a = p.parse_args()
    out = a.output.resolve()
    if not out.is_relative_to(ROOT / 'build'):
        p.error('output must be under build/')
    out.mkdir(parents=True, exist_ok=True)
    ctrl = out / 'ctrl'
    ctrl.mkdir(exist_ok=True)
    config = out / 'wpa.conf'
    config.write_text(f'ctrl_interface={ctrl}\nupdate_config=1\n')
    local = out / 'client.sock'
    local.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(local))
    sock.settimeout(3)

    def command(value):
        sock.send(value.encode())
        reply = sock.recv(65536).decode().strip()
        if reply.startswith('FAIL') or reply.startswith('UNKNOWN'):
            raise RuntimeError('supplicant refused generated profile setting')
        return reply

    process = None
    try:
        with (out / 'supplicant.log').open('w') as log:
            process = subprocess.Popen([str(a.supplicant), '-Dnone', '-ilo', '-c', str(config)], stdout=log, stderr=log)
            deadline = time.monotonic() + 5
            while not (ctrl / 'lo').exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('supplicant did not start; see generated log')
                time.sleep(0.05)
            sock.connect(str(ctrl / 'lo'))
            # The none driver deliberately advertises no radio capabilities;
            # this test verifies the real parser/control/save implementation.
            for mode, keys, pmf in [('wpa2', 'WPA-PSK', 0), ('wpa2-wpa3', 'SAE WPA-PSK', 1), ('wpa3', 'SAE', 2)]:
                ident = int(command('ADD_NETWORK'))
                fields = {'ssid': f'"Generated-{mode}"', 'proto': 'WPA RSN' if mode == 'wpa2' else 'RSN',
                    'key_mgmt': keys, 'ieee80211w': str(pmf)}
                if mode != 'wpa3':
                    fields['psk'] = '"generated-only-password"'
                if mode != 'wpa2':
                    fields['sae_password'] = '"generated-only-password"'
                    fields['sae_pwe'] = '2'
                for name, value in fields.items():
                    assert command(f'SET_NETWORK {ident} {name} {value}') == 'OK'
                assert set(command(f'GET_NETWORK {ident} key_mgmt').split()) == set(keys.split())
                assert command(f'GET_NETWORK {ident} ieee80211w') == str(pmf)
            assert command('SAVE_CONFIG') == 'OK'
            assert command('RECONFIGURE') == 'OK'
            assert len(command('LIST_NETWORKS').splitlines()) == 4
            (out / 'result.json').write_text(json.dumps({'result': 'pass',
                'profiles': ['wpa2', 'wpa2-wpa3', 'wpa3'], 'driver': 'none',
                'association_tested': False, 'hardware_qualified': False}, indent=2) + '\n')
            print('PASS: WPA2, transition and SAE/PMF profiles accepted, saved and reloaded')
    finally:
        sock.close()
        local.unlink(missing_ok=True)
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    main()
