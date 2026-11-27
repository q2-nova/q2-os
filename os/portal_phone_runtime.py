"""Cross-build and admit the companion private AP radio payload."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / 'os/hardware/build-wifi-tools.py'
NETWORK = Path('usr/libexec/q2-network')
INTEGRATION = ROOT / 'os/install/integration' / NETWORK
SCRIPTS = ('portal-phone-ap', 'dhcp', 'wifi')
NAMES = ('iw', 'hostapd', 'hostapd_cli', 'dnsmasq')
SOURCES = {
    'iw-6.17': ('https://www.kernel.org/pub/software/network/iw/iw-6.17.tar.xz',
                '7d182e498289ab39b257da6780d562e415377107f50358ee5b55b8cfe40b1e33'),
    'hostapd-2.12': ('https://w1.fi/releases/hostapd-2.12.tar.gz',
                     'f43502561c28ba47ab77e18e1a973d07361c68cc8b14178e619bd5796b70eabd'),
    'dnsmasq-2.93': ('https://thekelleys.org.uk/dnsmasq/dnsmasq-2.93.tar.xz',
                     '0c00d4e5c97c8306e5fb932b348b34269c9c29a0e7df0e8e82958b407092bc19'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(out, engine, expected_recipes):
    (out / 'bin').mkdir(exist_ok=True)
    engine('iw-6.17', ['sh', '-ec', '''
export PATH=/br/host/bin:$PATH
make -j2 CC=mipsel-buildroot-linux-gnu-gcc NO_PKG_CONFIG=1 \
 CFLAGS='-Os -I/wifi/sysroot/usr/include/libnl3 -DCONFIG_LIBNL30' \
 LIBS='-L/wifi/sysroot/usr/lib -lnl-genl-3 -lnl-3 -lm'
mipsel-buildroot-linux-gnu-strip -o /wifi/bin/iw iw
'''])
    (out / 'hostapd-2.12/hostapd/.config').write_text('''CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CONFIG_CTRL_IFACE=y
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_INTERNAL_LIBTOMMATH_FAST=y
CONFIG_IEEE80211N=y
CONFIG_IEEE80211W=y
CFLAGS += -Os -I/wifi/sysroot/usr/include/libnl3
LIBS += -L/wifi/sysroot/usr/lib -lm
''')
    engine('hostapd-2.12/hostapd', ['sh', '-ec', '''
export PATH=/br/host/bin:$PATH
make -j2 CC=mipsel-buildroot-linux-gnu-gcc PKG_CONFIG=false hostapd hostapd_cli
for name in hostapd hostapd_cli; do mipsel-buildroot-linux-gnu-strip -o /wifi/bin/$name $name; done
'''])
    engine('dnsmasq-2.93', ['sh', '-ec', '''
export PATH=/br/host/bin:$PATH
make -j2 CC=mipsel-buildroot-linux-gnu-gcc CFLAGS=-Os \
 COPTS='-DNO_DBUS -DNO_IDN -DNO_DHCP6 -DNO_TFTP -DNO_LUA -DNO_AUTH -DNO_DNSSEC -DNO_IPSET -DNO_NFTSET -DNO_UBUS -DNO_INOTIFY'
mipsel-buildroot-linux-gnu-strip -o /wifi/bin/dnsmasq src/dnsmasq
'''])
    if recipes() != expected_recipes:
        raise ValueError('private AP tool recipes changed during build')
    record = {'sources': SOURCES, 'recipes': expected_recipes,
              'binaries': {n: sha(out / 'bin' / n) for n in NAMES}}
    with (out / 'portal-phone.json').open('x') as stream:
        stream.write(json.dumps(record, indent=2) + '\n')


def recipes():
    return {p.name: sha(p) for p in (BUILDER, Path(__file__), *(INTEGRATION / n for n in SCRIPTS))}


def admit(directory):
    directory = Path(directory)
    receipt = directory / 'portal-phone.json'
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError('build current private AP radio tools with os/hardware/build-wifi-tools.py')
    record = json.loads(receipt.read_text())
    if (record.get('sources') != {name: list(row) for name, row in SOURCES.items()}
            or record.get('recipes') != recipes()
            or set(record.get('binaries', {})) != set(NAMES)):
        raise ValueError('private AP radio tool receipt requires current recipes and sources')
    for name in NAMES:
        binary = directory / 'bin' / name
        if binary.resolve() != binary.absolute() or not binary.is_file():
            raise ValueError('regular private AP tool required: ' + name)
        raw = binary.read_bytes()
        if (raw[:6] != b'\x7fELF\x01\x01' or raw[18:20] != b'\x08\x00'
                or hashlib.sha256(raw).hexdigest() != record['binaries'][name]):
            raise ValueError('private AP tool fails MIPS ELF and receipt admission: ' + name)
    return record | {'receipt_sha256': sha(receipt)}


def install(root, directory, record):
    sources = {name: Path(directory) / 'bin' / name for name in NAMES}
    sources.update({name: INTEGRATION / name for name in SCRIPTS})
    expected = record['binaries'] | {name: record['recipes'][name] for name in SCRIPTS}
    changes = {}
    for name, source in sources.items():
        target = root / NETWORK / name
        if target.resolve() != target.absolute() or (target.exists() and not target.is_file()):
            raise ValueError('regular private AP install destination required: ' + name)
        previous = (sha(target), target.stat().st_mode & 0o7777) if target.is_file() else None
        if previous is not None and previous[1] != 0o755:
            raise ValueError('unexpected installed private AP tool mode: ' + name)
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('private AP runtime changed during copying: ' + name)
        if previous != (expected[name], 0o755):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755)
            changes[(NETWORK / name).as_posix()] = 'add' if previous is None else 'replace'
    return changes


def validate(root, record):
    if not record or set(record.get('binaries', {})) != set(NAMES):
        raise ValueError('the captive portal companion requires the private AP payload')
    files = record['binaries'] | {name: record['recipes'][name] for name in SCRIPTS}
    for name, digest in files.items():
        installed = root / NETWORK / name
        if (installed.resolve() != installed.absolute() or not installed.is_file()
                or installed.stat().st_mode & 0o7777 != 0o755 or sha(installed) != digest):
            raise ValueError('installed private AP payload differs from its receipt: ' + name)
