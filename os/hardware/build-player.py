#!/usr/bin/env python3
"""Build a RAM-only full-userspace hardware qualification image.

Inputs must be a generated Buildroot target and generated qualification SSH
keys. Prepare --kernel-source with kernel/prepare.sh first. No device access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from ssh_provision import client_public_key, host_identity, write_key

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'os/hardware'

def run(args, **kw):
    subprocess.run([str(a) for a in args], check=True, **kw)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def hardware_bootargs(board, boot_cpus):
    """Replace our generated override too when reusing a prepared source tree."""
    args = 'console=ttyS3,115200 clk_ignore_unused panic=10 q2.hardware=1'
    if boot_cpus == 1:
        args += ' maxcpus=1'
    assignment = f'bootargs = "{args}";'
    if 'q2.hardware=1' in board:
        board, count = re.subn(r'bootargs\s*=\s*"[^"\n]*\bq2\.hardware=1\b[^"\n]*"\s*;',
                              lambda _: assignment, board)
        if count != 1:
            raise ValueError('expected exactly one hardware bootargs override')
        return board
    return board + '\n/ { chosen { ' + assignment + ' }; };\n&sfc { status = "disabled"; };\n'

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', type=Path, required=True)
    p.add_argument('--buildroot-target', type=Path, required=True)
    p.add_argument('--authorized-key', type=Path, required=True)
    p.add_argument('--host-key', type=Path, required=True)
    p.add_argument('--qemu-user', type=Path, default=ROOT / 'build/qemu/qemu-mipsel-static',
                   help='qemu-user binary used to validate the host key with target Dropbear')
    p.add_argument('--wifi-tools', type=Path, help='output of build-wifi-tools.py; also build the pinned Wi-Fi module')
    p.add_argument('--player-binary', type=Path, help='explicit generated mainline q2nd artifact; does not modify the base target')
    p.add_argument('--platform-library', type=Path, help='explicit generated libq2platform.so artifact; does not modify the base target')
    p.add_argument('--boot-cpus', type=int, choices=(1, 2), default=2,
                   help='online CPU count at boot (default: 2); 1 retains the diagnostic fallback')
    p.add_argument('--output', type=Path, default=ROOT / 'build/os/player-hardware')
    a = p.parse_args()
    src, out = a.kernel_source.resolve(), a.output.resolve()
    for path in (src, out):
        if not path.is_relative_to(ROOT / 'build'):
            p.error('source and output must be under this repository build/')
    try:
        key = client_public_key(a.authorized_key)
        host_key_bytes, host_key = host_identity(a.host_key, a.buildroot_target, a.qemu_user)
    except ValueError as error:
        p.error(str(error))
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.chmod(0o700)
    patches = []
    for number in ('0002', '0003', '0004', '0006'):
        patches += list((ROOT / 'kernel/ram-handoff/patches').glob(number + '-*.patch'))
    patches += sorted((HERE / 'patches').glob('*.patch'))
    patches += list((ROOT / 'os/patches/kernel').glob('*watchdog*.patch'))
    for patch in patches:
        done = subprocess.run(['git', '-C', src, 'apply', '--reverse', '--check', patch], capture_output=True)
        if done.returncode:
            run(['git', '-C', src, 'apply', patch])
    # Generic pinctrl part of the qualified sleep-clock patch. The normal
    # board DT differs from the diagnostic board used in the original patch.
    clock_patch = next((ROOT / 'kernel/ram-handoff/patches').glob('0007-*.patch'))
    clock = clock_patch.read_bytes().split(b'--- a/arch/mips/boot/dts/')[0]
    done = subprocess.run(['git', '-C', src, 'apply', '--reverse', '--check'], input=clock, capture_output=True)
    if done.returncode: run(['git', '-C', src, 'apply'], input=clock)
    dt = src / 'arch/mips/boot/dts/ingenic/shanling-q2.dts'
    board = dt.read_text().replace(' earlycon ', ' ')
    if 'clk32k_pins:' not in board:
        board += '\n&pinctrl { clk32k_pins: clk32k-pins { function = "clk32k"; groups = "clk32k"; }; };\n&msc1 { pinctrl-0 = <&msc1_pins &clk32k_pins>; };\n'
    board = hardware_bootargs(board, a.boot_cpus)
    dt.write_text(board)
    root = out / 'root'
    if root.exists(): shutil.rmtree(root)
    shutil.copytree(a.buildroot_target, root, symlinks=True)
    for source, dest in ((a.player_binary, 'usr/bin/q2nd'),
                         (a.platform_library, 'usr/lib/libq2platform.so')):
        if source is not None:
            source = source.resolve()
            if not source.is_relative_to(ROOT / 'build') or not source.is_file():
                p.error('application overrides must be generated artifacts under build/')
            with source.open('rb') as f:
                if f.read(6) != b'\x7fELF\x01\x01':
                    p.error('application override must be a little-endian ELF32 artifact')
            path = root / dest
            if path.is_symlink(): path.unlink()
            shutil.copyfile(source, path)
            os.chmod(path, 0o755)
    for name in ('root/.ssh', 'etc/dropbear'):
        path = root / name
        if path.is_symlink(): path.unlink()
        path.mkdir(parents=True, exist_ok=True)
    for name in ('dev', 'proc', 'sys', 'run', 'tmp', 'state', 'mnt/mmc', 'etc/q2-hardware'):
        (root / name).mkdir(parents=True, exist_ok=True)
    for relative, data in [('root/.ssh/authorized_keys', key),
                           ('etc/q2-hardware/hostkey', host_key_bytes)]:
        path = root / relative
        path.unlink(missing_ok=True)
        write_key(path, data)
    os.chmod(root / 'root/.ssh', 0o700)
    for source, dest in [('player-init', 'init'), ('usb', 'sbin/q2-diagnostic-usb'),
                         ('usb-storage', 'sbin/q2-usb-storage'),
                         ('player-bringup', 'sbin/q2-player-bringup')]:
        path = root / dest
        if path.is_symlink(): path.unlink()
        shutil.copyfile(HERE / source, path)
        os.chmod(path, 0o755)
    # Defaults contain generated fixture credentials only. Network fixtures
    # are served on the host USB address during qualification.
    if a.wifi_tools:
        for name in ('wpa_supplicant', 'wpa_cli'):
            shutil.copyfile(a.wifi_tools / 'bin' / name, root / 'usr/sbin' / name)
            os.chmod(root / 'usr/sbin' / name, 0o755)
    config = (ROOT / 'os/overlay/etc/q2-defaults/mainline.toml').read_text()
    config = config.replace('10.0.2.100:8080', '192.168.77.1:4533')
    (root / 'etc/q2-defaults/hardware-fixture.toml').write_text(config)
    (root / 'etc/q2-release.json').write_text(json.dumps({'profile': 'hardware-ram-qualification', 'hardware_qualified': False}) + '\n')
    names = b'\0'.join(str(x.relative_to(root)).encode() for x in sorted(root.rglob('*'))) + b'\0'
    with (out / 'root.cpio').open('wb') as f:
        run(['cpio', '--null', '-o', '-H', 'newc', '--owner=0:0', '--reproducible'], input=names, stdout=f, cwd=root)
    def inside(path): return '/work/' + str(path.relative_to(ROOT))
    def engine(args):
        run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
             '-v', f'{ROOT}:/work', '-w', '/work', 'q2-qemu-build', *args])
    kout = out / 'kernel'
    kout.mkdir(exist_ok=True)
    make = ['make', '-s', '-C', inside(src), 'O=' + inside(kout), 'ARCH=mips', 'CROSS_COMPILE=mipsel-linux-gnu-', '-j12']
    if not (kout / '.config').exists(): engine(make + ['shanling_q2_defconfig'])
    engine(['sh', inside(src / 'scripts/kconfig/merge_config.sh'), '-m', '-O', inside(kout), inside(kout / '.config'), '/work/os/hardware/player.config'])
    engine([inside(src / 'scripts/config'), '--file', inside(kout / '.config'), '--set-str', 'INITRAMFS_SOURCE', inside(out / 'root.cpio')])
    engine(make + ['olddefconfig', 'vmlinux', 'modules', 'dtbs'])
    engine(make + ['modules_install', 'INSTALL_MOD_PATH=' + inside(root), 'INSTALL_MOD_STRIP=1', 'DEPMOD=true'])
    release = (kout / 'include/config/kernel.release').read_text().strip()
    modroot = root / 'lib/modules' / release
    for link in ('build', 'source'): (modroot / link).unlink(missing_ok=True)
    if a.wifi_tools:
        driver = ROOT / 'build/rtl8733bs/player-src'
        if not driver.exists(): run(['python3', ROOT / 'kernel/rtl8733bs/prepare.py', '--dest', driver])
        engine(['make', '-s', '-C', inside(driver), '-j8', 'ARCH=mips', 'CROSS_COMPILE=mipsel-linux-gnu-', 'KSRC=' + inside(kout), 'modules'])
        (modroot / 'extra').mkdir(exist_ok=True)
        shutil.copyfile(driver / '8733bs.ko', modroot / 'extra/8733bs.ko')
        engine(['mipsel-linux-gnu-strip', '--strip-debug', inside(modroot / 'extra/8733bs.ko')])
    engine(['depmod', '-b', inside(root), release])
    names = b'\0'.join(str(x.relative_to(root)).encode() for x in sorted(root.rglob('*'))) + b'\0'
    with (out / 'root.cpio').open('wb') as f:
        run(['cpio', '--null', '-o', '-H', 'newc', '--owner=0:0', '--reproducible'], input=names, stdout=f, cwd=root)
    engine(make + ['vmlinux'])
    config = (kout / '.config').read_text()
    if '\nCONFIG_MTD=' in config: raise RuntimeError('RAM qualification must not expose NAND')
    for opt in ('BLK_DEV_LOOP', 'EXT4_FS', 'EXFAT_FS'):
        if f'CONFIG_{opt}=y' not in config: raise RuntimeError('missing built-in: ' + opt)
    if 'CONFIG_USB_CONFIGFS_MASS_STORAGE=y' not in config:
        raise RuntimeError('USB mass-storage configfs function missing')
    if 'CONFIG_SMP=y' not in config or 'CONFIG_NR_CPUS=2' not in config:
        raise RuntimeError('hardware qualification requires the two-CPU SMP kernel')
    for opt in ('I2C', 'DRM_INGENIC_X2000', 'SND_SOC_SHANLING_Q2'):
        if f'CONFIG_{opt}=m' not in config: raise RuntimeError('not modular: ' + opt)
    shutil.copyfile(kout / 'vmlinux', out / 'vmlinux')
    shutil.copyfile(kout / 'arch/mips/boot/dts/ingenic/shanling-q2.dtb', out / 'shanling-q2.dtb')
    (out / 'artifact.json').write_text(json.dumps({
        'profile': 'RAM-only application qualification', 'hardware_qualified': False,
        'boot_cpus': a.boot_cpus,
        'bootstrap_host_key': host_key,
        'kernel_series': {x.name: sha(x) for x in (ROOT / 'kernel/patches').glob('*.patch')},
        'hardware_patches': {str(x.relative_to(ROOT)): sha(x) for x in patches + [clock_patch]},
        'artifacts': {n: sha(out / n) for n in ('vmlinux', 'shanling-q2.dtb', 'root.cpio')},
        'player_sha256': sha(root / 'usr/bin/q2nd'), 'config_sha256': sha(kout / '.config'),
        'builder_sha256': sha(__file__), 'init_sha256': sha(HERE / 'player-init'),
        'bringup_sha256': sha(HERE / 'player-bringup'),
        'usb_storage_sha256': sha(HERE / 'usb-storage'),
        'platform_library_sha256': sha(root / 'usr/lib/libq2platform.so'),
        'wifi_tools': json.loads((a.wifi_tools / 'artifact.json').read_text()) if a.wifi_tools else None,
        'modules': {str(x.relative_to(root)): sha(x) for x in modroot.rglob('*.ko')},
    }, indent=2) + '\n')

if __name__ == '__main__': main()
