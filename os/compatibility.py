"""Verify the repository-local inputs for the OS compatibility builds."""
import hashlib
import json

from build import ROOT, LOCK, sha

INPUTS = ROOT / 'os/compat'
MANIFEST = json.loads((INPUTS / 'inputs.json').read_text())
PROFILES = MANIFEST['profiles']


def verified_file(relative, expected):
    path = INPUTS / relative
    if path.is_symlink() or not path.is_file() or sha(path) != expected:
        raise RuntimeError(f'compatibility input mismatch: {relative}')
    return path


def kernel_patches(profile):
    name = f'kernel/{profile}.series'
    series = verified_file(name, PROFILES[profile]['series_sha256'])
    patches = []
    for entry in series.read_text().splitlines():
        if entry and not entry.startswith('#'):
            relative = f'kernel/patches/{entry}'
            patches.append(verified_file(relative, MANIFEST['kernel_patches'][relative]))
    return patches


def input_digest(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def kernel_input_digest(profile, patches, local_patches):
    return input_digest({
        'upstream': LOCK['kernel']['sha256'],
        'series': PROFILES[profile]['series_sha256'],
        'patches': [MANIFEST['kernel_patches'][str(patch.relative_to(INPUTS))] for patch in patches],
        'local_patches': local_patches,
    })
