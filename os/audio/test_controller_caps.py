import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE=Path(__file__).with_name('controller_caps.py')
SPEC=importlib.util.spec_from_file_location('controller_caps',SOURCE)
CAPS=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(CAPS)
APP='a'*64
NONCE='b'*32


def response():
    fields={'schema':'2','boot':'12345678-1234-1234-1234-123456789abc','pid':'42','start_ticks':'123456',
            'app':APP,'kernel':'c'*64,'charge':'[auto] inhibit-charge','method':'owned-ro-debugfs',
            'cleanup':'1','unchanged':'1'}
    fields.update({f'cap_{name}':'1' for name in CAPS.FIELDS})
    fields['cap_snpsid']='0x4F54280A';fields['cap_dev_ep_dirs']='0x0'
    return ''.join(f'{key}={value}\n' for key,value in fields.items())


class ControllerCapsTests(unittest.TestCase):
    def setUp(self):
        base=CAPS.ROOT/'build/perf/tests';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='cached-controller-',dir=base))
    def tearDown(self):shutil.rmtree(self.root)

    def test_protocol_reads_only_cached_whitelist_and_preserves_identity(self):
        protocol=CAPS.script(APP,NONCE)
        for forbidden in ['devmem','/dev/mem','regdump','/class/gpio','config.toml','player.log','dmesg','hold','power/state','echo auto','rm -rf']:
            self.assertNotIn(forbidden,protocol)
        self.assertIn('ro,nosuid,nodev,noexec',protocol)
        self.assertIn('mounted_debugfs "$q2cap_root/debug" 1',protocol)
        self.assertIn('|'.join(CAPS.FIELDS),protocol)
        self.assertIn('/proc/$q2cap_pid/exe',protocol)
        self.assertEqual(protocol.count('/proc/$q2cap_pid/stat'),2)
        self.assertEqual(protocol.count('/proc/sys/kernel/random/boot_id'),2)
        self.assertEqual(protocol.count('/sys/class/power_supply/axp20x-battery/charge_behaviour'),2)
        self.assertEqual(protocol.count('/sys/class/udc/13500000.usb/state'),2)
        subprocess.run(['sh','-n'],input=protocol.encode(),check=True,timeout=5)
        with self.assertRaises(ValueError):CAPS.script(APP,'../unknown')

    def test_numeric_cache_qualification_is_not_a_role_or_power_claim(self):
        parsed=CAPS.parse(response(),APP)
        self.assertEqual(parsed['cached_hardware_parameters']['snpsid'],'0x4f54280a')
        self.assertEqual(parsed['cached_hardware_parameters']['dev_ep_dirs'],'0x00000000')
        self.assertEqual(parsed['charge_behaviour'],'[auto] inhibit-charge')

    def test_incomplete_changed_duplicate_wrong_identity_and_unknown_fields_fail(self):
        good=response()
        for invalid in [good.replace('unchanged=1\n',''),good.replace('unchanged=1','unchanged=0'),
                        good.replace('cleanup=1','cleanup=0'),good+'cap_snpsid=0x4F54280A\n',
                        good.replace(APP,'d'*64),good.replace('0x4F54280A','0x0'),
                        good.replace('cap_arch=1','cap_arch=not-a-number'),good+'unexpected=1\n',
                        good.replace('start_ticks=123456','start_ticks=0'),
                        good.replace('[auto] inhibit-charge','auto [inhibit-charge]'),
                        good.replace('12345678-1234-1234-1234-123456789abc','-'*36)]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):CAPS.parse(invalid,APP)

    def test_configured_dma_uses_only_cached_params_two_boolean_fields(self):
        protocol=CAPS.script(APP,NONCE,configured=True)
        self.assertIn('/usb/13500000.usb/params',protocol)
        self.assertNotIn('/hw_params',protocol)
        self.assertIn('^(g_dma|g_dma_desc)$',protocol)
        self.assertIn('schema=3',protocol)
        self.assertNotIn('printf "cap_%s',protocol)
        for forbidden in ['regdump','devmem','config.toml','player.log','/class/gpio']:
            self.assertNotIn(forbidden,protocol)
        subprocess.run(['sh','-n'],input=protocol.encode(),check=True,timeout=5)
        header=''.join(line+'\n' for line in response().splitlines() if not line.startswith('cap_'))
        header=header.replace('schema=2','schema=3')
        good=header+'param_g_dma=1\nparam_g_dma_desc=1\n'
        parsed=CAPS.parse(good,APP,configured=True)
        self.assertEqual(parsed['cached_configured_parameters'],{'g_dma':1,'g_dma_desc':1})
        for invalid in [good.replace('param_g_dma=1','param_g_dma=0'),
                        good.replace('param_g_dma_desc=1','param_g_dma_desc=2'),
                        good.replace('param_g_dma_desc=1\n',''),
                        good+'param_g_dma_desc=1\n',
                        good.replace('schema=3','schema=2'),response()]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):CAPS.parse(invalid,APP,configured=True)
        with self.assertRaises(ValueError):CAPS.parse(good,APP)

    def test_readonly_mount_guard_rejects_ignored_flags_wrong_type_and_ambiguous_mounts(self):
        info=self.root/'mountinfo'
        guard=CAPS.mount_guard().replace('/proc/self/mountinfo',str(info))
        for options,kind,duplicates,allowed in [('ro,nosuid,nodev,noexec','debugfs',1,True),
                ('rw,nosuid,nodev,noexec','debugfs',1,False),('ro','debugfs',1,False),
                ('ro,nosuid,nodev,noexec','sysfs',1,False),('ro,nosuid,nodev,noexec','debugfs',2,False)]:
            with self.subTest(options=options,kind=kind,duplicates=duplicates):
                info.write_text(f'1 0 0:1 / /owned/mount {options} - {kind} debugfs rw\n'*duplicates)
                result=subprocess.run(['sh','-c','set -- /owned/mount 1\n'+guard],timeout=5)
                self.assertEqual(result.returncode==0,allowed)

    def cleanup_case(self,*,owner=NONCE,umount_exit=0):
        owned=self.root/'owned';(owned/'debug').mkdir(parents=True)
        (owned/'owner').write_text(owner)
        info=self.root/'mountinfo';info.write_text(f'1 0 0:1 / {owned}/debug ro,nosuid,nodev,noexec - debugfs debugfs rw\n')
        helper=self.root/'umount';calls=self.root/'calls'
        helper.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {calls}\nexit {umount_exit}\n');helper.chmod(0o700)
        function=re.search(r'remove_owned\(\) \{.*?\n\}',CAPS.script(APP,NONCE),re.S).group()
        function=function.replace('/proc/self/mountinfo',str(info)).replace('/bin/busybox umount',str(helper))
        result=subprocess.run(['sh','-c',f'q2cap_owned=1\nq2cap_mount_tried=1\nq2cap_root={owned}\n'+function+'\nremove_owned'],timeout=5)
        return result,owned,calls

    def test_cleanup_unmounts_only_owned_target_then_removes_only_empty_scratch(self):
        result,owned,calls=self.cleanup_case()
        self.assertEqual(result.returncode,0);self.assertFalse(owned.exists())
        self.assertEqual(calls.read_text(),f'{owned}/debug\n')

    def test_cleanup_failure_or_wrong_owner_is_not_hidden_or_force_deleted(self):
        result,owned,calls=self.cleanup_case(umount_exit=1)
        self.assertNotEqual(result.returncode,0);self.assertTrue((owned/'owner').exists())
        shutil.rmtree(owned);calls.unlink()
        result,owned,calls=self.cleanup_case(owner='unknown')
        self.assertNotEqual(result.returncode,0);self.assertFalse(calls.exists())
        self.assertEqual((owned/'owner').read_text(),'unknown')

    def test_dry_run_never_invokes_transport(self):
        helper=self.root/'never-run';helper.write_text('#!/bin/sh\nexit 91\n');helper.chmod(0o700)
        result=subprocess.run([sys.executable,str(SOURCE),'--expected-app-sha256',APP,
            '--out',str(self.root/'out'),'--ssh-helper',str(helper),'--dry-run'],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        receipt=json.loads((self.root/'out/result.json').read_text())
        self.assertFalse(receipt['hardware']);self.assertFalse(receipt['passed'])
        self.assertFalse((self.root/'out/public-parameters.txt').exists())
        self.assertFalse(receipt['gpio_role_vbus_pmic_service_writes'])


if __name__=='__main__':unittest.main()
