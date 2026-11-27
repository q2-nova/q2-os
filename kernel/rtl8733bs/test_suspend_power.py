#!/usr/bin/env python3
"""Execute actual SDIO callbacks against owned fault/lifecycle stimuli.

No RF/firmware or electrical power claim. Hardware and full-kernel tests remain.
"""
import argparse,hashlib,json,subprocess
from pathlib import Path
import driver_source
from driver_source import function

PREFIX=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#define _TRUE 1
#define _FALSE 0
#define _SUCCESS 1
#define MMC_PM_KEEP_POWER 1
#define KERNEL_VERSION(a,b,c) (((a)<<16)|((b)<<8)|(c))
#define LINUX_VERSION_CODE KERNEL_VERSION(7,3,0)
#define MI_AP_MODE 1
#define RTW_INFO log_sink
#define RTW_PRINT log_sink
#define RTW_ERR log_sink
static void log_sink(const char *format,...) {(void)format;}
typedef unsigned long systime;
typedef unsigned mmc_pm_flag_t;
struct task {char *comm;int pid;} task={"fixture",1},*current=&task;
struct pwrctrl_priv {int bInSuspend,wowlan_mode,wowlan_ap_mode,wowlan_in_resume;};
struct debug_priv {unsigned dbg_suspend_error_cnt,dbg_resume_cnt,dbg_resume_error_cnt;};
struct mlme_ext_priv {unsigned last_scan_time;};
struct dvobj_priv;
typedef struct adapter {int bup,stopped;struct dvobj_priv *dvobj;struct mlme_ext_priv mlmeextpriv;} _adapter;
struct dvobj_priv {struct pwrctrl_priv p;struct debug_priv drv_dbg;_adapter *adapter;bool sdio_pm_inactive;unsigned irq_alloc;};
struct device {unsigned unused;};
struct sdio_func {struct dvobj_priv *dv;};
static struct dvobj_priv dv;
static _adapter adapter;
static struct sdio_func func;
static struct device dev;
static unsigned caps,requested,sets,suspends,resumes,inits,locks,unlocks;
static int common_error,flag_error,init_error,normal_error,wow,ap,irq_left,ap_mode;
static struct sdio_func *dev_to_sdio_func(struct device *d) {assert(d==&dev);return &func;}
static const char *sdio_func_id(struct sdio_func *f){assert(f==&func);return "fixture";}
static struct dvobj_priv *sdio_get_drvdata(struct sdio_func *f){assert(f==&func);return f->dv;}
static struct pwrctrl_priv *dvobj_to_pwrctl(struct dvobj_priv*d){return &d->p;}
static _adapter *dvobj_get_primary_adapter(struct dvobj_priv*d){return d->adapter;}
static struct pwrctrl_priv *adapter_to_pwrctl(_adapter*a){return &a->dvobj->p;}
static int rtw_is_drv_stopped(_adapter*a){return a->stopped;}
static unsigned sdio_get_host_pm_caps(struct sdio_func*f){assert(f==&func);return caps;}
static int sdio_set_host_pm_flags(struct sdio_func*f,unsigned flags){assert(f==&func && flags==1);sets++;if(flag_error)return flag_error;requested|=flags;return 0;}
static unsigned rtw_get_current_time(void){return 1234;}
static unsigned rtw_get_passing_time_ms(unsigned start){return 1234-start;}
static int sdio_init(struct dvobj_priv*d){assert(d==&dv);inits++;return init_error?0:_SUCCESS;}
static int rtw_mi_check_status(_adapter*a,int mode){assert(a==&adapter && mode==MI_AP_MODE);return ap_mode;}
static int rtw_resume_process_normal(_adapter*a){assert(a==&adapter);resumes++;if(normal_error)return -1;dv.irq_alloc=1;return _SUCCESS;}
#ifdef CONFIG_WOWLAN
static int rtw_resume_process_wow(_adapter*a){assert(a==&adapter);resumes++;return _SUCCESS;}
#endif
#ifdef CONFIG_AP_WOWLAN
static int rtw_resume_process_ap_wow(_adapter*a){assert(a==&adapter);resumes++;return _SUCCESS;}
#endif
static void rtw_resume_lock_suspend(void){locks++;}
static void rtw_resume_unlock_suspend(void){unlocks++;}
static bool rtw_is_earlysuspend_registered(struct pwrctrl_priv*p){(void)p;return false;}
static void rtw_set_do_late_resume(struct pwrctrl_priv*p,bool set){(void)p;(void)set;assert(false);}
static int rtw_resume_common(_adapter*);
static int rtw_suspend_common(_adapter*a){
 assert(a==&adapter);suspends++;dv.p.bInSuspend=1;dv.p.wowlan_mode=wow;dv.p.wowlan_ap_mode=ap;dv.irq_alloc=irq_left;
 return common_error;
}
static void reset(void){
 memset(&dv,0,sizeof(dv));memset(&adapter,0,sizeof(adapter));dv.adapter=&adapter;adapter.dvobj=&dv;adapter.bup=1;func.dv=&dv;
 caps=1;requested=sets=suspends=resumes=inits=locks=unlocks=0;
 common_error=flag_error=init_error=normal_error=wow=ap=irq_left=ap_mode=0;
}
'''
MAIN=r'''
int main(void){
 reset();assert(rtw_sdio_suspend(NULL)==-EINVAL && !sets && !suspends);
 assert(rtw_sdio_resume(NULL)==-EINVAL);
 func.dv=NULL;assert(rtw_sdio_suspend(&dev)==-ENODEV && !sets);assert(rtw_sdio_resume(&dev)==-ENODEV);
 reset();dv.adapter=NULL;assert(rtw_sdio_suspend(&dev)==-ENODEV && !sets);
 assert(rtw_sdio_resume(&dev)==-ENODEV);
 reset();dv.p.bInSuspend=1;assert(rtw_sdio_suspend(&dev)==-EBUSY && !suspends && !sets);
 for(unsigned stopped=0;stopped<2;stopped++){
  reset();adapter.bup=0;adapter.stopped=stopped;dv.irq_alloc=1;
  assert(rtw_sdio_suspend(&dev)==-EBUSY && !dv.sdio_pm_inactive && !sets && !suspends);
  dv.irq_alloc=0;assert(!rtw_sdio_suspend(&dev) && dv.sdio_pm_inactive && !sets && !suspends);
  assert(rtw_sdio_suspend(&dev)==-EBUSY);
  init_error=1;assert(rtw_sdio_resume(&dev)==-EIO && dv.sdio_pm_inactive && !resumes);
  init_error=0;assert(!rtw_sdio_resume(&dev) && !dv.sdio_pm_inactive && !resumes && inits==2);
  assert(!adapter.bup && adapter.stopped==(int)stopped);
 }
 reset();common_error=-EIO;assert(rtw_sdio_suspend(&dev)==-EIO && !sets && resumes==1 && !dv.p.bInSuspend);
 reset();irq_left=1;assert(rtw_sdio_suspend(&dev)==-EBUSY && !sets && resumes==1);
 reset();normal_error=1;assert(!rtw_sdio_suspend(&dev));assert(rtw_sdio_resume(&dev)==-EIO && dv.p.bInSuspend && locks==unlocks);
 unsigned state=0x89abcdef;
 for(unsigned i=0;i<4096;i++){
  reset();state=state*1664525U+1013904223U;ap_mode=(state>>31)&1;caps=(state>>30)&1;
  assert(!rtw_sdio_suspend(&dev) && !requested && !sets && suspends==1 && !dv.irq_alloc);
  assert(!rtw_sdio_resume(&dev) && !dv.p.bInSuspend && resumes==1 && locks==unlocks);
  assert(adapter.bup && !adapter.stopped);
 }
#ifdef CONFIG_WOWLAN
 for(unsigned which=0;which<2;which++)for(unsigned cap=0;cap<2;cap++)for(unsigned err=0;err<2;err++){
  reset();wow=!which;ap=which;ap_mode=which;caps=cap;flag_error=err?-EIO:0;
  int ret=rtw_sdio_suspend(&dev);
#ifdef CONFIG_RTW_SDIO_PM_KEEP_POWER
  if(cap && !err){assert(ret==0 && requested==1 && sets==1);assert(!rtw_sdio_resume(&dev));}
  else {assert(ret==(cap?-EIO:-ENOSYS) && !requested && resumes==1 && !dv.p.bInSuspend);}
#else
  assert(ret==-EOPNOTSUPP && !sets && !requested && resumes==1 && !dv.p.bInSuspend);
#endif
 }
#endif
 puts("ACTUAL_SDIO_PM_PASS 4096 repeated cold cycles plus inactive, IRQ ownership, error rollback and wake modes");
}
'''

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(source,out):
 out.mkdir(parents=True,exist_ok=False);sdio=source/'os_dep/linux/sdio_intf.c';osint=source/'os_dep/linux/os_intfs.c'
 impl='\n'.join([function(sdio.read_text(),n) for n in ('rtw_sdio_suspend','rtw_resume_process','rtw_sdio_resume')]+[function(osint.read_text(),'rtw_resume_common')])
 # Definition order is explicit: suspend uses the forward common declaration;
 # resume_process uses common; callbacks then execute its actual implementation.
 variants=[('actual',impl,True),
  ('retain-normal',impl.replace('if (!pwrpriv->wowlan_mode && !pwrpriv->wowlan_ap_mode)', 'if (false)'),False),
  ('ignore-irq-owner',impl.replace('if (!psdpriv->irq_alloc)','if (true)'),False),
  ('reopen-inactive',impl.replace('if (psdpriv->sdio_pm_inactive)', 'if (false)'),False),
  ('ignore-flag-error',impl.replace('ret = sdio_set_host_pm_flags(func, MMC_PM_KEEP_POWER);','sdio_set_host_pm_flags(func, MMC_PM_KEEP_POWER); ret = 0;'),False),
  ('hide-normal-resume-error',impl.replace('ret = -EIO;', 'ret = 0;'),False)]
 r=dict(passed=False,hardware=False,inputs={str(p.relative_to(source)):sha(p) for p in (sdio,osint,source/'include/drv_types.h')},tool_sha256=sha(Path(__file__)),cases=[],scope='actual callback contracts with owned host API fault injection; no real RF/firmware, card power or reconnect qualification')
 try:
  for name,body,want in variants:
   if name!='actual' and body==impl:raise ValueError('inactive negative '+name)
   configs=[('keep',['-DCONFIG_RTW_SDIO_PM_KEEP_POWER']),('wow',['-DCONFIG_RTW_SDIO_PM_KEEP_POWER','-DCONFIG_WOWLAN','-DCONFIG_AP_WOWLAN']),('no-keep',['-DCONFIG_WOWLAN','-DCONFIG_AP_WOWLAN'])] if want else [('wow',['-DCONFIG_RTW_SDIO_PM_KEEP_POWER','-DCONFIG_WOWLAN','-DCONFIG_AP_WOWLAN'])]
   for config,flags in configs:
    label=name+'-'+config;path=out/(label+'.c');path.write_text(PREFIX+body+MAIN);binary=out/label
    built=subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function',*flags,str(path),'-o',str(binary)],capture_output=True,timeout=30)
    log=out/(label+'.build.log');log.write_bytes(built.stdout+built.stderr)
    if built.returncode:raise ValueError('compile '+label)
    done=subprocess.run([str(binary)],capture_output=True,timeout=30);log=out/(label+'.log');log.write_bytes(done.stdout+done.stderr)
    r['cases'].append(dict(name=label,expected_success=want,exit_status=done.returncode,source_sha256=sha(path),binary_sha256=sha(binary),log_sha256=sha(log)))
    if (done.returncode==0)!=want:raise ValueError('unexpected callback result '+label)
  if any(sha(source/n)!=d for n,d in r['inputs'].items()):raise ValueError('source changed during tests')
  r['passed']=True
 finally:(out/'result.json').write_text(json.dumps(r,indent=2)+'\n')
 return r
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--source',type=Path,help='cold-suspend driver tree (default: prepared cold variant)')
 p.add_argument('--out',type=Path,help='fresh output directory (default: under build/rtl8733bs/tests/)')
 a=p.parse_args();r=run(a.source or driver_source.tree('cold'),a.out or driver_source.output('suspend-power'));print(json.dumps(dict(passed=r['passed'],cases=len(r['cases']))))
