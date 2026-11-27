import difflib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
import prepare_cold_association as p
import cold_association as change
import test_cold_association as native
sys.path.insert(0,str(p.prep.ROOT/'tests/perf'))
import qualified_fixtures as fixtures

class ColdAssociation(unittest.TestCase):
    def setUp(self):
        fixtures.bind_inputs(self,p,'BASELINE')
        root=p.prep.ROOT/'build/rf-cold-association-controls';root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        self.before=(p.BASELINE/'frozen'/p.CHANGED).read_text();self.old=p.prep.function(self.before,'rtw_suspend_free_assoc_resource');self.body=change.transformed(self.old);self.after=self.before.replace(self.old,self.body,1)

    def test_projection_accepts_only_the_exact_cold_lifecycle_delta(self):
        change.projection(self.before,self.after)
        for bad in (self.after+'\n',self.after.replace('rtw_set_to_roam(padapter, 0);','rtw_set_to_roam(padapter, 1);',1),self.after.replace('int rtw_suspend_normal(', 'int changed_suspend_normal(',1)):
            with self.assertRaises(ValueError):change.projection(self.before,bad)
        with self.assertRaises(ValueError):change.transformed(self.body)

    def test_actual_c_config_matrix_keeps_retained_behavior_and_clears_cold_before_callbacks(self):
        for name,flags in native.CONFIGS:
            row=native.compile_case(self.body,self.old,self.out,name,flags)
            self.assertTrue(row['passed'],(self.out/(name+'.log')).read_text());self.assertEqual(row['compile_exit_status'],0);self.assertEqual(row['run_exit_status'],0)
            self.assertEqual(p.prep.sha(self.out/(name+'.c')),row['harness_sha256'])

    def test_old_missing_late_and_overbroad_clears_are_causal_native_negatives(self):
        mutants={
            'actual-old':self.old,
            'missing':self.body.replace('\t\trtw_set_to_roam(padapter, 0);','\t\t(void)0;',1),
            'late':self.body.replace('\t\trtw_set_to_roam(padapter, 0);','\t\t(void)0;',1).replace('rtw_disassoc_cmd(padapter, 0, RTW_CMDF_DIRECTLY);','rtw_disassoc_cmd(padapter, 0, RTW_CMDF_DIRECTLY);\n\t\trtw_set_to_roam(padapter, 0);',1),
            'overbroad':self.body.replace('if (!pwrpriv->wowlan_mode && !pwrpriv->wowlan_ap_mode)','if (1)',1),
        }
        for name,body in mutants.items():
            row=native.compile_case(body,self.old,self.out,name,native.CONFIGS[-1][1]);self.assertEqual(row['compile_exit_status'],0,(self.out/(name+'.log')).read_text());self.assertFalse(row['passed']);self.assertNotEqual(row['run_exit_status'],0)

    def seed(self):
        repo=self.out/'repo';repo.mkdir();baseline=self.out/'baseline';(baseline/'frozen').mkdir(parents=True)
        files={p.CHANGED:self.before,'core/rtw_debug.c':(p.BASELINE/'frozen/core/rtw_debug.c').read_text()}
        def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.PIPE,text=True)
        git('init','-q')
        for name,text in files.items():
            for root in (repo,baseline/'frozen'):
                f=root/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text)
        git('add','.');git('-c','user.name=Q2 fixture','-c','user.email=noreply@localhost','-c','commit.gpgsign=false','commit','-qm','generated source fixture')
        r=dict(passed=True,build=dict(cleanup=[dict(absent=True)]),module_source_root=str(repo),source_commit=git('rev-parse','HEAD').strip(),inputs={n:p.prep.sha(baseline/'frozen'/n) for n in files},input_modes={n:False for n in files})
        (baseline/'result.json').write_text(json.dumps(r));return baseline,p.prep.sha(baseline/'result.json'),r

    def test_actual_preparation_generalization_preserves_every_undeclared_input_and_mode(self):
        baseline,digest,old=self.seed();out=self.out/'source-result'
        r=p.prep.prepare(out,baseline=baseline,expected=digest,patch=p.PATCH,projection=change.projection,changed_file=p.CHANGED,commit_message='generated cold-lifecycle fixture')
        self.assertTrue(r['passed']);self.assertEqual(r['changed'],[p.CHANGED]);self.assertEqual(set(r['inputs']),set(old['inputs']));self.assertEqual(r['inputs']['core/rtw_debug.c'],old['inputs']['core/rtw_debug.c'])
        change.projection(self.before,(out/'source'/p.CHANGED).read_text())
        with self.assertRaises(FileExistsError):p.prep.prepare(out,baseline=baseline,expected=digest,patch=p.PATCH,projection=change.projection,changed_file=p.CHANGED)

    def test_missing_declared_source_and_an_extra_file_delta_are_refused(self):
        baseline,digest,old=self.seed()
        with self.assertRaisesRegex(ValueError,'complete baseline inventory'):p.prep.prepare(self.out/'missing',baseline=baseline,expected=digest,patch=p.PATCH,projection=change.projection,changed_file='../other.c')
        self.assertFalse((self.out/'missing').exists())
        debug=(p.BASELINE/'frozen/core/rtw_debug.c').read_text();changed=debug.replace('/*','/* unexpected',1)
        patch=self.out/'extra.patch';patch.write_text(p.PATCH.read_text()+''.join(difflib.unified_diff(debug.splitlines(keepends=True),changed.splitlines(keepends=True),fromfile='a/core/rtw_debug.c',tofile='b/core/rtw_debug.c')))
        with self.assertRaisesRegex(ValueError,'only declared driver source'):p.prep.prepare(self.out/'extra',baseline=baseline,expected=digest,patch=patch,projection=change.projection,changed_file=p.CHANGED)
        self.assertFalse(json.loads((self.out/'extra/result.json').read_text())['passed'])

if __name__=='__main__':unittest.main()
