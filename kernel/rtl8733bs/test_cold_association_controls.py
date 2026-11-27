"""Causal controls for the cold-disconnect teardown (patch 0009) on prepared driver trees."""
import tempfile,unittest
from pathlib import Path
import cold_association as change
import driver_source
from driver_source import function,sha
import test_cold_association as native

class ColdAssociation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline=driver_source.tree('cached-datapath');cls.source=driver_source.tree('cold-disconnect')
    def setUp(self):
        root=driver_source.ROOT/'build/rtl8733bs/tests';root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        self.before=(self.baseline/change.CHANGED).read_text();self.old=function(self.before,'rtw_suspend_free_assoc_resource');self.body=change.transformed(self.old);self.after=self.before.replace(self.old,self.body,1)

    def test_patch_0009_is_exactly_the_transformed_teardown(self):
        self.assertEqual((self.source/change.CHANGED).read_text(),self.after)
        r=native.run(self.source,self.baseline,self.out/'native');self.assertTrue(r['passed'])

    def test_projection_accepts_only_the_exact_cold_lifecycle_delta(self):
        change.projection(self.before,self.after)
        for bad in (self.after+'\n',self.after.replace('rtw_set_to_roam(padapter, 0);','rtw_set_to_roam(padapter, 1);',1),self.after.replace('int rtw_suspend_normal(', 'int changed_suspend_normal(',1)):
            with self.assertRaises(ValueError):change.projection(self.before,bad)
        with self.assertRaises(ValueError):change.transformed(self.body)

    def test_actual_c_config_matrix_keeps_retained_behavior_and_clears_cold_before_callbacks(self):
        for name,flags in native.CONFIGS:
            row=native.compile_case(self.body,self.old,self.out,name,flags)
            self.assertTrue(row['passed'],(self.out/(name+'.log')).read_text());self.assertEqual(row['compile_exit_status'],0);self.assertEqual(row['run_exit_status'],0)
            self.assertEqual(sha(self.out/(name+'.c')),row['harness_sha256'])

    def test_old_missing_late_and_overbroad_clears_are_causal_native_negatives(self):
        mutants={
            'actual-old':self.old,
            'missing':self.body.replace('\t\trtw_set_to_roam(padapter, 0);','\t\t(void)0;',1),
            'late':self.body.replace('\t\trtw_set_to_roam(padapter, 0);','\t\t(void)0;',1).replace('rtw_disassoc_cmd(padapter, 0, RTW_CMDF_DIRECTLY);','rtw_disassoc_cmd(padapter, 0, RTW_CMDF_DIRECTLY);\n\t\trtw_set_to_roam(padapter, 0);',1),
            'overbroad':self.body.replace('if (!pwrpriv->wowlan_mode && !pwrpriv->wowlan_ap_mode)','if (1)',1),
        }
        for name,body in mutants.items():
            row=native.compile_case(body,self.old,self.out,name,native.CONFIGS[-1][1]);self.assertEqual(row['compile_exit_status'],0,(self.out/(name+'.log')).read_text());self.assertFalse(row['passed']);self.assertNotEqual(row['run_exit_status'],0)

if __name__=='__main__':unittest.main()
