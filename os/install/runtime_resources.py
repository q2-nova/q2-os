"""Exact packaged app resources shared by fresh builds and release composition.

FILES maps each installed path to its path in the q2-player checkout the
player was built from. Only generated build trees are inputs. Reject symlinks
before reading sources or writing destinations; this is not a sandbox against
concurrent tree mutation.
"""
import hashlib
from pathlib import Path, PurePosixPath
import shutil

FILES = {
    **{f'usr/share/q2nd/fonts/{name}': f'assets/fonts/{name}' for name in
       ('OpenSans-Regular.ttf', 'OpenSans-SemiBold.ttf', 'OpenSans-Bold.ttf')},
    'usr/share/licenses/q2nd/OPEN-SANS-LICENSE.txt': 'assets/fonts/OpenSans-LICENSE.txt',
    **{f'usr/share/q2nd/autoeq/{name}': f'crates/q2nd/assets/autoeq/{name}' for name in
       ('index.tsv', 'custom-studio/index.tsv', 'custom-studio/step-1.txt',
        'custom-studio/step-2.txt', 'custom-studio/step-3.txt', 'custom-studio/step-4.txt',
        'README.md', 'custom-studio/README.md', 'custom-studio/provenance.json')},
    'usr/share/licenses/q2nd/AUTOEQ-LICENSE': 'crates/q2nd/assets/autoeq/LICENSE',
    'usr/share/licenses/q2nd/AUTOEQ-CUSTOM-STUDIO-LICENSE': 'crates/q2nd/assets/autoeq/custom-studio/LICENSE',
}


def parts(root, name):
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or '..' in relative.parts or str(relative) != name
            or root.is_symlink() or not root.is_dir()):
        raise ValueError('regular generated root and canonical relative resource path required')
    return relative.parts


def regular(root, name):
    path = root
    for part in parts(root, name):
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink in resource path: ' + name)
    if not path.is_file():
        raise ValueError('missing regular runtime resource: ' + name)
    return path


def parent(root, name, allowed=None):
    """Create only declared missing parents, rejecting every symlink ancestor."""
    path, made = root, []
    for part in parts(root, name)[:-1]:
        path = path / part
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError('regular resource directory required: ' + relative)
        if not path.exists():
            if allowed is not None and relative not in allowed:
                raise ValueError('undeclared resource directory: ' + relative)
            path.mkdir(); path.chmod(0o755); made.append(relative)
    return made


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(root, source_root):
    result = {}
    for name, source in FILES.items():
        target = regular(root, name)
        if target.stat().st_mode & 0o7777 != 0o644:
            raise ValueError('runtime resource must be 0644: ' + name)
        actual = digest(target)
        if actual != digest(regular(source_root, source)):
            raise ValueError('required runtime resource differs: ' + name)
        result[name] = actual
    return result


def install(root, source_root):
    sources = {name: regular(source_root, source) for name, source in FILES.items()}
    for name, source in sources.items():
        parent(root, name)
        target = root / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('regular runtime resource destination required: ' + name)
        shutil.copyfile(source, target); target.chmod(0o644)
    return validate(root, source_root)
