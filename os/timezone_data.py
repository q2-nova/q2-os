#!/usr/bin/env python3
"""Validate the IANA data required by the player's public timezone choices."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def required_zones():
    table = ROOT / "os/timezones/zone.tab"
    ids = [line.split("\t")[2] for line in table.read_text().splitlines()
           if line and not line.startswith("#")]
    return sorted(["Etc/UTC", *ids])


def validate(root):
    directory=Path(root)/'usr/share/zoneinfo'
    hashes={}
    for zone in required_zones():
        path=directory/zone
        try:
            resolved=path.resolve(strict=True)
            if not resolved.is_relative_to(directory.resolve()):raise ValueError('zone leaves root')
            data=resolved.read_bytes()
            if not data.startswith(b'TZif'):raise ValueError('not a TZif file')
        except (OSError,ValueError) as error:
            raise ValueError(f'missing/invalid timezone data for {zone}; enable BR2_TARGET_TZ_INFO and rebuild tzdata') from error
        hashes[zone]=hashlib.sha256(data).hexdigest()
    return hashes


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    print(json.dumps({'timezone_data':validate(args.root)},indent=2))
