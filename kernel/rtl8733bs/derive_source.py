#!/usr/bin/env python3
"""Derive a tracked RTL8733BS source: a qualified source tree plus patches.

The installed sources (build/rtl-next-source-02 to -04) were each made as a
clone of the previous tree and `git am` of one patch. This does the same
from an exact, clean base commit, so the result differs from the base by
the listed patches only. The output is a fresh git repository under the
main checkout's build/, for kernel/build_verified_module.py --source and
--source-commit. A receipt (base, patches, new HEAD) is written beside it
as DEST-receipt.json.

  kernel/rtl8733bs/derive_source.py --base build/rtl-next-source-04 \\
      --base-commit c7e3c696e3a9c1a93b282ff8d2a58cc55dfc1e61 \\
      --patch kernel/rtl8733bs/patches/0015-Add-a-build-option-for-LPS-power-gating.patch \\
      --dest build/rtl-lps-pg-source-01
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
# The main checkout, also from a worktree: build_verified_module.py admits
# sources under this build/ only.
ROOT = Path(subprocess.check_output(['git', '-C', str(HERE), 'rev-parse', '--path-format=absolute',
                                     '--git-common-dir'], text=True).strip()).parent
GIT_ENV = {
    'GIT_AUTHOR_NAME': 'Q2 source preparation', 'GIT_AUTHOR_EMAIL': 'noreply@localhost',
    'GIT_COMMITTER_NAME': 'Q2 source preparation', 'GIT_COMMITTER_EMAIL': 'noreply@localhost',
    'GIT_AUTHOR_DATE': '@0 +0000', 'GIT_COMMITTER_DATE': '@0 +0000',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=cwd, check=True,
                          env={**os.environ, **GIT_ENV}, capture_output=True, text=True).stdout.strip()


def under_build(path):
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT / 'build'):
        sys.exit(f'{path} must be under {ROOT / "build"}')
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', type=Path, required=True, help='tracked source repository under build/')
    ap.add_argument('--base-commit', required=True, help='its exact HEAD (40 hex digits)')
    ap.add_argument('--patch', type=Path, action='append', required=True, help='mail-format patch, in order')
    ap.add_argument('--dest', type=Path, required=True, help='new repository under build/')
    a = ap.parse_args()
    base, dest = under_build(a.base), under_build(a.dest)
    receipt = dest.with_name(dest.name + '-receipt.json')
    if dest.exists() or receipt.exists():
        sys.exit(f'Refusing existing output: {dest}')
    if len(a.base_commit) != 40 or any(c not in '0123456789abcdef' for c in a.base_commit):
        sys.exit('--base-commit: 40 lower-case hex digits')
    if Path(git(base, 'rev-parse', '--show-toplevel')).resolve() != base:
        sys.exit('--base is not the top of its repository')
    if git(base, 'rev-parse', 'HEAD') != a.base_commit:
        sys.exit('--base HEAD differs from --base-commit')
    if git(base, 'status', '--porcelain', '--untracked-files=no'):
        sys.exit('--base has uncommitted tracked changes')
    patches = [p.resolve() for p in a.patch]
    staging = dest.with_name(dest.name + '.staging')
    shutil.rmtree(staging, ignore_errors=True)
    git(base.parent, 'clone', '-q', '--no-local', '--no-hardlinks', str(base), str(staging))
    if git(staging, 'rev-parse', 'HEAD') != a.base_commit:
        shutil.rmtree(staging)
        sys.exit('clone HEAD differs from --base-commit')
    git(staging, 'am', '-q', '--committer-date-is-author-date', *map(str, patches))
    head = git(staging, 'rev-parse', 'HEAD')
    changed = git(staging, 'diff', '--name-only', a.base_commit, head).split()
    staging.rename(dest)
    record = dict(base=str(base), base_commit=a.base_commit, dest=str(dest), head=head,
                  patches=[dict(path=str(p), sha256=sha(p)) for p in patches], changed_files=changed,
                  tool_sha256=sha(Path(__file__)))
    receipt.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(dest=str(dest), head=head, changed_files=changed), indent=2))


if __name__ == '__main__':
    main()
