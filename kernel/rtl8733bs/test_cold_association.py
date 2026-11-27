#!/usr/bin/env python3
"""Execute actual cold teardown C; compare retained/WoW to exact predecessor.

Subset host ABI and controlled external callbacks are not RF or MIPS ABI proof.
The complete matching-module build and final hardware gates remain separate.
"""
import argparse,hashlib,json,resource,shutil,subprocess
from pathlib import Path
import prepare_cold_association as prep
import cold_association as change
ROOT=prep.prep.ROOT
CONTEXTS=16384
CONFIGS=(('station',[]),('ap',['-DCONFIG_AP_MODE']),('p2p',['-DCONFIG_P2P','-DRTW_P2P_GROUP_INTERFACE=0']),('p2p-group',['-DCONFIG_P2P','-DRTW_P2P_GROUP_INTERFACE=1','-DCONFIG_AP_MODE']))
PREFIX=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define _TRUE 1
#define _FALSE 0
#define _SUCCESS 1
#define RTW_INFO(...) ((void)0)
#define RTW_PRINT(...) ((void)0)
#define RTW_ROAM_ON_RESUME 2
#define WIFI_STATION_STATE 1
#define WIFI_ASOC_STATE 2
#define WIFI_UNDER_SURVEY 4
#define WIFI_UNDER_LINKING 8
#define RTW_CMDF_DIRECTLY 1
#define DISCONNECTION_BY_SYSTEM_DUE_TO_SYSTEM_IN_SUSPEND 99
#define WLAN_REASON_DEAUTH_LEAVING 3
#define P2P_STATE_NONE 0
#define P2P_ROLE_DEVICE 1
struct pwrctrl_priv {int wowlan_mode,wowlan_ap_mode;};
struct mlme_priv {unsigned state,to_roam,flags;};
struct mlme_ext_info {unsigned disconnect_occurred_time,disconnect_code,wifi_reason_code;};
struct mlme_ext_priv {struct mlme_ext_info mlmext_info;};
struct wifidirect_info {unsigned state,role;};
typedef struct adapter {struct mlme_priv mlmepriv;struct pwrctrl_priv pwr;struct mlme_ext_priv mlmeextpriv;struct wifidirect_info wdinfo;} _adapter;
static _adapter adapter;
static unsigned events[64],count,effect;
static int cold(void){return !adapter.pwr.wowlan_mode && !adapter.pwr.wowlan_ap_mode;}
static void event(unsigned kind,unsigned value){assert(count<64);events[count++]=(kind<<16)|value;}
static void owned(_adapter *a){assert(a==&adapter);}
static struct pwrctrl_priv *adapter_to_pwrctl(_adapter*a){owned(a);return &a->pwr;}
static int check_fwstate(struct mlme_priv*m,unsigned mask){assert(m==&adapter.mlmepriv);return !!(m->state&mask);}
static void clr_fwstate(struct mlme_priv*m,unsigned mask){assert(m==&adapter.mlmepriv);event(8,mask);m->state&=~mask;}
static int rtw_chk_roam_flags(_adapter*a,unsigned mask){owned(a);return !!(a->mlmepriv.flags&mask);}
static void rtw_set_to_roam(_adapter*a,unsigned value){owned(a);event(1,value);a->mlmepriv.to_roam=value;}
static int rtw_p2p_chk_state(struct wifidirect_info*p,unsigned v){assert(p==&adapter.wdinfo);return p->state==v;}
static int rtw_p2p_chk_role(struct wifidirect_info*p,unsigned v){assert(p==&adapter.wdinfo);return p->role==v;}
static int MLME_IS_AP(_adapter*a){owned(a);return !!(a->mlmepriv.state&16);}
static int MLME_IS_MESH(_adapter*a){owned(a);return !!(a->mlmepriv.state&32);}
static void policy_at_callback(_adapter*a){owned(a);if(cold())assert(a->mlmepriv.to_roam==0);}
static void rtw_disassoc_cmd(_adapter*a,unsigned delay,unsigned flag){policy_at_callback(a);assert(!delay && flag==RTW_CMDF_DIRECTLY);event(2,a->mlmepriv.to_roam);}
static void rtw_indicate_disconnect(_adapter*a,unsigned reason,unsigned local){policy_at_callback(a);assert((reason==0 && !local)||(reason==3 && local));event(3,a->mlmepriv.to_roam);if(effect)a->mlmepriv.state&=~(WIFI_ASOC_STATE|WIFI_UNDER_LINKING);}
static void rtw_sta_flush(_adapter*a,unsigned local){policy_at_callback(a);assert(local==1);event(4,a->mlmepriv.to_roam);}
static void rtw_free_assoc_resources(_adapter*a,unsigned locked){policy_at_callback(a);assert(locked==1);event(5,a->mlmepriv.to_roam);}
static void rtw_free_network_queue(_adapter*a,unsigned locked){policy_at_callback(a);assert(locked==1);event(6,a->mlmepriv.to_roam);}
static void rtw_indicate_scan_done(_adapter*a,unsigned aborted){policy_at_callback(a);assert(aborted==1);event(7,a->mlmepriv.to_roam);}
static unsigned rtw_get_current_time(void){return 1234;}
static unsigned rtw_systime_to_ms(unsigned t){return t;}
static void reset(unsigned wow,unsigned previous,unsigned state,unsigned roam,unsigned p2p,unsigned behavior){
 memset(&adapter,0,sizeof(adapter));memset(events,0,sizeof(events));count=0;effect=behavior;
 adapter.pwr.wowlan_mode=wow&1;adapter.pwr.wowlan_ap_mode=(wow>>1)&1;
 adapter.mlmepriv.to_roam=previous;adapter.mlmepriv.state=state;adapter.mlmepriv.flags=roam?2:0;
 adapter.wdinfo.state=p2p&1;adapter.wdinfo.role=(p2p>>1)&1;
}
'''
MAIN=r'''
int main(void){
 static const unsigned previous[]={0,1,2,17};unsigned contexts=0,cold_cases=0,warm_cases=0;
 for(unsigned wow=0;wow<4;wow++)for(unsigned prior=0;prior<4;prior++)for(unsigned state=0;state<64;state++)for(unsigned roam=0;roam<2;roam++)for(unsigned p2p=0;p2p<4;p2p++)for(unsigned behavior=0;behavior<2;behavior++){
  reset(wow,previous[prior],state,roam,p2p,behavior);
  assert(rtw_suspend_free_assoc_resource(&adapter)==_SUCCESS);contexts++;
  if(!wow){assert(adapter.mlmepriv.to_roam==0 && count && events[0]==(1U<<16));cold_cases++;}
  else {
   _adapter actual=adapter;unsigned actual_events[64],actual_count=count;memcpy(actual_events,events,sizeof(events));
   reset(wow,previous[prior],state,roam,p2p,behavior);assert(reference_teardown(&adapter)==_SUCCESS);
   assert(!memcmp(&actual,&adapter,sizeof(adapter)) && actual_count==count && !memcmp(actual_events,events,sizeof(events)));warm_cases++;
  }
 }
 assert(contexts==16384 && cold_cases==4096 && warm_cases==12288);
 printf("COLD_ASSOCIATION_PASS contexts=%u cold=%u warm=%u\n",contexts,cold_cases,warm_cases);
 return 0;
}
'''

def harness(body,reference):
    before='int rtw_suspend_free_assoc_resource('
    if reference.count(before)!=1:raise ValueError('one actual predecessor teardown required')
    return PREFIX+reference.replace(before,'static int reference_teardown(',1)+'\n'+body+'\n'+MAIN

def no_core():resource.setrlimit(resource.RLIMIT_CORE,(0,0))

def compile_case(body,reference,out,name,flags):
    source=out/(name+'.c');binary=out/name;log=out/(name+'.log')
    if any(p.exists() for p in (source,binary,log)):raise ValueError('fresh native case required')
    source.write_text(harness(body,reference));compiler=Path(shutil.which('cc')).resolve()
    command=[str(compiler),'-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-unused-variable',*flags,str(source),'-o',str(binary)]
    compiled=subprocess.run(command,capture_output=True,timeout=30)
    output=compiled.stdout+compiled.stderr
    row=dict(name=name,flags=flags,command=command,compiler=str(compiler),compiler_sha256=prep.prep.sha(compiler),compiler_version=subprocess.check_output([str(compiler),'--version'],timeout=5,text=True).splitlines()[0],compile_exit_status=compiled.returncode,harness_sha256=prep.prep.sha(source),run_exit_status=None,passed=False)
    if compiled.returncode==0:
        reply=subprocess.run([str(binary)],capture_output=True,timeout=10,preexec_fn=no_core);output+=reply.stdout+reply.stderr
        row.update(run_exit_status=reply.returncode,binary_sha256=prep.prep.sha(binary),passed=reply.returncode==0 and reply.stdout==b'COLD_ASSOCIATION_PASS contexts=16384 cold=4096 warm=12288\n')
    log.write_bytes(output);row['log_sha256']=prep.prep.sha(log);return row

def run(source,out):
    out=out.absolute()
    if out!=out.resolve() or not out.is_relative_to(ROOT/'build'):raise ValueError('fresh worktree-local native output required')
    before=(prep.BASELINE/'frozen'/prep.CHANGED).read_text();after=(source/prep.CHANGED).read_text();change.projection(before,after)
    if prep.prep.sha(prep.BASELINE/'result.json')!=prep.BASELINE_SHA:raise ValueError('exact predecessor receipt required')
    out.mkdir(parents=True,exist_ok=False);body=prep.prep.function(after,'rtw_suspend_free_assoc_resource');reference=prep.prep.function(before,'rtw_suspend_free_assoc_resource')
    r=dict(passed=False,hardware=False,source_sha256=hashlib.sha256(after.encode()).hexdigest(),baseline_source_sha256=hashlib.sha256(before.encode()).hexdigest(),baseline_receipt_sha256=prep.BASELINE_SHA,tool_sha256=prep.prep.sha(Path(__file__)),projection_sha256=prep.prep.sha(Path(change.__file__)),cases=[],scope='actual teardown C, controlled external callbacks and subset ABI; cold clears before callbacks, WoW behavior equals exact predecessor; full MIPS ABI/RF/hardware remain separate')
    try:
        for name,flags in CONFIGS:r['cases'].append(compile_case(body,reference,out,name,flags))
        r.update(passed=all(x['passed'] for x in r['cases']),contexts=CONTEXTS*len(CONFIGS),cold_contexts=4096*len(CONFIGS),retained_contexts=12288*len(CONFIGS))
    except BaseException as e:r['failure_type']=type(e).__name__;raise
    finally:(out/'result.json').write_text(json.dumps(r,indent=2)+'\n')
    return r

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=run(a.source,a.out);print(json.dumps({k:r[k] for k in ('passed','contexts','cold_contexts','retained_contexts')}));raise SystemExit(0 if r['passed'] else 1)
