#!/usr/bin/env python3
"""Read committed builder milestones and handoff notes; never contact a device.

New kernel/audio/input work is a review candidate, not a readiness assertion.
Suitable for a local systemd user timer. No agent messages or automatic merges.
"""
import argparse
import fcntl
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from compatibility import PROFILES


def scan_locked(builder, notes, output):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(builder), *args], text=True).strip()
    output.mkdir(parents=True, exist_ok=True)
    previous = json.loads((output / 'status.json').read_text()) if (output / 'status.json').exists() else {}
    head = git('rev-parse', 'HEAD')
    files = git('ls-tree', '-r', '--name-only', head, 'kernel').splitlines()
    relevant = [p for p in files if any(word in p.lower() for word in ['audio', 'sound', 'alsa', 'wheel', 'input', 'watchdog', 'kexec'])]
    handoffs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(notes.glob('*.md')) if p.is_file()}
    kernel_tree = git('rev-parse', head + ':kernel')
    adopted = PROFILES['os-kernel']
    changed = head != previous.get('head') or handoffs != previous.get('handoff_sha256')
    kernel_changed = kernel_tree != previous.get('kernel_tree')
    status = {'checked_at': datetime.now(timezone.utc).isoformat(), 'head': head,
              'kernel_tree': kernel_tree, 'kernel_changed_since_last_check': kernel_changed,
              'adopted_kernel_commit': adopted['origin_commit'],
              'kernel_differs_from_adopted': kernel_tree != adopted['origin_tree'],
              'recent_commits': git('log', '-8', '--format=%h %s', head).splitlines(),
              'integration_candidate_paths': relevant, 'handoff_sha256': handoffs,
              'readiness': 'manual integration tests required', 'physical_device_access': False}
    if changed:
        with (output / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(status) + '\n')
    temporary = output / 'status.json.new'
    temporary.write_text(json.dumps(status, indent=2) + '\n')
    temporary.replace(output / 'status.json')
    print(json.dumps({'head': head, 'changed': changed, 'kernel_changed': kernel_changed,
                      'status': str(output / 'status.json')}))


def scan(builder, notes, output):
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.lock').open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        scan_locked(builder, notes, output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--builder', required=True, type=Path)
    parser.add_argument('--notes', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    scan(args.builder.resolve(), args.notes.resolve(), args.output.resolve())
