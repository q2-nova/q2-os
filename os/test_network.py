#!/usr/bin/env python3
"""Default player -> emulated RNDIS -> isolated real Navidrome, including TLS."""
import argparse
import base64
import json
import re
import time
import uuid
from build import WORK, ROOT
from navidrome_fixture import Fixture
from test_audio import audio_guest, control, expect, pcm_capture
from test_input import key, wheel
from test_appliance import healthy, reset
from test_recovery import boot_id


def configure_network(g,f):
    g.command[g.command.index('--network')+1]='host'
    g.command[g.command.index('-nic')+1]=('user,restrict=on,guestfwd=tcp:10.0.2.100:8080-cmd:socat - TCP:127.0.0.1:'+str(f.http)+
        ',guestfwd=tcp:10.0.2.100:8443-cmd:socat - TCP:127.0.0.1:'+str(f.https))
    i=g.command.index('-append')+1;g.command[i]=g.command[i].replace('q2.diagnostic=1','')
    g.result['network']='restricted SLIRP guestfwd to two loopback fixture ports; Navidrome on internal Podman network'


def start_song(g):
    key(g,'power');wheel(g,[100,80,60,40]);key(g,'power')
    g.check('album metadata loaded','sleep 2')
    key(g,'power');g.check('song metadata loaded','sleep 1');key(g,'power')
    g.check('actual player opens Navidrome media','timeout 30 sh -c \'until grep -q "playing.*USB tone" /run/q2nd/launcher.log && grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.1; done\'',40)


def restart(g):
    g.check('restart managed mainline service','/etc/init.d/S80q2-player restart',45);g.wait_player();healthy(g)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--tls',action='store_true');p.add_argument('--icount',action='store_true');a=p.parse_args()
    out=WORK/'appliance'/('navidrome-'+uuid.uuid4().hex[:8])
    with Fixture(out) as f:
        g=audio_guest(a.icount);configure_network(g,f);g.result.update(test='usb-navidrome',tls=a.tls,fixture=str(out.relative_to(ROOT)))
        try:
            g.start();g.seed_entropy();g.wait_player();healthy(g);initial=boot_id(g)
            g.check('USB route with no default escape','ip route get 10.0.2.100 && ! ip route get 1.1.1.1')
            g.check('USB actually transfers HTTP before navigation','wget -q -T 10 -O /tmp/q2-before http://10.0.2.100:8080/ping && test -s /tmp/q2-before')
            if a.tls:
                ca=base64.b64encode((out/'ca.der').read_bytes()).decode()
                g.check('provision generated private CA and HTTPS URL',f'mkdir -p /state/trust; printf %s {ca} | base64 -d > /state/trust/navidrome-ca.der; sed -i s,http://10.0.2.100:8080,https://10.0.2.100:8443, /state/Q2Navidrome/config.toml')
                restart(g)
                before=len(f.requests)
                key(g,'power');wheel(g,[100,80,60,40]);key(g,'power')
                g.check('unprovisioned HTTPS attempt leaves time explicitly unsynchronized','sleep 3; test "$(cat /run/q2/clock-status)" = build-floor-unsynchronized')
                expect(g,'unprovisioned clock sends no authenticated API request',len(f.requests)==before)
                g.screenshot('clock-unprovisioned.ppm')
                g.check('authenticated fixture clock provisioned through local test console',f'q2-clock-set --trusted-epoch {int(time.time())}')
                restart(g)
                g.check('provisioned clock status','test "$(cat /run/q2/clock-status)" = provisioned')
                g.result['time_trust']='fresh host clock explicitly provisioned through local harness; no NTP or TLS bypass'
                # App requests begin when browsing, below; startup intentionally does no polling.
                g.check('real USB TLS accepts provisioned CA', 'q2-rust-probe --tls-client https://10.0.2.100:8443/ping /state/trust/navidrome-ca.der')
                g.check('real USB TLS rejects untrusted CA', '! q2-rust-probe --tls-client https://10.0.2.100:8443/ping - >/tmp/q2-tls-negative 2>&1 && grep -q Q2_EXTERNAL_TLS_REJECT /tmp/q2-tls-negative')
                g.check('future clock rejects expired certificate', 'date -u -s @2208988801; ! q2-rust-probe --tls-client https://10.0.2.100:8443/ping /state/trust/navidrome-ca.der >/tmp/q2-tls-negative 2>&1 && grep -qi expired /tmp/q2-tls-negative')
                g.check('past clock rejects not-yet-valid certificate', 'date -u -s @1600000000; ! q2-rust-probe --tls-client https://10.0.2.100:8443/ping /state/trust/navidrome-ca.der >/tmp/q2-tls-negative 2>&1 && grep -qi "not.*valid" /tmp/q2-tls-negative')
                g.check('restore explicitly provisioned fixture time',f'q2-clock-set --trusted-epoch {int(time.time())}')

            start_song(g)
            if a.tls:expect(g,'actual app browses and streams over validated TLS',any(path.endswith('/stream') for path in f.requests))
            key(g,'down')
            g.check('pause closes PCM without being mistaken for a hang','sleep 32; test -s /run/q2nd/healthy && test -s /run/q2nd/player.pid && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)" && grep -q closed /proc/asound/Q2/pcm0p/sub0/status',45)
            expect(g,'pause did not reboot',boot_id(g)==initial)
            key(g,'down');g.check('resume works','sleep 2; grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status')
            control(g,'usb out');g.check('USB cable absent while cached playback continues','sleep 30; test -s /run/q2nd/healthy && test -s /run/q2nd/player.pid && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)" && grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status',45)
            control(g,'usb in');g.check('USB reconnect reaches real server','sleep 2; wget -q -T 10 -O /tmp/q2-reconnect http://10.0.2.100:8080/ping && test -s /tmp/q2-reconnect')
            # Persist queue/cache before reboot, then make every server request fail.
            key(g,'down');g.check('persist generated player state','/etc/init.d/S80q2-player stop && sync',45)
            f.offline=True
            count=(g.out/'console.log').read_text().count('Q2_OS_READY');g.serial.sendall(b'reboot\n');reset(g,count);g.wait_player();healthy(g)
            if a.tls:g.check('provisioned RTC trusted after warm reset','test "$(cat /run/q2/clock-status)" = trusted-rtc')
            key(g,'down')
            g.check('offline reboot restores cached queue and playback','timeout 25 sh -c \'until grep -q RUNNING /proc/asound/Q2/pcm0p/sub0/status; do sleep 0.1; done\'',35)
            g.check('cached source used','grep -q "playing.*USB tone.*Cached" /run/q2nd/launcher.log')
            g.check('stop cached player before blocked-stream trial','/etc/init.d/S80q2-player stop',45)
            g.check('remove only generated test song cache','rm -f /state/Q2Navidrome/cache/songs/*')
            f.offline=False;f.blackhole='stream'
            before_stream=sum(path.endswith('/stream') for path in f.requests)
            g.check('start player with uncached queue','/etc/init.d/S80q2-player start');g.wait_player();healthy(g)
            key(g,'down')
            deadline=time.monotonic()+25
            while time.monotonic()<deadline and sum(path.endswith('/stream') for path in f.requests)==before_stream:time.sleep(.1)
            expect(g,'audio attempted an uncached stream',sum(path.endswith('/stream') for path in f.requests)>before_stream)
            waiting_boot=boot_id(g)
            g.check('blocked network reader remains responsive past audio lease','sleep 30; test -s /run/q2nd/healthy && test -s /run/q2nd/player.pid && test "$(cat /run/q2nd/healthy)" = "$(cat /run/q2nd/player.pid)"',45)
            expect(g,'blocked audio network wait did not reboot',boot_id(g)==waiting_boot)
            f.blackhole=False
            g.check('network returns','wget -q -T 10 -O /tmp/q2-online http://10.0.2.100:8080/ping')
            meta,capture=pcm_capture(g);expect(g,'real ALSA capture across network lifecycle',meta==(44100,2,4) and len(capture)>44100*8)
            g.result['captured_frames']=len(capture)//8
            g.check('flush state','sync');g.poweroff();g.result['passed']=True
        except Exception as e:
            g.result['error']=str(e)
            if g.serial:
                try:g.check('failure logs','tail -n 60 /run/q2nd/launcher.log; cat /run/q2nd/supervisor.log /state/Q2Navidrome/config.toml; cat /proc/net/dev; true',5)
                except Exception:pass
                try:g.screenshot()
                except Exception:pass
            raise
        finally:
            g.close();(WORK/f'network-{"tls" if a.tls else "http"}-{"icount" if a.icount else "normal"}.json').write_text(json.dumps({'artifacts':str(g.out),**g.result},indent=2)+'\n')
if __name__=='__main__':main()
