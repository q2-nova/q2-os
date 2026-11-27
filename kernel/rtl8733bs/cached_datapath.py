"""Closed diagnostic cache format; counters/shadows are explicitly non-atomic."""
import re
FIELDS=('stopped','removed','up','closed','netif_up','rf_state','free_xmitbuf','free_xmitframe','free_extbuf','free_extframe','free_recvframe','himr','hisr','rx_fifo_seq','rx_fifo_size','tx_oqt','tx_packets','tx_drop','rx_packets','rx_drop','rx_core_drop','rx_ampdu_drop','rx_ampdu_loss','rx_conflict')
BOOL=set(FIELDS[:5]);HEX={'himr','hisr'};WIDE=set(FIELDS[16:])

def parse(line):
    if line==' Cached Data: version=1 busy=1':return None
    prefix=' Cached Data: version=1 busy=0 coherent=0 '
    if not line.startswith(prefix):raise ValueError('explicit non-atomic cached data required')
    tokens=line[len(prefix):].split(' ')
    if len(tokens)!=len(FIELDS):raise ValueError('closed cached data inventory required')
    result={}
    for name,token in zip(FIELDS,tokens,strict=True):
        m=re.fullmatch(name+'=('+('0x[0-9a-f]{8}' if name in HEX else '[0-9]+')+')',token)
        if not m:raise ValueError('known numeric cache field required')
        value=int(m[1],16 if name in HEX else 10)
        limit=1 if name in BOOL else 2 if name=='rf_state' else 255 if name=='rx_fifo_seq' else 65535 if name in ('rx_fifo_size','tx_oqt') else 2**64-1 if name in WIDE else 2**32-1
        if value>limit:raise ValueError('bounded cached field required')
        result[name]=value
    return result

FORMATTER=r'''/* Cached data-path state only: no bus/FW queries and no counter resets.
 * Other writers do not take the power lock. Each value is best-effort; the
 * group is non-atomic, including multiword u64 reads on 32-bit MIPS. HIMR/HISR
 * are software copies, not proof of current hardware masks or IRQ delivery.
 */
static void rtw_dump_cached_datapath(struct seq_file *m, _adapter *adapter)
{
	struct pwrctrl_priv *pwr = adapter_to_pwrctl(adapter);
	struct dvobj_priv *dvobj = adapter_to_dvobj(adapter);
	struct {
		u32 stopped, removed, up, closed, netif_up, rf_state;
		u32 free_xmitbuf, free_xmitframe, free_extbuf, free_extframe, free_recvframe;
		u32 himr, hisr, rx_fifo_seq, rx_fifo_size, tx_oqt;
		u64 tx_packets, tx_drop, rx_packets, rx_drop, rx_core_drop;
		u64 rx_ampdu_drop, rx_ampdu_loss, rx_conflict;
	} s;

	if (down_trylock(&pwr->lock)) {
		RTW_PRINT_SEL(m, " Cached Data: version=1 busy=1\n");
		return;
	}
	s.rf_state = READ_ONCE(pwr->rf_pwrstate);
	_exit_pwrlock(&pwr->lock);
	s.stopped = !!ATOMIC_READ(&dvobj->bDriverStopped);
	s.removed = !!ATOMIC_READ(&dvobj->bSurpriseRemoved);
	s.up = !!READ_ONCE(adapter->bup);
	s.closed = !!READ_ONCE(adapter->net_closed);
	s.netif_up = !!READ_ONCE(adapter->netif_up);
	s.free_xmitbuf = READ_ONCE(adapter->xmitpriv.free_xmitbuf_cnt);
	s.free_xmitframe = READ_ONCE(adapter->xmitpriv.free_xmitframe_cnt);
	s.free_extbuf = READ_ONCE(adapter->xmitpriv.free_xmit_extbuf_cnt);
	s.free_extframe = READ_ONCE(adapter->xmitpriv.free_xframe_ext_cnt);
	s.free_recvframe = READ_ONCE(adapter->recvpriv.free_recvframe_cnt);
	s.himr = READ_ONCE(GET_HAL_DATA(adapter)->sdio_himr);
	s.hisr = READ_ONCE(GET_HAL_DATA(adapter)->sdio_hisr);
	s.rx_fifo_seq = READ_ONCE(GET_HAL_DATA(adapter)->SdioRxFIFOCnt);
	s.rx_fifo_size = READ_ONCE(GET_HAL_DATA(adapter)->SdioRxFIFOSize);
	s.tx_oqt = READ_ONCE(GET_HAL_DATA(adapter)->SdioTxOQTFreeSpace);
	s.tx_packets = READ_ONCE(adapter->xmitpriv.tx_pkts);
	s.tx_drop = READ_ONCE(adapter->xmitpriv.tx_drop);
	s.rx_packets = READ_ONCE(adapter->recvpriv.rx_pkts);
	s.rx_drop = READ_ONCE(adapter->recvpriv.rx_drop);
	s.rx_core_drop = READ_ONCE(adapter->recvpriv.dbg_rx_drop_count);
	s.rx_ampdu_drop = READ_ONCE(adapter->recvpriv.dbg_rx_ampdu_drop_count);
	s.rx_ampdu_loss = READ_ONCE(adapter->recvpriv.dbg_rx_ampdu_loss_count);
	s.rx_conflict = READ_ONCE(adapter->recvpriv.dbg_rx_conflic_mac_addr_cnt);
	RTW_PRINT_SEL(m, " Cached Data: version=1 busy=0 coherent=0 stopped=%u removed=%u up=%u closed=%u netif_up=%u rf_state=%u free_xmitbuf=%u free_xmitframe=%u free_extbuf=%u free_extframe=%u free_recvframe=%u himr=0x%08x hisr=0x%08x rx_fifo_seq=%u rx_fifo_size=%u tx_oqt=%u tx_packets=%llu tx_drop=%llu rx_packets=%llu rx_drop=%llu rx_core_drop=%llu rx_ampdu_drop=%llu rx_ampdu_loss=%llu rx_conflict=%llu\n",
		s.stopped, s.removed, s.up, s.closed, s.netif_up, s.rf_state,
		s.free_xmitbuf, s.free_xmitframe, s.free_extbuf, s.free_extframe, s.free_recvframe,
		s.himr, s.hisr, s.rx_fifo_seq, s.rx_fifo_size, s.tx_oqt,
		(unsigned long long)s.tx_packets, (unsigned long long)s.tx_drop,
		(unsigned long long)s.rx_packets, (unsigned long long)s.rx_drop,
		(unsigned long long)s.rx_core_drop, (unsigned long long)s.rx_ampdu_drop,
		(unsigned long long)s.rx_ampdu_loss, (unsigned long long)s.rx_conflict);
}
'''

HOOK='\trtw_dump_cached_datapath(m, padapter);\n'


def projection(before,after):
    """`after` is `before` plus FORMATTER and its one proc hook, nothing else."""
    if after.count(FORMATTER+'\n')!=1 or after.count(HOOK)!=1 or after.replace(FORMATTER+'\n','').replace(HOOK,'')!=before:raise ValueError('only exact passive formatter and one hook may change')
