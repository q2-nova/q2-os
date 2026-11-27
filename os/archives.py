"""Inspect and extract generated component archives without path traversal."""
from pathlib import PurePosixPath
import tarfile

from build import run


def archive_names(archive):
    with tarfile.open(archive) as content:
        members = content.getmembers()
    files = {}
    for member in members:
        path = PurePosixPath(member.name)
        if (path.is_absolute() or '..' in path.parts or path in files
                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
            raise ValueError('unsafe or ambiguous SDK archive member')
        files[path] = member
    for path, member in files.items():
        for parent in path.parents:
            if parent in files and not files[parent].isdir():
                raise ValueError('SDK archive member descends through a non-directory')
        if member.islnk():
            target = PurePosixPath(member.linkname)
            if target not in files or not files[target].isfile():
                raise ValueError('SDK archive hard link must target a bundled regular file')
    return files


def unpack(archive, output):
    archive_names(archive)
    run(['tar', '-xzf', archive, '--no-same-owner', '-C', output])
