#!/usr/bin/env python3
"""Build a disposable FAT32 microSD and evdev prelude for an installed Q2 guest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import wave


SOURCE_ROOT = Path(__file__).resolve().parents[2]
CARD_BYTES = 128 * 1024**2
OFFSET = 1024**2
TRACK_SECONDS = 120
TRACK_ID = 'generated-portal-track'
TRACK = 'media/offline/portal-fixture.wav'
GUEST_CARD = '/mnt/mmc/Q2Mainline'
CONFIG = '''[server]
url = "http://192.0.2.1"
user = "generated-portal"
password = "generated-only"
[wifi]
enabled = false
[tailscale]
enabled = false
[playback]
sync_queue = false
scrobble = false
[storage]
reserve_mb = 0
prefetch_songs = 0
[power]
sleep_minutes = 0
'''


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def play_event(value):
    # Same Q2 keycode and 32-bit little-endian evdev records as device_input.py.
    events = struct.pack('<IIHHi', 0, 0, 1, 108, value) + bytes(16)
    return ''.join(f'\\{byte:03o}' for byte in events)


def guest_script():
    return r'''#!/bin/sh
# Prepend this to the owned browser phase. Call playback_sample before/after
# the measured browser interval to retain numeric ALSA progress in guest stdout.
set -eu
playback_status=/proc/asound/Q2/pcm0p/sub0/status
playback_pid_file=/run/q2nd/player.pid

playback_sample() {
    playback_label=$1
    playback_now=$(cut -d ' ' -f 1 /proc/uptime)
    playback_current_pid=$(cat "$playback_pid_file")
    [ "$playback_current_pid" = "$playback_pid" ] && kill -0 "$playback_pid"
    playback_position=$(awk '
        /^state:/ { running=($2 == "RUNNING") }
        /^hw_ptr[[:space:]]*:/ { sub(/^[^:]*:[[:space:]]*/, ""); hw=$1 }
        /^appl_ptr[[:space:]]*:/ { sub(/^[^:]*:[[:space:]]*/, ""); appl=$1 }
        END {
            if (!running || hw !~ /^[0-9]+$/ || appl !~ /^[0-9]+$/) exit 1
            printf "%s %s", hw, appl
        }' "$playback_status")
    playback_hw=${playback_position%% *}
    [ "$playback_hw" -gt "$playback_last_hw" ]
    playback_last_hw=$playback_hw
    printf 'Q2_GENERATED_PCM %s %s %s %s\n' "$playback_label" "$playback_now" "$playback_pid" "$playback_position"
}

grep -q '^/dev/mmcblk0p1 /mnt/mmc ' /proc/mounts
test -s /mnt/mmc/Q2Mainline/media/offline/portal-fixture.wav
playback_pid=$(cat "$playback_pid_file")
case "$playback_pid" in ''|*[!0-9]*) exit 1 ;; esac
[ "$playback_pid" -gt 1 ] && kill -0 "$playback_pid"
test "$(cat "$playback_status")" = closed
keys=
for event in /sys/class/input/event*; do
    if test "$(cat "$event/device/name")" = 'Q2 buttons'; then
        keys=/dev/input/${event##*/}
        break
    fi
done
test -n "$keys"
printf 'PLAY_DOWN' > "$keys"
printf 'PLAY_UP' > "$keys"
attempt=0
while ! grep -q '^state:[[:space:]]*RUNNING' "$playback_status"; do
    test "$attempt" -lt 100
    attempt=$((attempt + 1))
    sleep .1
done
playback_last_hw=-1
playback_sample start
sleep .3
playback_sample advanced
'''.replace('PLAY_DOWN', play_event(1)).replace('PLAY_UP', play_event(0))


def create_output(out):
    out = out.resolve()
    if not any(out.is_relative_to(root / 'build') for root in
               (SOURCE_ROOT, Path(subprocess.check_output([
                   'git', '-C', SOURCE_ROOT, 'rev-parse', '--path-format=absolute',
                   '--git-common-dir'], text=True).strip()).parent)):
        raise ValueError('playback fixture output must be under build/')
    out.mkdir(parents=True, exist_ok=False)
    return out


def create(out, *, flac=False):
    out = create_output(out)
    data = out / 'Q2Mainline'
    track = data / TRACK
    track.parent.mkdir(parents=True)
    (data / 'config.toml').write_text(CONFIG)
    db_path = data / 'library.db'
    tracks = [track]
    if flac:
        tracks = [track.with_name(f'track{i}.flac') for i in range(1, 4)]
        for i, file in enumerate(tracks, 1):
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi',
                            '-i', f'sine=frequency={431 + 300*i}:sample_rate=44100:duration=60',
                            '-f', 'lavfi', '-i',
                            f'anoisesrc=seed={i}:sample_rate=44100:duration=60:amplitude=0.17',
                            '-filter_complex', '[0:a][1:a]amerge', '-sample_fmt', 's16',
                            '-c:a', 'flac', str(file)], check=True, timeout=20)
    seconds = 60 if flac else TRACK_SECONDS
    songs = [{'id': f'{TRACK_ID}-{i}' if flac else TRACK_ID,
              'title': f'Generated local playback {i}' if flac else 'Generated local playback',
              'suffix': file.suffix[1:], 'duration': seconds}
             for i, file in enumerate(tracks, 1)]
    queue = {'songs': songs, 'index': 0, 'position_ms': 0}
    with sqlite3.connect(db_path) as db:
        db.execute('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('CREATE TABLE offline_songs(song_id TEXT PRIMARY KEY,path TEXT NOT NULL,size INTEGER NOT NULL,song TEXT NOT NULL)')
        db.executemany('INSERT INTO kv VALUES(?,?)', [
            ('server', 'http://192.0.2.1|generated-portal'), ('queue', json.dumps(queue))])
        # One second of a repeating generated waveform. Write incrementally
        # so the generator never opens a real media library or holds a full track.
        if not flac:
            second = b''.join(struct.pack('<hh', sample, -sample)
                              for _ in range(44100 // 50) for sample in range(-2000, 2000, 80))
            with wave.open(str(track), 'wb') as audio:
                audio.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                for _ in range(seconds):
                    audio.writeframesraw(second)
        db.executemany('INSERT INTO offline_songs VALUES(?,?,?,?)', [
            (song['id'], f'{GUEST_CARD}/{file.relative_to(data)}', file.stat().st_size, json.dumps(song))
            for song, file in zip(songs, tracks)])
    for path in sorted(data.rglob('*'), reverse=True):
        os.utime(path, (1793404800, 1793404800))
    os.utime(data, (1793404800, 1793404800))

    image = out / 'sd.img'
    with image.open('xb') as stream:
        stream.truncate(CARD_BYTES)
        mbr = bytearray(512)
        mbr[446:462] = struct.pack('<B3sB3sII', 0, bytes(3), 12, bytes(3),
                                   OFFSET // 512, (CARD_BYTES - OFFSET) // 512)
        mbr[510:512] = b'\x55\xaa'
        stream.write(mbr)
    partition = f'{image}@@{OFFSET}'
    fat_env = {**os.environ, 'LC_ALL': 'C', 'TZ': 'UTC', 'MTOOLSRC': '/dev/null'}
    subprocess.run(['mformat', '-i', partition, '-F', '-N', '51324d4c', '-v', 'Q2', '::'],
                   check=True, timeout=12, env=fat_env)
    subprocess.run(['mcopy', '-m', '-s', '-i', partition, str(data), '::/'],
                   check=True, timeout=12, env=fat_env)
    receipt = {'hardware': False, 'scope': 'generated FAT32 SD and normal installed player',
               'image': image.name, 'image_bytes': CARD_BYTES, 'image_sha256': sha(image),
               'partition_offset': OFFSET, 'audio_seconds': len(tracks) * seconds,
               'inputs': {str(path.relative_to(data)): sha(path)
                          for path in (data / 'config.toml', db_path, *tracks)},
               'producer_sha256': sha(Path(__file__))}
    if not flac:
        script = out / 'playback.sh'
        script.write_text(guest_script())
        receipt['guest_script_sha256'] = sha(script)
    with (out / 'result.json').open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    return receipt


def create_direct_read(out):
    """Create an empty exFAT card for a guest-created direct-I/O fixture."""
    out = create_output(out)
    partition = out / 'partition.img'
    with partition.open('xb') as stream:
        stream.truncate(CARD_BYTES - OFFSET)
    subprocess.run(['mkfs.exfat', '-P', 'none', '-L', 'Q2', '-q', str(partition)],
                   check=True, timeout=12)
    image = out / 'sd.img'
    mbr = bytearray(512)
    mbr[446:462] = struct.pack('<B3sB3sII', 0, bytes(3), 7, bytes(3),
                               OFFSET // 512, (CARD_BYTES - OFFSET) // 512)
    mbr[510:512] = b'\x55\xaa'
    with image.open('xb') as destination, partition.open('rb') as source:
        destination.write(mbr)
        destination.seek(OFFSET)
        shutil.copyfileobj(source, destination)
    partition.unlink()
    receipt = {'hardware': False, 'scope': 'generated exFAT SD for direct I/O',
               'image': image.name, 'image_bytes': CARD_BYTES, 'image_sha256': sha(image),
               'partition_offset': OFFSET, 'producer_sha256': sha(Path(__file__))}
    with (out / 'result.json').open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        create(args.out)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(args.out / 'result.json')


if __name__ == '__main__':
    main()
