"""Root squashfs compression: the recorded build input of every root composer.

A composer packs the root with one named setting and records the name and
its mksquashfs arguments. The kernel that boots the image must read the
compressor: composers that ship their own kernel check its .config; those
that keep the base kernel keep the base image's compressor.

zstd19-128k is the zstd setting: level 19 gives the smallest zstd root (22
is no smaller) at no host decompression cost over lower levels, and 128 KiB
blocks match the kernel's 128 KiB fault-around reads. The default stays
xz-128k until zstd decompression is measured on the device;
`--root-compression zstd19-128k` selects it.
"""
from pathlib import Path
import struct

SETTINGS = {
    'xz-128k': dict(compressor='xz', block=131072, args=['-comp', 'xz', '-b', '131072']),
    'zstd19-128k': dict(compressor='zstd', block=131072,
                        args=['-comp', 'zstd', '-Xcompression-level', '19', '-b', '131072']),
    'gzip-128k': dict(compressor='gzip', block=131072, args=['-comp', 'gzip', '-b', '131072']),
}
DEFAULT = 'xz-128k'
# squashfs superblock compression ids (fs/squashfs/squashfs_fs.h).
IDS = {1: 'gzip', 2: 'lzma', 3: 'lzo', 4: 'xz', 5: 'lz4', 6: 'zstd'}
KCONFIG = {'gzip': 'CONFIG_SQUASHFS_ZLIB', 'lzo': 'CONFIG_SQUASHFS_LZO', 'xz': 'CONFIG_SQUASHFS_XZ',
           'lz4': 'CONFIG_SQUASHFS_LZ4', 'zstd': 'CONFIG_SQUASHFS_ZSTD'}
# NAND root partition: 600 eraseblocks of 128 KiB (0x4b00000 in the Q2 DT).
PARTITION_BYTES = 75 * 1024 * 1024


def setting(name):
    if name not in SETTINGS:
        raise ValueError('unknown root compression: '+str(name)+'; one of '+', '.join(SETTINGS))
    return dict(name=name, **SETTINGS[name])


def args(name):
    return list(setting(name)['args'])


def kernel_reads(name, config_text):
    """Refuse a setting the kernel's squashfs cannot mount."""
    option = KCONFIG[setting(name)['compressor']]
    if option+'=y' not in config_text.splitlines():
        raise ValueError('the kernel lacks '+option+' for root compression '+name)
    return option


def detect(image):
    """(compressor, block size) from a squashfs superblock."""
    with open(image, 'rb') as f:
        head = f.read(96)
    if head[:4] != b'hsqs':
        raise ValueError('not a squashfs image: '+str(image))
    block, = struct.unpack_from('<I', head, 12)
    comp, = struct.unpack_from('<H', head, 20)
    return IDS.get(comp, 'unknown-%d' % comp), block


def of_image(image):
    """The setting that matches an existing root image."""
    found = detect(image)
    for name, value in SETTINGS.items():
        if (value['compressor'], value['block']) == found:
            return name
    raise ValueError('base root uses an unlisted compression: %s/%d' % found)


def verify(image, name):
    """The packed image uses the setting and fits the root partition."""
    value = setting(name)
    if detect(image) != (value['compressor'], value['block']):
        raise ValueError('packed root compression differs from '+name)
    size = Path(image).stat().st_size
    if size > PARTITION_BYTES:
        raise ValueError('root image exceeds the NAND root partition')
    return dict(setting=name, compressor=value['compressor'], block=value['block'],
                mksquashfs_args=value['args'], bytes=size, partition_bytes=PARTITION_BYTES,
                margin_bytes=PARTITION_BYTES-size)
