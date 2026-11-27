#!/usr/bin/env python3
"""Prepare reusable stock radio/kernel inputs and compose a local keyless candidate."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from build import ROOT, WORK, run, sha
from compose_ci import RELEASE_FILES, restore_platform
from fork_checkout import verify
from install.kernel_base import produce, source_commit, verify_base
import portal_phone_runtime
import sdk_platform
import sdk_release

sys.path.insert(0, str(ROOT / 'tools'))
import fetch_firmware

SUPPLICANT = ('wpa_supplicant', 'wpa_cli')
STOCK_MANIFEST = 'stock-inputs.json'
STOCK_OVERLAY = ROOT / 'os/install/integration'


def platform_assets(assets, commit):
    platform = sdk_platform.compatible_assets(assets, commit)
    sums = ''.join(f'{sha(sdk_release.regular(assets, name))}  {name}\n'
                   for name in RELEASE_FILES)
    if sdk_release.regular(assets, 'RELEASE-SHA256SUMS').read_text() != sums:
        raise ValueError('stock input files differ from the tagged release checksums')
    verify_base(assets, platform)
    return platform


def wifi_inputs(directory):
    receipt = sdk_release.regular(directory, 'artifact.json')
    expected = sdk_release.read_json(receipt)
    if expected.get('builder_sha256') != sha(ROOT / 'os/hardware/build-wifi-tools.py'):
        raise ValueError('Wi-Fi tools require the current public build recipe')
    binaries = {name: sha(sdk_release.regular(directory / 'bin', name)) for name in SUPPLICANT}
    if expected.get('binaries') != binaries or expected.get('profile') != 'WPA2 internal crypto':
        raise ValueError('Wi-Fi supplicant differs from the supported WPA2 profile receipt')
    return {'supplicant_receipt_sha256': sha(receipt),
            'portal_radio_receipt_sha256': portal_phone_runtime.admit(directory)['receipt_sha256']}


def restore(assets, working):
    target = restore_platform(assets, working)
    run([sys.executable, ROOT / 'os/build.py', 'container'])
    inside = '/work/' + str((working / 'host/relocate-sdk.sh').relative_to(ROOT))
    run(['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
         '-v', f'{ROOT}:/work', 'q2-os-build', inside])
    return target


def bluetooth_inputs(directory):
    if directory.is_symlink():
        raise ValueError('Bluetooth firmware kit must be a regular directory')
    return fetch_firmware.admit(directory) if directory.exists() else None


def prepare(assets, output, platform, options):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.stock-kit-') as work:
        stage = Path(work) / 'kit'
        stage.mkdir()
        if not options.without_bluetooth_firmware:
            fetch_firmware.run(options.bluetooth_source or fetch_firmware.DEFAULT_SOURCE,
                               stage / 'bluetooth', options.bluetooth_cache,
                               options.bluetooth_offline, options.bluetooth_zip)
        prepare_radio_kernel(assets, stage, platform)
        stage.rename(output)


def prepare_radio_kernel(assets, output, platform):
    with tempfile.TemporaryDirectory(dir=WORK, prefix='stock-platform-') as directory:
        working = Path(directory) / 'platform'
        restore(assets, working)
        wifi = output / 'wifi'
        run([sys.executable, ROOT / 'os/hardware/build-wifi-tools.py',
             '--buildroot-output', working, '--output', wifi])
    kernel = produce(assets, assets, output / 'kernel', stock_wifi=True)
    record = {'profile': 'keyless-stock-wifi', 'platform': platform, 'wifi': wifi_inputs(wifi),
              'kernel_manifest_sha256': sha(output / 'kernel/kernel-base.json'),
              'kernel_release': kernel['kernel_release'], 'hardware_qualified': False,
              'bluetooth_firmware': bluetooth_inputs(output / 'bluetooth')}
    with (output / STOCK_MANIFEST).open('x') as result:
        json.dump(record, result, indent=2)
        result.write('\n')


def compose(args, assets, output, platform):
    stock = args.stock_inputs.resolve(strict=True)
    record = sdk_release.read_json(sdk_release.regular(stock, STOCK_MANIFEST))
    if (record.get('profile') != 'keyless-stock-wifi' or record.get('platform') != platform
            or record.get('wifi') != wifi_inputs(stock / 'wifi')
            or record.get('kernel_manifest_sha256') != sha(stock / 'kernel/kernel-base.json')):
        raise ValueError('stock radio/kernel kit does not match the selected OS release')
    bluetooth = stock / 'bluetooth'
    if record.get('bluetooth_firmware') != bluetooth_inputs(bluetooth):
        raise ValueError('Bluetooth firmware does not match its stock kit receipt')
    verify_base(stock / 'kernel', platform, stock_wifi=True)
    with tempfile.TemporaryDirectory(dir=WORK, prefix='stock-compose-') as directory:
        target = restore(assets, Path(directory) / 'platform')
        run([sys.executable, ROOT / 'os/install/build.py', '--kernel-base', stock / 'kernel',
             '--kernel-platform-assets', assets, '--buildroot-target', target,
             '--wifi-tools', stock / 'wifi', '--integration-overlay', STOCK_OVERLAY,
             '--player-source', args.player_source.resolve(), '--app', args.app.resolve(),
             '--tsrs-source', args.tsrs_source.resolve(), '--tsrsd', args.tsrsd.resolve(),
             '--release-tag', args.release_tag, '--output', output,
             *(['--bluetooth-firmware', bluetooth] if record.get('bluetooth_firmware') else []),
             *(['--qemu-user', args.qemu_user.resolve()] if args.qemu_user else [])])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='action', required=True)
    prep = modes.add_parser('prepare')
    image = modes.add_parser('image')
    for mode in (prep, image):
        mode.add_argument('--assets', type=Path, required=True)
        mode.add_argument('--output', type=Path, required=True)
    for name in ('stock-inputs', 'player-source', 'app', 'tsrsd', 'tsrs-source'):
        image.add_argument('--' + name, type=Path, required=True)
    prep.add_argument('--without-bluetooth-firmware', action='store_true',
                      help='omit restricted controller firmware from the stock kit')
    prep.add_argument('--bluetooth-source', choices=sorted(fetch_firmware.SOURCES))
    prep.add_argument('--bluetooth-zip', type=Path, help='local stock ZIP matching --bluetooth-source')
    prep.add_argument('--bluetooth-cache', type=Path, default=fetch_firmware.DEFAULT_CACHE)
    prep.add_argument('--bluetooth-offline', action='store_true', help='use only the local ZIP or verified download cache')
    image.add_argument('--qemu-user', type=Path)
    image.add_argument('--release-tag', required=True)
    args = parser.parse_args()
    try:
        commit = source_commit()
        verify(ROOT, commit)
        assets = args.assets.resolve(strict=True)
        output = args.output.resolve()
        if not output.is_relative_to(ROOT / 'build') or output.exists():
            raise ValueError('use a new output under build/')
        platform = platform_assets(assets, commit)
        WORK.mkdir(parents=True, exist_ok=True)
        if args.action == 'prepare':
            if args.without_bluetooth_firmware and (args.bluetooth_zip or args.bluetooth_source or args.bluetooth_offline):
                parser.error('Bluetooth source and offline options require Bluetooth firmware')
            prepare(assets, output, platform, args)
        else:
            compose(args, assets, output, platform)
    except (ValueError, OSError, fetch_firmware.FetchError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
