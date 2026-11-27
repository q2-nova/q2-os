#!/usr/bin/env python3
"""Run the pinned QEMU fork's DPU regression using the OS build container."""
import argparse
from pathlib import Path

from build import WORK
from emulator import run_qemu_control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, default=WORK / 'kernel-disk/uImage.gz')
    args = parser.parse_args()
    run_qemu_control('dpu', args.kernel)


if __name__ == '__main__':
    main()
