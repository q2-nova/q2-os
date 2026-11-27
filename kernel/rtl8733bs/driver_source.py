"""Driver source trees and C function extraction for the native function tests.

tree(NAME) returns a driver tree that prepare.py made from the pinned release
with one of the VARIANTS' patch selections, prepared once under
build/rtl8733bs/test-sources/ and reused while prepare.py and the selected
patches are unchanged. prepare.py downloads the release on first use.
"""
import fcntl
import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PREPARE = HERE / 'prepare.py'
WORK = ROOT / 'build/rtl8733bs/test-sources'
# prepare.py options per variant. The cold, cached-ps, cached-datapath and
# cold-disconnect variants stack in that order; each adds one patch.
VARIANTS = {
    'baseline': (),
    'sae': ('--sae-external-auth',),
    'cold': ('--cold-suspend',),
    'cached-ps': ('--cold-suspend', '--cached-power-state'),
    'cached-datapath': ('--cold-suspend', '--cached-power-state', '--cached-datapath'),
    'cold-disconnect': ('--cold-suspend', '--cached-power-state', '--cached-datapath', '--cold-disconnect'),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def function(text, name):
    """The C definition of `name` in `text`, from its declarator to the closing brace."""
    pattern = r'^(?:static\s+)?[A-Za-z_][\w *]*\b' + re.escape(name) + r'\('
    start = brace = None
    for match in re.finditer(pattern, text, re.M):
        opening = text.index('{', match.end())
        if ';' not in text[match.end():opening]:
            start, brace = match.start(), opening
            break
    if start is None:
        raise ValueError('C function definition missing: ' + name)
    level, end = 1, brace + 1
    while level:
        level += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def _key(options):
    sys.path.insert(0, str(HERE))
    import prepare
    patches = prepare.selected_patches(
        sae='--sae-external-auth' in options, cold='--cold-suspend' in options,
        cached='--cached-power-state' in options, datapath='--cached-datapath' in options,
        disconnect='--cold-disconnect' in options)
    h = hashlib.sha256(sha(PREPARE).encode())
    for patch in patches:
        h.update(f'{patch.name} {sha(patch)}\n'.encode())
    return h.hexdigest()[:16]


def tree(name):
    """A prepared driver tree for variant `name`; raises SkipTest when the
    pinned release cannot be fetched."""
    options = VARIANTS[name]
    dest = WORK / f'{name}-{_key(options)}'
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK / 'prepare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not dest.is_dir():
            done = subprocess.run([sys.executable, str(PREPARE), '--dest', str(dest), *options],
                                  capture_output=True, text=True, timeout=600)
            if done.returncode:
                if not any((ROOT / 'build/rtl8733bs').glob('rtl8733bs-*.tar.gz')):
                    raise unittest.SkipTest('needs the pinned RTL8733BS release; prepare.py could not fetch it: '
                                            + done.stderr.strip()[-200:])
                raise RuntimeError(f'prepare.py {" ".join(options)} failed: {done.stderr.strip()}')
    return dest


def output(name):
    """A fresh output directory under build/rtl8733bs/tests/."""
    import uuid
    out = ROOT / 'build/rtl8733bs/tests' / f'{name}-{uuid.uuid4().hex[:10]}'
    out.parent.mkdir(parents=True, exist_ok=True)
    return out
