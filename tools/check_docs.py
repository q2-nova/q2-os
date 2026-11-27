#!/usr/bin/env python3
"""Check the repository's Markdown documents.

Every relative link must reach a tracked file, and an anchor must name a
heading in the target. Links listed in tools/doc-links-pending.txt point at
documents that are not ported yet; they are allowed until the target exists.

The hardware and system references (REFERENCES below) must give every
table row a source and a level, and must carry no dates, process narration
or references to people.
"""
import pathlib
import re
import subprocess
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
PENDING = ROOT / 'tools' / 'doc-links-pending.txt'
REFERENCES = ('docs/hardware.md', 'docs/boot-chain.md', 'docs/stock-firmware.md',
              'docs/bluetooth.md', 'docs/network-time.md', 'docs/system-sleep.md',
              'docs/usb-host.md', 'docs/usb-storage.md', 'docs/wifi-security.md',
              'os/hardware/PLAYER.md')
LEVELS = ('device-verified', 'emulator', 'datasheet', 'vendor source',
          'stock firmware', 'host', 'unverified')
LINK = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)\)')
DATE = re.compile(r'\b(19|20)\d\d-\d\d-\d\d\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? (19|20)\d\d\b')
NARRATION = re.compile(r'(?i)\brecorded in `?[0-9a-f]{7,}|\bthe agent\b|\bagent (infers|implementation)')
PEOPLE = re.compile(r"(?i)\b(device owner|the owner|owner's)\b|\b(he|she|his|her|him)\b")


def tracked_markdown():
    out = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '--cached', '--others', '--exclude-standard', '*.md'], text=True)
    return [line for line in out.splitlines() if line]


def slug(heading):
    text = re.sub(r'`([^`]*)`', r'\1', heading.strip()).lower()
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    kept = ''.join(c for c in text if c in ' -_' or unicodedata.category(c)[0] in 'LN')
    return kept.replace(' ', '-')


def anchors(path):
    seen, result = {}, set()
    fence = False
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('```'):
            fence = not fence
            continue
        m = None if fence else re.match(r'#{1,6}\s+(.*?)\s*#*\s*$', line)
        if m:
            base = slug(m.group(1))
            n = seen.get(base, 0)
            seen[base] = n + 1
            result.add(base if n == 0 else f'{base}-{n}')
    return result


def links(path):
    fence = False
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.startswith('```'):
            fence = not fence
            continue
        if not fence:
            for m in LINK.finditer(re.sub(r'`[^`]*`', '', line)):
                yield number, m.group(1)


def check_links(files, pending):
    errors, used = [], set()
    tracked = set(files) | set(subprocess.check_output(
        ['git', '-C', str(ROOT), 'ls-files', '--cached', '--others', '--exclude-standard'], text=True).splitlines())
    for name in files:
        source = ROOT / name
        for number, target in links(source):
            if re.match(r'[a-z][a-z0-9+.-]*:', target):
                continue
            file_part, _, anchor = target.partition('#')
            dest = (source.parent / file_part).resolve() if file_part else source
            try:
                rel = dest.relative_to(ROOT).as_posix()
            except ValueError:
                errors.append(f'{name}:{number}: {target}: outside the repository')
                continue
            key = f'{name} {target}'
            if rel not in tracked and not dest.is_dir():
                if key in pending:
                    used.add(key)
                    continue
                errors.append(f'{name}:{number}: {target}: no such file')
                continue
            if anchor and dest.suffix == '.md' and anchor not in anchors(dest):
                if key in pending:
                    used.add(key)
                    continue
                errors.append(f'{name}:{number}: {target}: no such heading')
    for key in sorted(pending - used):
        errors.append(f'{PENDING.relative_to(ROOT)}: {key}: resolves or is gone; remove it')
    return errors


def check_reference(name):
    errors = []
    header = None
    for number, line in enumerate((ROOT / name).read_text(encoding='utf-8').splitlines(), 1):
        if DATE.search(line):
            errors.append(f'{name}:{number}: date')
        if NARRATION.search(line):
            errors.append(f'{name}:{number}: process narration')
        if PEOPLE.search(line):
            errors.append(f'{name}:{number}: reference to a person')
        if not line.startswith('|'):
            header = None
            continue
        cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip())[1:-1]]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r':?-+:?', c) for c in cells):
            continue
        row = dict(zip(header, cells))
        if 'source' in row and not row['source']:
            errors.append(f'{name}:{number}: row without a source')
        if 'level' in row and not row['level'].startswith(LEVELS):
            errors.append(f'{name}:{number}: level {row["level"]!r} is not one of {", ".join(LEVELS)}')
    return errors


def main():
    pending = set()
    if PENDING.exists():
        for line in PENDING.read_text(encoding='utf-8').splitlines():
            if line.strip() and not line.startswith('#'):
                pending.add(' '.join(line.split()))
    errors = check_links(tracked_markdown(), pending)
    for name in REFERENCES:
        errors += check_reference(name)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
