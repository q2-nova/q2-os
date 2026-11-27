import collections
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import bind_installed_module as b
C=Path(subprocess.check_output(['git','-C',str(b.ROOT),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip()).parent
INSTALLED=C/'build/os/install-vk043-wake-intc-02/root/lib/modules/7.3.0-q2+/extra/8733bs.ko'
REFERENCE=C/'build/worktrees/pm-v034-integration/build/sync-abort-v7/rtl/8733bs.ko'

class ModuleBinding(unittest.TestCase):
    def test_retained_real_module_matches_every_allocated_byte_and_all_function_records(self):
        result=b.compare(INSTALLED,REFERENCE)
        self.assertTrue(result['passed'])
        records=result['installed']['functions']
        self.assertEqual(len(records),4980)
        self.assertEqual(len({x[0] for x in records}),4978)

    def test_one_changed_instruction_refuses_a_code_match(self):
        with tempfile.TemporaryDirectory(dir=b.ROOT/'build') as tmp:
            path=Path(tmp)/'changed.ko';raw=bytearray(INSTALLED.read_bytes())
            obj=b.elf.Elf(bytes(raw));first=next(x for x in b.describe(INSTALLED)['functions'] if x[1]=='.text')
            raw[obj.named['.text'][4]+first[2]]^=1;path.write_bytes(raw)
            result=b.compare(INSTALLED,path)
            self.assertFalse(result['passed']);self.assertFalse(result['code_matches'])

    def test_changed_static_data_is_not_a_complete_binary_match(self):
        with tempfile.TemporaryDirectory(dir=b.ROOT/'build') as tmp:
            path=Path(tmp)/'changed.ko';raw=bytearray(INSTALLED.read_bytes())
            obj=b.elf.Elf(bytes(raw));raw[obj.named['.data'][4]]^=1;path.write_bytes(raw)
            result=b.compare(INSTALLED,path)
            self.assertFalse(result['passed']);self.assertTrue(result['code_matches'])
            self.assertFalse(result['allocated_bytes_match'])

    def test_relative_public_source_is_frozen_and_untracked_fixture_is_excluded(self):
        with tempfile.TemporaryDirectory(dir=b.ROOT/'build') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir()
            (source/'driver.c').write_text('/* generated public source */\n')
            (source/'private-fixture').write_text('synthetic untracked value\n')
            subprocess.run(['git','init','-q',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'add','driver.c'],check=True)
            env={**os.environ,'GIT_AUTHOR_NAME':'Fixture','GIT_AUTHOR_EMAIL':'fixture@localhost',
                'GIT_COMMITTER_NAME':'Fixture','GIT_COMMITTER_EMAIL':'fixture@localhost'}
            subprocess.run(['git','-C',str(source),'-c','commit.gpgsign=false','commit','-qm','generated'],env=env,check=True)
            result=b.run(INSTALLED,REFERENCE,root/'out',source.relative_to(b.ROOT))
            self.assertTrue(result['passed'])
            self.assertEqual(set(result['current_source']['inputs']),{'driver.c'})
            self.assertFalse((root/'out/frozen/source/private-fixture').exists())
            self.assertEqual((root/'out/frozen/source/driver.c').read_bytes(),(source/'driver.c').read_bytes())

if __name__=='__main__':unittest.main()
