"""Quiet console is not permission to omit the real NAND mapping proof."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
p=Path(__file__).with_name('test_boot.py');spec=importlib.util.spec_from_file_location('skipbad_boot',p);boot=importlib.util.module_from_spec(spec);spec.loader.exec_module(boot)

class SkipbadProof(unittest.TestCase):
    def test_same_geometry_proves_every_bad_count_from_console_or_ram(self):
        for bad in range(601):
            console=f'[ 1.234567] mtdskip: rootfs: {600-bad} good eraseblocks, {bad} skipped\n'
            ram=f'Q2_MTDSKIP_PROOF {600-bad} {bad}\n'
            self.assertEqual(boot.combine_skipbad_proof(console,'',bad)['skipped_eraseblocks'],bad)
            self.assertEqual(boot.combine_skipbad_proof('',ram,bad)['source'],'guest-ram-printk')
            self.assertEqual(boot.combine_skipbad_proof(console,ram,bad)['source'],'console-and-ram')
    def test_missing_zero_wrong_partition_duplicates_and_incorrect_counts_fail_closed(self):
        rows=[('', '',0),('unrelated 3 skipped\n','',3),
          ('mtdskip: userdata: 597 good eraseblocks, 3 skipped\n','',3),
          ('','Q2_MTDSKIP_PROOF 598 3\n',3),('','Q2_MTDSKIP_PROOF 597 3\n',2),
          ('','Q2_MTDSKIP_PROOF 597 3\n'*2,3),
          ('mtdskip: rootfs: 597 good eraseblocks, 3 skipped\n','Q2_MTDSKIP_PROOF 599 1\n',3)]
        for console,ram,bad in rows:
            with self.subTest(console=console,ram=ram,bad=bad),self.assertRaises(ValueError):boot.combine_skipbad_proof(console,ram,bad)
    def test_actual_shell_query_exports_only_fixed_rootfs_numbers(self):
        text='[1.2] mtdskip: rootfs: 597 good eraseblocks, 3 skipped\n[2.3] generated-private-value\n[3.4] mtdskip: userdata: 0 good eraseblocks, 8 skipped\n'
        command='dmesg() { printf "%s" "$1"; }; '+boot.MTDSKIP_QUERY.replace('dmesg |','dmesg "$1" |',1)
        result=subprocess.run(['sh','-c',command,'generated',text],capture_output=True,text=True,timeout=2,check=True)
        self.assertEqual(result.stdout,'Q2_MTDSKIP_PROOF 597 3\n')

if __name__=='__main__':unittest.main()
