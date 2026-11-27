import hashlib,importlib.util,json,shutil,sys,tempfile,unittest,uuid
from pathlib import Path
import cached_ps as codec
import test_cached_ps as native
import prepare_cached_ps as prep
sys.path.insert(0,str(prep.ROOT/'tests/perf'))
import qualified_fixtures as fixtures
class CachedPS(unittest.TestCase):
    def setUp(self):
        self.source=fixtures.input('rf-ps-cache-source-02/source');fixtures.bind_inputs(self,prep,'BASELINE')
        self.out=prep.ROOT/'build/cached-ps-controls'/uuid.uuid4().hex;self.out.mkdir(parents=True)
    def variant(self,name,replace):
        src=self.out/name;(src/'core').mkdir(parents=True);(src/'include').mkdir()
        text=(self.source/'core/rtw_debug.c').read_text();changed=replace(text)
        self.assertNotEqual(changed,text);(src/'core/rtw_debug.c').write_text(changed)
        shutil.copyfile(self.source/'include/osdep_service_linux.h',src/'include/osdep_service_linux.h')
        return src

    def test_actual_formatter_property_features_busy_and_source_projection(self):
        r=native.run(self.source,self.out/'actual');self.assertTrue(r['passed']);self.assertEqual(r['contexts'],16384);self.assertEqual(r['busy_cases'],4)
        prep.only_snapshot_changed((prep.BASELINE/'frozen/core/rtw_debug.c').read_text(),(self.source/'core/rtw_debug.c').read_text())
        with self.assertRaises(ValueError):prep.only_snapshot_changed((prep.BASELINE/'frozen/core/rtw_debug.c').read_text(),(self.source/'core/rtw_debug.c').read_text()+'\n/* unrelated edit */\n')

    def test_native_real_timer_wrapper_rejects_the_original_bad_pointer(self):
        src=self.variant('bad-timer',lambda s:s.replace('!!_check_timer_is_active(&dvobj->dynamic_chk_timer)','!!timer_pending(&dvobj->dynamic_chk_timer)'))
        with self.assertRaisesRegex(ValueError,'compile failed'):native.run(src,self.out/'bad-timer-run')
        self.assertIn('incompatible-pointer-types',(self.out/'bad-timer-run/scan0-cfg0.build.log').read_text())

    def test_actual_c_rejects_unlocked_deny_and_input_mutation(self):
        for name,transform in [
            ('unlocked-deny',lambda s:s.replace('s.deny = rtw_ps_deny_get(adapter);','s.deny = 0;').replace('_exit_pwrlock(&pwr->lock);','_exit_pwrlock(&pwr->lock);\n\ts.deny = rtw_ps_deny_get(adapter);')),
            ('mutating-reader',lambda s:s.replace('s.mode = READ_ONCE(pwr->pwr_mode);','s.mode = READ_ONCE(pwr->pwr_mode);\n\tpwr->pwr_mode = 0;'))]:
            src=self.variant(name,transform)
            with self.assertRaisesRegex(ValueError,'noninterference'):native.run(src,self.out/(name+'-run'))

    def test_exit_zero_but_wrong_cached_field_is_not_success(self):
        src=self.variant('wrong-shadow',lambda s:s.replace('s.cpwm = READ_ONCE(pwr->cpwm);','s.cpwm = READ_ONCE(pwr->rpwm);'))
        with self.assertRaisesRegex(ValueError,'mismatch'):native.run(src,self.out/'wrong-shadow-run')
        r=json.loads((self.out/'wrong-shadow-run/result.json').read_text());self.assertFalse(r['passed']);self.assertEqual(r['cases'][0]['exit_status'],0)

    def test_source_refuses_non_cached_calls_private_output_and_removed_consumer(self):
        for name,transform in [
            ('active-query',lambda s:s.replace('s.mode = READ_ONCE(pwr->pwr_mode);','PS_RDY_CHECK(adapter);\n\ts.mode = READ_ONCE(pwr->pwr_mode);')),
            ('private-output',lambda s:s.replace('s.mode = READ_ONCE(pwr->pwr_mode);','RTW_PRINT_SEL(m, "%s", adapter->securitypriv.private_key);\n\ts.mode = READ_ONCE(pwr->pwr_mode);')),
            ('consumer-missing',lambda s:s.replace('\trtw_dump_cached_ps_state(m, padapter);',''))]:
            src=self.variant(name,transform)
            with self.assertRaises(ValueError):native.extract(src/'core/rtw_debug.c')

    def test_decoder_closed_enums_bounds_and_busy(self):
        line=fixtures.input('rf-ps-cache-native-04/scan1-cfg1.log').read_text().splitlines()[0]
        self.assertEqual(codec.parse(line)['cpwm'],13);self.assertIsNone(codec.parse(' Cached PS: version=1 busy=1'))
        for bad in (line+' extra=1',line.replace('firmware_ps=0','firmware_ps=2'),line.replace('cpwm=13','cpwm=256'),line.replace('cfg_pm=0','cfg_pm=3'),line.replace('deny=0xdead0000','deny=private'),' Cached PS: version=1 busy=1 mode=0'):
            with self.assertRaises(ValueError):codec.parse(bad)

    def test_baseline_digest_source_bytes_and_modes_are_admitted(self):
        base=self.out/'baseline';(base/'frozen/core').mkdir(parents=True);file=base/'frozen/core/rtw_debug.c';file.write_text('fixture');file.chmod(0o644)
        r=dict(passed=True,build={'cleanup':[{'absent':True}]},inputs={'core/rtw_debug.c':prep.sha(file)},input_modes={'core/rtw_debug.c':False})
        (base/'result.json').write_text(json.dumps(r));digest=prep.sha(base/'result.json');prep.admit(base,digest)
        with self.assertRaises(ValueError):prep.admit(base,'0'*64)
        file.write_text('changed')
        with self.assertRaises(ValueError):prep.admit(base,digest)
        file.write_text('fixture');file.chmod(0o755)
        with self.assertRaises(ValueError):prep.admit(base,digest)

    def test_normal_preparation_keeps_diagnostics_opt_in(self):
        spec=importlib.util.spec_from_file_location('rtl_source_prepare',Path(__file__).with_name('prepare.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        ordinary=module.selected_patches();diagnostic=module.selected_patches(cached=True)
        self.assertNotIn(prep.PATCH,ordinary);self.assertEqual(set(diagnostic)-set(ordinary),{prep.PATCH})
        self.assertEqual(set(module.selected_patches(cold=True,cached=True))-set(module.selected_patches(cold=True)),{prep.PATCH})

if __name__=='__main__':unittest.main()
