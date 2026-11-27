#!/usr/bin/env python3
"""Prepare generated WPA2/transition/SAE AP fixtures and a verified target overlay.

No radio, network, device or owner data access. The caller starts hostapd only
after the primary coordinates a physical test. Fixture secrets stay in files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(tools, out, interface, country, channel):
    manifest = json.loads((tools / 'artifact.json').read_text())
    if manifest.get('profile') != 'WPA2/WPA3 SAE with PMF, static OpenSSL EC':
        raise ValueError('an explicit SAE/PMF tools build is required')
    if manifest.get('builder_sha256') != sha(ROOT / 'os/hardware/build-wifi-tools.py'):
        raise ValueError('tools were not built with the current builder')
    for name in ('wpa_supplicant', 'wpa_cli'):
        binary = tools / 'bin' / name
        if sha(binary) != manifest['binaries'][name]:
            raise ValueError('target binary hash mismatch')
        elf = binary.read_bytes()[:20]
        if elf[:6] != b'\x7fELF\x01\x01' or elf[18:20] != b'\x08\x00':
            raise ValueError('target binary is not little-endian ELF32 MIPS')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface):
        raise ValueError('invalid generated AP interface name')
    if not re.fullmatch(r'[A-Z]{2}', country) or channel not in range(1, 12):
        raise ValueError('use a real two-letter country and channel 1 through 11')
    out.mkdir(mode=0o700, parents=True)  # Preserve an existing session verbatim.
    overlay = out / 'overlay/usr/sbin'
    overlay.mkdir(parents=True)
    for name in ('wpa_supplicant', 'wpa_cli'):
        shutil.copy2(tools / 'bin' / name, overlay / name)
    nonce = secrets.token_hex(3)
    results = []
    for mode, keys, pmf in [('wpa2', 'WPA-PSK', 0),
                            ('transition', 'WPA-PSK SAE', 1), ('sae', 'SAE', 2)]:
        directory = out / mode
        directory.mkdir(mode=0o700)
        ssid = f'Q2-{mode}-{nonce}'
        password = 'q2-' + secrets.token_hex(16)
        config = (f'interface={interface}\ndriver=nl80211\ncountry_code={country}\n'
                  f'hw_mode=g\nchannel={channel}\nssid={ssid}\n'
                  f'wpa=2\nwpa_key_mgmt={keys}\nrsn_pairwise=CCMP\n'
                  f'ieee80211w={pmf}\nwpa_passphrase={password}\n'
                  'logger_syslog=0\nlogger_stdout=0\n')
        if mode != 'wpa2':
            config += 'sae_pwe=2\nsae_require_mfp=1\n'
        (directory / 'hostapd.conf').write_text(config)
        client = ('ctrl_interface=/run/q2-wpa3/ctrl\nupdate_config=0\nnetwork={\n'
                  f'    ssid={ssid.encode().hex()}\n    proto=RSN\n'
                  f'    key_mgmt={keys}\n    ieee80211w={pmf}\n')
        if mode != 'sae':
            client += f'    psk="{password}"\n'
        if mode != 'wpa2':
            client += f'    sae_password="{password}"\n    sae_pwe=2\n'
        (directory / 'wpa.conf').write_text(client + '}\n')
        (directory / 'client.json').write_text(json.dumps({
            'ssid': ssid, 'password': password,
            'security': {'wpa2': 'wpa2', 'transition': 'wpa2-wpa3', 'sae': 'wpa3'}[mode],
            'expected_key_mgmt': 'WPA2-PSK' if mode == 'wpa2' else 'SAE',
            'expected_pairwise_cipher': 'CCMP', 'pmf': pmf,
        }, indent=2) + '\n')
        results.append({'mode': mode, 'ssid': ssid, 'expected_pmf': pmf,
                        'expected_negotiation': 'WPA2-PSK' if mode == 'wpa2' else 'SAE',
                        'associated': False, 'dhcp': False, 'transfer': False,
                        'reconnected_after_ap_restart': False})
    payload = out / 'payload.bin'
    payload.write_bytes(bytes(range(256)) * 16384)
    record = {'fixture_only': True, 'hardware_qualified': False,
              'tools': manifest, 'cases': results,
              'payload': {'bytes': payload.stat().st_size, 'sha256': sha(payload)},
              'overlay': {str(p.relative_to(out / 'overlay')): sha(p)
                          for p in overlay.iterdir()}}
    (out / 'qualification.json').write_text(json.dumps(record, indent=2) + '\n')
    for path in out.rglob('*'):
        if path.is_file():
            path.chmod(0o755 if path.parent == overlay else 0o600)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wifi-tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ap-interface', default='q2fixture0')
    parser.add_argument('--country', required=True)
    parser.add_argument('--channel', type=int, default=6)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'build'):
        parser.error('output must be in this checkout\'s ignored build/')
    os.umask(0o077)
    record = prepare(args.wifi_tools.resolve(), out, args.ap_interface,
                     args.country, args.channel)
    print('Prepared three generated AP cases and verified MIPS tools; no radio operation.')
    print('Transfer payload SHA256: ' + record['payload']['sha256'])


if __name__ == '__main__':
    main()
