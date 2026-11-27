#!/usr/bin/env python3
"""Build an isolated install candidate. Never opens hardware or stages/flashes it."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HW = ROOT / 'os/hardware'
sys.path.insert(0, str(HW))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'os'))
import root_compression
from package import uimage, squashfs
from ssh_provision import InstalledSsh
from timezone_data import validate as validate_timezone_data
from usb_runtime import validate as validate_usb_runtime
from packaging_profile import production as production_packaging
import portal_artifact
import runtime_resources
import startup_read_ahead
import tsrs_artifact
import portal_phone_runtime
import x2000_c
from kernel_build import SUPPORT_DIRS, build_kernel, copy, engine, inside, prepare_rescue
from kernel_base import install_base, source_commit
from build import LOCK
from fork_checkout import verify

def run(args, **kw):
    subprocess.run([str(x) for x in args], check=True, **kw)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

OVERLAY = ROOT / 'os/overlay'

def apply_overlay(root):
    """Install this checkout's os/overlay over a copied Buildroot target.

    Buildroot applies the overlay only when it builds the target, so a target
    built at an earlier commit carries that commit's service scripts. The
    post-build script writes no overlay path, so a second copy here gives the
    files a fresh Buildroot build would install.
    """
    shutil.copytree(OVERLAY, root, dirs_exist_ok=True, symlinks=True)

import bluetooth_firmware as bt_firmware
BLUETOOTH_FIRMWARE = bt_firmware.FILES

def bluetooth_firmware(directory, root):
    """Install the two verified files; return their hashes for the manifest."""
    return bt_firmware.install(directory, root)


def stock_bluetooth_firmware(directory, root):
    sys.path.insert(0, str(ROOT / 'tools'))
    import fetch_firmware

    source = fetch_firmware.admit(directory)
    hashes = bluetooth_firmware(directory, root)
    notice = root / 'usr/share/doc/q2-os/rtl8733bs-provenance.json'
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_text(json.dumps(source, indent=2) + '\n')
    return hashes, source


def toolchain_identity(repo):
    """patch_identity() of a q2-toolchain checkout's tools/x2000_toolchain.py:
    the X2000 compiler sources that checkout pins."""
    path = Path(repo) / 'tools/x2000_toolchain.py'
    if not path.is_file():
        raise ValueError(f'{repo}: not a q2-toolchain checkout')
    spec = importlib.util.spec_from_file_location('x2000_toolchain', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch_identity()


def install_x2000_c(result_dir, root, toolchain_repo):
    """Replace GCC-built programs in `root` with the tested X2000-clang builds
    (os/x2000_c.py), built with the compiler `toolchain_repo` pins."""
    return x2000_c.install(result_dir, root, toolchain_identity(toolchain_repo))


# The image ships exactly one Tailscale daemon: tailscale-rs by default, the Go
# tailscaled as a build option (os/install/README.md).
TAILSCALE_DAEMONS = ('rs', 'go')


def tailscale_inputs_refusal(daemon, tsrsd, tailscale_dir):
    """Why these inputs cannot build an image with exactly `daemon`, or None."""
    if daemon == 'rs':
        if tsrsd is None: return '--tailscale rs (the default) needs --tsrsd'
        if tailscale_dir is not None: return '--tailscale-dir belongs with --tailscale go: the image ships one daemon'
    elif daemon == 'go':
        if tailscale_dir is None: return '--tailscale go needs --tailscale-dir'
        if tsrsd is not None: return '--tsrsd belongs with --tailscale rs: the image ships one daemon'
    else:
        return '--tailscale is rs or go'
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    kernel = p.add_mutually_exclusive_group(required=True)
    kernel.add_argument('--kernel-source', type=Path, help='clean public Linux source from os/install/prepare.py')
    kernel.add_argument('--kernel-base', type=Path, help='tagged keyless kernel and matched release assets')
    p.add_argument('--kernel-platform-assets', type=Path, help='tagged SDK/platform inputs for a keyless stock kernel kit')
    p.add_argument('--buildroot-target', type=Path, required=True)
    p.add_argument('--authorized-key', type=Path, help='client public key outside Git: a personal image, or a generated '
                   'key for test_boot.py guest SSH with --qemu-fixture')
    p.add_argument('--host-key', type=Path, help='override the generated host identity with an external Dropbear private key')
    p.add_argument('--qemu-user', type=Path, default=ROOT / 'build/qemu/qemu-mipsel-static',
                   help='QEMU user-mode binary for host key generation and validation with target Dropbear')
    p.add_argument('--app', type=Path, required=True, help='q2nd from a paired q2nd/q2-portal target build')
    p.add_argument('--player-source', type=Path, required=True, help='clean player checkout matching the paired artifact receipt')
    p.add_argument('--platform-library', type=Path, help='defaults to the Buildroot target libq2platform.so')
    p.add_argument('--wifi-tools', type=Path, help='required for installed hardware Wi-Fi, omitted in QEMU fixture')
    p.add_argument('--qemu-fixture', action='store_true', help='local QEMU package with no hardware Wi-Fi or Bluetooth payload, '
                   'keyless unless --authorized-key')
    p.add_argument('--tailscale', choices=TAILSCALE_DAEMONS, default='rs',
                   help='the one Tailscale daemon the image ships: rs (tailscale-rs, the default; needs --tsrsd) '
                        'or go (the Go tailscaled and CLI; needs --tailscale-dir)')
    p.add_argument('--tsrsd', type=Path, help='standalone tsrs Q2 build output directory (see os/install/README.md)')
    p.add_argument('--tsrs-source', type=Path, help='clean checkout of the selected tsrs release tag')
    p.add_argument('--tailscale-dir', type=Path, help='--tailscale go: staged tailscale and tailscaled binaries with TAILSCALE.json')
    p.add_argument('--integration-overlay', type=Path, help='generated installed service/config integration overlay')
    p.add_argument('--x2000-c', type=Path,
                   help='os/x2000-c-build.py output for this Buildroot tree: its X2000-clang programs replace the GCC ones in the '
                        'installed root where their output tests passed and their GCC control is the installed file '
                        '(os/x2000_c.py). The rescue image keeps GCC. Needs --toolchain-repo.')
    p.add_argument('--toolchain-repo', type=Path,
                   help='with --x2000-c: the q2-toolchain checkout whose pinned X2000 compiler built it')
    p.add_argument('--root-compression', choices=sorted(root_compression.SETTINGS), default=root_compression.DEFAULT,
                   help='root SquashFS setting; the built kernel must read it')
    p.add_argument('--bluetooth-firmware', type=Path,
                   help='locally sourced hardware Bluetooth firmware; never a QEMU fixture input')
    p.add_argument('--release-tag', default='unreleased', help='candidate release identity, matching its eventual update package')
    p.add_argument('--output', type=Path, default=ROOT / 'build/os/install-candidate')
    p.add_argument('--packaging-profile', choices=('development', 'production'), default='development',
                   help='development keeps original binaries; production archives Tailscale debug originals off-device')
    p.add_argument('--strip-tool', type=Path, help='host-runnable MIPS GNU strip; required for production packaging')
    a = p.parse_args()
    try:
        ssh = InstalledSsh(a.authorized_key, a.host_key, a.buildroot_target, a.qemu_user)
    except ValueError as error:
        p.error(str(error))
    if a.qemu_fixture and (a.wifi_tools or a.bluetooth_firmware or a.integration_overlay):
        p.error('--qemu-fixture uses the maintained local service overlay, with no hardware radio payload or emulator shell')
    stock_base = a.kernel_base is not None and not a.qemu_fixture
    if stock_base and (a.kernel_platform_assets is None or a.authorized_key):
        p.error('a stock kernel kit requires matching platform assets and a keyless image')
    if not a.qemu_fixture and a.wifi_tools is None:
        p.error('installed hardware builds require --wifi-tools')
    refusal = tailscale_inputs_refusal(a.tailscale, a.tsrsd, a.tailscale_dir)
    if refusal: p.error(refusal)
    tsrsd = None
    if a.tailscale == 'rs':
        try:
            selected_tsrs = None
            if a.tsrs_source is not None:
                source = a.tsrs_source.resolve()
                selected_tsrs = subprocess.check_output(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip()
                verify(source, selected_tsrs)
            tsrs_binary, tsrsd = tsrs_artifact.admit(a.tsrsd, selected_tsrs)
        except (OSError, ValueError) as error:
            p.error(str(error))
    if a.x2000_c is not None and a.toolchain_repo is None:
        p.error('--x2000-c needs --toolchain-repo')
    if a.packaging_profile == 'production' and a.strip_tool is None:
        p.error('--strip-tool is required for production packaging')
    try:
        portal_binary, portal, app_commit, app_sha = portal_artifact.paired_full_build(a.app.absolute())
        app_source = a.player_source.resolve()
        verify(app_source, app_commit)
        portal_radio = portal_phone_runtime.admit(a.wifi_tools) if a.wifi_tools else None
    except (OSError, ValueError) as error:
        p.error(str(error))
    validate_timezone_data(a.buildroot_target)
    src = a.kernel_source.resolve() if a.kernel_source is not None else None
    out = a.output.resolve()
    for path in (out,) if src is None else (src, out):
        if not path.is_relative_to(ROOT / 'build') or any(part.startswith('player-hardware') for part in path.parts):
            p.error('use isolated source/output under build/, never player-hardware')
    if src is not None and (src == out or src.is_relative_to(out) or out.is_relative_to(src)):
        p.error('source and output must not overlap')
    br = a.buildroot_target.resolve().parent
    if out.exists(): p.error('choose a fresh --output directory')
    out.mkdir(parents=True, mode=0o700 if a.authorized_key is not None else 0o755)
    source_record = LOCK['linux_production']
    root = out / 'root'
    shutil.copytree(a.buildroot_target, root, symlinks=True)
    apply_overlay(root)
    for name in SUPPORT_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    copy(HW / 'usb', root / 'sbin/q2-diagnostic-usb', 0o755)
    copy(HERE / 'root-init', root / 'sbin/q2-installed-init', 0o755)
    copy(HERE / 'state', root / 'sbin/q2-state', 0o755)
    copy(HW / 'player-bringup', root / 'sbin/q2-player-bringup', 0o755)
    copy(HW / 'usb-storage', root / 'sbin/q2-usb-storage', 0o755)
    copy(ROOT / 'os/platform/usb-status-control', root / 'usr/sbin/q2-usb-status-control', 0o755)
    copy(ROOT / 'os/platform/usb-auto', root / 'usr/sbin/q2-usb-auto', 0o755)
    copy(a.app, root / 'usr/bin/q2nd', 0o755)
    portal_artifact.install(root, portal_binary, portal)
    runtime_resources.install(root, app_source)
    for source_name, installed_name in (('LICENSE', 'SONIC-LICENSE'), ('UPSTREAM.json', 'SONIC-UPSTREAM.json')):
        copy(app_source / 'crates/q2nd/vendor/sonic' / source_name,
             root / 'usr/share/licenses/q2nd' / installed_name, 0o644)
    if a.platform_library:
        copy(a.platform_library, root / 'usr/lib/libq2platform.so', 0o755)
    if a.wifi_tools:
        for name in ('wpa_supplicant', 'wpa_cli'):
            copy(a.wifi_tools / 'bin' / name, root / 'usr/sbin' / name, 0o755)
    tailscale = None
    if a.tailscale == 'go':
        tailscale = json.loads((a.tailscale_dir / 'TAILSCALE.json').read_text())
        for name in ('tailscale', 'tailscaled'):
            source = a.tailscale_dir / name
            if sha(source) != tailscale['files'][name]['q2_sha256']: raise RuntimeError('Tailscale manifest mismatch')
            copy(source, root / 'usr/libexec/q2-network' / name, 0o755)
    (root / 'etc/init.d/S60q2-mock').unlink(missing_ok=True)
    fixtures = root / 'usr/share/q2-fixtures'
    if fixtures.exists(): shutil.rmtree(fixtures)
    (root / 'etc/q2-defaults/mainline.toml').write_text('[server]\nurl = ""\nuser = ""\npassword = ""\n')
    overlay = HERE / 'integration' if a.qemu_fixture else a.integration_overlay
    if overlay:
        shutil.copytree(overlay, root, dirs_exist_ok=True, symlinks=True)
    if tsrsd is not None:
        tsrs_artifact.install(tsrs_binary, root)
    ssh.stage(root)
    copy(HERE / 'diagnostic-ssh', root / 'sbin/q2-diagnostic-ssh', 0o755)
    if not a.qemu_fixture and not a.authorized_key:
        copy(HERE / 'card-ssh', root / 'sbin/q2-card-ssh', 0o755)
    (root / 'sbin/q2-sim-ready').unlink(missing_ok=True)
    if a.qemu_fixture:
        copy(HERE / 'qemu-ready', root / 'sbin/q2-sim-ready', 0o755)
    if portal_radio is not None:
        portal_phone_runtime.install(root, a.wifi_tools, portal_radio)
    bluetooth = bluetooth_source = None
    if a.bluetooth_firmware:
        if stock_base:
            bluetooth, bluetooth_source = stock_bluetooth_firmware(a.bluetooth_firmware, root)
        else:
            bluetooth = bluetooth_firmware(a.bluetooth_firmware, root)
    x2000_record = install_x2000_c(a.x2000_c, root, a.toolchain_repo) if a.x2000_c else None
    runtime_resource_hashes = runtime_resources.validate(root, app_source)
    packaging = {'packaging_profile': 'development', 'transform': 'none'}
    if a.packaging_profile == 'production':
        # The standalone tsrs builder strips tsrsd; its symbols stay in its
        # build output. Only the Go pair, when included, is transformed here.
        packaging = (production_packaging(root, out, tailscale, a.strip_tool) if tailscale is not None
                     else {'packaging_profile': 'production', 'transform': 'none: no Go pair; tsrsd stripped by its build'})
    timezone_data = validate_timezone_data(root)
    compiler = inside(br / 'host/bin/mipsel-buildroot-linux-gnu-gcc')
    engine([compiler, '-Os', '-Wall', '-Wextra', '-Werror',
            inside(ROOT / 'os/platform/usb-status.c'), '-o', inside(root / 'usr/sbin/q2-usb-status'),
            '-L' + inside(root / 'usr/lib'), '-lq2platform'], 'q2-os-build')
    for source, dest in [(ROOT / 'os/platform/usb-auto-events.c', root / 'usr/sbin/q2-usb-auto-events'), (HERE / 'ubi-attach.c', root / 'sbin/q2-ubi-attach'), (HW / 'update/q2-hw-update.c', root / 'sbin/q2-hw-update')]:
        engine([compiler, '-Os', '-Wall', '-Wextra', '-Werror', inside(source), '-o', inside(dest)], 'q2-os-build')
    epoch = LOCK['source_date_epoch']
    os_commit = source_commit()
    if a.kernel_base is None:
        prepare_rescue(a.buildroot_target, out / 'rescue', out / 'rescue.cpio', ssh)
        release, modroot, config_path = build_kernel(src, out, root, hardware=not a.qemu_fixture)
    else:
        release, modroot, config_path = install_base(
            a.kernel_base.resolve(), out, root, a.buildroot_target, os_commit,
            platform_assets=a.kernel_platform_assets.resolve() if stock_base else None,
            stock_wifi=stock_base)
    config = config_path.read_text()
    if a.bluetooth_firmware and 'CONFIG_REGULATOR_USERSPACE_CONSUMER=y' not in config:
        raise RuntimeError('missing the Bluetooth enable hold: REGULATOR_USERSPACE_CONSUMER')
    uimage((out / 'xImage').read_bytes())
    # Keep the watcher and its target event executable together in both fresh
    # builds and refresh workflows. A script-only image cannot return on eject.
    usb_runtime = validate_usb_runtime(root, a.qemu_user if a.qemu_user.is_file() else None)
    if portal_radio is not None:
        portal_phone_runtime.validate(root, portal_radio)
        if portal_phone_runtime.admit(a.wifi_tools) != portal_radio:
            raise ValueError('private AP radio tool inputs changed during composition')
    verify(app_source, app_commit)
    installed_portal = root / portal_artifact.INSTALLED
    if (installed_portal.resolve() != installed_portal or not installed_portal.is_file()
            or sha(installed_portal) != portal['sha256']
            or installed_portal.stat().st_mode & 0o7777 != 0o755):
        raise ValueError('installed portal companion differs from its paired build')
    if runtime_resources.validate(root, app_source) != runtime_resource_hashes:
        raise RuntimeError('runtime resources changed during build')
    root_compression.kernel_reads(a.root_compression, config)
    installed_app = root / 'usr/bin/q2nd'
    if (installed_app.resolve() != installed_app or not installed_app.is_file()
            or sha(installed_app) != app_sha or sha(a.app) != app_sha):
        raise RuntimeError('staged canonical app differs from the build input')
    startup_profile = startup_read_ahead.sidecar(a.app.absolute().parent)
    startup = startup_read_ahead.install(root, startup_profile,
                                        root_compression.setting(a.root_compression)['block'])
    # The player may use this digest when its executable and this file are
    # on the same immutable SquashFS root.
    release_metadata = root / 'etc/q2-os-release.json'
    if release_metadata.parent.resolve() != release_metadata.parent:
        raise RuntimeError('release metadata requires a canonical directory')
    release_metadata.unlink(missing_ok=True)
    release_metadata.write_text(json.dumps({
        'profile': 'split-installed-candidate', 'release_tag': a.release_tag,
        'qemu_fixture': a.qemu_fixture, 'kernel_source_commit': source_record['commit'],
        'hardware_qualified': False, 'private_configuration_imported': False,
        'app_source_commit': app_commit, 'app_sha256': app_sha, 'portal_helper': portal,
        'portal_phone_radio': portal_radio}, indent=2) + '\n')
    engine(['mksquashfs', inside(root), inside(out / 'rootfs.squashfs'), '-noappend', '-all-root', *root_compression.args(a.root_compression), '-no-xattrs', '-mkfs-time', str(epoch)], 'q2-os-build')
    squashfs((out / 'rootfs.squashfs').read_bytes())
    compression = root_compression.verify(out / 'rootfs.squashfs', a.root_compression)
    manifest = {'os_source_commit': os_commit, 'profile': 'split-installed-candidate', 'hardware_qualified': False, 'emulator_shell': False,
                'qemu_fixture': a.qemu_fixture, 'kernel_source_commit': source_record['commit'],
                'root_compression': compression,
                'integration_hook_present': (root / 'sbin/q2-installed-services').is_file(),
                'artifacts': {n: {'sha256': sha(out / n), 'bytes': (out / n).stat().st_size} for n in ('xImage', 'rootfs.squashfs', 'rescue.cpio', 'shanling-q2.dtb')},
                'kernel_release': release, 'kernel_config_sha256': sha(config_path),
                'prepared_source_commit': source_record['commit'],
                'packaging': packaging, 'x2000_c': x2000_record,
                'installer_sources': {p.name: sha(p) for p in HERE.iterdir() if p.is_file()}, 'app_source_commit': app_commit, 'app_sha256': app_sha, 'portal_helper': portal, 'tailscale': tailscale, 'tsrsd': tsrsd,
                'tailscale_daemon': a.tailscale,
                'prepared_source': source_record,
                'timezone_data': timezone_data,
                'bluetooth_firmware': bluetooth,
                'runtime_resources': runtime_resource_hashes,
                'usb_runtime': usb_runtime,
                'portal_phone_radio': portal_radio,
                'modules': {str(p.relative_to(root)): sha(p) for p in modroot.rglob('*.ko')},
                'diagnostic_ssh': ssh.receipt}
    if bluetooth_source:
        manifest['bluetooth_firmware_provenance'] = bluetooth_source
    if stock_base:
        manifest['regulatory_database'] = json.loads(
            (a.kernel_base / 'kernel-base.json').read_text())['regulatory_database']
    if startup:
        if startup_read_ahead.admit(startup_profile, installed_app, compression['block']) != startup:
            raise ValueError('startup read-ahead inputs changed during packing')
        manifest['startup_read_ahead'] = startup
    (out / 'artifact.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest['artifacts'], indent=2))

if __name__ == '__main__': main()
