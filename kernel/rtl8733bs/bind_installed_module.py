#!/usr/bin/env python3
"""Compare real MIPS module bytes; freeze public source separately from provenance claims."""
import argparse,hashlib,importlib.util,json,shutil,struct,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PARSER=ROOT/'kernel/ram-handoff/abi.py'
spec=importlib.util.spec_from_file_location('q2_module_binding_elf',PARSER)
elf=importlib.util.module_from_spec(spec);spec.loader.exec_module(elf)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def describe(path):
    raw=path.read_bytes();obj=elf.Elf(raw);sections={}
    for name,section in obj.named.items():
        if section[2]&2:
            sections[name]=dict(bytes=section[5],sha256=None if section[1]==8 else hashlib.sha256(obj.blob(section)).hexdigest())
    symbols=obj.named['.symtab'];strings=obj.blob(obj.sections[symbols[6]]);functions=[]
    section_names={tuple(section):name for name,section in obj.named.items()}
    if symbols[9]!=16 or symbols[5]%16:raise ValueError('invalid symbol table')
    for pos in range(0,symbols[5],16):
        name,value,size,info,other,index=struct.unpack_from('<IIIBBH',obj.blob(symbols),pos)
        if size and info&15==2 and index<len(obj.sections):
            section=obj.sections[index];data=obj.blob(section);start=value-section[3]
            if not 0<=start<=len(data)-size:raise ValueError('function out of bounds')
            symbol=obj.string(strings,name)
            functions.append((symbol,section_names[tuple(section)],start,size,info,
                              hashlib.sha256(data[start:start+size]).hexdigest()))
    if not functions:raise ValueError('nonempty actual code inventory required')
    return dict(module_sha256=hashlib.sha256(raw).hexdigest(),allocated_sections=sections,functions=sorted(functions))

def compare(installed,reference):
    left,right=describe(installed),describe(reference)
    return dict(passed=left['allocated_sections']==right['allocated_sections'] and left['functions']==right['functions'],
        hardware=False,code_matches=left['functions']==right['functions'],
        allocated_bytes_match=left['allocated_sections']==right['allocated_sections'],
        installed=left,reference=right,
        scope='actual ELF allocated bytes/function inventory; current source capture is not fresh-build provenance or kernel ABI qualification')

def run(installed,reference,out,source=None):
    canonical=Path(subprocess.check_output(['git','-C',str(ROOT),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip()).parent
    out=out.absolute()
    if out!=out.resolve() or not out.is_relative_to(canonical/'build'):raise ValueError('fresh canonical build output required')
    out.mkdir(parents=True,exist_ok=False)
    result=compare(installed,reference)
    frozen=out/'frozen';frozen.mkdir()
    for path,name in ((Path(__file__),'tool.py'),(PARSER,'abi.py')):
        shutil.copyfile(path,frozen/name)
    result['tool_sha256']=sha(frozen/'tool.py');result['parser_sha256']=sha(frozen/'abi.py')
    if source is not None:
        source=source.absolute()
        top=Path(subprocess.check_output(['git','-C',str(source),'rev-parse','--show-toplevel'],text=True).strip())
        if source.absolute()!=source.resolve() or source.resolve()!=top:raise ValueError('explicit repository root required')
        names=subprocess.check_output(['git','-C',str(source),'ls-files','-z']).decode().split('\0')
        inputs={}
        for name in (n for n in names if n):
            path=source/name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):raise ValueError('regular in-tree source required')
            target=frozen/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target);inputs[name]=dict(sha256=sha(target),mode=target.stat().st_mode&0o777)
        result['current_source']=dict(path=str(source),
            commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip(),inputs=inputs)
        (frozen/'working.patch').write_bytes(subprocess.check_output(['git','-C',str(source),'diff','--binary','HEAD','--']))
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('installed','reference','out'):cli.add_argument('--'+name,type=Path,required=True)
    cli.add_argument('--source',type=Path)
    args=cli.parse_args();r=run(args.installed,args.reference,args.out,args.source)
    print(json.dumps(dict(passed=r['passed'],functions=len(r['installed']['functions']),code_matches=r['code_matches'],allocated_bytes_match=r['allocated_bytes_match'])))
    raise SystemExit(0 if r['passed'] else 1)
