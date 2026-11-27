"""Execute installed init with owned FIFO phase barriers, no host/device writes."""
import os
import fcntl
import hashlib
import json
from pathlib import Path
import select
import signal
import re
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class Overlap(unittest.TestCase):
    def run_init(self,mode='normal',source=None,expect_overlap=True,prefix=None,evidence=None,autostart=False,target_inputs=None):
        base=ROOT/'build/perf';base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root=Path(directory)
            for path in ('bin','sbin','usr/sbin','usr/libexec/q2-network','etc/init.d','etc/q2-bootstrap','run/q2','run/log','run/q2nd','state/network','mnt/mmc'):(root/path).mkdir(parents=True,exist_ok=True)
            if autostart:(root/'state/network/autostart').touch()
            for name in ('events','state-release','peripheral-release'):os.mkfifo(root/name,0o600)
            fds={n:os.open(root/n,os.O_RDWR|os.O_NONBLOCK|os.O_CLOEXEC) for n in ('events','state-release','peripheral-release')}
            journal=root/'journal';journal.touch();(root/'tty').touch()
            def script(path,body):
                p=root/path;p.write_text('#!/bin/sh\nset -eu\n'+body+'\n');p.chmod(0o700)
            event=lambda name:f"printf '%s\\n' {name} > {root}/events;printf '%s\\n' {name} >> {journal}"
            script('bin/hostname',':')
            script('bin/ifconfig','exit '+('7' if mode=='loopback-fail' else '0'))
            script('sbin/q2-state',event('state-enter')+f"\nread release < {root}/state-release\nprintf '%s\\n' "+('missing' if mode=='state-fail' else 'ready')+f' > {root}/run/q2/state-status\n: > {root}/state-retired\n'+event('state-end')+'\nexit '+('7' if mode=='state-fail' else '0'))
            release='' if mode=='peripheral-early-fail' else f'read release < {root}/peripheral-release\n'
            script('sbin/q2-player-bringup',f'echo $$ > {root}/peripheral.pid\n'+event('peripheral-enter')+'\n'+release+f': > {root}/peripheral-retired\n'+event('peripheral-end')+'\nexit '+('9' if mode.startswith('peripheral-') else '0'))
            script('sbin/q2-diagnostic-usb',f'test -e {root}/state-retired\n'+event('diagnostic'))
            script('usr/sbin/dropbear',':')
            for label,name in [('clock-floor','S02q2-clock'),('clock-trust','S25q2-clock-trust'),('clock-network','S26q2-network-clock')]:
                check=f'test -e {root}/state-retired\n'
                if expect_overlap and mode!='loopback-fail':check+=f'test -e {root}/peripheral-retired\np=$(cat {root}/peripheral.pid);test ! -e /proc/$p\n'
                script('etc/init.d/'+name,check+event(label))
            script('sbin/q2-installed-services',f'test "$(cat {root}/run/q2/state-status)" = ready\ntest -e {root}/peripheral-retired\n'+event('admission'))
            script('usr/sbin/q2-usb-auto',event('usb-auto'))
            script('usr/libexec/q2-network/wifi',f'test "$1" = up\ntest -e {root}/peripheral-retired\ntest "$(cat {root}/run/q2/state-status)" = ready\n'+f"printf '%s\\n' wifi-early-up >> {journal}")
            original=(ROOT/'os/install/root-init').read_text() if source is None else source
            generated=original.split('dmesg > /run/log/boot',1)[0]
            generated=generated.replace('export PATH=/bin:/sbin:/usr/bin:/usr/sbin',f'export PATH={root}/bin:/usr/bin:/bin')
            # Only fixed production absolute namespaces are redirected.
            prefixes=('/usr/sbin/','/usr/libexec/','/sbin/','/etc/init.d/','/etc/q2-bootstrap/','/mnt/mmc','/state','/run/')
            generated=re.sub(r'(?<![A-Za-z0-9_./-])(?:'+'|'.join(re.escape(p) for p in prefixes)+')',lambda m:str(root)+m.group(),generated)
            generated=generated.replace('/dev/ttyS3',str(root/'tty'))
            init=root/'init';init.write_text(generated);events=[];buffer=b'';child=None
            def until(wanted,seconds=2):
                nonlocal buffer
                end=time.monotonic()+seconds
                while not set(wanted).issubset(events):
                    left=end-time.monotonic()
                    if left<=0 or not select.select([fds['events']],[],[],left)[0]:return False
                    buffer+=os.read(fds['events'],4096)
                    while b'\n' in buffer:
                        line,buffer=buffer.split(b'\n',1);events.append(line.decode('ascii'))
                return True
            try:
                with (root/'host-output').open('wb') as log:
                    child=subprocess.Popen([*map(str,prefix or ('/bin/sh',)),str(init)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    self.assertTrue(until(['state-enter']),(root/'tty').read_text()+(root/'host-output').read_text())
                    overlap=until(['state-enter','peripheral-enter'],.4) if mode!='loopback-fail' else False
                    self.assertEqual(overlap,expect_overlap and mode!='loopback-fail')
                    os.write(fds['state-release'],b'G\n')
                    self.assertTrue(until(['diagnostic']))
                    if mode!='loopback-fail':
                        self.assertTrue(until(['peripheral-enter']))
                        if expect_overlap and mode != 'peripheral-early-fail':
                            self.assertNotIn('clock-floor',journal.read_text())
                        os.write(fds['peripheral-release'],b'G\n')
                    self.assertEqual(child.wait(timeout=3),0)
                history=journal.read_text().splitlines()
                if mode=='normal':
                    self.assertIn('admission',history);self.assertLess(history.index('admission'),history.index('usb-auto'))
                else:self.assertNotIn('admission',history)
                if expect_overlap and mode!='loopback-fail':self.assertLess(history.index('peripheral-end'),history.index('clock-floor'))
                if evidence is not None:
                    evidence.mkdir(parents=True)
                    bindings={}
                    for p in root.rglob('*'):
                        if p.is_file():
                            target=evidence/p.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(p.read_bytes())
                            bindings[str(target)]=hashlib.sha256(target.read_bytes()).hexdigest()
                    (evidence/'receipt.json').write_text(json.dumps(dict(passed=True,hardware=False,mode=mode,phase_barriers=history,
                        source_sha256=hashlib.sha256(original.encode()).hexdigest(),owned_leader_reaped=child.returncode==0,
                        target_inputs=target_inputs,
                        scope='Generated namespace +FIFO synchronization; overlap correctness, not board timing',files=bindings),indent=2)+'\n')
                return history
            finally:
                for name in ('state-release','peripheral-release'):
                    try:os.write(fds[name],b'G\n')
                    except OSError:pass
                if child is not None and child.returncode is None:
                    # The owned group leader remains unreaped until after this
                    # bounded retirement, preventing PID/group identity reuse.
                    try:os.killpg(child.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    child.wait(timeout=3)
                for fd in fds.values():os.close(fd)

    def test_both_phases_enter_before_release_and_diagnostics_precede_join(self):
        self.assertNotIn('wifi-early-up',self.run_init())
    def test_boot_marker_starts_the_supplicant_after_bring_up(self):
        for _ in range(50):
            history=self.run_init(autostart=True)
            if 'wifi-early-up' in history:break
            time.sleep(.05)
        self.assertIn('wifi-early-up',history)
        self.assertLess(history.index('peripheral-end'),history.index('wifi-early-up'))
    def test_failure_paths_never_admit_player(self):
        for mode in ('state-fail','peripheral-fail','peripheral-early-fail','loopback-fail'):
            with self.subTest(mode=mode):self.run_init(mode)
    def test_retained_serial_source_cannot_overlap_both_phase_barriers(self):
        # The serial root-init that ran the phases in turn. The harness stubs
        # its ifconfig and dropbear calls, so the fixture omits their addresses.
        source=(ROOT/'os/test_root_init_overlap_serial.sh').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),
                         'd1206dcb792ddd3c464f62277598924e51fe2878b6c5d3c0c8f7b9e41bdc9ea5')
        history=self.run_init(source=source.decode(),expect_overlap=False)
        self.assertLess(history.index('state-end'),history.index('peripheral-enter'))

    @unittest.skipUnless(os.environ.get('Q2_RUN_MIPS_INIT_CONTROL')=='1','explicit canonical emulator allocation required')
    def test_exact_normal_busybox_waits_for_owned_child_and_cached_failure(self):
        canon=Path(subprocess.check_output(['git','rev-parse','--path-format=absolute','--git-common-dir'],cwd=ROOT,text=True).strip()).parent
        qemu=canon/'build/qemu/qemu-mipsel-static'
        proof=canon/'build/os/install-v040-actors-01/proof'
        runtime=proof.parent/'root'
        if not (runtime/'bin/busybox').is_file():
            runtime=proof/'unpacked'
        busybox=runtime/'bin/busybox'
        inputs={
            qemu:'40be2a933d47494134ddc8947ce09c41af6ea29056a4e552da8e8cd8375f2229',
            proof/'result.json':'abcc3cd206f958082076f630fbb7c7ab2d6837a66f7f625a7923d14288a6ac23',
            busybox:'863d7536821e0eca26d3e549e8a5e6990e6a412b2d2d1799c8f39ef8e78dd545',
            runtime/'lib/ld-linux-mipsn8.so.1':'27d21a1a6afc4cede1875f3122a6571e9b74bc1e812b250e1bb6098706b321b8',
            runtime/'lib/libc.so.6':'251a3520bc5349e7edc884862435ad851250eed75587e4507c46beaebc791e0e',
            runtime/'lib/libresolv.so.2':'8ce8bcacb9ce9cbdca3394d9d7d3ed180ad1eb9946f35511ebd83028f37ba478',
        }
        output_override=os.environ.get('Q2_MIPS_INIT_CONTROL_OUT')
        prefix=[qemu,'-cpu','P5600','-L',runtime,busybox,'sh']
        with (canon/'build/emulator-benchmark.lock').open('a') as lease:
            fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            for path,digest in inputs.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest,str(path))
            base=ROOT/'build/perf'
            base.mkdir(parents=True,exist_ok=True)
            if output_override:
                output=Path(output_override).absolute()
                self.assertEqual(output,output.resolve())
                self.assertTrue(output.is_relative_to(base))
                output.mkdir(parents=True)
            else:
                output=Path(tempfile.mkdtemp(prefix='root-init-overlap-target-',dir=base))
            for mode in ('normal','peripheral-early-fail'):
                self.run_init(mode,prefix=prefix,evidence=output/mode,
                              target_inputs={str(path):digest for path,digest in inputs.items()})

if __name__=='__main__':unittest.main()
