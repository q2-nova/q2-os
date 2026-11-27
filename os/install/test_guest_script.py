#!/usr/bin/env python3
"""External-process evidence survives failure; no guest or physical device used."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
import json
import uuid
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("q2_install_boot_fixture", Path(__file__).with_name("test_boot.py"))
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class GuestScriptEvidence(unittest.TestCase):
    def fixture(self):
        out = ROOT / "build/ui/guest-evidence" / uuid.uuid4().hex
        out.mkdir(parents=True)
        return out

    def test_timeout_keeps_partial_stdout_and_stderr(self):
        out = self.fixture()
        code = "import sys,time;print('generated-before-timeout',flush=True);print('generated-stderr',file=sys.stderr,flush=True);time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired):
            boot.run_guest_script([sys.executable, "-c", code], b"generated stdin", out, 0, 1, host_grace=0)
        self.assertIn(b"generated-before-timeout", (out / "guest-script-0.log").read_bytes())
        self.assertIn(b"generated-stderr", (out / "guest-script-0.stderr").read_bytes())

    def test_nonzero_status_keeps_exact_stdin_and_failure_output(self):
        out = self.fixture()
        code = "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());print('generated-failure',file=sys.stderr);sys.exit(7)"
        payload = b"generated fixture\r\nexact bytes\n"
        result = boot.run_guest_script([sys.executable, "-c", code], payload, out, 1, 2)
        self.assertEqual(result.returncode, 7)
        self.assertEqual((out / "guest-script-1.log").read_bytes(), payload)
        self.assertIn(b"generated-failure", (out / "guest-script-1.stderr").read_bytes())

    def test_independent_phases_keep_outputs_and_stop_at_first_failure(self):
        out=self.fixture();records=[];snapshots=[]
        code="import sys;data=sys.stdin.buffer.read();sys.stdout.buffer.write(data);sys.exit(7 if data==b'fail' else 0)"
        result=boot.run_guest_phases([sys.executable,'-c',code],[b'first',b'fail',b'unrequested'],out,0,2,
            records,lambda:snapshots.append([row['status'] for row in records]))
        self.assertEqual(result.returncode,7)
        self.assertEqual([r['status'] for r in records],['succeeded','failed'])
        self.assertEqual(snapshots[0],['requested'])
        self.assertEqual((out/'guest-script-0-phase-0.log').read_bytes(),b'first')
        self.assertEqual((out/'guest-script-0-phase-1.log').read_bytes(),b'fail')
        self.assertFalse((out/'guest-script-0-phase-2.log').exists())

    def test_phase_timeout_preserves_partial_evidence_and_never_starts_next(self):
        out=self.fixture();records=[]
        code="import sys,time;print('generated-partial',flush=True);time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired):
            boot.run_guest_phases([sys.executable,'-c',code],[b'first',b'not-started'],out,0,1,records,lambda:None,host_grace=0)
        self.assertEqual(len(records),1);self.assertEqual(records[0]['status'],'failed')
        self.assertIn(b'generated-partial',(out/'guest-script-0-phase-0.log').read_bytes())
        self.assertFalse((out/'guest-script-0-phase-1.log').exists())

    def test_phase_count_and_budget_stay_bounded(self):
        for scripts,timeout in [([],1),([b'x']*4,1),([b'x'],241),([b'x'],0)]:
            with self.assertRaises(ValueError):boot.run_guest_phases([],scripts,self.fixture(),0,timeout,[],lambda:None)

    def test_receipt_paths_resolve_both_layouts_and_refuse_tampering(self):
        for scripts in ([b'single'],[b'first',b'second']):
            out=self.fixture();records=[]
            boot.run_guest_phases([sys.executable,'-c','import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'],
                scripts,out,0,2,records,lambda:None)
            for row,expected in zip(records,scripts):
                self.assertEqual(boot.read_guest_evidence(out,row),expected)
                self.assertEqual(boot.read_guest_evidence(out,row,'stderr'),b'')
            path=out/records[0]['logs']['stdout']['path']
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):
                boot.read_guest_evidence(out,records[0])
            records[0]['logs']['stdout']['path']='../private-config'
            with self.assertRaisesRegex(ValueError,'unsafe'):
                boot.read_guest_evidence(out,records[0])

    def test_existing_evidence_is_never_overwritten(self):
        out=self.fixture()
        (out/'guest-script-0.log').write_bytes(b'original')
        with patch.object(boot.subprocess,'run') as run:
            with self.assertRaises(FileExistsError):boot.run_guest_script([],b'new',out,0,1)
            run.assert_not_called()
        self.assertEqual((out/'guest-script-0.log').read_bytes(),b'original')

    def test_transition_failure_keeps_first_output_and_blocks_reader(self):
        out=self.fixture();records=[]
        def failed(phase):
            self.assertEqual(phase,0)
            raise TimeoutError('no new guest boot')
        with self.assertRaises(TimeoutError):
            boot.run_guest_phases([sys.executable,'-c','import sys;print(sys.stdin.read())'],
                [b'writer',b'never-read'],out,0,2,records,lambda:None,after_phase=failed)
        self.assertEqual(records[0]['exit_code'],0)
        self.assertEqual(records[0]['transition_status'],'failed')
        self.assertFalse((out/'guest-script-0-phase-1.log').exists())

    def test_warm_reset_requires_changed_boot_without_new_process(self):
        old='11111111-1111-1111-1111-111111111111'
        new='22222222-2222-2222-2222-222222222222'
        replies=[SimpleNamespace(returncode=0,stdout=text) for text in
                 (old,'RESET_REQUESTED',old,new)]
        process=SimpleNamespace(pid=321,poll=lambda:None)
        with patch.object(boot.subprocess,'run',side_effect=replies) as run,patch.object(boot.time,'sleep'):
            result=boot.warm_reboot(['generated-ssh'],process,'install-boot-12345678')
        self.assertEqual(result['before_boot'],old)
        self.assertEqual(result['after_boot'],new)
        self.assertEqual(result['same_emulator_pid'],321)
        self.assertEqual(sum('nohup reboot' in c.args[0][-1] for c in run.call_args_list),1)
        self.assertEqual([c.kwargs['timeout'] for c in run.call_args_list[2:]],[30,30])

    def test_warm_reset_retains_every_observation_and_refuses_existing_file(self):
        out=self.fixture()/'attempts.jsonl'
        old='11111111-1111-1111-1111-111111111111';new='22222222-2222-2222-2222-222222222222'
        replies=[SimpleNamespace(returncode=0,stdout=text,stderr='') for text in (old,'RESET_REQUESTED',old,new)]
        process=SimpleNamespace(pid=321,poll=lambda:None)
        with patch.object(boot.subprocess,'run',side_effect=replies),patch.object(boot.time,'sleep'):
            self.assertTrue(boot.warm_reboot(['generated-ssh'],process,'install-boot-12345678',evidence=out)['passed'])
        rows=[json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([row['stdout'] for row in rows],[old,new])
        with patch.object(boot.subprocess,'run') as run:
            with self.assertRaises(FileExistsError):boot.warm_reboot(['generated-ssh'],process,'install-boot-12345678',evidence=out)
            run.assert_not_called()

    def test_transition_evidence_rejects_tampering_and_unsafe_paths(self):
        out=self.fixture();path=out/'warm-probe-0-0.jsonl';path.write_bytes(b'{"generated":true}\n')
        row={'transition_evidence':{'path':path.name,'sha256':boot.sha(path),'bytes':path.stat().st_size}}
        self.assertEqual(boot.read_transition_evidence(out,row),path.read_bytes())
        path.write_bytes(b'changed')
        with self.assertRaises(ValueError):boot.read_transition_evidence(out,row)
        row['transition_evidence']['path']='../private'
        with self.assertRaises(ValueError):boot.read_transition_evidence(out,row)

    def test_warm_reset_rejects_unowned_context_and_emulator_exit(self):
        process=SimpleNamespace(pid=321,poll=lambda:1)
        with patch.object(boot.subprocess,'run') as run:
            with self.assertRaises(ValueError):boot.warm_reboot([],process,'owner-device')
            run.assert_not_called()
        old='11111111-1111-1111-1111-111111111111'
        with patch.object(boot.subprocess,'run',side_effect=[SimpleNamespace(stdout=old),SimpleNamespace(stdout='RESET_REQUESTED')]):
            with self.assertRaisesRegex(RuntimeError,'emulator exited'):
                boot.warm_reboot(['generated-ssh'],process,'install-boot-12345678')

    def test_warm_reset_retains_last_transport_failure(self):
        out=self.fixture()/'warm.json'
        old='11111111-1111-1111-1111-111111111111'
        replies=[SimpleNamespace(stdout=old),SimpleNamespace(stdout='RESET_REQUESTED'),
                 subprocess.TimeoutExpired(['generated-ssh'],5,output=b'partial',stderr=b'handshake')]
        process=SimpleNamespace(pid=321,poll=lambda:None)
        with patch.object(boot.subprocess,'run',side_effect=replies) as run,patch.object(boot.time,'sleep'),patch.object(boot.time,'monotonic',side_effect=[0,0,0,1,2]):
            with self.assertRaises(TimeoutError):
                boot.warm_reboot(['generated-ssh'],process,'install-boot-12345678',timeout=2,evidence=out)
        import json
        self.assertEqual(json.loads(out.read_text()),dict(seconds=1,timeout=True,stdout='partial',stderr='handshake'))
        self.assertEqual(run.call_args_list[-1].kwargs['timeout'],2)


if __name__ == "__main__":
    unittest.main()
