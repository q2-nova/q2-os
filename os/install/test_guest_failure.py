"""Actual fixed collector filtering and bounded failure evidence, no device."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
spec=importlib.util.spec_from_file_location('boot_failure_test',HERE/'test_boot.py')
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)

class FailureEvidence(unittest.TestCase):
    def setUp(self):
        base=ROOT/'build/perf';base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=base);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def shell_fixture(self,marker=True):
        for p in ['state','run/q2nd','proc/23/task/23']:(self.root/p).mkdir(parents=True,exist_ok=True)
        if marker:(self.root/'state/.install-fixture').write_text('install-boot-abcdef12\n')
        (self.root/'run/q2nd/player.pid').write_text('23\n')
        # /proc fields after comm: state, ppid, then fields5..22.
        (self.root/'proc/23/task/23/stat').write_text('23 (private name ) here) S 1 '+'0 '*17+'456\n')
        (self.root/'proc/23/task/23/wchan').write_text('futex_wait_queue')
        (self.root/'run/q2nd/service-boot-times').write_text('1.00 service-cleanup-child-begin\n2.00 owner_secret\nsecret service-cleanup-returned\n')
        source=(HERE/'guest_failure.sh').read_text()
        for prefix in ['/state/','/run/','/proc/']:source=source.replace(prefix,str(self.root)+prefix)
        return source

    def test_actual_shell_collects_state_and_filters_private_or_invalid_rows(self):
        result=subprocess.run(['sh','-s'],input=self.shell_fixture(),text=True,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        for value in ['owner=player pid=23','tid=23 state=S ppid=1 start_ticks=456','wchan=futex_wait_queue','1.00 service-cleanup-child-begin','Q2_GUEST_FAILURE_END']:self.assertIn(value,result.stdout)
        for value in ['private name','owner_secret','secret service-cleanup','service-cleanup-returned']:self.assertNotIn(value,result.stdout)

    def test_missing_disposable_guest_marker_refuses_before_output(self):
        result=subprocess.run(['sh','-s'],input=self.shell_fixture(False),text=True,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')

    def test_many_threads_keep_cap_and_collect_stages_before_task_inspection(self):
        script=self.shell_fixture()
        for tid in range(100,165):
            directory=self.root/f'proc/23/task/{tid}';directory.mkdir()
            (directory/'stat').write_text(f'{tid} (fixture thread) S 1 '+'0 '*17+'999\n')
            (directory/'wchan').write_text('futex_wait_queue')
        result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.count(' state='),64)
        self.assertEqual(result.stdout.count(' wchan='),64)
        self.assertIn('tasks_truncated=1',result.stdout)
        self.assertLess(result.stdout.index('service-cleanup-child-begin'),result.stdout.index('owner=player'))
        self.assertTrue(result.stdout.endswith('Q2_GUEST_FAILURE_END\n'))

    def test_symlink_stage_and_invalid_pid_are_not_read(self):
        script=self.shell_fixture();p=self.root/'run/q2nd/service-boot-times';p.unlink()
        secret=self.root/'private';secret.write_text('1.00 shutdown-begin\n');p.symlink_to(secret)
        (self.root/'run/q2nd/player.pid').write_text('../private\n')
        result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0);self.assertNotIn('shutdown-begin',result.stdout);self.assertNotIn('owner=',result.stdout)

    def test_generated_console_files_bounded_and_snapshot_cleaned(self):
        script=self.shell_fixture();folder=self.root/'run/q2-console-profile';folder.mkdir();(self.root/'run/log').mkdir()
        (folder/'result').write_text('Q2_CONSOLE_PROFILE 2\nmode=0\n');(folder/'progress').write_text('1\n');(self.root/'run/log/bringup').write_text('g'*2050)
        result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr);self.assertIn('Q2_CONSOLE_PROFILE 2',result.stdout);self.assertIn('bytes=2049 limit=2048',result.stdout)
        self.assertNotIn('g'*2049,result.stdout);self.assertEqual(list((self.root/'run').glob('q2-guest-failure-*')),[])
    def test_readiness_failure_calls_same_bounded_collector_or_reports_absence(self):
        with patch.object(boot,'capture_guest_failure',return_value=dict(status='complete')) as capture:
            self.assertEqual(boot.capture_readiness_failure(None,self.root,0)['status'],'not-available');capture.assert_not_called()
            row=boot.capture_readiness_failure(['owned-ssh'],self.root,0);self.assertEqual(row['trigger'],'root-readiness-timeout');capture.assert_called_once_with(['owned-ssh'],self.root,0)
    def test_capture_error_does_not_replace_readiness_timeout_or_bypass_cleanup(self):
        record={};cleaned=False
        with patch.object(boot,'capture_guest_failure',side_effect=RuntimeError('generated capture failure')):
            try:
                with self.assertRaisesRegex(TimeoutError,'root readiness timeout'):boot.raise_readiness_timeout(['owned-ssh'],self.root,0,record)
            finally:cleaned=True
        self.assertTrue(cleaned);self.assertEqual(record['readiness_failure_snapshot']['error_type'],'RuntimeError')
    def test_timeout_keeps_partial_output_and_fixed_budgets(self):
        def run(command,**kw):
            self.assertEqual(command,['owned-ssh','timeout','8','sh','-s']);self.assertEqual(kw['timeout'],10)
            kw['stdout'].write(b'Q2_GUEST_FAILURE_BEGIN\n')
            raise subprocess.TimeoutExpired(command,10)
        with patch.object(boot.subprocess,'run',side_effect=run):r=boot.capture_guest_failure(['owned-ssh'],self.root,0)
        self.assertEqual(r['status'],'timed-out');self.assertGreater(r['bytes'],0);self.assertIn('sha256',r)

    def test_nonzero_missing_transport_and_existing_receipt_do_not_mask_original_failure(self):
        with patch.object(boot.subprocess,'run',return_value=subprocess.CompletedProcess([],2)):
            r=boot.capture_guest_failure([],self.root,0)
        self.assertEqual((r['status'],r['exit_code']),('failed',2))
        with patch.object(boot.subprocess,'run',side_effect=FileNotFoundError):
            self.assertEqual(boot.capture_guest_failure([],self.root,1)['status'],'unavailable')
        with patch.object(boot.subprocess,'run') as run:
            r=boot.capture_guest_failure([],self.root,0);run.assert_not_called()
        self.assertEqual(r['status'],'unavailable');self.assertNotIn('sha256',r)

if __name__=='__main__':unittest.main()
