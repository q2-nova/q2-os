"""Bind Home/first Play file ranges to the immutable installed player."""

import hashlib
import json
from pathlib import Path
import re

APP = 'usr/bin/q2nd'
INIT = 'sbin/q2-installed-init'
PROFILE = 'startup-read-ahead/profile.json'
BLOCK = 128 * 1024
BEGIN = '    # BEGIN Q2 PLAYER READ-AHEAD\n'
END = '    # END Q2 PLAYER READ-AHEAD\n'
FALLBACK = '    nice -n 10 cat /usr/bin/q2nd "$@"\n'
LEGACY = 'nice -n 10 cat /usr/bin/q2nd /usr/lib/libasound.so.2.0.0 /usr/lib/libq2platform.so \\\n'
DIGEST = re.compile('[a-f0-9]{64}')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ranges(blocks):
    """Return ordered runs as (skip, count), in logical SquashFS blocks."""
    result = []
    for block in sorted(set(blocks)):
        if result and block == sum(result[-1]):
            start, count = result[-1]
            result[-1] = (start, count + 1)
        else:
            result.append((block, 1))
    return result


def admit(profile, player, block_size=BLOCK):
    """Validate against the staged executable, never just the app build path."""
    if profile.is_symlink() or not profile.is_file() or profile.stat().st_size > 128 * 1024:
        raise ValueError('startup read-ahead profile must be a bounded regular file')
    raw = profile.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('passed') is not True:
        raise ValueError('startup read-ahead profile did not pass')
    app_sha = value.get('app_sha256', '')
    if (not isinstance(app_sha, str) or not DIGEST.fullmatch(app_sha)
            or player.resolve() != player or not player.is_file() or sha(player) != app_sha
            or player.stat().st_size != value.get('app_bytes')):
        raise ValueError('startup read-ahead profile does not match installed player')
    if block_size != BLOCK or value.get('block_bytes') != BLOCK:
        raise ValueError('startup read-ahead requires matching 128 KiB blocks')
    result = dict(app_sha256=app_sha, app_bytes=value['app_bytes'], block_bytes=BLOCK,
                  profile_sha256=hashlib.sha256(raw).hexdigest())
    if value.get('sampling') == 'pagemap-unavailable' and value.get('blocks') is None:
        return dict(result, mode='full', reason='pagemap-unavailable', ranges=[])
    limit = (value['app_bytes'] + BLOCK - 1) // BLOCK
    stages = value.get('blocks')
    if not isinstance(stages, dict) or set(stages) != {'home', 'first_play'}:
        raise ValueError('startup read-ahead requires Home and first Play observations')
    for blocks in stages.values():
        if (not isinstance(blocks, list) or not blocks
                or any(type(n) is not int or not 0 <= n < limit for n in blocks)
                or blocks != sorted(set(blocks))):
            raise ValueError('startup read-ahead block list is invalid')
    combined = set(stages['home']) | set(stages['first_play'])
    if not all(0 in stage for stage in stages.values()):
        raise ValueError('startup read-ahead observations require the executable header')
    if len(combined) == limit:
        return dict(result, mode='full', reason='all-blocks-observed', ranges=[])
    return dict(result, mode='selected', ranges=ranges(combined))


def checked_record(record, player):
    """Read back the packed recipe using only the digest and integer file ranges."""
    if not isinstance(record, dict) or record.get('app_sha256') != sha(player):
        raise ValueError('read-ahead identity differs from packaged player')
    size = player.stat().st_size
    if record.get('app_bytes') != size or record.get('block_bytes') != BLOCK:
        raise ValueError('read-ahead geometry differs from packaged player')
    if record.get('mode') == 'full' and record.get('ranges') == []:
        return record
    runs = record.get('ranges')
    if record.get('mode') != 'selected' or not isinstance(runs, list) or not runs:
        raise ValueError('missing selected read-ahead ranges')
    limit = (size + BLOCK - 1) // BLOCK
    previous_end = -1
    for run in runs:
        if not isinstance(run, list) or len(run) != 2 or any(type(n) is not int for n in run):
            raise ValueError('integer read-ahead ranges required')
        start, count = run
        if start <= previous_end or count <= 0 or start + count > limit:
            raise ValueError('ordered, coalesced read-ahead ranges required')
        previous_end = start + count
    if runs[0][0] != 0:
        raise ValueError('first executable block missing from read-ahead')
    return record


def commands(record):
    if record is None or record['mode'] == 'full':
        return FALLBACK
    # Runtime guard handles a future composer inheriting the init script while
    # changing the immutable root's app/release metadata. Use ash builtins only.
    digest = record['app_sha256']
    return (
        '    while IFS= read -r metadata_line; do\n'
        '        case "$metadata_line" in\n'
        f'            \'  "app_sha256": "{digest}"\'|\'  "app_sha256": "{digest}",\')\n'
        + ''.join(f'                nice -n 10 dd if=/usr/bin/q2nd bs={BLOCK} skip={skip} count={count} || break\n'
                  for skip, count in record['ranges'])
        + '                [ "$#" = 0 ] || nice -n 10 cat "$@"\n'
        '                return 0 ;;\n'
        '            \'  "app_sha256": \'*) break ;;\n'
        '        esac\n'
        '    done < /etc/q2-os-release.json\n'
        + FALLBACK
    )


def render(source, record):
    """Render the complete init script from its template and release record."""
    if source.count(BEGIN) == source.count(END) == 1:
        before, body = source.split(BEGIN)
        _, after = body.split(END)
        updated = before + BEGIN + commands(record) + END + after
    elif source.count(LEGACY) == 1 and BEGIN not in source and END not in source:
        if not record or record['mode'] == 'full':
            return source
        # Older ordinary roots keep their original init except for this reader.
        function = 'player_read_ahead() {\n' + BEGIN + commands(record) + END + '}\n'
        updated = source.replace(LEGACY, function + LEGACY.replace(
            'nice -n 10 cat /usr/bin/q2nd ', 'player_read_ahead '))
    elif not record and BEGIN not in source and END not in source:
        return source
    else:
        raise ValueError('startup read-ahead could not identify the installed init reader')
    return updated


def install(root, profile, block_size=BLOCK):
    """Refresh the managed init region and return its exact packaging record."""
    init = root / INIT
    if profile is None and not init.exists():
        return None
    if init.resolve() != init or not init.is_file():
        raise ValueError('startup read-ahead requires a canonical installed init')
    source = init.read_text()
    record = admit(profile, root / APP, block_size) if profile is not None else None
    updated = render(source, record)
    if updated != source:
        init.write_text(updated)
    return record


def sidecar(build):
    profile = build / PROFILE
    return profile if profile.exists() or profile.is_symlink() else None
