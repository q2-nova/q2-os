#!/usr/bin/env python3
"""microSD and SPI NAND fault recovery of the production kernel; emulation only.

Runs the fault kernel (os/kernel_faults.py) on generated media and injects
faults through q2ctl. Each suite writes its logs and result.json under
build/os/kernel-faults/runs/. docs/kernel-faults.md lists every check.

  python3 os/test_storage_faults.py [--suite sd|nand|all] [--container IMAGE]
"""
import argparse
import subprocess
import uuid

import kernel_faults
from kernel_faults import Guest, RUNS

MIB = 1 << 20
# microSD: an exFAT volume over the first 32 MiB, raw areas beyond it.
SD_BYTES, SD_VOLUME, SD_READ, SD_WRITE, SD_AREA = 64 * MIB, 32 * MIB, 48 * MIB, 52 * MIB, 65536
SD_FAULTS = ('command-timeout', 'data-crc', 'data-timeout')
CARD = '/dev/mmcblk0'
# SPI NAND: DS35Q1GA geometry and the production DT's userdata partition.
PAGE, SPARE, PAGES_PER_BLOCK = 2048, 64, 64
RAW_PAGE = PAGE + SPARE
CHIP_PAGES = 65536
USERDATA = 0x6440000 // (PAGE * PAGES_PER_BLOCK)
DATA = b'\xa5' * PAGE + b'\xff' * SPARE
ERASED = b'\xff' * RAW_PAGE


def sd_image(path):
    """64 MiB card: exFAT over 32 MiB, 0xa5 in both raw areas."""
    with path.open('wb') as f:
        f.truncate(SD_VOLUME)
    subprocess.run(['mkfs.exfat', '-L', 'Q2FAULT', str(path)], check=True, capture_output=True)
    with path.open('r+b') as f:
        f.truncate(SD_BYTES)
        for offset in (SD_READ, SD_WRITE):
            f.seek(offset)
            f.write(b'\xa5' * SD_AREA)


def sd_area(path):
    with path.open('rb') as f:
        f.seek(SD_WRITE)
        return f.read(SD_AREA)


def nand_page(block):
    """Absolute page number of page 0 of a userdata block."""
    return (USERDATA + block) * PAGES_PER_BLOCK


def nand_image(path):
    """Erased DS35Q1GA image with data pages in userdata blocks 0, 1 and 3."""
    with path.open('wb') as f:
        block = ERASED * PAGES_PER_BLOCK
        for _ in range(CHIP_PAGES // PAGES_PER_BLOCK):
            f.write(block)
        for b in (0, 1, 3):
            f.seek(nand_page(b) * RAW_PAGE)
            f.write(DATA * PAGES_PER_BLOCK)


def raw_page(path, page):
    with path.open('rb') as f:
        f.seek(page * RAW_PAGE)
        return f.read(RAW_PAGE)


def io_pattern(offset):
    """nandfault.c pattern(): the io case's data at an offset from block 8."""
    return (offset * 17 + (offset >> 8) * 37 + (offset >> 17) * 53) & 255


def mtd_device(listing, name):
    """/dev/mtdN for the MTD whose sysfs name is `name` in lines of
    `mtdN NAME` (the read-only mtdNro aliases are ignored)."""
    found = [line.split(' ', 1)[0] for line in listing.splitlines()
             if line.startswith('mtd') and line.split(' ', 1)[-1].strip() == name
             and line.split(' ', 1)[0][3:].isdigit()]
    if len(found) != 1:
        raise AssertionError(f'expected one MTD named {name}, found {found}')
    return '/dev/' + found[0]


def check(guest, label, condition):
    if not condition:
        raise AssertionError(label)
    guest.result['checks'].append(label)


def sd_suite(args):
    RUNS.mkdir(parents=True, exist_ok=True)
    media = RUNS / f'sd-{uuid.uuid4().hex[:10]}.img'
    sd_image(media)
    guest = Guest('sd', kernel_dir=args.kernel_dir, qemu_dir=args.qemu_dir, sd=media, container=args.container)
    try:
        guest.start()
        sd = lambda command='': guest.control(('sd ' + command).strip())  # noqa: E731
        guest.probe('baseline direct read', 'sdfault', 'read', CARD)
        guest.run('exFAT file created', f'mount -t exfat {CARD} /mnt/card && echo generated-sd-fixture > /mnt/card/probe '
                  '&& sync && umount /mnt/card')
        for fault in SD_FAULTS:
            before = sd()['hits']
            sd(f'fault {fault} once')
            guest.probe(f'{fault}: one read fault is retried', 'sdfault', 'read', CARD)
            state = sd()
            check(guest, f'{fault}: the retry consumed the one-shot fault', state == {'fault': 0, 'hits': before + 1})
            sd(f'fault {fault} on')
            guest.probe(f'{fault}: a persistent read fault returns EIO', 'sdfault', 'read-fails', CARD)
            check(guest, f'{fault}: the driver retried before failing', sd()['hits'] > before + 1)
            sd('clear')
            guest.probe(f'{fault}: reads recover after clear', 'sdfault', 'read', CARD)
            guest.probe(f'{fault}: write area restored', 'sdfault', 'restore', CARD)
            check(guest, f'{fault}: write area holds the restore pattern', sd_area(media) == b'\xa5' * SD_AREA)
            sd(f'fault {fault} on')
            guest.probe(f'{fault}: a persistent write fault returns EIO', 'sdfault', 'write-fails', CARD)
            check(guest, f'{fault}: the failed write left the card unchanged', sd_area(media) == b'\xa5' * SD_AREA)
            sd('clear')
            before = sd()['hits']
            sd(f'fault {fault} once')
            guest.probe(f'{fault}: one write fault is retried', 'sdfault', 'write', CARD)
            guest.probe(f'{fault}: the retried write reads back', 'sdfault', 'verify', CARD)
            check(guest, f'{fault}: the write retry consumed the one-shot fault', sd() == {'fault': 0, 'hits': before + 1})
            check(guest, f'{fault}: the retried write reached the card', sd_area(media) == b'\x5a' * SD_AREA)
        guest.run('exFAT remounts with the file intact', f'mount -t exfat {CARD} /mnt/card && '
                  'test "$(cat /mnt/card/probe)" = generated-sd-fixture && umount /mnt/card')
        guest.result['fault_status'] = sd()
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()
    fsck = subprocess.run(['fsck.exfat', '-n', str(media)], capture_output=True, text=True)
    (guest.out / 'fsck.log').write_text(fsck.stdout + fsck.stderr)
    if fsck.returncode:
        raise AssertionError(f'offline fsck.exfat found errors; see {guest.out / "fsck.log"}')
    media.unlink()


def nand_suite(args):
    RUNS.mkdir(parents=True, exist_ok=True)
    media = RUNS / f'nand-{uuid.uuid4().hex[:10]}.img'
    nand_image(media)
    guest = Guest('nand', kernel_dir=args.kernel_dir, qemu_dir=args.qemu_dir, nand=media,
                  container=args.container, paused=True)
    try:
        guest.start()
        nand = lambda command: guest.control('nand ' + command)  # noqa: E731
        # Armed before the first instruction, so no driver state predates them.
        for command in (f'ecc {nand_page(0)} corrected', f'ecc {nand_page(1)} uncorrectable',
                        f'program {nand_page(2)} on', f'erase {USERDATA + 3} on', f'bad {USERDATA + 4}'):
            nand(command)
        guest.result['checks'].append('faults armed before boot')
        guest.resume()
        listing = guest.run('MTD names', 'for d in /sys/class/mtd/mtd*; do echo "${d##*/} $(cat $d/name)"; done')
        device = mtd_device(listing, 'userdata')
        guest.result['device'] = device
        guest.probe('erase, program and unaligned reads', 'nandfault', 'io', device)
        check(guest, 'block 8 erased again', all(raw_page(media, nand_page(8) + p) == ERASED for p in range(PAGES_PER_BLOCK)))
        programmed = all(raw_page(media, nand_page(9) + p)[:PAGE] == bytes(
            io_pattern((PAGES_PER_BLOCK + p) * PAGE + i) for i in range(PAGE)) for p in range(PAGES_PER_BLOCK))
        check(guest, 'block 9 holds the programmed pattern on the medium', programmed)
        guest.probe('ECC, program, erase and bad-block faults reported', 'nandfault', 'inject', device)
        check(guest, 'corrected page unchanged on the medium', raw_page(media, nand_page(0)) == DATA)
        check(guest, 'uncorrectable page unchanged on the medium', raw_page(media, nand_page(1)) == DATA)
        check(guest, 'failed program left the page erased', raw_page(media, nand_page(2)) == ERASED)
        check(guest, 'failed erase left the block programmed', raw_page(media, nand_page(3)) == DATA)
        check(guest, 'host bad-block marker on the medium', raw_page(media, nand_page(4))[PAGE] == 0)
        check(guest, 'MEMSETBADBLOCK wrote a marker', raw_page(media, nand_page(5))[PAGE] == 0)
        nand('clear')
        guest.probe('clean reads, program and erase after clear', 'nandfault', 'recover', device)
        check(guest, 'program after clear reached the medium', raw_page(media, nand_page(2)) == b'\x3c' * PAGE + b'\xff' * SPARE)
        check(guest, 'erase after clear reached the medium', raw_page(media, nand_page(3)) == ERASED)
        check(guest, 'bad-block marker kept', raw_page(media, nand_page(4))[PAGE] == 0)
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()
    media.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--suite', choices=('sd', 'nand', 'all'), default='all')
    kernel_faults.guest_arguments(parser)
    args = parser.parse_args()
    if args.suite in ('sd', 'all'):
        sd_suite(args)
    if args.suite in ('nand', 'all'):
        nand_suite(args)


if __name__ == '__main__':
    main()
