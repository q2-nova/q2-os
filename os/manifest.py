#!/usr/bin/env python3
"""Record package, source and tool identities without inspecting user settings."""
import json
import subprocess
from build import ROOT, WORK, LOCK, sha


def capture(args):
    return subprocess.check_output([str(x) for x in args], cwd=ROOT, text=True)


out = WORK / 'out'
env = {
    'sources': LOCK,
    'repository_commit': capture(['git', 'rev-parse', 'HEAD']).strip(),
    'build_container': capture(['podman', 'image', 'inspect', 'q2-os-build', '--format', '{{.Id}}']).strip(),
    'build_container_packages': capture(['podman', 'run', '--rm', '--network', 'none', 'q2-os-build', 'dpkg-query', '-W', '-f=${Package}\t${Version}\n']).splitlines(),
    'rustc': capture(['rustc', '+' + LOCK['rust_toolchain'], '-vV']),
    'gcc': capture([out / 'host/bin/mipsel-buildroot-linux-gnu-gcc', '--version']),
    'config_sha256': sha(out / '.config'),
    'emulation_only': True,
    'input_files': {},
}
files = [p for p in (ROOT / 'os').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
for path in sorted(files): env['input_files'][str(path.relative_to(ROOT))] = sha(path)
packages = {}
metadata = json.loads(capture(['cargo', 'metadata', '--locked', '--offline', '--format-version=1',
                               '--manifest-path', 'os/probes/rust/Cargo.toml']))
for p in metadata['packages']:
    packages[p['id']] = {key: p[key] for key in ['name', 'version', 'license', 'source']}
env['cargo_packages'] = sorted(packages.values(), key=lambda p: (p['name'], p['version']))
# os/rust-build.py writes the Rust target from the q2-toolchain checkout's spec.
target_spec = WORK / 'mipsel-q2-os-linux-gnu.json'
env['rust_target_spec_sha256'] = sha(target_spec) if target_spec.exists() else None
env['artifacts'] = {str(path.relative_to(WORK)): sha(path) for path in [
    out / 'images/q2-sd.img', out / 'images/rootfs.squashfs', out / 'images/rootfs.cpio.gz',
    WORK / 'kernel-disk/uImage.gz', WORK / 'kernel-initramfs/uImage.gz',
    WORK / 'qemu-out/qemu-system-mipsel']}
env['recovery_artifacts'] = {str(path.relative_to(WORK)): sha(path) for path in [
    WORK / 'kernel-recovery/uImage.gz', WORK / 'kernel-rescue/uImage.gz',
    WORK / 'recovery/manifest.json'] if path.exists()}
(WORK / 'build-manifest.json').write_text(json.dumps(env, indent=2) + '\n')
print('build/os/build-manifest.json')
