"""Shared rescue and production kernel builder for local images and Q2 OS releases."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from build import LOCK, ROOT, WORK, run, sha
from fork_checkout import verify
from install import stock_regdb

HERE = Path(__file__).resolve().parent
HW = ROOT / 'os/hardware'
SUPPORT_DIRS = ('bin', 'sbin', 'lib', 'usr/lib', 'usr/sbin', 'dev', 'proc', 'sys',
                'run', 'tmp', 'root', 'etc', 'mnt/mmc', 'mnt/userdata',
                'state', 'newroot')


def inside(path):
    return '/work/' + str(Path(path).absolute().relative_to(ROOT))


def engine(args, image='q2-qemu-build'):
    run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
         '-v', f'{ROOT}:/work', '-w', '/work', image, *args])


def copy(source, target, mode=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        target.unlink()
    shutil.copyfile(source, target)
    if mode is not None:
        target.chmod(mode)


def prepare_rescue(target, rescue, output, ssh, *, regdb=None):
    for name in SUPPORT_DIRS:
        (rescue / name).mkdir(parents=True, exist_ok=True)
    for name in ('bin/busybox', 'usr/sbin/dropbear', 'lib/libc.so.6',
                 'lib/ld-linux-mipsn8.so.1', 'lib/libresolv.so.2', 'usr/lib/libcrypt.so.2'):
        copy(target / name, rescue / name, 0o755)
    for name in ('passwd', 'shadow', 'group', 'nsswitch.conf', 'hosts'):
        copy(target / 'etc' / name, rescue / 'etc' / name, 0o600 if name == 'shadow' else 0o644)
    (rescue / 'bin/sh').symlink_to('busybox')
    copy(HERE / 'bootstrap', rescue / 'init', 0o755)
    copy(HW / 'usb', rescue / 'sbin/q2-diagnostic-usb', 0o755)
    ssh.stage(rescue)
    copy(HERE / 'diagnostic-ssh', rescue / 'sbin/q2-diagnostic-ssh', 0o755)
    if regdb is not None:
        stock_regdb.install(rescue, regdb)
    epoch = LOCK['source_date_epoch']
    paths = sorted(rescue.rglob('*'))
    for path in (rescue, *paths):
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    names = b'\0'.join(str(x.relative_to(rescue)).encode() for x in paths) + b'\0'
    with output.open('xb') as archive:
        subprocess.run(['cpio', '--null', '-o', '-H', 'newc', '--owner=0:0', '--reproducible'],
                       input=names, stdout=archive, cwd=rescue, check=True)


def build_kernel(src, out, root, *, hardware=False):
    pin = LOCK['linux_production']
    verify(src, pin['commit'])
    kout = out / 'kernel'
    kout.mkdir()
    make = ['make', '-s', '-C', inside(src), 'O=' + inside(kout), 'ARCH=mips',
            'CROSS_COMPILE=mipsel-linux-gnu-', '-j2']
    engine(make + [pin['defconfig']])
    engine([inside(src / 'scripts/config'), '--file', inside(kout / '.config'),
            '--set-str', 'INITRAMFS_SOURCE', inside(out / 'rescue.cpio')])
    engine(make + ['olddefconfig', 'uzImage.bin', 'modules', 'dtbs'])
    engine(make + ['modules_install', 'INSTALL_MOD_PATH=' + inside(root),
                   'INSTALL_MOD_STRIP=1', 'DEPMOD=true'])
    release = (kout / 'include/config/kernel.release').read_text().strip()
    modroot = root / 'lib/modules' / release
    for name in ('build', 'source'):
        (modroot / name).unlink(missing_ok=True)
    if hardware:
        driver = out / 'rtl8733bs'
        run(['python3', ROOT / 'kernel/rtl8733bs/prepare.py', '--dest', driver])
        engine(['make', '-s', '-C', inside(driver), '-j2', 'ARCH=mips',
                'CROSS_COMPILE=mipsel-linux-gnu-', 'KSRC=' + inside(kout), 'modules'])
        copy(driver / '8733bs.ko', modroot / 'extra/8733bs.ko')
        engine(['mipsel-linux-gnu-strip', '--strip-debug', inside(modroot / 'extra/8733bs.ko')])
    engine(['depmod', '-b', inside(root), release])
    copy(kout / 'arch/mips/boot/uzImage.bin', out / 'xImage')
    copy(kout / 'arch/mips/boot/dts/ingenic/shanling-q2.dtb', out / 'shanling-q2.dtb')
    config = (kout / '.config').read_text()
    for option in ('MTD_BLOCK_SKIPBAD', 'MTD_UBI', 'UBIFS_FS', 'SQUASHFS', 'USB_CONFIGFS_RNDIS', 'SMP'):
        if f'CONFIG_{option}=y' not in config:
            raise ValueError('missing required built-in: ' + option)
    for option in ('MTD_PARTITIONED_MASTER', 'MTD_CMDLINE_PARTS', 'MTD_BLOCK'):
        if f'CONFIG_{option}=y' in config or f'CONFIG_{option}=m' in config:
            raise ValueError('unwanted MTD interface: ' + option)
    verify(src, pin['commit'])
    return release, modroot, kout / '.config'


def build_kernel_container():
    qemu = LOCK['qemu_installed']
    repo = qemu['url'].removeprefix('https://github.com/').removesuffix('.git')
    url = 'https://raw.githubusercontent.com/' + repo + '/' + qemu['commit'] + '/tests/q2/Containerfile'
    WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK, prefix='kernel-container-') as directory:
        recipe = Path(directory) / 'Containerfile'
        run(['curl', '--fail', '--location', '--retry', '3', url, '-o', recipe])
        if sha(recipe) != qemu['containerfile_sha256']:
            raise ValueError('public QEMU kernel container recipe digest differs')
        run(['podman', 'build', '-t', 'q2-qemu-build', '-f', recipe, directory])
    image = subprocess.check_output(
        ['podman', 'image', 'inspect', 'q2-qemu-build', '--format', '{{.Id}}'], text=True).strip()
    return 'sha256:' + image.removeprefix('sha256:')
