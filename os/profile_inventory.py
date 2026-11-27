#!/usr/bin/env python3
"""Read explicit build inputs only; report artifact bytes, never runtime RAM.

No directory traversal, device access, deployment or build-tree mutation. Config
values are restricted to build symbols relevant to this audit, not dumped.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

SYMBOLS = '''CONFIG_CC_OPTIMIZE_FOR_SIZE CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE
CONFIG_DEBUG_INFO_NONE CONFIG_DEBUG_INFO_DWARF4 CONFIG_DEBUG_INFO_DWARF5
CONFIG_LTO_NONE CONFIG_LTO_CLANG CONFIG_LTO_CLANG_FULL CONFIG_LTO_CLANG_THIN
CONFIG_MEM_ALLOC_PROFILING CONFIG_KFENCE CONFIG_DAMON CONFIG_AUDIT CONFIG_KSM
CONFIG_TRANSPARENT_HUGEPAGE CONFIG_PSI CONFIG_ZRAM CONFIG_SWAP CONFIG_MODULES
CONFIG_KALLSYMS CONFIG_KALLSYMS_ALL CONFIG_IKCONFIG CONFIG_IKCONFIG_PROC
CONFIG_DEBUG_KERNEL CONFIG_DEBUG_FS CONFIG_FTRACE CONFIG_FUNCTION_TRACER
CONFIG_PRINTK CONFIG_MAGIC_SYSRQ CONFIG_KEXEC CONFIG_WATCHDOG CONFIG_PROC_FS
CONFIG_SYSFS CONFIG_PREEMPT CONFIG_SMP CONFIG_INITRAMFS_COMPRESSION_NONE
CONFIG_KERNEL_XZ BR2_ENABLE_DEBUG BR2_STRIP_strip BR2_STRIP_none BR2_OPTIMIZE_2
BR2_OPTIMIZE_S BR2_INIT_BUSYBOX BR2_INIT_SYSTEMD BR2_PACKAGE_DROPBEAR
BR2_REPRODUCIBLE'''.split()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config_inventory(data):
    symbols = {}
    for line in data.decode('utf-8').splitlines():
        match = re.fullmatch(r'(CONFIG_\w+|BR2_\w+)=(.*)', line)
        unset = re.fullmatch(r'# (CONFIG_\w+|BR2_\w+) is not set', line)
        if match:
            key, value = match.groups()
        elif unset:
            key, value = unset.group(1), 'n'
        else:
            continue
        if key in SYMBOLS:
            if key in symbols and symbols[key] != value:
                raise ValueError('conflicting build symbol: ' + key)
            symbols[key] = value
    if not symbols:
        raise ValueError('no recognized build symbols')
    # Missing and explicitly disabled are distinct: sparse fragments are not
    # resolved configurations and unsupported architecture options may be absent.
    return {key: symbols.get(key) for key in sorted(SYMBOLS)}


def elf_inventory(data):
    """Section inventory for ELF32/64 LE/BE, with allocation-content fingerprint.

    Allocated section bytes include init memory and embedded rescue. They are
    not permanent resident memory or a substitute for /proc measurements.
    """
    if data[:4] != b'\x7fELF':
        return None
    if len(data) < 16 or data[4] not in (1, 2) or data[5] not in (1, 2):
        raise ValueError('unsupported ELF encoding')
    endian = '<' if data[5] == 1 else '>'
    wide = data[4] == 2
    header_format = endian + ('HHIQQQIHHHHHH' if wide else 'HHIIIIIHHHHHH')
    section_format = endian + ('IIQQQQIIQQ' if wide else 'IIIIIIIIII')

    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('truncated ELF table')
        return struct.unpack_from(fmt, data, offset)

    header = unpack(header_format, 16)
    offset, size, count, names_index = header[5], header[10], header[11], header[12]
    if not count or names_index == 0xffff:
        raise ValueError('ELF extended/absent section table unsupported')
    if size < struct.calcsize(section_format) or names_index >= count:
        raise ValueError('invalid ELF section table')
    sections = [unpack(section_format, offset + index * size) for index in range(count)]

    def contents(section):
        start, length = section[4], section[5]
        if start + length > len(data):
            raise ValueError('truncated ELF section')
        return data[start:start + length]

    names = contents(sections[names_index])
    allocated = hashlib.sha256()
    rows = []
    for section in sections[1:]:
        name_index, kind, flags, address, _, length = section[:6]
        if name_index >= len(names) or b'\0' not in names[name_index:]:
            raise ValueError('invalid ELF section name')
        name = names[name_index:].split(b'\0', 1)[0].decode('utf-8', errors='replace')
        payload = b'' if kind == 8 else contents(section)  # SHT_NOBITS
        rows.append({'name': name, 'bytes': length, 'file_bytes': len(payload),
                     'allocated': bool(flags & 2), 'nobits': kind == 8})
        if flags & 2:
            allocated.update(json.dumps([name, kind, flags, address, length],
                                        separators=(',', ':')).encode())
            allocated.update(payload)
    return {'class': 64 if wide else 32, 'endian': 'little' if endian == '<' else 'big',
            'machine': header[1], 'allocated_sha256': allocated.hexdigest(),
            'allocated_file_bytes': sum(x['file_bytes'] for x in rows if x['allocated']),
            'allocated_nobits_bytes': sum(x['bytes'] for x in rows if x['allocated'] and x['nobits']),
            'nonallocated_file_bytes': sum(x['file_bytes'] for x in rows if not x['allocated']),
            'sections': rows}


def inventory(profile, configs, artifacts):
    result = {'schema': 1, 'profile': profile, 'measurement': 'host artifact inventory; not runtime RAM',
              'configs': {}, 'artifacts': {}}
    for category, paths in (('configs', configs), ('artifacts', artifacts)):
        for label, path in paths:
            if label in result[category]:
                raise ValueError('duplicate label: ' + label)
            # Explicit regular-file inputs; reject symlinks to prevent accidental
            # aliasing to owner/device state. No implicit manifest/root scanning.
            if path.is_symlink() or not path.is_file():
                raise ValueError('input must be an explicit regular file: ' + str(path))
            data = path.read_bytes()
            entry = {'bytes': len(data), 'sha256': digest(data)}
            if category == 'configs':
                entry['symbols'] = config_inventory(data)
            else:
                entry['elf'] = elf_inventory(data)
            result[category][label] = entry
    return result


def named_file(value):
    label, sep, filename = value.partition('=')
    if not sep or not re.fullmatch(r'[A-Za-z0-9_.-]+', label) or label in ('.', '..') or not filename:
        raise argparse.ArgumentTypeError('use LABEL=PATH')
    return label, Path(filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('development', 'production', 'baseline'), required=True,
                        help='inventory label only; does not change build flags')
    parser.add_argument('--config', type=named_file, action='append', default=[])
    parser.add_argument('--artifact', type=named_file, action='append', default=[])
    args = parser.parse_args()
    try:
        result = inventory(args.profile, args.config, args.artifact)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
