import json,shutil,sys,unittest,uuid
import cached_datapath as codec
import prepare_cached_datapath as prep
import test_cached_datapath as native
sys.path.insert(0,str(prep.prep.ROOT/'tests/perf'))
import qualified_fixtures as fixtures

class CachedData(unittest.TestCase):
    def setUp(self):
        self.source=fixtures.input('rf-datapath-source-01/source');fixtures.bind_inputs(self,prep,'BASELINE')
        self.out=prep.prep.ROOT/'build/cached-data-controls'/uuid.uuid4().hex;self.out.mkdir(parents=True)
    def variant(self,name,change):
        path=self.out/name/'core';path.mkdir(parents=True);text=(self.source/'core/rtw_debug.c').read_text();changed=change(text);self.assertNotEqual(text,changed);(path/'rtw_debug.c').write_text(changed);return path.parent
    def test_actual_c_widths_poison_noninterference_busy_and_source_projection(self):
        r=native.run(self.source,self.out/'native');self.assertTrue(r['passed']);self.assertEqual(r['contexts'],4096);self.assertEqual(r['busy_cases'],1)
        before=(prep.BASELINE/'frozen/core/rtw_debug.c').read_text();after=(self.source/'core/rtw_debug.c').read_text();prep.projection(before,after)
        with self.assertRaises(ValueError):prep.projection(before,after+'\n/* unrelated */\n')
    def test_same_exit_zero_wrong_counter_or_truncation_is_rejected(self):
        for name,change in [('swapped',lambda t:t.replace('s.rx_drop = READ_ONCE(adapter->recvpriv.rx_drop);','s.rx_drop = READ_ONCE(adapter->recvpriv.rx_pkts);')),('truncated',lambda t:t.replace('s.rx_drop = READ_ONCE(adapter->recvpriv.rx_drop);','s.rx_drop = (u32)READ_ONCE(adapter->recvpriv.rx_drop);'))]:
            s=self.variant(name,change)
            with self.assertRaisesRegex(ValueError,'field mismatch'):native.run(s,self.out/(name+'-run'))
            self.assertEqual(json.loads((self.out/(name+'-run/result.json')).read_text())['exit_status'],0)
    def test_mutation_and_blocking_lock_are_causal_negatives(self):
        for name,change in [('mutate',lambda t:t.replace('s.rf_state = READ_ONCE(pwr->rf_pwrstate);','s.rf_state = READ_ONCE(pwr->rf_pwrstate);pwr->rf_pwrstate=0;')),('busy-reads',lambda t:t.replace('if (down_trylock(&pwr->lock)) {','if (down_trylock(&pwr->lock)) {s.rf_state=READ_ONCE(pwr->rf_pwrstate);'))]:
            with self.assertRaisesRegex(ValueError,'noninterference'):native.run(self.variant(name,change),self.out/(name+'-run'))
    def test_no_hardware_queries_private_output_or_removed_consumer(self):
        for name,change in [('bus',lambda t:t.replace('s.rf_state = READ_ONCE(pwr->rf_pwrstate);','rtw_read32(adapter,0);s.rf_state = READ_ONCE(pwr->rf_pwrstate);')),('private',lambda t:t.replace('s.rf_state = READ_ONCE(pwr->rf_pwrstate);','RTW_PRINT_SEL(m,"%s",adapter->securitypriv.key);s.rf_state = READ_ONCE(pwr->rf_pwrstate);')),('hook',lambda t:t.replace(prep.HOOK,''))]:
            with self.assertRaises(ValueError):native.extract(self.variant(name,change)/'core/rtw_debug.c')
    def test_closed_decoder_never_promotes_non_atomicity_or_out_of_range(self):
        src=native.extract(self.source/'core/rtw_debug.c');self.assertIn('coherent=0',src)
        d=native.expected(4095);line=' Cached Data: version=1 busy=0 coherent=0 '+' '.join(n+'='+('0x%08x'%d[n] if n in codec.HEX else str(d[n])) for n in codec.FIELDS)
        self.assertEqual(codec.parse(line),d);self.assertIsNone(codec.parse(' Cached Data: version=1 busy=1'))
        for bad in (line.replace('coherent=0','coherent=1'),line+' other=0',line.replace('rf_state=0','rf_state=3'),line.replace('stopped=1','stopped=2'),line.replace('18446744073709551615','18446744073709551616')):
            with self.assertRaises(ValueError):codec.parse(bad)

if __name__=='__main__':unittest.main()
