#!/usr/bin/env python3
"""Build an isolated SPL-entry diagnostic from pinned kernel + Buildroot binaries.
No device access. Input is a generated Buildroot target, never a device rootfs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build

import emulator
from compatibility import PROFILES
from ssh_provision import client_public_key, host_identity, write_key

HERE = Path(__file__).resolve().parent
OUT = build.WORK / 'hardware'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--buildroot-target', required=True, type=Path)
    p.add_argument('--authorized-key', required=True, type=Path)
    p.add_argument('--wifi', action='store_true', help='embed manually loaded RTL8733BS and SDIO modules')
    p.add_argument('--updates', action='store_true', help='include manual SD/ota update transport (requires --wifi module profile)')
    p.add_argument('--host-key', type=Path, help='newly generated device-specific Dropbear host key; never an owner private key')
    p.add_argument('--qemu-user', type=Path, default=build.ROOT / 'build/qemu/qemu-mipsel-static',
                   help='QEMU user-mode binary for optional host key validation')
    a = p.parse_args()
    if a.updates and not a.wifi: p.error('--updates requires --wifi')
    profile = ('hardware-wifi-update' if a.updates else 'hardware-wifi') if a.wifi else 'hardware'
    OUT = build.WORK / profile
    source_profile = 'diagnostic-wifi' if a.wifi else 'diagnostic'
    try:
        key = client_public_key(a.authorized_key)
        host_key_bytes, host_key = host_identity(a.host_key, a.buildroot_target, a.qemu_user) if a.host_key else (None, None)
    except ValueError as error:
        p.error(str(error))
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    OUT.chmod(0o700)
    root = OUT / 'root'
    if root.exists(): shutil.rmtree(root)
    for d in ['bin', 'sbin', 'usr/sbin', 'lib', 'usr/lib', 'etc', 'etc/dropbear',
              'root/.ssh', 'dev', 'proc', 'sys', 'run', 'tmp', 'var/run']:
        (root / d).mkdir(parents=True, exist_ok=True)
    os.chmod(root / 'root', 0o700)
    os.chmod(root / 'root/.ssh', 0o700)
    os.chmod(root / 'tmp', 0o1777)
    inputs = {}
    if host_key_bytes is not None:
        write_key(root / 'etc/dropbear/hostkey', host_key_bytes)
        inputs['provisioned_host_identity'] = host_key
    for name in ['bin/busybox', 'usr/sbin/dropbear', 'lib/libc.so.6',
                 'lib/ld-linux-mipsn8.so.1', 'lib/libresolv.so.2', 'usr/lib/libcrypt.so.2']:
        src = a.buildroot_target / name
        inputs[name] = build.sha(src)
        shutil.copyfile(src, root / name)
        os.chmod(root / name, 0o755)
    shutil.copyfile(HERE / 'init', root / 'init')
    shutil.copyfile(HERE / 'usb', root / 'sbin/q2-diagnostic-usb')
    for n in ['init', 'sbin/q2-diagnostic-usb']: os.chmod(root / n, 0o755)
    write_key(root / 'root/.ssh/authorized_keys', key)
    (root / 'etc/passwd').write_text('root:x:0:0:Diagnostic:/root:/bin/sh\n')
    (root / 'etc/shadow').write_text('root:*:0:0:99999:7:::\n')
    os.chmod(root / 'etc/shadow', 0o600)
    (root / 'etc/group').write_text('root:x:0:\n')
    (root / 'etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nshadow: files\nhosts: files\n')
    (root / 'etc/hosts').write_text('127.0.0.1 localhost\n')
    if a.wifi:
        shutil.copyfile(HERE / 'wifi', root / 'sbin/q2-wifi')
        os.chmod(root / 'sbin/q2-wifi', 0o755)
    # Build the on-device updater with the same Buildroot userspace ABI.
    br_out = a.buildroot_target.resolve().parent
    build.run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
        '-v', f'{build.ROOT}:/work', '-v', f'{br_out}:/br:ro', 'q2-os-build',
        '/br/host/bin/mipsel-buildroot-linux-gnu-gcc', '-Os', '-Wall', '-Wextra', '-Werror',
        '/work/os/hardware/update/q2-hw-update.c', '-o',
        '/work/build/os/' + profile + '/root/sbin/q2-hw-update'])
    inputs['q2-hw-update.c'] = build.sha(HERE / 'update/q2-hw-update.c')
    if a.updates:
        shutil.copyfile(HERE / 'update-storage', root / 'sbin/q2-update-storage')
        os.chmod(root / 'sbin/q2-update-storage', 0o755)
    # Stable cpio ordering/ownership/time; no private SSH material in the image.
    def pack_root():
        epoch = build.LOCK['source_date_epoch']
        for item in [root, *root.rglob('*')]: os.utime(item, (epoch, epoch), follow_symlinks=False)
        names = b'\0'.join(str(p.relative_to(root)).encode() for p in sorted(root.rglob('*'))) + b'\0'
        with (OUT / 'root.cpio').open('wb') as f:
            subprocess.run(['cpio', '--null', '-o', '-H', 'newc', '--owner=0:0', '--reproducible'],
                           input=names, stdout=f, cwd=root, check=True)
    pack_root()
    cpio = OUT / 'root.cpio'
    kernel_source, source_inputs, _ = emulator.kernel_source(source_profile, work_label=profile)
    local_patches = {}
    if a.updates:
        # Qualified by the earlier OS reboot tests; no physical expiry guarantee.
        patches = list((HERE.parent / 'patches/kernel').glob('*watchdog*'))
        if len(patches) != 1: raise RuntimeError('expected one watchdog restart patch')
        restart_patch = patches[0]
        applied = subprocess.run(['git', '-C', str(kernel_source), 'apply', '--reverse', '--check', str(restart_patch)], capture_output=True)
        if applied.returncode:
            build.run(['git', '-C', kernel_source, 'apply', restart_patch])
        local_patches[restart_patch.name] = build.sha(restart_patch)
    for patch in sorted((HERE / 'patches').glob('*.patch')):
        applied = subprocess.run(['git', '-C', str(kernel_source), 'apply', '--reverse', '--check', str(patch)], capture_output=True)
        if applied.returncode: build.run(['git', '-C', kernel_source, 'apply', patch])
        local_patches[patch.name] = build.sha(patch)
    # Only this worktree's diagnostic source is modified, never another build.
    shutil.copyfile(HERE / 'diagnostic.dts', kernel_source / 'arch/mips/boot/dts/ingenic/shanling-q2.dts')
    if a.wifi:
        dt = kernel_source / 'arch/mips/boot/dts/ingenic/shanling-q2.dts'
        dt.write_text(dt.read_text() + '\n' + (HERE / 'wifi.dtsi').read_text())
        if a.updates: dt.write_text(dt.read_text() + '\n' + (HERE / 'update.dtsi').read_text())
    out = build.WORK / ('kernel-' + profile)
    out.mkdir(exist_ok=True)
    base = ['make', '-C', '/work/' + str(kernel_source.relative_to(build.ROOT)),
            'O=/work/build/os/kernel-' + profile, 'ARCH=mips', 'CROSS_COMPILE=mipsel-linux-gnu-', '-j2']
    emulator.engine(base + ['shanling_q2_defconfig'])
    script = '/work/' + str((kernel_source / 'scripts/kconfig/merge_config.sh').relative_to(build.ROOT))
    fragments = ['/work/os/hardware/diagnostic.config']
    if a.wifi: fragments.append('/work/os/hardware/wifi.config')
    if a.updates: fragments.append('/work/os/hardware/update.config')
    emulator.engine(['sh', script, '-m', '-O', '/work/build/os/kernel-' + profile,
                     '/work/build/os/kernel-' + profile + '/.config', *fragments])
    cfgscript = '/work/' + str((kernel_source / 'scripts/config').relative_to(build.ROOT))
    emulator.engine([cfgscript, '--file', '/work/build/os/kernel-' + profile + '/.config',
                     '--set-str', 'INITRAMFS_SOURCE', '/work/build/os/' + profile + '/root.cpio'])
    emulator.engine(base + ['olddefconfig', 'vmlinux', 'modules', 'dtbs'])
    module_hashes = {}
    if a.wifi:
        driver = build.ROOT / 'build/rtl8733bs/src'
        if not driver.exists(): build.run(['python3', 'kernel/rtl8733bs/prepare.py'])
        # Same pinned compiler/container and exact configured kernel output.
        emulator.engine(['make', '-C', '/work/build/rtl8733bs/src', '-j2',
                         'ARCH=mips', 'CROSS_COMPILE=mipsel-linux-gnu-',
                         'KSRC=/work/build/os/kernel-' + profile, 'modules'])
        root_in_container = '/work/build/os/' + profile + '/root'
        emulator.engine(base + ['modules_install', 'INSTALL_MOD_PATH=' + root_in_container,
                                'INSTALL_MOD_STRIP=1', 'DEPMOD=true'])
        release = (out / 'include/config/kernel.release').read_text().strip()
        modroot = root / 'lib/modules' / release
        for link in ['build', 'source']:
            (modroot / link).unlink(missing_ok=True)
        (modroot / 'extra').mkdir(exist_ok=True)
        shutil.copyfile(driver / '8733bs.ko', modroot / 'extra/8733bs.ko')
        emulator.engine(['mipsel-linux-gnu-strip', '--strip-debug',
                         root_in_container + '/lib/modules/' + release + '/extra/8733bs.ko'])
        emulator.engine(['depmod', '-b', root_in_container, release])
        module_hashes = {str(m.relative_to(root)): build.sha(m) for m in modroot.rglob('*.ko')}
        pack_root()
    emulator.engine(base + ['uzImage.bin'])
    config = (out / '.config').read_text()
    forbidden = ['MTD', 'MMC_BLOCK', 'I2C', 'SPI', 'DRM', 'FB', 'SOUND', 'PWM', 'DEVMEM']
    if a.updates:
        for opt in ['MTD', 'MMC_BLOCK', 'SPI']: forbidden.remove(opt)
        forbidden += ['MTD_PARTITIONED_MASTER', 'MTD_BLOCK', 'MTD_CMDLINE_PARTS']
    if not a.wifi: forbidden += ['MODULES', 'MMC']
    if a.wifi:
        for opt in ['CFG80211', 'MMC', 'MMC_SDHCI_INGENIC', 'REGULATOR_FIXED_VOLTAGE']:
            if f'CONFIG_{opt}=m' not in config: raise RuntimeError('Wi-Fi must stay modular: ' + opt)
    for opt in forbidden:
        if f'CONFIG_{opt}=y' in config or f'CONFIG_{opt}=m' in config:
            raise RuntimeError('resolved diagnostic config enables ' + opt)
    for opt in ['USB_CONFIGFS_RNDIS', 'USB_DWC2_PERIPHERAL', 'BUILTIN_DTB', 'MIPS_CMDLINE_FROM_DTB']:
        if f'CONFIG_{opt}=y' not in config: raise RuntimeError('missing ' + opt)
    image = out / 'arch/mips/boot/uzImage.bin'
    if image.stat().st_size > 6 * 1024 * 1024: raise RuntimeError('kernel exceeds slot')
    shutil.copyfile(image, OUT / 'xImage')
    (OUT / 'artifact.json').write_text(json.dumps({
        'kernel_commit': PROFILES[source_profile]['origin_commit'], 'kernel_source_inputs_sha256': source_inputs,
        'wifi_modules': module_hashes, 'local_kernel_patches': local_patches, 'buildroot_version': build.LOCK['buildroot']['version'],
        'input_binaries_sha256': inputs, 'initramfs_sha256': build.sha(cpio),
        'authorized_public_key_sha256': hashlib.sha256(key).hexdigest(),
        'builder_sha256': build.sha(__file__),
        'xImage_sha256': build.sha(image), 'xImage_bytes': image.stat().st_size,
        'config_sha256': build.sha(out / '.config'), 'dt_source_sha256': build.sha(kernel_source / 'arch/mips/boot/dts/ingenic/shanling-q2.dts'),
        'container': build.LOCK['kernel_build_container'], 'hardware_qualified': False,
        'self_update_available': a.updates, 'hardware_update_qualified': False,
    }, indent=2) + '\n')
    print((OUT / 'artifact.json').read_text())


if __name__ == '__main__': main()
