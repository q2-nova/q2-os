#!/usr/bin/env python3
"""Execute the actual installed/preserved IPS admission prefix under bounded faults."""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'kernel/watchdog'))
from test_driver import function
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prefix(path):
    text=function(path.read_text(),'rtw_suspend_common')
    begin=text.index('pdbgpriv->dbg_suspend_cnt++;')
    end=text.index('#ifdef CONFIG_IOL_READ_EFUSE_MAP',begin)
    body=text[begin:end]
    if 'READ_ONCE(pwrpriv->bips_processing)' not in body:raise ValueError('actual published worker state required')
    return body

PRE=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#define _TRUE 1
#define _FALSE 0
#define READ_ONCE(x) (*(volatile __typeof__(x) *)&(x))
struct power {unsigned bInSuspend,bips_processing;} power;
struct debug {unsigned dbg_suspend_cnt,dbg_suspend_error_cnt;} debug;
static uint32_t tick,origin,release_after,calls;
static uint32_t rtw_get_passing_time_ms(uint32_t start){return tick-start;}
static void rtw_msleep_os(unsigned ms){
 assert(ms==1);tick++;calls++;assert(calls<2048);
 if(tick-origin>=release_after)power.bips_processing=0;
}
static int admission(void){
 struct power *pwrpriv=&power;struct debug *pdbgpriv=&debug;uint32_t start_time=tick;
'''
POST=r'''
 return 77;
}
int main(void){
 for(unsigned i=0;i<4096;i++){
  release_after=i%1101;tick=UINT32_MAX-(i%300);origin=tick;calls=0;
  power=(struct power){.bips_processing=release_after!=0};debug=(struct debug){0};
  int ret=admission();
  assert(debug.dbg_suspend_cnt==1);
  if(release_after<=1000){
   assert(ret==77 && calls==release_after && power.bInSuspend && !debug.dbg_suspend_error_cnt);
  }else{
   assert(ret==-EBUSY && calls==1000 && !power.bInSuspend && debug.dbg_suspend_error_cnt==1);
  }
 }
 release_after=UINT32_MAX;tick=origin=0;calls=0;
 power=(struct power){.bips_processing=1};debug=(struct debug){0};
 assert(admission()==-EBUSY && calls==1000 && !power.bInSuspend);
 puts("ACTUAL_IPS_ADMISSION_PASS 4096 release/deadline/wrap cases and stuck worker");
}
'''
def run(source,control,out):
    out.mkdir(parents=True,exist_ok=False)
    actual=source/'os_dep/linux/os_intfs.c';reference=control/'os_dep/linux/os_intfs.c'
    body=prefix(actual)
    if prefix(reference)!=body:raise ValueError('control and cold must preserve identical installed IPS admission')
    variants=[('actual',body,True),
        ('unbounded',body.replace('if (rtw_get_passing_time_ms(start_time) >= 1000)','if (rtw_get_passing_time_ms(start_time) >= 1000 && false)'),False),
        ('stranded-suspend',body.replace('pwrpriv->bInSuspend = _FALSE;','pwrpriv->bInSuspend = _TRUE;'),False)]
    result=dict(passed=False,hardware=False,tool_sha256=sha(Path(__file__)),
        inputs={str(p):sha(p) for p in (actual,reference)},
        prefix_sha256=hashlib.sha256(body.encode()).hexdigest(),cases=[],
        scope='actual IPS prefix under deterministic worker release/time/wrap faults; no firmware or physical wait validation')
    try:
        for name,part,want in variants:
            if name!='actual' and part==body:raise ValueError('inactive negative')
            path=out/(name+'.c');path.write_text(PRE+part+POST);binary=out/name
            built=subprocess.run(['cc','-std=gnu11','-O2','-Wall','-Wextra','-Werror',str(path),'-o',str(binary)],capture_output=True,timeout=30)
            (out/(name+'.build.log')).write_bytes(built.stdout+built.stderr)
            if built.returncode:raise ValueError('compile '+name)
            done=subprocess.run([str(binary)],capture_output=True,timeout=5)
            log=out/(name+'.log');log.write_bytes(done.stdout+done.stderr)
            row=dict(name=name,expected_success=want,exit_status=done.returncode,source_sha256=sha(path),binary_sha256=sha(binary),log_sha256=sha(log))
            result['cases'].append(row)
            if (done.returncode==0)!=want:raise ValueError('unexpected IPS result '+name)
        if any(sha(Path(n))!=d for n,d in result['inputs'].items()):raise ValueError('input changed')
        result['passed']=True
    finally:(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','control','out'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();r=run(args.source,args.control,args.out)
    print(json.dumps(dict(passed=r['passed'],cases=len(r['cases']))))
