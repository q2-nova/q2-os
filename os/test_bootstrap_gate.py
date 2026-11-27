#!/usr/bin/env python3
"""Assert the current emulator cannot substantiate a BootROM/U-Boot entry path."""
import json
import os
import uuid
import subprocess
from build import ROOT,WORK,LOCK,sha
from emulator import QSRC, qemu_artifact
from fork_checkout import verify

verify(QSRC, LOCK['qemu_commit'])
source = QSRC / 'hw/mips/x2000/q2.c'
text=source.read_text()
assert 'env->active_tc.PC = (int32_t)q->entry;' in text
qemu, receipt = qemu_artifact()
command=['podman','run','--rm','--network','none','--security-opt','label=disable','-v',f'{ROOT}:/work',LOCK['kernel_build_container'],
    '/work/'+str(qemu.relative_to(ROOT)),'-M','q2','-nographic','-nic','none']
result=subprocess.run(command,capture_output=True,text=True,timeout=20)
assert result.returncode!=0 and '-kernel xImage is required' in result.stderr,(result.returncode,result.stderr)
report={'test_run':os.environ.get('Q2_OS_ACCEPTANCE_ID'),'gate_test_passed':True,'stock_bootstrap_validated':False,'emulation_only':True,'source_commit':receipt['source_commit'],
    'qemu_sha256':sha(qemu),'q2_source_sha256':sha(source),'without_kernel_exit_code':result.returncode,'diagnostic':result.stderr.strip(),
    'reset_entry':'loaded kernel entry, post-bootloader setup',
    'remaining_requirement':'BootROM/U-Boot execution and a one-shot removable-media entry that leaves the stock default unchanged; separately qualify physical recovery before any hardware trial'}
out = WORK / 'tests' / ('bootstrap-gate-' + uuid.uuid4().hex[:10])
out.mkdir(parents=True)
(out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report,indent=2))
