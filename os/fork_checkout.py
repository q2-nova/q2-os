"""Materialize a selected public fork revision without modifying its source checkout."""
import os
from pathlib import Path
import subprocess
import tempfile


def checkout(destination, commit, variable, public_url):
    source = os.environ.get(variable) or public_url
    if destination.exists():
        verify(destination, commit)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        work = Path(tmp) / 'source'
        subprocess.run(['git', 'init', '-q', work], check=True)
        for args in (['fetch', '--quiet', '--depth=1', '--no-tags', '--', source, commit],
                     ['checkout', '--quiet', '--detach', commit]):
            subprocess.run(['git', '-C', work, *args], check=True)
        verify(work, commit)
        work.rename(destination)


def verify(path, commit):
    def git(*args):
        return subprocess.check_output(['git', '-C', path, *args], text=True).strip()
    if git('rev-parse', 'HEAD') != commit or git('status', '--porcelain', '--untracked-files=normal'):
        raise ValueError(f'Expected clean source at {commit} in {path}')
