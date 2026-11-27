#!/usr/bin/env python3
"""Materialize the exact public Linux revision for installed images."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'os'))
from build import LOCK
from fork_checkout import checkout


def prepare(destination):
    if not destination.is_relative_to(ROOT / 'build'):
        raise ValueError('kernel source destination must be under this checkout’s build directory')
    kernel = LOCK['linux_production']
    checkout(destination, kernel['commit'], 'Q2_LINUX_REPO', kernel['url'])
    return kernel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.dest.resolve()), indent=2))
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
