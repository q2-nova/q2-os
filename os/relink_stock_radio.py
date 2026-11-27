#!/usr/bin/env python3
"""Relink Q2 Wi-Fi userspace with a replacement static libnl build."""
import argparse
import json
from pathlib import Path
import subprocess

PREFIX = 'mipsel-buildroot-linux-gnu-'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True, help='relocated SDK root')
    parser.add_argument('--libnl', type=Path, required=True, help='directory containing libnl-3.a and libnl-genl-3.a')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    out, sdk, libnl = (path.resolve() for path in (args.out, args.sdk, args.libnl))
    for name in ('libnl-3.a', 'libnl-genl-3.a'):
        if not (libnl / name).is_file():
            parser.error(f'missing static library {name}')
    record = json.loads((root / 'radio-source.json').read_text())
    out.mkdir(parents=True)
    for tool in ('wpa_supplicant', 'hostapd', 'iw'):
        command = [item.replace('{libnl}', str(libnl)) for item in record['links'][tool]]
        command[command.index('-o') + 1] = str(out / tool)
        subprocess.run([sdk / 'bin' / (PREFIX + 'gcc'), *command], cwd=root, check=True)
        subprocess.run([sdk / 'bin' / (PREFIX + 'strip'), out / tool], check=True)


if __name__ == '__main__':
    main()
