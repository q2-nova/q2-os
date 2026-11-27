#!/usr/bin/env python3
"""Strip DWARF on NEW artifact copies, preserving allocated bytes and symbols.

An offline packaging experiment, never a deployment or runtime-RAM measurement.
The input executables must be public generated build artifacts, not owner data.
"""
import argparse
import json
from pathlib import Path
import subprocess
from profile_inventory import inventory, named_file

ROOT = Path(__file__).resolve().parents[1]


def experiment(strip, inputs, output):
    output = output.absolute()
    if not output.resolve().is_relative_to(ROOT / 'build') or output.exists():
        raise ValueError('output must be a NEW directory under this worktree build/')
    if not inputs or len({name for name, _ in inputs}) != len(inputs):
        raise ValueError('provide uniquely labelled artifacts')
    for name, source in inputs:
        named_file(name + '=' + str(source))
        if name == 'result.json':
            raise ValueError('result.json is reserved for the receipt')
    before = inventory('development', [], inputs)
    if any(not row['elf'] for row in before['artifacts'].values()):
        raise ValueError('strip inputs must be ELF')
    output.mkdir(parents=True)
    version = subprocess.check_output([str(strip), '--version'], text=True).splitlines()[0]
    copies = []
    for name, source in inputs:
        destination = output / name
        subprocess.run([str(strip), '--strip-debug', '-o', str(destination), str(source)], check=True)
        copies.append((name, destination))
    after = inventory('production', [], copies)
    result = {'schema': 1, 'experiment': 'isolated --strip-debug copies; not deployment',
              'strip_version': version, 'before': before, 'after': after, 'saved_bytes': {}}
    for name, original in before['artifacts'].items():
        changed = after['artifacts'][name]
        if original['elf']['allocated_sha256'] != changed['elf']['allocated_sha256']:
            raise ValueError('allocated sections changed: ' + name)
        original_symbols = {x['name'] for x in original['elf']['sections'] if x['name'] in ('.symtab', '.strtab')}
        changed_symbols = {x['name'] for x in changed['elf']['sections'] if x['name'] in ('.symtab', '.strtab')}
        # --strip-debug also removes debug-only symbol entries, so sizes need
        # not match. Preserve tables and archive the full original separately.
        if original_symbols != changed_symbols:
            raise ValueError('symbol table removed: ' + name)
        result['saved_bytes'][name] = original['bytes'] - changed['bytes']
    # Re-read sources to detect mutation during the experiment, rather than
    # silently assigning the original identity to a changing input.
    if inventory('development', [], inputs) != before:
        raise ValueError('input changed during experiment')
    (output / 'result.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strip-tool', type=Path, required=True)
    parser.add_argument('--artifact', type=named_file, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = experiment(args.strip_tool, args.artifact, args.output)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    print(json.dumps(result['saved_bytes'], sort_keys=True))


if __name__ == '__main__':
    main()
