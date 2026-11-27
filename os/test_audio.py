#!/usr/bin/env python3
"""Buildroot libasound/time64 and player playback in disposable QEMU only."""
import argparse
import hashlib
import json
import math
import re
import socket
import struct
import time
import wave
from build import ROOT, WORK
from test_boot import Guest
from test_faults import disk_copy


def audio_guest(icount, disk=None):
    guest = Guest(WORK / 'kernel-disk/uImage.gz', disk or disk_copy(), icount, exclusive_clock=not icount)
    path = '/work/' + str(guest.out.relative_to(ROOT))
    guest.command[guest.command.index('-M') + 1] = 'q2,audio-wav=' + path + '/capture.wav'
    guest.command += ['-chardev', 'socket,id=q2ctl,path=' + path + '/ctl.sock,server=on,wait=off']
    return guest


def control(guest, command):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(str(guest.out / 'ctl.sock'))
        sock.sendall(command.encode() + b'\n')
        reply = b''
        while not reply.endswith(b'\n'):
            chunk = sock.recv(4096)
            if not chunk: raise RuntimeError('QEMU control closed')
            reply += chunk
        if reply != b'ok\n' and not reply.startswith(b'ok '): raise RuntimeError(reply)
        return reply[3:].decode().strip()


def registers(guest, label):
    addresses = [0x1000007c, 0x10000020, 0x10000028, 0x134d6018,
                 0x134d4008, 0x134d4020, 0x134d4028]
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(str(guest.out / 'monitor.sock'))
        def prompt():
            reply = b''
            while b'(qemu)' not in reply:
                chunk = sock.recv(65536)
                if not chunk: raise RuntimeError('QEMU monitor closed')
                reply += chunk
            return reply
        prompt()
        values = {}
        transcript = b''
        for address in addresses:
            sock.sendall(f'xp /1wx {address:#x}\n'.encode())
            reply = prompt()
            transcript += reply
            found = re.findall(rb':\s+0x([0-9a-fA-F]+)', reply)
            if len(found) != 1: raise RuntimeError(reply)
            values[address] = int(found[0], 16)
        (guest.out / (label + '-registers.txt')).write_bytes(transcript)
        return values


def expect(guest, label, condition):
    if not condition: raise AssertionError(label)
    guest.result['checks'].append(label)


def gate(guest, name, advance=True):
    if advance: guest.serial.sendall(b'\n')
    data = guest.receive(('ALSA_' + name + r'\r?\n|ALSA_FAIL|ALSA_RC=\d+').encode(), 90)
    expect(guest, 'libasound ' + name.lower(), ('ALSA_' + name).encode() in data)
    return data


def pcm_capture(guest):
    with wave.open(str(guest.out / 'capture.wav'), 'rb') as wav:
        metadata = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
        capture = wav.readframes(wav.getnframes())
    return metadata, capture


def probe(guest, rate):
    width = 2 if rate == 44100 else 4
    guest.result.update(rate=rate, storage_bytes=width, test='libasound')
    guest.serial.sendall(f'Q2_EMULATOR_TEST=1 q2-alsa-probe {rate}; echo ALSA_RC=$?\n'.encode())
    data = gate(guest, 'PAUSED', False)
    expect(guest, 'time64 ALSA timestamp after 2038', b'ALSA_TIME64=2208988' in data)
    dac = json.loads(control(guest, 'dac'))
    guest.result['playing_dac'] = dac
    ready = 0x10 if rate == 44100 else 0x14
    expect(guest, 'four codecs routed, clocked and set to -30 dB', len(dac) == 4 and all(
        not d['reset'] and d['pcm_path'] == (5 if i < 2 else 7) and
        d['volume_a'] == d['volume_b'] == 60 and d['ready'] & ready == ready
        for i, d in enumerate(dac)))
    regs = registers(guest, 'paused')
    cdr = regs[0x1000007c]
    mclk = 1200000000 * ((cdr >> 20) & 511) // (cdr & 0xfffff)
    expect(guest, 'fractional MCLK and BAIC sample rate', not cdr & (1 << 30) and
           mclk in (22579200, 24576000) and mclk // ((regs[0x134d6018] >> 16) & 255) // 64 == rate)
    expect(guest, 'DSP DMA0 to BAIC1 slot 9', regs[0x134d4008] == 512 and
           regs[0x134d4020] == 9 << 10 and regs[0x134d4028] == 9 << 5)
    before = (guest.out / 'capture.wav').read_bytes()
    time.sleep(.1)
    expect(guest, 'pause stops capture', (guest.out / 'capture.wav').read_bytes() == before)
    gate(guest, 'DRAINED')
    meta, capture = pcm_capture(guest)
    expect(guest, 'PCM capture metadata', meta == (rate, 2, width))
    if rate == 44100:
        expected = b''.join(struct.pack('<h', (i * m) % 60001 - 30000)
                            for i in range(rate // 2) for m in (173, 319))
    elif rate == 48000:
        expected = b''.join(struct.pack('<i', (i * m) % 16000001 - 8000000)
                            for i in range(rate // 2) for m in (44461, 81983))
    else:
        expected = b''.join(struct.pack('<I', (i * m + 1013904223) & 0xffffffff)
                            for i in range(rate // 2) for m in (1664525, 22695477))
    expect(guest, 'every source PCM sample captured exactly', capture[:len(expected)] == expected)
    extra = (len(capture) - len(expected)) // (2 * width)
    guest.result.update(source_frames=rate // 2, drain_extra_frames=extra,
                        first_capture_sha256=hashlib.sha256(capture).hexdigest())
    expect(guest, 'drain excess bounded by period plus poll', 0 <= extra <= 1024 + rate // 1000)
    expect(guest, 'drain stops DMA', 'ch0=stop' in control(guest, 'audio'))
    gate(guest, 'CLOSED')
    expect(guest, 'four codecs share asserted reset after suspend', all(d['reset'] for d in json.loads(control(guest, 'dac'))))
    regs = registers(guest, 'closed')
    expect(guest, 'audio clocks gated after close', regs[0x10000020] & (1 << 11) and regs[0x10000028] & (1 << 9))
    gate(guest, 'REOPENED')
    expect(guest, 'codec caches restored after runtime PM', json.loads(control(guest, 'dac')) == dac)
    guest.serial.sendall(b'\n')
    data = guest.receive(rb'ALSA_RC=\d+\r?\n', 90)
    expect(guest, 'libasound XRUN recovery and reopen passed', b'ALSA_PASS' in data and b'ALSA_RC=0' in data)


def player(guest, pause=False):
    guest.result['test'] = 'player-kms-alsa'
    guest.result['pause_resume'] = pause
    # Keep the script alive until the harness observes the decoder's drain.
    # A fixed five-second exit can cut off a four-second song when normal-time
    # emulation falls behind while loading/decoding; that is not EOF evidence.
    tail = ('play\\nwait 8000\\nplay\\n' if pause else '') + 'wait 90000\\n'
    guest.check('prepare synthetic player library',
        'mkdir -p /tmp/q2-audio-data && cp /etc/q2-defaults/config.toml /tmp/q2-audio-data/config.toml && '
        'cp /usr/share/q2-fixtures/ui.script /tmp/q2-audio.script && '
        f"printf '{tail}' >> /tmp/q2-audio.script")
    guest.check('start player with mainline KMS and ALSA',
        'Q2ND_DRM_ALSA=1 q2nd --drm-test --data /tmp/q2-audio-data '
        '--script /tmp/q2-audio.script > /tmp/q2-audio.log 2>&1 & echo $! > /tmp/q2-audio.pid')
    if pause:
        guest.check('player reaches ALSA paused state',
            "timeout 40 sh -c 'until grep -q PAUSED /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.05; done'", 60)
        before = (guest.out / 'capture.wav').read_bytes()
        guest.check('player PCM stays paused',
            'sleep 0.2; grep -q PAUSED /proc/asound/Q2/pcm0p/sub0/status')
        expect(guest, 'player pause freezes DMA capture', (guest.out / 'capture.wav').read_bytes() == before)
    guest.check('player reaches active PCM before natural end',
        "timeout 40 sh -c 'until grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.05; done'", 60)
    guest.check('player naturally drains the complete song',
        "timeout 45 sh -c 'until grep -Eq \"SETUP|closed\" /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.1; done'", 75)
    guest.check('player exits cleanly after song without playback errors',
        'pid=$(cat /tmp/q2-audio.pid); kill -TERM "$pid"; wait "$pid" && '
        "grep -q 'audio=ALSA hw:Q2,0' /tmp/q2-audio.log && "
        "grep -q 'playing.*Synthetic tone' /tmp/q2-audio.log && "
        "! grep -Ei 'playback:|KMS presentation failed|panicked|ALSA.*error' /tmp/q2-audio.log", 120)
    guest.fetch('/tmp/q2-audio.log', 'player.log')
    guest.fetch('/tmp/q2-playing.png', 'playing.png')
    meta, capture = pcm_capture(guest)
    expect(guest, 'player PCM is 44.1 kHz stereo S32', meta == (44100, 2, 4))
    frames = list(struct.iter_unpack('<ii', capture))
    guest.result.update(captured_frames=len(frames), capture_sha256=hashlib.sha256(capture).hexdigest())
    expect(guest, 'complete four-second generated song captured', 176400 <= len(frames) <= 176400 + 16384)
    expect(guest, 'stereo channels match generated fixture', all(l == r for l, r in frames))
    # A steady half-second window after startup excludes fades, but detects
    # timing/corruption errors in the actual decoder/software-volume/PCM path.
    samples = [l for l, _ in frames[44100:66150]]
    energy = sum(x * x for x in samples)
    expect(guest, 'player outputs nonzero PCM', energy > 0)
    sin = sum(x * math.sin(i * 2 * math.pi * 440 / 44100) for i, x in enumerate(samples))
    cos = sum(x * math.cos(i * 2 * math.pi * 440 / 44100) for i, x in enumerate(samples))
    coherent = 2 * (sin * sin + cos * cos) / (len(samples) * energy)
    guest.result['tone_coherent_energy_fraction'] = coherent
    expect(guest, '440 Hz fixture survives player and DMA path', coherent > .999)
    expect(guest, 'player closes PCM DMA', 'ch0=stop' in control(guest, 'audio'))
    guest.check('allow delayed codec power-down', 'sleep 6')
    expect(guest, 'player close suspends all codecs', all(d['reset'] for d in json.loads(control(guest, 'dac'))))
    regs = registers(guest, 'player-closed')
    expect(guest, 'player close gates audio clocks', regs[0x10000020] & (1 << 11) and regs[0x10000028] & (1 << 9))
    guest.check('player close disables backlight', 'test "$(cat /sys/class/backlight/backlight/actual_brightness)" = 0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icount', action='store_true')
    parser.add_argument('--player', action='store_true')
    parser.add_argument('--pause', action='store_true', help='pause/resume the player mid-song')
    parser.add_argument('--rate', type=int, choices=(44100, 48000, 96000), default=44100)
    args = parser.parse_args()
    if args.pause and not args.player: parser.error('--pause requires --player')
    guest = audio_guest(args.icount)
    try:
        guest.start()
        guest.seed_entropy()
        guest.check('card Q2 discovered by ALSA ID', "grep -q Q2 /proc/asound/cards && test -r /proc/asound/Q2/pcm0p/info")
        if args.player: player(guest, args.pause)
        else: probe(guest, args.rate)
        console = (guest.out / 'console.log').read_text(errors='replace')
        expect(guest, 'no kernel or ASoC failures', not re.search(
            r'Kernel panic|Oops:|BUG:|WARNING:|ASoC:.*fail|Error waiting|Failed to', console))
        guest.result['passed'] = True
    except Exception as error:
        guest.result['error'] = str(error)
        raise
    finally:
        guest.close()
        tag = ('pause-' if args.pause else '') + ('icount' if args.icount else 'normal')
        (WORK / f'audio-{"player" if args.player else args.rate}-{tag}.json').write_text(json.dumps({
            'artifacts': str(guest.out.relative_to(ROOT)), **guest.result}, indent=2) + '\n')


if __name__ == '__main__': main()
