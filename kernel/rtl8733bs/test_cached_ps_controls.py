"""Causal controls for the cached-PS formatter (patch 0007) on prepared driver trees."""
import json,shutil,unittest
import cached_ps as codec
import driver_source
import prepare
import test_cached_ps as native
PATCH=driver_source.HERE/'patches/0007-Expose-nonblocking-cached-power-management-state.patch'
class CachedPS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline=driver_source.tree('cold');cls.source=driver_source.tree('cached-ps')
    def setUp(self):
        self.out=driver_source.output('cached-ps-controls');self.out.mkdir()
    def variant(self,name,replace):
        src=self.out/name;(src/'core').mkdir(parents=True);(src/'include').mkdir()
        text=(self.source/'core/rtw_debug.c').read_text();changed=replace(text)
        self.assertNotEqual(changed,text);(src/'core/rtw_debug.c').write_text(changed)
        shutil.copyfile(self.source/'include/osdep_service_linux.h',src/'include/osdep_service_linux.h')
        return src

    def test_actual_formatter_property_features_busy_and_source_projection(self):
        r=native.run(self.source,self.out/'actual');self.assertTrue(r['passed']);self.assertEqual(r['contexts'],16384);self.assertEqual(r['busy_cases'],4)
        before=(self.baseline/'core/rtw_debug.c').read_text();after=(self.source/'core/rtw_debug.c').read_text()
        codec.only_snapshot_changed(before,after)
        with self.assertRaises(ValueError):codec.only_snapshot_changed(before,after+'\n/* unrelated edit */\n')

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
        r=native.run(self.source,self.out/'decoder');self.assertTrue(r['passed'])
        line=(self.out/'decoder/scan1-cfg1.log').read_text().splitlines()[0]
        self.assertEqual(codec.parse(line)['cpwm'],13);self.assertIsNone(codec.parse(' Cached PS: version=1 busy=1'))
        for bad in (line+' extra=1',line.replace('firmware_ps=0','firmware_ps=2'),line.replace('cpwm=13','cpwm=256'),line.replace('cfg_pm=0','cfg_pm=3'),line.replace('deny=0xdead0000','deny=private'),' Cached PS: version=1 busy=1 mode=0'):
            with self.assertRaises(ValueError):codec.parse(bad)

    def test_normal_preparation_keeps_diagnostics_opt_in(self):
        ordinary=prepare.selected_patches();diagnostic=prepare.selected_patches(cached=True)
        self.assertNotIn(PATCH,ordinary);self.assertEqual(set(diagnostic)-set(ordinary),{PATCH})
        self.assertEqual(set(prepare.selected_patches(cold=True,cached=True))-set(prepare.selected_patches(cold=True)),{PATCH})
        with self.assertRaises(ValueError):prepare.selected_patches(datapath=True)

if __name__=='__main__':unittest.main()
