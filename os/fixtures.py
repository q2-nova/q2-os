#!/usr/bin/env python3
"""Deterministic local-only media/API fixtures; no private/server input."""
import json
import math
from pathlib import Path
import struct
import wave
import zlib

def generate(dest, seconds=4):
    dest = Path(dest)
    rest = dest / 'rest'
    rest.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest / 'tone.wav'), 'wb') as f:
        f.setnchannels(2); f.setsampwidth(2); f.setframerate(44100)
        data = bytearray()
        for i in range(44100 * seconds):
            value = int(3000 * math.sin(i * 2 * math.pi * 440 / 44100))
            data.extend(struct.pack('<hh', value, value))
        f.writeframes(data)
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    pixels = b''.join(
        b'\0' + b''.join(bytes((x * 4, y * 4, 128)) for x in range(64)) for y in range(64))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
    (dest / 'cover.png').write_bytes(png)
    song = {'id': 'fixture-song', 'title': 'Synthetic tone', 'album': 'Q2 Test Album',
            'albumId': 'fixture-album', 'artist': 'Fixture Artist', 'artistId': 'fixture-artist',
            'duration': seconds, 'size': (dest / 'tone.wav').stat().st_size, 'suffix': 'wav',
            'contentType': 'audio/wav', 'isDir': False, 'coverArt': 'fixture-cover', 'track': 1}
    album = {'id': 'fixture-album', 'name': 'Q2 Test Album', 'artist': 'Fixture Artist',
             'artistId': 'fixture-artist', 'songCount': 1, 'duration': seconds, 'coverArt': 'fixture-cover', 'song': [song]}
    artist = {'id': 'fixture-artist', 'name': 'Fixture Artist', 'albumCount': 1, 'album': [album]}
    data = {
        'ping': {}, 'getAlbumList2': {'albumList2': {'album': [album]}},
        'getAlbum': {'album': album}, 'getSong': {'song': song},
        'getArtists': {'artists': {'index': [{'name': 'F', 'artist': [artist]}]}},
        'getArtist': {'artist': artist}, 'getArtistInfo2': {'artistInfo2': {}},
        'getPlaylists': {'playlists': {'playlist': []}}, 'getGenres': {'genres': {'genre': []}},
        'getPlayQueue': {'playQueue': {'entry': []}}, 'savePlayQueue': {},
        'getStarred2': {'starred2': {'artist': [], 'album': [], 'song': []}},
        'getRandomSongs': {'randomSongs': {'song': [song]}},
        'getInternetRadioStations': {'internetRadioStations': {'internetRadioStation': [
            {'id': 'fixture-radio', 'name': 'Local fixture', 'streamUrl': 'http://127.0.0.1:4533/tone.wav'}]}},
        'scrobble': {}, 'getOpenSubsonicExtensions': {'openSubsonicExtensions': []},
    }
    for endpoint, body in data.items():
        (rest / endpoint).write_text(json.dumps({'subsonic-response': {'status': 'ok', 'version': '1.16.1', **body}}))
    (rest / 'stream').write_bytes((dest / 'tone.wav').read_bytes())
    (rest / 'getCoverArt').write_bytes(png)
    guest = '/usr/share/q2-fixtures'
    specs = {
        'decode': {'kind': 'decode', 'fixture': guest + '/tone.wav'},
        'eq': {'kind': 'eq', 'iterations': 3},
        'image': {'kind': 'image', 'fixture': guest + '/cover.png', 'iterations': 3},
        'radio': {'kind': 'radio-player', 'url': 'http://127.0.0.1:4533/tone.wav', 'radio_seconds': 2},
        'ui': {'kind': 'ui', 'scratch': '/tmp/q2-ui-fixture', 'iterations': 3},
    }
    for name, spec in specs.items(): (dest / (name + '.json')).write_text(json.dumps(spec))
    (dest / 'ui.script').write_text('wait 1200\nshot /tmp/q2-home.png\ncenter\nwait 500\nshot /tmp/q2-menu.png\nscroll 3\ncenter\nwait 1800\nshot /tmp/q2-albums.png\ncenter\nwait 1000\nshot /tmp/q2-tracks.png\ncenter\nwait 1200\nshot /tmp/q2-playing.png\n')

if __name__ == '__main__':
    import sys
    generate(sys.argv[1])
