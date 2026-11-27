"""X2000-clang OS programs into an image root.

os/x2000-c-build.py builds each package with the SDK's GCC and with a
q2-toolchain checkout's q2/toolchain/q2-x2000-cc and compares their output.
A clang build replaces an image file only when

- every output test of its package ran and was identical, and
- the GCC arm's file is byte for byte the file the image already ships, or
  the image already ships this result's clang file (a base that is itself a
  release with this overlay, such as V0.128 under V0.129), and
- the clang file has every protection its GCC control has (`hardening`:
  stack protector, RELRO, BIND_NOW, PIE; Buildroot's GCC wrapper adds them
  and q2-x2000-cc adds the same).

The second condition ties each clang build to the source, configuration and
flags that built the shipped program: a result from another Buildroot tree,
another os/probes revision or another BusyBox configuration (the image's has
the ash patch and the blockdev applet) replaces nothing. Files that fail it
stay on GCC and are listed with the reason. The files of a PAIRED package
(wpa_supplicant and wpa_cli) are replaced together or not at all.

Used by os/install/build.py.
"""
import hashlib
import json
import struct
from pathlib import Path

# Package -> {file in its gcc/ and x2000/ directories: installed path}.
# dropbear: the Q2 session test's empty echo of 2026-11-21 was the test's
# (authorized_keys under /tmp, refused for every build, GCC's too).
# wpa: the image's SAE/PMF build of os/hardware/build-wifi-tools.py --wpa3.
TARGETS = {
    'busybox': {'busybox': 'bin/busybox'},
    'dropbear': {'dropbearmulti': 'usr/sbin/dropbear'},
    # alsactl, alsamixer and aserver are built but stay on GCC: the emulator
    # runs no more of them than their version or usage (no card, no terminal).
    'alsa-utils': {'aplay': 'usr/bin/aplay', 'arecord': 'usr/bin/arecord'},
    'alsa-lib-tools': {'libatopology.so.2': 'usr/lib/libatopology.so.2.0.0'},
    'ncurses': {'libncurses.so.6': 'usr/lib/libncurses.so.6.6', 'libform.so.6': 'usr/lib/libform.so.6.6',
                'libmenu.so.6': 'usr/lib/libmenu.so.6.6', 'libpanel.so.6': 'usr/lib/libpanel.so.6.6'},
    'libxcrypt': {'libcrypt.so.2': 'usr/lib/libcrypt.so.2.0.0'},
    'zlib': {'libz.so.1': 'usr/lib/libz.so.1.3.2'},
    'kexec': {'kexec': 'usr/sbin/kexec', 'vmcore-dmesg': 'usr/sbin/vmcore-dmesg'},
    'fsck': {'fsck.exfat': 'usr/sbin/fsck.exfat'},
    'wpa': {'wpa_supplicant': 'usr/sbin/wpa_supplicant', 'wpa_cli': 'usr/sbin/wpa_cli'},
    'alsa-lib': {'libasound.so.2': 'usr/lib/libasound.so.2.0.0'},
    'chrony': {'chronyd': 'usr/libexec/q2-network/chronyd', 'chronyc': 'usr/libexec/q2-network/chronyc'},
    'probes': {'q2-player-supervisor': 'usr/sbin/q2-player-supervisor', 'q2-memory-guard': 'usr/sbin/q2-memory-guard',
               'q2-rtc-probe': 'usr/bin/q2-rtc-probe', 'q2-watchdog-guard': 'usr/sbin/q2-watchdog-guard',
               'q2-input-probe': 'usr/bin/q2-input-probe', 'q2-alsa-probe': 'usr/bin/q2-alsa-probe',
               'q2-abi-probe': 'usr/bin/q2-abi-probe', 'libq2probe.so': 'usr/lib/libq2probe.so',
               'q2-seed-entropy': 'usr/sbin/q2-seed-entropy'},
    # The q2-platform package's file.
    'platform': {'libq2platform.so': 'usr/lib/libq2platform.so'},
}
PLATFORM = 'usr/lib/libq2platform.so'
# Packages whose files are replaced together or not at all: the supplicant
# and its control client ship as one build (os/hardware/build-wifi-tools.py).
PAIRED = {'wpa'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


HARDENING = ('stack_protector', 'relro', 'bind_now', 'pie')


def hardening(path):
    """What Buildroot's GCC wrapper adds, read from a little-endian ELF32's
    program headers and dynamic section: a GNU_RELRO segment; BIND_NOW (DT_BIND_NOW,
    DF_BIND_NOW or DF_1_NOW); a PIE executable (DF_1_PIE); __stack_chk_fail
    or __stack_chk_guard in the dynamic strings (a static program has none).
    None for a file that is not such an ELF."""
    data = Path(path).read_bytes()
    if data[:6] != b'\x7fELF\x01\x01':
        return None
    phoff, = struct.unpack_from('<I', data, 28)
    phsize, phcount = struct.unpack_from('<HH', data, 42)
    headers = [struct.unpack_from('<8I', data, phoff + i * phsize) for i in range(phcount)]
    def offset(vaddr):
        for kind, off, va, _, filesz, *_ in headers:
            if kind == 1 and va <= vaddr < va + filesz:
                return off + vaddr - va
        raise ValueError(f'{path}: address {vaddr:#x} outside the file')
    dynamic = {}
    for kind, off, _, _, filesz, *_ in headers:
        if kind == 2:
            for pos in range(off, off + filesz, 8):
                tag, value = struct.unpack_from('<iI', data, pos)
                if tag == 0:
                    break
                dynamic.setdefault(tag, value)
    strings = b''
    if 5 in dynamic and 10 in dynamic:
        start = offset(dynamic[5])
        strings = data[start:start + dynamic[10]]
    flags, flags_1 = dynamic.get(30, 0), dynamic.get(0x6ffffffb, 0)
    return {'stack_protector': b'__stack_chk_fail\0' in strings or b'__stack_chk_guard\0' in strings,
            'relro': any(h[0] == 0x6474e552 for h in headers),
            'bind_now': 24 in dynamic or bool(flags & 0x8) or bool(flags_1 & 0x1),
            'pie': bool(flags_1 & 0x08000000)}


def weaker(gcc, x2000):
    """The protections the GCC file has and the clang file lacks."""
    a, b = hardening(gcc), hardening(x2000)
    if a is None or b is None:
        return [] if a == b else ['ELF']
    return [k for k in HARDENING if a[k] and not b[k]]


def admit(result_dir, identity=None):
    """The passed result and its candidate files: {target: dict(package, gcc, x2000)}.

    `identity`: the checkout's x2000_toolchain.patch_identity(), which the
    result's must equal."""
    result_dir = Path(result_dir)
    result = json.loads((result_dir/'result.json').read_text())
    if result.get('passed') is not True:
        raise ValueError('x2000-c: the output tests did not pass')
    if identity is not None and result.get('x2000') != identity:
        raise ValueError('x2000-c: built with another X2000 toolchain patch than this checkout pins')
    candidates = {}
    for package, files in TARGETS.items():
        tests = [t for t in result['tests'] if t['package'] == package]
        if package not in result['packages'] or not tests or not all(t['same'] and t['ran'] for t in tests):
            continue
        for name, target in files.items():
            gcc, x2000 = result_dir/package/'gcc'/name, result_dir/package/'x2000'/name
            if gcc.is_file() and x2000.is_file():
                candidates[target] = dict(package=package, gcc=gcc, x2000=x2000)
    return result, candidates


def base_record(manifest):
    """The x2000-c files of a base image's manifest (the record install()
    returned when it was composed): {target: row with gcc_sha256 and x2000_sha256}."""
    return manifest.get('kernel_release_integration', {}).get('x2000_c', {}).get('files', {})


def replaces(base, target, shipped, gcc):
    """The base ships an earlier clang build of `target` over the GCC control
    with digest `gcc` (base: base_record of its manifest)."""
    row = (base or {}).get(target, {})
    return shipped is not None and row.get('x2000_sha256') == shipped and row.get('gcc_sha256') == gcc


def plan(candidates, digest, base=None):
    """Split candidates by the GCC-control rule. `digest(target)`: the SHA-256 of
    the image's regular file at `target`, or None when it has none. `base`:
    base_record of the base image's manifest; a file the base ships as an
    earlier clang build whose recorded GCC control is this result's GCC arm
    is replaced too (base_x2000_sha256)."""
    install, kept = {}, {}
    for target, c in sorted(candidates.items()):
        shipped = digest(target)
        lost = weaker(c['gcc'], c['x2000'])
        if lost:
            kept[target] = 'the clang build lacks the GCC control\'s ' + ', '.join(lost)
        elif shipped is None:
            kept[target] = 'the image has no regular file here'
        elif shipped == sha(c['x2000']):
            install[target] = dict(package=c['package'], gcc_sha256=sha(c['gcc']), x2000_sha256=shipped,
                                   source=c['x2000'], base_had_x2000=True)
        elif replaces(base, target, shipped, sha(c['gcc'])):
            install[target] = dict(package=c['package'], gcc_sha256=sha(c['gcc']), x2000_sha256=sha(c['x2000']),
                                   source=c['x2000'], base_x2000_sha256=shipped)
        elif sha(c['gcc']) != shipped:
            kept[target] = 'the GCC control %s is not the shipped %s' % (sha(c['gcc'])[:16], shipped[:16])
        else:
            install[target] = dict(package=c['package'], gcc_sha256=shipped, x2000_sha256=sha(c['x2000']), source=c['x2000'])
    for package in PAIRED:
        targets = set(TARGETS[package].values())
        if targets & set(install) and not targets <= set(install):
            for target in sorted(targets & set(install)):
                del install[target]
                kept[target] = 'installed only with the rest of its pair: ' + ', '.join(sorted(targets - {target}))
    return install, kept


def gcc_control(result_dir, shipped_sha256, identity=None, base=None, target=None):
    """The GCC arm file whose clang build has `shipped_sha256`, or None.

    A base that already carries this result's clang file is tied to the
    source by the GCC arm built beside it; the release composer uses that file
    as the libc-ABI baseline for the programs it rebuilds with GCC. A base
    with an earlier clang build of `target` is tied to it by its manifest's
    record (`base`) when that GCC control is this result's GCC arm."""
    for t, c in admit(result_dir, identity)[1].items():
        if sha(c['x2000']) == shipped_sha256 or (t == target and replaces(base, t, shipped_sha256, sha(c['gcc']))):
            return c['gcc']
    return None


def platform_sha256(record, shipped):
    """The image's libq2platform.so digest after install() returned `record`:
    the clang file's when it replaced the library, else `shipped`. Manifests
    carry it as platform_library_sha256 (os/install/audit_refresh.py checks it)."""
    row = record['files'].get(PLATFORM)
    return row['x2000_sha256'] if row else shipped


def install(result_dir, root, identity=None, base=None):
    """Replace the shipped GCC programs (and, with `base`, earlier clang builds)
    in `root`; return the record for the manifest."""
    root = Path(root)
    result, candidates = admit(result_dir, identity)
    def digest(target):
        path = root/target
        return sha(path) if path.is_file() and not path.is_symlink() else None
    selected, kept = plan(candidates, digest, base)
    if not selected:
        raise ValueError('x2000-c: nothing to install')
    for target, row in selected.items():
        # Same inode, so type and mode stay the image's.
        (root/target).write_bytes(Path(row['source']).read_bytes())
        if sha(root/target) != row['x2000_sha256']:
            raise ValueError('x2000-c: changed while copying: '+target)
    return dict(identity=result['x2000'], result_sha256=sha(Path(result_dir)/'result.json'),
                toolchain=result.get('toolchain'), source_commit=result.get('source_commit'),
                files={t: {k: v for k, v in row.items() if k != 'source'} for t, row in selected.items()},
                kept_on_gcc=kept)
