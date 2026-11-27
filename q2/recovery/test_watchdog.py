"""Host fault-path tests. These do not qualify a physical watchdog or boot."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('physical_watchdog',Path(__file__).with_name('physical-watchdog.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class WatchdogRunner(unittest.TestCase):
    def trial(self, *, expire=False, recovered=False, wrong_module=False):
        root=Path(__file__).resolve().parents[2]/'build/recovery/runner-tests';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            path=Path(temp);binary=path/'helper';binary.write_bytes(b'generated fixture, never executed')
            hashes=[m.MODULE+'  /module_driver/soc_watchdog.ko']+['0'*64+'  '+x for x in ['player','q2nd','S90play']]
            before='old\n'+'\n'.join(hashes)+'\nCharging\n100\n'
            if wrong_module:before=before.replace(m.MODULE,'0'*64)
            calls=[];boots=0
            def remote(args, **kw):
                nonlocal boots
                cmd=args[-1];calls.append(cmd);output=b''
                if cmd==m.PREFLIGHT:output=before.encode()
                elif cmd.endswith('--stop-test'):output=b'Q2_WATCHDOG_STOPPED\n'
                elif cmd.endswith('--expire'):output=b'Q2_WATCHDOG_ARMED\n'
                elif cmd=='cat /proc/sys/kernel/random/boot_id':
                    boots+=1;output=b'new\n' if recovered and boots>1 else b'old\n'
                elif cmd.startswith('sha256sum '):output=('\n'.join(hashes)+'\n').encode()
                return subprocess.CompletedProcess(args,0,output,b'')
            argv=['probe','--out',str(path/'out'),'--binary',str(binary)]+(['--expire'] if expire else [])
            error=None
            with patch.object(sys,'argv',argv),patch.object(m.subprocess,'run',side_effect=remote),patch.object(m.time,'sleep'),patch.object(m.time,'monotonic',side_effect=[0,1] if recovered else [0,200]),patch.object(m,'wait_ready',return_value='PLAYER_READY\nclosed\n'),contextlib.redirect_stdout(io.StringIO()):
                try:m.main()
                except RuntimeError as e:error=str(e)
            return json.loads((path/'out/result.json').read_text()),calls,error
    def test_default_never_expires(self):
        result,calls,error=self.trial()
        self.assertTrue(result['passed']);self.assertIsNone(error)
        self.assertFalse(any(c.endswith('--expire') for c in calls))
    def test_unknown_module_never_arms(self):
        result,calls,error=self.trial(wrong_module=True,expire=True)
        self.assertFalse(result['passed']);self.assertIn('unqualified',error)
        self.assertEqual(len(calls),1)
    def test_missing_new_boot_fails_without_retry(self):
        result,calls,error=self.trial(expire=True)
        self.assertFalse(result['passed']);self.assertIn('no verified new boot',error)
        self.assertEqual(sum(c.endswith('--expire') for c in calls),1)
        self.assertEqual(sum(c.endswith('--disarm') for c in calls),1)
    def test_recovery_requires_new_boot_and_hashes(self):
        result,calls,error=self.trial(expire=True,recovered=True)
        self.assertTrue(result['passed']);self.assertTrue(result['expiry_recovery_passed'])
        self.assertEqual(result['recovered_boot_id'],'new');self.assertIsNone(error)
        self.assertFalse(any(c.endswith('--disarm') for c in calls))
        self.assertTrue(any(c.startswith('sha256sum ') for c in calls))

if __name__=='__main__':unittest.main()
