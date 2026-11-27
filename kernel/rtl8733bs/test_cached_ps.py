#!/usr/bin/env python3
"""Execute the real cached-PS formatter with locks, feature variants and poison."""
import argparse,hashlib,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'kernel/watchdog'))
from test_driver import function
from cached_ps import FIELDS,parse
import cached_ps
CALLS={'adapter_to_pwrctl','adapter_to_dvobj','down_trylock','RTW_PRINT_SEL','READ_ONCE','rtw_ps_deny_get','rtw_time_after','rtw_get_current_time','_exit_pwrlock','GET_HAL_DATA','ATOMIC_READ','timer_pending','_check_timer_is_active','rtw_cfg80211_pwr_mgmt'}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def extract(source):
    text=source.read_text();body=function(text,'rtw_dump_cached_ps_state');consumer=function(text,'proc_get_ps_info')
    if consumer.count('rtw_dump_cached_ps_state(m, padapter);')!=1:raise ValueError('actual proc consumer must invoke formatter exactly once')
    clean=re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"','',body,flags=re.S)
    calls=set(re.findall(r'\b([A-Za-z_]\w*)\s*\(',clean))-{'if','sizeof','rtw_dump_cached_ps_state'}
    if calls-CALLS:raise ValueError('non-cached or unknown call in snapshot: '+str(sorted(calls-CALLS)))
    if '%s' in body or '%p' in body or re.search(r'securitypriv\.(?!binstallGrpkey\b)',body):raise ValueError('private/opaque output forbidden')
    return body

PRE=r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef uint32_t u32;
struct seq_file {int unused;};
struct timer_list {unsigned pending;};
/* ACTUAL_TIMER_LAYOUT */
struct pwrctrl_priv {
 unsigned lock,pwr_mode,bFwCurrentInPSMode,rpwm,cpwm,tog,cpwm_tog;
 unsigned bLeisurePs,LpsIdleCount,bInSuspend,bips_processing,ps_processing,bpower_saving;
 uint32_t ps_deny,lps_deny_time;
};
struct mi_state {unsigned ld_sta_num,lg_sta_num;};
struct dvobj_priv {struct mi_state iface_state;struct rtw_timer_list dynamic_chk_timer;};
struct hal_data {unsigned bFWReady,hw_init_completed;};
struct mlme {struct {unsigned bBusyTraffic;uint32_t LowPowerTransitionCount;} LinkDetectInfo;
#ifdef CONFIG_SET_SCAN_DENY_TIMER
 unsigned set_scan_deny;
#endif
};
typedef struct {
 struct pwrctrl_priv power;struct dvobj_priv device;struct hal_data hal;
 struct {unsigned cmdthd_running;} cmdpriv;struct mlme mlmepriv;
 struct {unsigned binstallGrpkey;char private_key[32];} securitypriv;
 unsigned cfg_pm;char private_config[64];
} _adapter;
static unsigned owned,reads,deny_reads,try_calls;static uint32_t now;
#define adapter_to_pwrctl(a) (&(a)->power)
#define adapter_to_dvobj(a) (&(a)->device)
#define GET_HAL_DATA(a) (&(a)->hal)
#define READ_ONCE(x) (reads++,*(volatile __typeof__(x) *)&(x))
#define ATOMIC_READ(x) (*(x))
static int down_trylock(unsigned *lock){try_calls++;if(*lock)return 1;*lock=1;owned=1;return 0;}
static void _exit_pwrlock(unsigned *lock){assert(owned && *lock);*lock=0;owned=0;}
static u32 rtw_ps_deny_get(_adapter *a){assert(owned && a->power.lock);deny_reads++;return a->power.ps_deny;}
#define rtw_time_after(a,b) ((int32_t)((b)-(a))<0)
static uint32_t rtw_get_current_time(void){assert(owned);return now;}
static unsigned timer_pending(const struct timer_list *t){assert(!owned);return t->pending;}
/* ACTUAL_TIMER_ACCESSOR */
#ifdef CONFIG_IOCTL_CFG80211
static unsigned rtw_cfg80211_pwr_mgmt(_adapter *a){assert(!owned);return a->cfg_pm;}
#endif
static void output(struct seq_file *m,const char *fmt,...){
 (void)m;assert(!owned);va_list args;va_start(args,fmt);vprintf(fmt,args);va_end(args);
}
#define RTW_PRINT_SEL(m,...) output(m,__VA_ARGS__)
'''

POST=r'''
int main(void){
 struct seq_file stream={0};_adapter a,copy;
 for(unsigned i=0;i<4096;i++){
  memset(&a,0,sizeof(a));now=UINT32_MAX-(i%300);
  a.power=(struct pwrctrl_priv){.pwr_mode=7,.rpwm=11,.cpwm=13,.tog=128,.cpwm_tog=0,
   .bFwCurrentInPSMode=i&1,.bLeisurePs=i&2,.LpsIdleCount=i%256,.bInSuspend=i&4,
   .bips_processing=i&8,.ps_processing=i&16,.bpower_saving=i&32,
   .ps_deny=0xdead0000U+i,.lps_deny_time=now+(i&64?100U:(uint32_t)-100)};
  a.hal.bFWReady=i&128;a.hal.hw_init_completed=i&256;a.cmdpriv.cmdthd_running=i&512;
  a.device.dynamic_chk_timer.timer.pending=i&1024;a.mlmepriv.LinkDetectInfo.bBusyTraffic=i&2048;
  a.mlmepriv.LinkDetectInfo.LowPowerTransitionCount=UINT32_MAX-i;
  a.device.iface_state.ld_sta_num=i%4;a.device.iface_state.lg_sta_num=(i+1)%4;
  a.securitypriv.binstallGrpkey=i&2;a.cfg_pm=i&8;
#ifdef CONFIG_SET_SCAN_DENY_TIMER
  a.mlmepriv.set_scan_deny=i&4;
#endif
  memset(a.securitypriv.private_key,'K',sizeof(a.securitypriv.private_key));
  memset(a.private_config,'P',sizeof(a.private_config));copy=a;
  reads=deny_reads=try_calls=0;rtw_dump_cached_ps_state(&stream,&a);
  assert(!owned && !a.power.lock && deny_reads==1 && try_calls==1 && reads>15);
  assert(memcmp(&a,&copy,sizeof(a))==0);
 }
 a.power.lock=1;copy=a;reads=deny_reads=try_calls=0;
 rtw_dump_cached_ps_state(&stream,&a);
 assert(reads==0 && deny_reads==0 && try_calls==1 && !owned && memcmp(&a,&copy,sizeof(a))==0);
 return 0;
}
'''

def expected(i,scan,cfg):
    vals=[7,bool(i&1),11,13,128,0,bool(i&2),i%256,bool(i&4),bool(i&8),bool(i&16),bool(i&32),0xdead0000+i,bool(i&64),bool(i&128),bool(i&256),bool(i&512),bool(i&1024),bool(i&2048),2**32-1-i,i%4,(i+1)%4,bool(i&2),bool(i&4) if scan else 2,bool(i&8) if cfg else 2]
    return dict(zip(FIELDS,map(int,vals),strict=True))

def run(source,out):
    actual=source/'core/rtw_debug.c';body=extract(actual);header=source/'include/osdep_service_linux.h';text=header.read_text()
    timer=re.search(r'struct rtw_timer_list \{.*?\n\};',text,re.S)
    if not timer:raise ValueError('actual vendor timer wrapper required')
    alias=re.search(r'typedef\s+struct\s+rtw_timer_list\s+_timer;',text)
    if not alias:raise ValueError('actual vendor timer alias required')
    prefix=PRE.replace('/* ACTUAL_TIMER_LAYOUT */',timer[0]+'\n'+alias[0]).replace('/* ACTUAL_TIMER_ACCESSOR */',function(text,'_check_timer_is_active'))
    out.mkdir(parents=True,exist_ok=False)
    r=dict(passed=False,hardware=False,source=str(actual),source_sha256=sha(actual),timer_header_sha256=sha(header),formatter_sha256=hashlib.sha256(body.encode()).hexdigest(),tool_sha256=sha(Path(__file__)),decoder_sha256=sha(Path(cached_ps.__file__)),cases=[])
    try:
        for scan,cfg in ((False,False),(True,False),(False,True),(True,True)):
            name=f'scan{int(scan)}-cfg{int(cfg)}';c=out/(name+'.c');binary=out/name
            c.write_text(prefix+'\n'+body+'\n'+POST)
            flags=(['-DCONFIG_SET_SCAN_DENY_TIMER'] if scan else [])+(['-DCONFIG_IOCTL_CFG80211'] if cfg else [])
            built=subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror',*flags,str(c),'-o',str(binary)],capture_output=True,timeout=30)
            (out/(name+'.build.log')).write_bytes(built.stdout+built.stderr)
            if built.returncode:raise ValueError('actual snapshot compile failed: '+name)
            done=subprocess.run([str(binary)],capture_output=True,timeout=10);log=out/(name+'.log');log.write_bytes(done.stdout+done.stderr)
            row=dict(name=name,exit_status=done.returncode,source_sha256=sha(c),binary_sha256=sha(binary),log_sha256=sha(log));r['cases'].append(row)
            if done.returncode:raise ValueError('noninterference/lock/formatting contract failed: '+name)
            lines=done.stdout.decode().splitlines()
            if len(lines)!=4097 or lines[-1]!=' Cached PS: version=1 busy=1':raise ValueError('all property cases plus bounded busy witness required')
            for i,line in enumerate(lines[:-1]):
                if parse(line)!=expected(i,scan,cfg):raise ValueError('actual cached field/feature/wrap mismatch: '+name+' '+str(i))
        if sha(actual)!=r['source_sha256'] or sha(header)!=r['timer_header_sha256'] or sha(Path(__file__))!=r['tool_sha256'] or sha(Path(cached_ps.__file__))!=r['decoder_sha256']:raise ValueError('native inputs changed')
        r.update(passed=True,contexts=4*4096,busy_cases=4,scope='actual C formatter fields/feature branches/time-wrap/try-lock/noninterference only; physical semantics remain separate')
    except BaseException as e:r['failure_type']=type(e).__name__;raise
    finally:(out/'result.json').write_text(json.dumps(r,indent=2)+'\n')
    return r

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','out'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();r=run(a.source,a.out);print(json.dumps({k:r[k] for k in ['passed','contexts','busy_cases']}))
