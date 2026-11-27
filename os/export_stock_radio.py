#!/usr/bin/env python3
"""Export the stock radio source and static-libnl relink materials."""
import argparse
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile

from build import ROOT, fetch, sha
from compose_stock import STOCK_MANIFEST, wifi_inputs
from install import kernel_base, stock_regdb
from portal_phone_runtime import NAMES as PHONE_TOOLS, NETWORK, SOURCES as PHONE_SOURCES
from sdk_release import read_json, regular

PREFIX = 'mipsel-buildroot-linux-gnu-'
WIFI_RECIPE = ROOT / 'os/hardware/build-wifi-tools.py'
DRIVER_RECIPE = ROOT / 'kernel/rtl8733bs/prepare.py'
LIBNL = ('libnl-3.a', 'libnl-genl-3.a')
LINKS = {
    'wpa_supplicant': ('wpa_supplicant-2.12/wpa_supplicant', ('PKG_CONFIG=false',)),
    'hostapd': ('hostapd-2.12/hostapd', ('PKG_CONFIG=false',)),
    'iw': ('iw-6.17', ('NO_PKG_CONFIG=1',
                      'CFLAGS=-Os -I/wifi/sysroot/usr/include/libnl3 -DCONFIG_LIBNL30',
                      'LIBS=-L/wifi/sysroot/usr/lib -lnl-genl-3 -lnl-3 -lm')),
}


def module(path):
    spec = spec_from_file_location(path.stem, path)
    loaded = module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def link_arguments(wifi, dest, binary):
    directory, assignments = LINKS[binary]
    work = wifi / directory
    compiler = PREFIX + 'gcc'
    commands = subprocess.check_output(
        ['make', '-Bn', f'CC={compiler}', *assignments, binary], cwd=work, text=True)
    matches = []
    for line in commands.splitlines():
        if not line.startswith(compiler + ' '):
            continue
        args = shlex.split(line)
        if '-c' not in args and '-o' in args and args[args.index('-o') + 1] == binary:
            matches.append(args[1:])
    if len(matches) != 1:
        raise ValueError(f'cannot recover the stock link command for {binary}')
    result = []
    for arg in matches[0]:
        if arg.endswith('.o'):
            obj = (work / arg).resolve(strict=True)
            name = obj.relative_to(wifi)
            relative = Path('objects') / name
            copy = dest / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(obj, copy)
            result.append(str(relative))
        elif arg == '-L/wifi/sysroot/usr/lib':
            result.append('-L{libnl}')
        elif arg.startswith('/') or '/wifi/' in arg:
            raise ValueError(f'unsupported stock link path for {binary}')
        else:
            result.append(arg)
    if not {'-lnl-3', '-lnl-genl-3', '-L{libnl}'} <= set(result):
        raise ValueError(f'expected static libnl consumers in {binary}')
    return result


def archive_driver(kit, dest, record):
    driver = kit / 'kernel/rtl8733bs'
    recipe = module(DRIVER_RECIPE)
    subprocess.run(['git', '-C', driver, 'diff', '--exit-code', 'HEAD'], check=True)
    module_root = kit / 'kernel/module-stage/lib/modules' / record['kernel_release']
    binary = regular(module_root / 'extra', '8733bs.ko')
    digest = sha(binary)
    if record['modules'].get('extra/8733bs.ko') != digest:
        raise ValueError('Realtek module differs from the stock kernel receipt')
    raw = binary.read_bytes()
    arrays = recipe.firmware_arrays(driver)
    hashes = {}
    for name, prefix in recipe.STOCK_FW.items():
        identity = sha256(arrays[name]).hexdigest()
        if not identity.startswith(prefix) or arrays[name] not in raw:
            raise ValueError('stock module firmware differs from its source arrays')
        hashes[name] = identity

    git = ['git', '-C', driver]
    upstream = subprocess.check_output([*git, 'rev-list', '--max-parents=0', 'HEAD'], text=True).strip()
    with tempfile.TemporaryFile() as archive, tempfile.TemporaryDirectory() as source:
        subprocess.run([*git, 'archive', upstream], check=True, stdout=archive)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tree:
            tree.extractall(source, filter='data')
        if recipe.tree_digest(Path(source)) != recipe.TREE_SHA256:
            raise ValueError('Realtek upstream source tree differs from the pinned source')
        subprocess.run(['git', '-C', source, 'init', '-q'], check=True)
        patches = recipe.selected_patches()
        subprocess.run(['git', '-C', source, 'apply', *patches], check=True)
        subprocess.run(['git', '-C', source, 'add', '-Af'], check=True)
        replay = subprocess.check_output(['git', '-C', source, 'write-tree'], text=True)
        actual = subprocess.check_output([*git, 'rev-parse', 'HEAD^{tree}'], text=True)
        if replay != actual:
            raise ValueError('Realtek built source differs from the pinned patch replay')

    with (dest / 'sources/rtl8733bs-patched.tar.gz').open('xb') as output:
        subprocess.run([*git, 'archive', '--format=tar.gz', '--prefix=rtl8733bs/', 'HEAD'],
                       check=True, stdout=output)
    downstream = dest / 'rtl8733bs'
    downstream.mkdir()
    for path in (DRIVER_RECIPE, *recipe.selected_patches()):
        shutil.copyfile(path, downstream / path.name)
    shutil.copyfile(kit / 'kernel/linux.config', downstream / 'linux.config')
    shutil.copyfile(ROOT / 'LICENSES/GPL-2.0.txt', downstream / 'COPYING')
    return {'upstream_commit': recipe.COMMIT, 'upstream_tree_sha256': recipe.TREE_SHA256,
            'module_sha256': digest, 'embedded_firmware_sha256': hashes,
            'linux': record['linux'], 'kernel_container': record['kernel_container']}


def image_readback(image, stage, kernel, wifi):
    manifest_path = regular(image, 'artifact.json')
    manifest = read_json(manifest_path)
    rootfs = regular(image, 'rootfs.squashfs')
    digest = sha(rootfs)
    if (manifest.get('qemu_fixture') is not False
            or manifest.get('kernel_source_commit') != kernel['linux']['commit']
            or manifest.get('kernel_release') != kernel['kernel_release']
            or manifest.get('kernel_config_sha256') != kernel['artifacts']['linux.config']['sha256']
            or manifest.get('artifacts', {}).get('rootfs.squashfs')
            != {'sha256': digest, 'bytes': rootfs.stat().st_size}
            or manifest.get('artifacts', {}).get('xImage') != kernel['artifacts']['xImage']):
        raise ValueError('stock image does not match the radio kit')
    tools = {name: f'usr/sbin/{name}' for name in ('wpa_supplicant', 'wpa_cli')}
    tools.update({name: str(NETWORK / name) for name in PHONE_TOOLS})
    expected = {path: sha(regular(wifi / 'bin', name)) for name, path in tools.items()}
    expected[f"lib/modules/{kernel['kernel_release']}/extra/8733bs.ko"] = kernel['modules']['extra/8733bs.ko']
    with tempfile.TemporaryDirectory(dir=stage, prefix='readback-') as directory:
        subprocess.run(
            ['podman', 'run', '--rm', '--network', 'none', '--security-opt', 'label=disable',
             '-v', f'{image}:/image:ro', '-v', f'{directory}:/readback', 'q2-os-build',
             'unsquashfs', '-no-progress', '-processors', '2', '-d', '/readback/root',
             '/image/rootfs.squashfs', *expected], check=True, stdout=subprocess.DEVNULL)
        root = Path(directory) / 'root'
        for path, expected_digest in expected.items():
            if sha(regular(root, path)) != expected_digest:
                raise ValueError(f'installed radio bytes differ: {path}')
    return {
        'rootfs_sha256': digest, 'image_manifest_sha256': sha(manifest_path),
        'os_source_commit': manifest['os_source_commit'],
        'binaries': {name: expected[path] for name, path in tools.items()},
    }


def export(kit, image, sdk, output):
    record = read_json(regular(kit, STOCK_MANIFEST))
    wifi = kit / 'wifi'
    if (record.get('wifi') != wifi_inputs(wifi)
            or record.get('kernel_manifest_sha256') != sha(kit / 'kernel/kernel-base.json')):
        raise ValueError('stock radio kit receipts do not match its files')
    kernel = kernel_base.verify_base(kit / 'kernel', record['platform'], stock_wifi=True)
    compiler = sdk / 'bin' / (PREFIX + 'gcc')
    if not compiler.is_file() or not compiler.resolve().is_relative_to(sdk):
        raise ValueError('expected a relocated SDK compiler')
    strip = regular(sdk / 'bin', PREFIX + 'strip')
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='stock-radio-') as temporary:
        stage = Path(temporary)
        sources = stage / 'sources'
        sources.mkdir()
        pins = module(WIFI_RECIPE).SOURCES | PHONE_SOURCES
        for url, expected in pins.values():
            path = regular(wifi, url.rsplit('/', 1)[1])
            if sha(path) != expected:
                raise ValueError(f'stock radio source checksum mismatch: {path.name}')
            shutil.copyfile(path, sources / path.name)
        regdb = stock_regdb.record(stock_regdb.read())
        if kernel.get('regulatory_database') != regdb:
            raise ValueError('wireless-regdb does not match the stock kernel receipt')
        path = fetch(stock_regdb.SOURCE)
        shutil.copyfile(path, sources / path.name)
        configurations = stage / 'build-config'
        configurations.mkdir()
        for program in ('wpa_supplicant', 'hostapd'):
            shutil.copyfile(wifi / LINKS[program][0] / '.config', configurations / (program + '.config'))
        for recipe in (WIFI_RECIPE, ROOT / 'os/portal_phone_runtime.py'):
            shutil.copyfile(recipe, configurations / recipe.name)

        baseline = stage / 'libnl'
        baseline.mkdir()
        for name in LIBNL:
            shutil.copyfile(regular(wifi / 'sysroot/usr/lib', name), baseline / name)
        links = {}
        for binary in LINKS:
            arguments = link_arguments(wifi, stage, binary)
            links[binary] = arguments
            subprocess.run([compiler, *(arg.replace('{libnl}', str(baseline)) for arg in arguments)],
                           cwd=stage, check=True)
            subprocess.run([strip, stage / binary], check=True)
            if sha(stage / binary) != sha(regular(wifi / 'bin', binary)):
                raise ValueError(f'relink does not reproduce the shipped {binary}')
            (stage / binary).unlink()
        driver = archive_driver(kit, stage, kernel)
        installed = image_readback(image, stage, kernel, wifi)
        receipt = {'schema': 1, 'platform': record['platform'], 'driver': driver, 'links': links,
                   'regulatory_database': regdb, **installed,
                   'source_archives_sha256': {path.name: sha(path) for path in sorted(sources.iterdir())}}
        (stage / 'radio-source.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        shutil.copyfile(ROOT / 'os/relink_stock_radio.py', stage / 'relink.py')
        shutil.copyfile(ROOT / 'docs/stock-radio-source.md', stage / 'README.md')
        with output.open('xb') as destination:
            subprocess.run(['tar', '--sort=name', '--owner=0', '--group=0', '--numeric-owner',
                            '-C', stage, '-czf', '-', '.'], stdout=destination, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('stock-inputs', 'image', 'sdk', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        export(args.stock_inputs.resolve(strict=True), args.image.resolve(strict=True),
               args.sdk.resolve(strict=True), args.output.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
