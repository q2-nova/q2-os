#!/usr/bin/env python3
"""Create an isolated, exact-baseline cached-PS driver variant; no device access."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BASELINE=ROOT/'build/sd-pad-radio-cold-01'
BASELINE_SHA='3c9677209cc800086c223b3c7b11ffc43061b47567a5c930e4c1301db3904259'
PATCH=Path(__file__).parent/'patches/0007-Expose-nonblocking-cached-power-management-state.patch'
CHANGED='core/rtw_debug.c'
sys.path.insert(0,str(ROOT/'kernel/watchdog'))
from test_driver import function
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def only_snapshot_changed(before,after):
    helper=function(after,'rtw_dump_cached_ps_state');start=after.index('/* Cached software state only.')
    end=after.index(helper,start)+len(helper)
    if after[end:end+2]!='\n\n':raise ValueError('closed new formatter block required')
    projected=after[:start]+after[end+2:];hook='\trtw_dump_cached_ps_state(m, padapter);\n'
    if projected.count(hook)!=1 or projected.replace(hook,'')!=before:raise ValueError('only new formatter and one proc hook may differ')

def admit(baseline,expected):
    if sha(baseline/'result.json')!=expected:raise ValueError('exact completed baseline receipt required')
    r=json.loads((baseline/'result.json').read_text())
    if not r.get('passed') or not r.get('build',{}).get('cleanup') or not all(x['absent'] for x in r['build']['cleanup']):raise ValueError('retired baseline module required')
    if set(r['inputs'])!=set(r['input_modes']) or CHANGED not in r['inputs']:raise ValueError('complete baseline source/mode inventory required')
    for name,digest in r['inputs'].items():
        rel=Path(name);p=baseline/'frozen'/rel
        if rel.is_absolute() or '..' in rel.parts or not p.is_file() or p.is_symlink() or p.resolve()!=p or sha(p)!=digest or bool(p.stat().st_mode&0o111)!=r['input_modes'][name]:raise ValueError('exact regular baseline input bytes/modes required')
    return r

def prepare(out,*,baseline=BASELINE,expected=BASELINE_SHA,patch=PATCH,projection=only_snapshot_changed,changed_file=CHANGED,commit_message='Preserved cold lifecycle with cached power-state diagnostics'):
    out=out.absolute()
    if out!=out.resolve() or not out.is_relative_to(ROOT/'build'):raise ValueError('fresh worktree-local build output required')
    r=admit(baseline,expected)
    if changed_file not in r['inputs']:raise ValueError('declared source must belong to the complete baseline inventory')
    patch_sha=sha(patch);repo=Path(r['module_source_root'])
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()!=r['source_commit']:raise ValueError('baseline repository revision changed')
    out.mkdir(parents=True,exist_ok=False);src=out/'source'
    shutil.copyfile(patch,out/'applied.patch')
    result=dict(passed=False,hardware=False,baseline_receipt_sha256=expected,patch_sha256=patch_sha,source=str(src),operations=[])
    def git(*args):
        index=len(result['operations']);env={**os.environ,'GIT_AUTHOR_NAME':'Q2 source preparation','GIT_AUTHOR_EMAIL':'noreply@localhost','GIT_COMMITTER_NAME':'Q2 source preparation','GIT_COMMITTER_EMAIL':'noreply@localhost','GIT_AUTHOR_DATE':'@0 +0000','GIT_COMMITTER_DATE':'@0 +0000'}
        reply=subprocess.run(['git',*args],capture_output=True,timeout=30,env=env)
        for suffix,data in [('stdout',reply.stdout),('stderr',reply.stderr)]:(out/f'git-{index}.{suffix}').write_bytes(data)
        result['operations'].append(dict(arguments=list(args),exit_status=reply.returncode))
        reply.check_returncode();return reply.stdout.decode().strip()
    try:
        git('-C',str(repo),'worktree','add','--detach',str(src),r['source_commit'])
        tracked=set(git('-C',str(src),'ls-files','-z').split('\0'))-{''}
        if tracked!=set(r['inputs']):raise ValueError('same tracked source set required')
        for name in r['inputs']:shutil.copy2(baseline/'frozen'/name,src/name)
        if any(sha(src/n)!=d for n,d in r['inputs'].items()):raise ValueError('source seed differs from frozen baseline')
        applied=out/'applied.patch'
        if sha(applied)!=patch_sha:raise ValueError('copied patch changed')
        git('-C',str(src),'apply','--check',str(applied));git('-C',str(src),'apply',str(applied))
        inputs={n:sha(src/n) for n in r['inputs']}
        changed=[n for n,d in inputs.items() if d!=r['inputs'][n]]
        if changed!=[changed_file] or any(bool((src/n).stat().st_mode&0o111)!=m for n,m in r['input_modes'].items()):raise ValueError('only declared driver source may change; all modes preserved')
        projection((baseline/'frozen'/changed_file).read_text(),(src/changed_file).read_text())
        git('-C',str(src),'add','-u');git('-C',str(src),'-c','commit.gpgsign=false','commit','-m',commit_message)
        commit=git('-C',str(src),'rev-parse','HEAD')
        if sha(patch)!=patch_sha or sha(baseline/'result.json')!=expected or any(sha(src/n)!=d for n,d in inputs.items()):raise ValueError('preparation inputs changed')
        result.update(passed=True,source_commit=commit,inputs=inputs,changed=changed,patch_file='applied.patch',scope='isolated diagnostic source only; exact-kernel module build/native/hardware gates remain')
    except BaseException as e:result['failure_type']=type(e).__name__;raise
    finally:(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);a=parser.parse_args();r=prepare(a.out)
    print(json.dumps({k:r[k] for k in ['passed','hardware','source','source_commit','changed']}))
