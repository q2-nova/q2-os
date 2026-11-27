"""USB display pixels and owned process lifecycle; generated RAM metadata only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
CONTROL=ROOT/'os/platform/usb-status-control'
BINARY=ROOT/'build/usb-status/q2-usb-status-host'

class UsbStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BINARY.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['cc','-std=c11','-Os','-Wall','-Wextra','-Werror','-DUSB_STATUS_HEADLESS','-o',str(BINARY),str(ROOT/'os/platform/usb-status.c')],check=True)
    def setUp(self):
        base=ROOT/'build/usb-status-tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='case-',dir=base))
        self.run=self.root/'run';(self.run/'usb-storage').mkdir(parents=True)
        (self.run/'usb-storage/phase').write_text('exported\n')
        self.children=[]
    def tearDown(self):
        try:self.control('stop')
        except Exception:pass
        for p in self.children:
            if p.poll() is None:p.terminate()
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        shutil.rmtree(self.root)
    def control(self,action,success=True,launch=None):
        script=f'Q2_USB_STATUS_SOURCE_ONLY=1\n. "{CONTROL}"\n'+'''
RUN=$FIXTURE/run
PROC=/proc
STATUS_HELPER=$BINARY
STATUS_FALLBACK=$FIXTURE/absent
LOG=$FIXTURE/status.log
id() { echo 0; }
launch_status() { "$exe" --fixture "$RUN" </dev/null >> "$LOG" 2>&1 & launched=$!; }
'''
        if launch:script+=launch+'\n'
        script+='main "$ACTION"\n'
        result=subprocess.run(['sh','-c',script],env=dict(os.environ,FIXTURE=str(self.root),BINARY=str(BINARY),ACTION=action),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result
    def wait_change(self,old):
        end=time.monotonic()+3
        while time.monotonic()<end:
            data=(self.run/'usb-status.ppm').read_bytes()
            if data!=old:return data
            time.sleep(.05)
        self.fail('status frame did not change')
    def test_start_is_idempotent_phase_updates_and_stop_releases(self):
        self.control('start');pid=(self.run/'usb-status.pid').read_text()
        self.assertEqual((self.run/'usb-status.ready').read_text(),pid)
        self.assertEqual(os.readlink(f'/proc/{pid.strip()}/cwd'),'/')
        old=(self.run/'usb-status.ppm').read_bytes()
        self.control('show');self.assertEqual((self.run/'usb-status.pid').read_text(),pid)
        (self.run/'usb-storage/phase').write_text('local-ready\n');self.wait_change(old)
        self.control('stop')
        self.assertFalse((self.run/'usb-status.pid').exists())
        self.assertFalse((self.run/'usb-status.ready').exists())
    def test_removed_session_with_start_hold_shows_finishing_setup(self):
        self.control('start');old=(self.run/'usb-status.ppm').read_bytes()
        hold=self.run.parent/'q2nd/start-held';hold.parent.mkdir();hold.touch()
        (self.run/'usb-storage/phase').unlink()
        actual=self.wait_change(old)
        expected=self.root/'expected.ppm'
        subprocess.run([str(BINARY),'--snapshot',str(expected),'--phase','local-ready'],check=True)
        self.assertEqual(actual,expected.read_bytes())
    def test_existing_manual_pid_without_exe_metadata_is_adopted(self):
        p=subprocess.Popen([str(BINARY),'--fixture',str(self.run)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);self.children.append(p)
        end=time.monotonic()+3
        while not (self.run/'usb-status.ready').exists() and time.monotonic()<end:time.sleep(.02)
        (self.run/'usb-status.pid').write_text(str(p.pid)+'\n')
        self.control('show');self.assertEqual((self.run/'usb-status.pid').read_text().strip(),str(p.pid))
        self.control('stop');p.wait(timeout=3)
    def test_foreign_pid_is_not_signalled(self):
        p=subprocess.Popen(['sleep','10']);self.children.append(p)
        (self.run/'usb-status.pid').write_text(str(p.pid)+'\n')
        result=self.control('stop',False)
        self.assertIn('another executable',result.stderr);self.assertIsNone(p.poll())
        (self.run/'usb-status.pid').unlink()
    def test_invalid_pid_is_refused_and_stale_pid_is_cleaned(self):
        (self.run/'usb-status.pid').write_text('0\n');self.control('stop',False)
        (self.run/'usb-status.pid').write_text('999999999\n');self.control('stop')
        self.assertFalse((self.run/'usb-status.pid').exists())
    def test_busy_controller_does_not_launch(self):
        (self.run/'usb-status.lock').mkdir();self.control('start',False)
        self.assertFalse((self.run/'usb-status.pid').exists());(self.run/'usb-status.lock').rmdir()
    def test_failed_first_frame_leaves_no_owned_pid(self):
        result=self.control('start',False,launch='launch_status() { "$exe" --fixture "$FIXTURE/absent" </dev/null >> "$LOG" 2>&1 & launched=$!; }')
        self.assertIn('did not present',result.stderr);self.assertFalse((self.run/'usb-status.pid').exists())
    def test_no_session_refuses_display_start(self):
        (self.run/'usb-storage/phase').unlink();self.control('start',False)
    def test_screenshots_and_error_content_are_deterministic(self):
        output=ROOT/'build/usb-status/screenshots';output.mkdir(parents=True,exist_ok=True)
        frames=[]
        for phase in ['preparing','exported','returning','local-ready']:
            path=output/(phase+'.ppm')
            subprocess.run([str(BINARY),'--snapshot',str(path),'--phase',phase],check=True)
            data=path.read_bytes();self.assertTrue(data.startswith(b'P6\n375 320\n255\n'));frames.append(data)
        self.assertEqual(len(set(frames)),4)
        a=output/'error-a.ppm';b=output/'error-b.ppm'
        for path,error in [(a,'synthetic-private-fixture-a'),(b,'synthetic-private-fixture-b')]:
            subprocess.run([str(BINARY),'--snapshot',str(path),'--error',error],check=True)
        self.assertEqual(a.read_bytes(),b.read_bytes(),'raw error contents must never be displayed')

if __name__=='__main__':unittest.main()
