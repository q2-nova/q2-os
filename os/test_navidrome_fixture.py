"""Generated fixture namespaces and cleanup faults; never starts containers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('navidrome_fixture_test',Path(__file__).with_name('navidrome_fixture.py'))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
ROOT=Path(__file__).resolve().parents[1]


class Ownership(unittest.TestCase):
    def setUp(self):
        directory=ROOT/'build/perf';directory.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=directory);self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)

    def test_identical_output_basenames_cannot_claim_another_fixture(self):
        first=m.Fixture(self.out/'a/connected');second=m.Fixture(self.out/'b/connected')
        self.assertNotEqual(first.name,second.name)
        self.assertNotEqual(first.network,second.network)
        self.assertRegex(first.name,r'^q2-nd-[0-9a-f]{12}$')

    def test_optional_lifetime_bounds(self):
        self.assertEqual(m.Fixture(self.out).lifetime,[])
        for seconds in [1,1200,3600]:self.assertEqual(m.Fixture(self.out,seconds).lifetime,['--timeout',str(seconds)])
        for seconds in [0,-1,3601]:
            with self.assertRaises(ValueError):m.Fixture(self.out,seconds)

    def test_log_timeout_still_removes_both_owned_resources(self):
        fixture=m.Fixture(self.out);commands=[]
        def call(command,**kwargs):
            commands.append(command)
            self.assertIn('timeout',kwargs)
            if command[1]=='logs':raise subprocess.TimeoutExpired(command,30)
            return subprocess.CompletedProcess(command,1)
        with patch.object(m.subprocess,'run',side_effect=call):fixture.__exit__()
        self.assertIn(['podman','rm','-f',fixture.name],commands)
        self.assertIn(['podman','network','rm',fixture.network],commands)
        result=json.loads((self.out/'cleanup.json').read_text())
        self.assertTrue(result['complete']);self.assertIn('log capture: TimeoutExpired',result['errors'])

    def test_remaining_resource_is_reported_as_failure(self):
        fixture=m.Fixture(self.out)
        with patch.object(m.subprocess,'run',return_value=subprocess.CompletedProcess([],0)):
            with self.assertRaisesRegex(RuntimeError,'cleanup failed'):fixture.__exit__()
        self.assertFalse(json.loads((self.out/'cleanup.json').read_text())['complete'])


if __name__=='__main__':unittest.main()
