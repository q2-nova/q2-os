#!/usr/bin/env python3
"""Run actual C formatter; full MIPS module build separately verifies vendor types."""
import argparse,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
import cached_datapath as codec
import prepare_cached_datapath as prep
function=prep.prep.function
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def extract(source):
    text=source.read_text();body=function(text,'rtw_dump_cached_datapath')
    if function(text,'proc_get_ps_info').count(prep.HOOK.strip())!=1:raise ValueError('actual proc consumer required')
    clean=re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"','',body,flags=re.S)
    calls=set(re.findall(r'\b([A-Za-z_]\w*)\s*\(',clean))-{'if','rtw_dump_cached_datapath'}
    if calls-{'adapter_to_pwrctl','adapter_to_dvobj','GET_HAL_DATA','down_trylock','_exit_pwrlock','RTW_PRINT_SEL','READ_ONCE','ATOMIC_READ'}:raise ValueError('only bounded cached reads allowed')
    if '%s' in body or '%p' in body or 'securitypriv' in body:raise ValueError('no private fields or opaque output')
    return body

PRE=r'''
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef uint32_t u32;typedef uint64_t u64;
struct seq_file {int unused;};
struct pwrctrl_priv {unsigned lock;uint8_t rf_pwrstate;};
struct dvobj_priv {int bDriverStopped,bSurpriseRemoved;};
struct hal {u32 sdio_himr,sdio_hisr;uint8_t SdioRxFIFOCnt;uint16_t SdioRxFIFOSize,SdioTxOQTFreeSpace;};
typedef struct {
 struct pwrctrl_priv power;struct dvobj_priv device;struct hal hal;
 int bup,net_closed;uint8_t netif_up;
 struct {unsigned free_xmitbuf_cnt,free_xmitframe_cnt,free_xmit_extbuf_cnt,free_xframe_ext_cnt;u64 tx_pkts,tx_drop;} xmitpriv;
 struct {unsigned free_recvframe_cnt;u64 rx_pkts,rx_drop,dbg_rx_drop_count,dbg_rx_ampdu_drop_count,dbg_rx_ampdu_loss_count,dbg_rx_conflic_mac_addr_cnt;} recvpriv;
 char poison[128];
} _adapter;
static unsigned owned,reads,tries;
#define adapter_to_pwrctl(a) (&(a)->power)
#define adapter_to_dvobj(a) (&(a)->device)
#define GET_HAL_DATA(a) (&(a)->hal)
#define READ_ONCE(x) (reads++,*(volatile __typeof__(x) *)&(x))
#define ATOMIC_READ(x) (*(x))
static int down_trylock(unsigned *lock){tries++;if(*lock)return 1;*lock=1;owned=1;return 0;}
static void _exit_pwrlock(unsigned *lock){assert(*lock && owned);*lock=0;owned=0;}
static void output(struct seq_file *m,const char *fmt,...){(void)m;assert(!owned);va_list a;va_start(a,fmt);vprintf(fmt,a);va_end(a);}
#define RTW_PRINT_SEL(m,...) output(m,__VA_ARGS__)
'''
POST=r'''
int main(void){
 struct seq_file m={0};_adapter a,copy;
 for(unsigned i=0;i<4096;i++){
  memset(&a,0,sizeof(a));memset(a.poison,'K',sizeof(a.poison));
  a.device.bDriverStopped=i&1;a.device.bSurpriseRemoved=i&2;
  a.bup=i&4;a.net_closed=i&8;a.netif_up=i&16;a.power.rf_pwrstate=i%3;
  a.xmitpriv.free_xmitbuf_cnt=UINT32_MAX-i;a.xmitpriv.free_xmitframe_cnt=i*3;
  a.xmitpriv.free_xmit_extbuf_cnt=i+1024;a.xmitpriv.free_xframe_ext_cnt=i+4096;
  a.recvpriv.free_recvframe_cnt=i+7;
  a.hal.sdio_himr=0x89abcdefU^i;a.hal.sdio_hisr=0x12345678U^i;
  a.hal.SdioRxFIFOCnt=i%256;a.hal.SdioRxFIFOSize=(i*13)%65536;a.hal.SdioTxOQTFreeSpace=(i*17)%65536;
  u64 n=(i==4095?UINT64_MAX-7:0x100000001ULL*i);
  a.xmitpriv.tx_pkts=n;a.xmitpriv.tx_drop=n+1;a.recvpriv.rx_pkts=n+2;a.recvpriv.rx_drop=n+3;
  a.recvpriv.dbg_rx_drop_count=n+4;a.recvpriv.dbg_rx_ampdu_drop_count=n+5;
  a.recvpriv.dbg_rx_ampdu_loss_count=n+6;a.recvpriv.dbg_rx_conflic_mac_addr_cnt=n+7;
  copy=a;reads=tries=0;rtw_dump_cached_datapath(&m,&a);
  assert(!owned && reads>=20 && tries==1 && memcmp(&a,&copy,sizeof(a))==0);
 }
 a.power.lock=1;copy=a;reads=tries=0;rtw_dump_cached_datapath(&m,&a);
 assert(!owned && !reads && tries==1 && memcmp(&a,&copy,sizeof(a))==0);
 return 0;
}
'''

def expected(i):
    n=2**64-8 if i==4095 else 0x100000001*i
    values=[bool(i&1),bool(i&2),bool(i&4),bool(i&8),bool(i&16),i%3,2**32-1-i,i*3,i+1024,i+4096,i+7,0x89abcdef^i,0x12345678^i,i%256,(i*13)%65536,(i*17)%65536,*range(n,n+8)]
    return dict(zip(codec.FIELDS,map(int,values),strict=True))

def run(source,out):
    actual=source/'core/rtw_debug.c';body=extract(actual);out.mkdir(parents=True,exist_ok=False)
    compiler=Path(shutil.which('cc')).resolve()
    result=dict(passed=False,hardware=False,source_sha256=sha(actual),formatter_sha256=hashlib.sha256(body.encode()).hexdigest(),tool_sha256=sha(Path(__file__)),decoder_sha256=sha(Path(codec.__file__)),compiler=dict(path=str(compiler),sha256=sha(compiler),version=subprocess.check_output([str(compiler),'--version'],text=True)))
    try:
        c=out/'formatter.c';c.write_text(PRE+'\n'+body+'\n'+POST);binary=out/'formatter'
        result['command']=[str(compiler),'-std=gnu11','-O2','-Wall','-Wextra','-Werror',str(c),'-o',str(binary)]
        built=subprocess.run(result['command'],capture_output=True,timeout=30)
        (out/'build.log').write_bytes(built.stdout+built.stderr);result['compile_exit_status']=built.returncode
        if built.returncode:raise ValueError('actual C compile failed')
        done=subprocess.run([str(binary)],capture_output=True,timeout=10);(out/'run.log').write_bytes(done.stdout+done.stderr)
        result.update(exit_status=done.returncode,binary_sha256=sha(binary),log_sha256=sha(out/'run.log'),harness_sha256=sha(c))
        if done.returncode:raise ValueError('actual C noninterference or bounded-lock failure')
        lines=done.stdout.decode().splitlines()
        if len(lines)!=4097 or codec.parse(lines[-1]) is not None:raise ValueError('all actual contexts and busy witness required')
        for i,line in enumerate(lines[:-1]):
            if codec.parse(line)!=expected(i):raise ValueError('actual cached field mismatch')
        if sha(actual)!=result['source_sha256'] or sha(Path(__file__))!=result['tool_sha256'] or sha(Path(codec.__file__))!=result['decoder_sha256']:raise ValueError('native inputs changed')
        result.update(passed=True,contexts=4096,busy_cases=1,scope='actual formatter values/widths/try-lock/noninterference; no atomicity, hardware or vendor ABI claim; exact MIPS module build is separate')
    except BaseException as e:result['failure_type']=type(e).__name__;raise
    finally:(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();r=run(a.source,a.out);print(r['passed'])
