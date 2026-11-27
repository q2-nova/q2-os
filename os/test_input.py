#!/usr/bin/env python3
"""SDK time64 evdev and player interaction driven solely by QEMU controls."""
import argparse
import json
import re
import shutil
import sqlite3
import tarfile
import uuid
from pathlib import Path
from build import ROOT, WORK, container
from image import STATE_START, STATE_SIZE
from fixtures import generate
from test_faults import disk_copy
from test_audio import audio_guest, control, expect, pcm_capture


def fixture_disk():
    fixture = WORK / 'tests' / ('input-fixture-' + uuid.uuid4().hex[:10])
    generate(fixture, seconds=40)
    disk = disk_copy()
    state = disk.with_suffix('.state.ext4')
    with disk.open('rb') as source, state.open('wb') as dest:
        source.seek(STATE_START); dest.write(source.read(STATE_SIZE))
    commands = ['mkdir /input-fixtures', 'mkdir /input-fixtures/rest']
    for p in sorted(fixture.rglob('*')):
        if p.is_file(): commands.append(f'write /work/{p.relative_to(ROOT)} /input-fixtures/{p.relative_to(fixture)}')
    batch = fixture / 'debugfs.txt'; batch.write_text('\n'.join(commands) + '\n')
    container(['debugfs', '-w', '-f', '/work/' + str(batch.relative_to(ROOT)), '/work/' + str(state.relative_to(ROOT))])
    with state.open('rb') as source, disk.open('r+b') as dest:
        dest.seek(STATE_START); shutil.copyfileobj(source, dest)
    state.unlink()
    return disk


def key(guest, name, hold=.08):
    control(guest, f'key {name} down')
    guest.check('held physical-model button ' + name, f'sleep {hold}')
    control(guest, f'key {name} up')
    guest.check('released physical-model button ' + name, 'sleep 0.25')


def wheel(guest, positions):
    for position in positions:
        control(guest, f'wheel {position}')
        guest.check('wheel position processed', 'sleep 0.25')
    control(guest, 'wheel up')
    guest.check('wheel lift processed', 'sleep 0.25')


def sdk(guest):
    guest.check('set generated future clock', 'old=$(date +%s); date -u -s @2208988801')
    for cycle in range(2):
        guest.check('start SDK wheel reader', 'rm -f /tmp/q2-input.rc; (q2-input-probe 1 20 > /tmp/q2-input.log 2>&1; echo $? > /tmp/q2-input.rc) & true')
        guest.check('reader opened by device name', "timeout 10 sh -c 'until grep -q INPUT_READY /tmp/q2-input.log; do sleep 0.05; done'")
        expected = []; active = False; last = 0
        for position in [1, 200, 199, 1, 0, 200, 0]:
            control(guest, f'wheel {position}' if position else 'wheel up')
            if position and position != last: expected.append((3, 0, position)); last = position
            if bool(position) != active:
                expected += [(3, 24, int(bool(position))), (1, 330, int(bool(position)))]; active = bool(position)
            expected.append((0, 0, 0))
            guest.check('exact event count', f'sleep 0.05; test "$(grep -c ^EVENT /tmp/q2-input.log)" = {len(expected)}')
            if position == 199:
                control(guest, 'wheel 199')
                guest.check('duplicate position suppressed', f'sleep 0.05; test "$(grep -c ^EVENT /tmp/q2-input.log)" = {len(expected)}')
        data = guest.check('SDK time64 reader succeeds', 'timeout 10 sh -c \'until test -s /tmp/q2-input.rc; do sleep 0.05; done\'; test "$(cat /tmp/q2-input.rc)" = 0 && cat /tmp/q2-input.log')
        events = [tuple(map(int, m)) for m in re.findall(rb'EVENT \d+ \d+ (\d+) (\d+) (-?\d+)', data)]
        expect(guest, 'exact SDK touch/wrap/lift events after 2038', events == expected)
        (guest.out / f'input-{cycle}.txt').write_bytes(data)
        if cycle == 0:
            control(guest, 'wheel 99')
            guest.check('unbind held wheel', 'echo 2-0015 > /sys/bus/i2c/drivers/shanling-q2-wheel/unbind')
            control(guest, 'wheel spin 1 200 100')
            guest.check('rebind wheel after reset-time input', 'sleep 0.2; echo 2-0015 > /sys/bus/i2c/drivers/shanling-q2-wheel/bind')
    guest.check('start SDK button reader', 'rm -f /tmp/q2-input.rc; (q2-input-probe 0 20 > /tmp/q2-input.log 2>&1; echo $? > /tmp/q2-input.rc) & true')
    guest.check('button reader opened', "timeout 10 sh -c 'until grep -q INPUT_READY /tmp/q2-input.log; do sleep 0.05; done'")
    expected=[]
    for name, code in [('power',116),('up',103),('down',108),('prev',165),('next',163)]:
        key(guest,name)
        expected += [(1,code,1),(0,0,0),(1,code,0),(0,0,0)]
    data=guest.check('button reader completes', 'timeout 10 sh -c \'until test -s /tmp/q2-input.rc; do sleep 0.05; done\'; test "$(cat /tmp/q2-input.rc)" = 0 && cat /tmp/q2-input.log')
    events=[tuple(map(int,m)) for m in re.findall(rb'EVENT \d+ \d+ (\d+) (\d+) (-?\d+)',data)]
    expect(guest,'all five button mappings through SDK time64 ABI',events==expected)
    guest.check('restore disposable clock', 'date -u -s "@$old"')


def player(guest):
    guest.check('prepare generated interactive player',
        'mkdir -p /tmp/q2-input-data /tmp/q2-input-runtime; '
        'sed s/4533/4544/ /etc/q2-defaults/config.toml > /tmp/q2-input-data/config.toml; '
        'httpd -f -vv -p 127.0.0.1:4544 -h /state/input-fixtures > /tmp/q2-http.log 2>&1 & true')
    guest.check('start KMS ALSA evdev player without input script',
        'Q2ND_RUNTIME=/tmp/q2-input-runtime Q2ND_DRM_INPUT=1 Q2ND_DRM_ALSA=1 '
        'q2nd --drm-test --data /tmp/q2-input-data > /tmp/q2-input-player.log 2>&1 & echo $! > /tmp/q2-input.pid')
    guest.check('interactive first frame ready', 'timeout 40 sh -c \'until test -s /tmp/q2-input-runtime/ready; do sleep 0.1; done\' && '
        'grep -q "input=evdev; audio=ALSA" /tmp/q2-input-player.log && sleep 1.5',60)
    key(guest,'power')
    wheel(guest,[100,80,60,40])
    key(guest,'power');guest.check('album list loaded','sleep 1.8')
    key(guest,'power');guest.check('tracks loaded','sleep 1')
    key(guest,'power')
    guest.check('wheel/button navigation starts intended song',
        "timeout 20 sh -c 'until grep -q \"playing.*Synthetic tone\" /tmp/q2-input-player.log; do sleep 0.1; done' && timeout 20 sh -c 'until grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.05; done'")
    key(guest,'down')
    guest.check('physical play/pause button pauses PCM',"timeout 10 sh -c 'until grep -q PAUSED /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.05; done'")
    before=(guest.out/'capture.wav').read_bytes()
    guest.check('pause remains stationary','sleep 0.3; grep -q PAUSED /proc/asound/Q2/pcm0p/sub0/status')
    expect(guest,'button pause freezes DMA', (guest.out/'capture.wav').read_bytes()==before)
    wheel(guest,[100,80,60,40])
    guest.check('wheel controls software volume', "timeout 6 sh -c 'until grep -q \"^volume = 53$\" /tmp/q2-input-data/config.toml; do sleep 0.1; done'")
    key(guest,'next',1.1)
    key(guest,'down')
    guest.check('resume after held-button seek','sleep 0.5; grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status')
    key(guest,'down')
    # Rebind while touched. The reader must cancel the gesture, rediscover the
    # event node by name, and accept the next fresh touch without phantom ticks.
    control(guest,'wheel 99')
    guest.check('unbind wheel while player owns input', 'sleep 0.1; echo 2-0015 > /sys/bus/i2c/drivers/shanling-q2-wheel/unbind')
    control(guest,'wheel spin 1 200 100')
    guest.check('rebind player input','sleep 0.2; echo 2-0015 > /sys/bus/i2c/drivers/shanling-q2-wheel/bind; '
        'timeout 10 sh -c \'until test "$(grep -c "connected: hyn_sliderbar" /tmp/q2-input-player.log)" -ge 2; do sleep 0.1; done\'')
    guest.check('rebind creates no phantom volume input',"timeout 6 sh -c 'until grep -q \"^volume = 53$\" /tmp/q2-input-data/config.toml; do sleep 0.1; done'")
    guest.check('pause reader to exercise real evdev overrun','kill -STOP "$(cat /tmp/q2-input.pid)"')
    control(guest,'wheel spin 1 200 1200')
    guest.check('fill evdev queue while player stopped','sleep 1.5')
    control(guest,'wheel up')
    guest.check('resume reader and resynchronize dropped input',
        'kill -CONT "$(cat /tmp/q2-input.pid)"; timeout 5 sh -c \'until grep -q "input overrun: kind=1" /tmp/q2-input-player.log; do sleep 0.1; done\'')
    guest.check('overrun recovery creates no phantom volume','sleep 3.5; grep -q "^volume = 53$" /tmp/q2-input-data/config.toml')
    wheel(guest,[10,190])
    guest.check('wrap movement works after rebind',"timeout 6 sh -c 'until grep -q \"^volume = 54$\" /tmp/q2-input-data/config.toml; do sleep 0.1; done'")
    guest.check('stop interactive player cleanly', 'pid=$(cat /tmp/q2-input.pid); kill -TERM "$pid"; wait "$pid"; '
        '! grep -Ei "playback:|KMS presentation failed|panicked" /tmp/q2-input-player.log')
    guest.fetch('/tmp/q2-input-player.log','player-input.log')
    guest.fetch('/tmp/q2-http.log','mock-http.log')
    meta,capture=pcm_capture(guest)
    expect(guest,'real player captured stereo S32 PCM',meta==(44100,2,4) and len(capture)>44100*8)
    guest.result['captured_frames']=len(capture)//8
    guest.check('audio and display settle after exit','sleep 6; test "$(cat /sys/class/backlight/backlight/actual_brightness)" = 0')
    expect(guest,'all codecs suspend after interactive exit',all(d['reset'] for d in json.loads(control(guest,'dac'))))
    guest.check('archive generated playback state after shutdown',
        '(cd /tmp/q2-input-data && tar cf - library.db*) | gzip > /tmp/q2-input-db.tar.gz')
    guest.fetch('/tmp/q2-input-db.tar.gz','input-db.tar.gz')
    dest=guest.out/'db';dest.mkdir()
    with tarfile.open(guest.out/'input-db.tar.gz') as archive: archive.extractall(dest,filter='data')
    with sqlite3.connect(dest/'library.db') as db:
        saved=json.loads(db.execute("select value from kv where key='queue'").fetchone()[0])
    expect(guest,'held Next button seeks within the generated song',10000 <= saved['position_ms'] < 30000 and saved['index']==0 and saved['songs'][0]['id']=='fixture-song')
    guest.result['saved_seek_position_ms']=saved['position_ms']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--icount',action='store_true')
    parser.add_argument('--probe-only',action='store_true')
    args=parser.parse_args()
    guest=audio_guest(args.icount,fixture_disk())
    guest.result['test']='mainline-input'
    try:
        guest.start();guest.seed_entropy()
        sdk(guest)
        if not args.probe_only: player(guest)
        console=(guest.out/'console.log').read_text(errors='replace')
        expect(guest,'no kernel input/audio diagnostics',not re.search(r'Kernel panic|Oops:|BUG:|WARNING:|position read failed|invalid position',console))
        guest.result['passed']=True
    except Exception as error:
        guest.result['error']=str(error)
        if guest.serial:
            try: guest.check('generated failure logs','cat /tmp/q2-input-player.log /proc/asound/Q2/pcm0p/sub0/status /tmp/q2-http.log 2>/dev/null; true',5)
            except Exception: pass
        raise
    finally:
        guest.close()
        (WORK/('input-'+('icount' if args.icount else 'normal')+'.json')).write_text(json.dumps({'artifacts':str(guest.out.relative_to(ROOT)),**guest.result},indent=2)+'\n')

if __name__=='__main__':main()
