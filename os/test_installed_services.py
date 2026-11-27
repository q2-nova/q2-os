"""Host-only installed service/network lifecycle tests, with generated endpoints."""
import os
from pathlib import Path
import shutil
import select
import signal
import subprocess
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
OVERLAY=ROOT/'os/install/integration'

class IntegrationTest(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/installed-service-tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='case-',dir=base))
        for name in ['run','state','log','proc','sys','ctrl']:(self.root/name).mkdir()
        (self.root/'state-status').write_text('ready\n')
        self.env=dict(os.environ,FIXTURE=str(self.root),PYTHON=shutil.which('python3'))
        self.events=self.root/'events'
        self.supervisor=self.root/'supervisor'
        self.supervisor.write_text('#!/bin/sh\necho "supervisor-$1" >> "$FIXTURE/events"\n')
        self.supervisor.chmod(0o755)

    def tearDown(self):shutil.rmtree(self.root)
    def run_shell(self,script,success=True,**env):
        r=subprocess.run(['sh','-c',script],env=dict(self.env,**env),capture_output=True,text=True,timeout=30)
        self.assertEqual(r.returncode==0,success,r.stdout+r.stderr)
        return r.stdout+r.stderr
    def source(self,kind):
        files={'service':('Q2_SERVICES_SOURCE_ONLY','sbin/q2-installed-services'),
               'wifi':('Q2_NETWORK_SOURCE_ONLY','usr/libexec/q2-network/wifi'),
               'dhcp':('Q2_DHCP_SOURCE_ONLY','usr/libexec/q2-network/dhcp')}
        flag,path=files[kind]
        return f'{flag}=1\n. "{OVERLAY/path}"\n'+'''
RUN=$FIXTURE/run
STATE=$FIXTURE/state
STATE_ROOT=$STATE
LOG=$FIXTURE/log
SYS=$FIXTURE/sys
PROC=$FIXTURE/proc
CTRL=$FIXTURE/ctrl
STATE_STATUS=$FIXTURE/state-status
USB_SESSION=$FIXTURE/usb-storage
SUPERVISOR=$FIXTURE/supervisor
PLAYER=$FIXTURE/player
RAM_MEDIA=$FIXTURE/ram-media
SD_MOUNT=$FIXTURE/card
RESOLV=$FIXTURE/resolv.conf
'''
    def test_installed_service_entrypoint_preserves_owner_arguments_and_exit_status(self):
        # The installed overlay must replace the RAM/legacy launcher entrypoint;
        # otherwise restart can seed/launch a stale mutable /state/app/player.
        helper=self.root/'owner'
        helper.write_text('#!/bin/sh\nprintf "%s\\n" "$#" "$@" > "$FIXTURE/dispatch"\nexit "${OWNER_STATUS:-0}"\n')
        helper.chmod(0o755)
        source=(OVERLAY/'etc/init.d/S80q2-player').read_text()
        self.assertIn('exec /sbin/q2-installed-services "$@"',source)
        entry=self.root/'entry'
        entry.write_text(source.replace('/sbin/q2-installed-services',str(helper)))
        for arguments in [[],['start'],['stop'],['restart'],['invalid','two words']]:
            result=subprocess.run(['sh',str(entry),*arguments],env=self.env,capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((self.root/'dispatch').read_text().splitlines(),[str(len(arguments)),*arguments])
        result=subprocess.run(['sh',str(entry),'start'],env=dict(self.env,OWNER_STATUS='17'),capture_output=True,timeout=5)
        self.assertEqual(result.returncode,17)

    def network_process(self,pid,name):
        p=self.root/'proc'/str(pid);p.mkdir();(p/'comm').write_text(name+'\n')
    def test_app_preparation_marker_wraps_actual_selection_and_fallback(self):
        updater=self.root/'updater'
        updater.write_text("#!/bin/sh\nset -eu\ntest \"$(cat \"$FIXTURE/run/app-preparing\")\" = \"$PPID\"\n[ \"${FIXTURE_FAIL:-0}\" = 0 ] || exit 1\nprintf '%s\\n' /run/q2-app/q2nd > \"$FIXTURE/run/app-player\"\n")
        updater.chmod(0o755)
        for fail in ('0','1'):
            output=self.run_shell(self.source('service')+'''
APP_UPDATE=$FIXTURE/updater
select_player
test ! -e "$RUN/app-preparing"
printf 'selected=%s\n' "$PLAYER"
''',FIXTURE_FAIL=fail)
            self.assertIn('selected='+('/run/q2-app/q2nd' if fail=='0' else '/usr/bin/q2nd'),output)

    def test_network_rejects_foreign_pid_without_signalling(self):
        self.network_process(123,'unrelated')
        (self.root/'run/wpa.pid').write_text('123\n')
        text=self.run_shell(self.source('wifi')+'''
kill() { echo "kill-$*" >> "$FIXTURE/events"; [ "$1" = -0 ]; }
down
''',False)
        self.assertIn('expected network process',text)
        self.assertNotIn('TERM',self.events.read_text())
    def test_network_defaults_have_no_credentials_and_await_async_interface(self):
        driver=self.root/'sys/bus/platform/drivers/sdhci-ingenic';driver.mkdir(parents=True)
        device=self.root/'sys/bus/platform/devices/13460000.mmc';device.mkdir(parents=True)
        (device/'driver').symlink_to(driver)
        wpa=self.root/'wpa';wpa.write_text('#!/bin/sh\necho wpa-start >> "$FIXTURE/events"\n');wpa.chmod(0o755)
        self.run_shell(self.source('wifi')+'''
modprobe() { echo "module-$*" >> "$FIXTURE/events"; }
ip() { echo "ip-$*" >> "$FIXTURE/events"; }
sleep() { count=$(( ${count:-0}+1 )); if [ "$count" -ge 2 ]; then mkdir -p "$SYS/class/net/wlan0"; fi; }
WPA=$FIXTURE/wpa
up
''')
        self.assertEqual((driver/'unbind').read_text(),'13460000.mmc\n')
        self.assertEqual((driver/'bind').read_text(),'13460000.mmc\n')
        config=self.root/'state/wpa.conf'
        self.assertEqual(config.read_text(),'ctrl_interface=/run/wpa_supplicant\nupdate_config=1\n')
        self.assertEqual(config.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.events.read_text().splitlines(),['module-8733bs','ip-link set wlan0 up','wpa-start'])
    def test_network_wait_is_bounded_and_never_rebinds_sd(self):
        driver=self.root/'sys/bus/platform/drivers/sdhci-ingenic';driver.mkdir(parents=True)
        device=self.root/'sys/bus/platform/devices/13460000.mmc';device.mkdir(parents=True);(device/'driver').symlink_to(driver)
        result=self.run_shell(self.source('wifi')+'''
modprobe() { :; }
sleep() { :; }
prepare_interface
''',False)
        self.assertIn('did not enumerate',result)
        self.assertNotIn('13490000',(driver/'bind').read_text())
    def test_missing_state_blocks_network_without_writing_credentials(self):
        (self.root/'state-status').write_text('missing\n')
        self.run_shell(self.source('wifi')+'up\n',False)
        self.assertFalse((self.root/'state/wpa.conf').exists())
    def test_dhcp_metrics_use_only_numeric_uptime_after_configuration(self):
        (self.root/'proc/uptime').write_text('12.34 9.00\n')
        script=self.source('dhcp')+'''
ip() { :; }
ifconfig() { :; }
main bound
'''
        self.run_shell(script,interface='wlan0',ip='10.1.2.3',subnet='255.255.255.0',router='10.1.2.1',dns='1.1.1.1')
        metric=self.root/'run/connect-times'
        self.assertEqual(metric.read_text(),'12.34 wifi-ipv4-ready\n')
        (self.root/'proc/uptime').write_text('invalid 0\n')
        self.run_shell(script,interface='wlan0',ip='10.1.2.3',subnet='255.255.255.0')
        self.assertEqual(metric.read_text(),'12.34 wifi-ipv4-ready\n')

    def test_dhcp_metrics_bound_memory_and_refuse_symlink(self):
        (self.root/'proc/uptime').write_text('12.34 9.00\n')
        script=self.source('dhcp')+'ip() { :; }; ifconfig() { :; }; main bound\n'
        metric=self.root/'run/connect-times';metric.write_bytes(b'x'*65408)
        self.run_shell(script,interface='wlan0',ip='10.1.2.3',subnet='255.255.255.0')
        self.assertEqual(metric.stat().st_size,65408)
        metric.unlink();target=self.root/'generated-sentinel';target.write_text('unchanged');metric.symlink_to(target)
        self.run_shell(script,interface='wlan0',ip='10.1.2.3',subnet='255.255.255.0')
        self.assertEqual(target.read_text(),'unchanged')

    def test_dhcp_limits_interface_and_filters_untrusted_dns(self):
        script=self.source('dhcp')+'''
ip() { echo "ip-$*" >> "$FIXTURE/events"; }
ifconfig() { echo "ifconfig-$*" >> "$FIXTURE/events"; }
main bound
'''
        self.run_shell(script,False,interface='usb0',ip='10.1.2.3',subnet='255.255.255.0')
        self.assertFalse(self.events.exists())
        self.run_shell(script,interface='wlan0',ip='10.1.2.3',subnet='255.255.255.0',router='999.1.1.1 10.1.2.1',dns='8.8.8.8 invalid 999.1.1.1 1.1.1.1')
        self.assertEqual((self.root/'resolv.conf').read_text(),'nameserver 8.8.8.8\nnameserver 1.1.1.1\n')
        self.assertIn('default via 10.1.2.1 dev wlan0',self.events.read_text())
    def test_missing_card_uses_bounded_ram_not_internal_nand(self):
        self.run_shell(self.source('service')+'''
mounted() { :; }
mount() { echo "$*" >> "$FIXTURE/events"; }
media_setup
[ "$Q2ND_SD_READY" = 0 ]
''')
        self.assertEqual(self.events.read_text().strip(),f'-t tmpfs -o size=16m,nosuid,nodev,noexec q2-media {self.root}/ram-media')
    def card_case(self,exfat_ok):
        dev=self.root/'sdblock';(self.root/'sys/class/block/mmcblk0').mkdir(parents=True)
        os.symlink('/sys/devices/platform/13490000.mmc/mmc_host/mmc0/mmc0:1234',self.root/'sys/class/block/mmcblk0/device')
        (self.root/'proc/modules').write_text('')
        import stat
        block=next((str(p) for p in sorted(Path('/dev').iterdir()) if p.exists() and stat.S_ISBLK(p.stat().st_mode)),None)
        if block is None:self.skipTest('no host block device to stand in for the card')
        return self.run_shell(self.source('service')+f'''
SD_DEV={block}
FAT_MODULES=$FIXTURE/fat;mkdir -p "$FAT_MODULES";: > "$FAT_MODULES/fat.ko";: > "$FAT_MODULES/vfat.ko"
mkdir -p "$SD_MOUNT"
readlink() {{ echo /sys/devices/platform/13490000.mmc/mmc_host/mmc0/mmc0:1234/block/mmcblk0; }}
state=$FIXTURE/mounted
mounted() {{ [ "$1" = "$SD_MOUNT" ] && cat "$state" 2>/dev/null || :; }}
insmod() {{ echo "insmod ${{1##*/}}" >> "$FIXTURE/events"; }}
mount() {{
 echo "mount $2" >> "$FIXTURE/events"
 if [ "$2" = exfat ] && [ {1 if exfat_ok else 0} = 1 ]; then echo "$SD_DEV exfat" > "$state"; return 0; fi
 if [ "$2" = vfat ]; then echo "$SD_DEV vfat" > "$state"; return 0; fi
 return 1
}}
media_setup
echo "ready=$Q2ND_SD_READY" >> "$FIXTURE/events"
''')
    def test_fat32_card_mounts_as_vfat_after_exfat_fails(self):
        self.card_case(False)
        self.assertEqual(self.events.read_text().split('\n')[:5],['mount exfat','insmod fat.ko','insmod vfat.ko','mount vfat','ready=1'])
    def test_exfat_card_needs_no_fat_modules(self):
        self.card_case(True)
        self.assertEqual(self.events.read_text().split('\n')[:2],['mount exfat','ready=1'])
    def test_unknown_temporary_media_mount_fails_closed(self):
        self.run_shell(self.source('service')+'''
mounted() { echo '/dev/foreign ext4'; }
media_setup
''',False)
    def service_script(self,fail=False):
        player=self.root/'player'
        if fail:player.write_text('#!/bin/sh\necho player-start >> "$FIXTURE/events"\nexit 7\n')
        else:player.write_text('''#!/usr/bin/env python3
import os,signal,time
from pathlib import Path
p=Path(os.environ['FIXTURE'])/'events'
def event(s):
 with p.open('a') as f:f.write(s+'\\n')
def stop(*_):event('player-stop');raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
event('player-start')
while True:time.sleep(.1)
''')
        player.chmod(0o755)
        script=self.root/'q2-installed-services'
        script.write_text('#!/bin/sh\n'+self.source('service')+'''
PROC=/proc
SELF=$FIXTURE/q2-installed-services
media_setup() { export Q2ND_SD_READY=0; }
main "$@"
''');script.chmod(0o755)
        return script
    def test_supervised_immutable_app_stops_and_releases_ownership(self):
        script=self.service_script()
        child=subprocess.Popen([str(script),'run'],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        waiter=threading.Thread(target=child.wait);waiter.start()
        try:
            end=time.monotonic()+5
            while time.monotonic()<end:
                if self.events.exists() and 'player-start' in self.events.read_text():break
                time.sleep(.02)
            else:self.fail('fixture player failed to start')
            self.assertTrue((self.root/'run/launcher.lock').is_dir())
            duplicate=subprocess.run([str(script),'run'],env=self.env,capture_output=True,text=True)
            self.assertNotEqual(duplicate.returncode,0)
            result=subprocess.run([str(script),'stop'],env=self.env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            waiter.join(3)
            self.assertFalse((self.root/'run/launcher.lock').exists())
            self.assertFalse((self.root/'run/player.pid').exists())
            self.assertFalse((self.root/'state/app/q2nd').exists())
            events=self.events.read_text().splitlines()
            self.assertLess(events.index('supervisor-stop'),events.index('player-stop'))
        finally:
            if child.poll() is None:child.terminate()
            waiter.join(25)
            child.stdout.close();child.stderr.close()
    def test_migration_hold_blocks_start_and_failed_resume_retains_hold(self):
        script=self.service_script(fail=True)
        self.run_shell(self.source('service')+'main hold\n')
        self.assertTrue((self.root/'run/start-held').exists())
        blocked=subprocess.run([str(script),'start'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(blocked.returncode,0)
        self.assertNotIn('player-start',self.events.read_text())
        (self.root/'state-status').write_text('usb-storage\n')
        denied=subprocess.run([str(script),'resume'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(denied.returncode,0)
        self.assertTrue((self.root/'run/start-held').exists())
        (self.root/'state-status').write_text('ready\n')
        resumed=subprocess.run([str(script),'resume'],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(resumed.returncode,0,resumed.stderr)
        self.assertFalse((self.root/'run/start-held').exists())

    def test_failed_player_restarts_are_bounded(self):
        script=self.service_script(fail=True)
        result=subprocess.run([str(script),'run'],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.events.read_text().count('player-start'),3)
        self.assertFalse((self.root/'run/launcher.lock').exists())

    def test_early_cancel_hold_return_preserves_real_accounting_and_failed_boots(self):
        # Actual service stop/run/hold/resume gates plus production C counter
        # persistence. This component does not simulate watchdog/health or claim
        # the generated player is a qualified app: target app parity is separate.
        source=(ROOT/'os/probes/player-supervisor.c').read_text()
        source=source.replace('/state/app','state/app').replace('/run/q2nd','run')
        (self.root/'supervisor.c').write_text(source)
        fixture=ROOT/'os/startup_hold_accounting_fixture.c'
        built=subprocess.run(['cc','-Wall','-Wextra','-Werror','-O2',
            '-I',str(self.root),'-I',str(ROOT/'os/probes'),str(fixture),
            '-o',str(self.root/'accounting')],capture_output=True,text=True,timeout=30)
        self.assertEqual(built.returncode,0,built.stderr)
        (self.root/'state/app').mkdir()
        counter=self.root/'state/app/boot-failures'
        counter.write_text('2\n') # Two genuine historical faults must survive.
        self.supervisor.write_text('''#!/bin/sh
cd "$FIXTURE"
exec ./accounting "$@"
''')
        script=self.service_script()
        (self.root/'player').write_text('''#!/usr/bin/env python3
import os, signal, sys
if '--telemetry-process-exit' in sys.argv: raise SystemExit(3)
def stop(*_):
    with open(os.environ['FIXTURE']+'/events','a') as f:f.write('early-cancel\\n')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
with open(os.environ['FIXTURE']+'/started','w') as f:f.write('armed\\n');f.flush()
while True:signal.pause()
''')
        fifo=self.root/'started';os.mkfifo(fifo)
        fd=os.open(fifo,os.O_RDWR|os.O_NONBLOCK)
        try:
            for cycle in range(3):
                # Preserve actual resume admission and hold marker behavior;
                # launch is foreground-owned below so the test can reap it.
                if cycle:
                    (self.root/'usb-storage/phase').write_text('exported\n')
                    self.run_shell(self.source('service')+'''start_service() { :; }
main resume
''',False)
                    self.assertTrue((self.root/'run/start-held').exists())
                    (self.root/'usb-storage/phase').write_text('local-ready\n')
                    self.run_shell(self.source('service')+'''start_service() { :; }
main resume
''')
                child=subprocess.Popen([str(script),'run'],env=self.env,
                    stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,
                    start_new_session=True)
                waiter=threading.Thread(target=child.wait);waiter.start()
                try:
                    self.assertTrue(select.select([fd],[],[],5)[0], 'player handler not armed')
                    self.assertEqual(os.read(fd,128),b'armed\n')
                    self.assertFalse((self.root/'run/ready').exists())
                    stopped=subprocess.run([str(script),'hold'],env=self.env,
                        capture_output=True,text=True,timeout=30)
                    self.assertEqual(stopped.returncode,0,stopped.stderr)
                    waiter.join(3)
                    self.assertEqual(child.poll(),0)
                finally:
                    if child.poll() is None:os.killpg(child.pid,signal.SIGKILL)
                    waiter.join(3);child.stderr.close()
                self.assertEqual(counter.read_text(),'3\n')
                self.assertTrue((self.root/'run/boot-counted').is_file())
                self.assertFalse((self.root/'run/boot-credit-claimed').exists())
                for name in ['player.pid','launcher.pid','launcher.lock','ready','rescue']:
                    self.assertFalse((self.root/'run'/name).exists(),name)
                if cycle==0:
                    (self.root/'usb-storage').mkdir()
                    (self.root/'usb-storage/phase').write_text('local-ready\n')
            self.assertEqual(self.events.read_text().splitlines(),['early-cancel']*3)
            # A real subsequent boot loses the RAM pending token, not history.
            (self.root/'run/boot-counted').unlink()
            result=subprocess.run([str(self.supervisor),'start'],env=self.env,timeout=3)
            self.assertEqual(result.returncode,3)
            self.assertEqual(counter.read_text(),'3\n')
            self.assertTrue((self.root/'run/rescue').exists())
            # Explicit policy contrast, not a fabricated health qualification:
            # once confirmed/live is supplied, only this attempt earns credit.
            (self.root/'run/boot-counted').write_text('1\n')
            (self.root/'run/confirmed').touch()
            for _ in range(2):
                result=subprocess.run([str(self.supervisor),'stop'],env=self.env,timeout=3)
                self.assertEqual(result.returncode,0)
                self.assertEqual(counter.read_text(),'2\n')
            self.assertFalse((self.root/'run/boot-counted').exists())
        finally:os.close(fd)
    def test_state_and_usb_gate_block_player_and_allow_return_phase(self):
        script=self.service_script(fail=True)
        (self.root/'state-status').write_text('missing\n')
        subprocess.run([str(script),'run'],env=self.env,capture_output=True)
        self.assertNotIn('player-start',self.events.read_text())
        (self.root/'state-status').write_text('ready\n')
        (self.root/'usb-storage').mkdir();(self.root/'usb-storage/phase').write_text('exported\n')
        subprocess.run([str(script),'run'],env=self.env,capture_output=True)
        self.assertNotIn('player-start',self.events.read_text())
        (self.root/'usb-storage/phase').write_text('local-ready\n')
        subprocess.run([str(script),'run'],env=self.env,capture_output=True,timeout=10)
        self.assertEqual(self.events.read_text().count('player-start'),3)

if __name__=='__main__':unittest.main()
