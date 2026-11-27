import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('cpm_boot',Path(__file__).with_name('test_boot.py'))
boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)


class DebugEvidence(unittest.TestCase):
    def test_distinct_cycles_and_fixed_debug_flags(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            a,p=boot.cpm_debug_args(root,0,lambda x:'/generated/'+x.name)
            b,q=boot.cpm_debug_args(root,1,lambda x:'/generated/'+x.name)
            self.assertEqual(a,['-d','unimp','-D','/generated/qemu-cpm-0.log'])
            self.assertNotEqual(p,q)
            self.assertIn('/generated/qemu-cpm-1.log',b)
            for cycle in (-1,2,True):
                with self.assertRaises(ValueError):boot.cpm_debug_args(root,cycle,str)

    def test_existing_trace_or_symlink_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'qemu-cpm-0.log';p.write_bytes(b'preserved')
            with self.assertRaises(ValueError):boot.cpm_debug_args(root,0,str)
            p.unlink();target=root/'target';target.write_bytes(b'preserved');p.symlink_to(target)
            with self.assertRaises(ValueError):boot.cpm_debug_args(root,0,str)
            self.assertEqual(target.read_bytes(),b'preserved')

    def test_producer_path_and_digest_are_required(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'qemu-cpm-0.log';p.write_bytes(b'generated trace')
            row=dict(cpm_trace=dict(path=p.name,sha256=boot.sha(p),bytes=p.stat().st_size))
            self.assertEqual(boot.read_cpm_debug_evidence(root,row),b'generated trace')
            p.write_bytes(b'changed trace!!')
            with self.assertRaises(ValueError):boot.read_cpm_debug_evidence(root,row)
            for name in ('../qemu-cpm-0.log','/qemu-cpm-0.log','guessed.log'):
                with self.assertRaises(ValueError):boot.read_cpm_debug_evidence(root,dict(cpm_trace=dict(path=name)))


if __name__=='__main__':unittest.main()
