#!/usr/bin/env python3
"""Actual USB-auto step: idle disconnect must not acquire ownership lock."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=ROOT/'os/platform/usb-auto');a=p.parse_args()
    base=ROOT/'build/usb-auto-idle';base.mkdir(parents=True,exist_ok=True)
    out=Path(tempfile.mkdtemp(prefix='case-',dir=base));records=[]
    try:
        for latch in (False,True):
            case=out/str(int(latch));run=case/'run';run.mkdir(parents=True)
            online=case/'sys/class/power_supply/axp20x-usb/online';online.parent.mkdir(parents=True);online.write_text('0\n')
            (case/'proc').mkdir();(case/'proc/cmdline').write_text('generated\n')
            if latch:(run/'usb-auto.used').touch()
            script=f'''Q2_USB_AUTO_SOURCE_ONLY=1
. '{a.source.resolve()}'
RUN='{run}'
SYS='{case}/sys'
PROC='{case}/proc'
storage() {{
 mkdir "$RUN/usb-storage.lock" || exit 21
 printf '%s\\n' "$1" >> '{case}/operations'
 rmdir "$RUN/usb-storage.lock"
 [ "$1" = disconnected ] || exit 22
}}
STORAGE=storage
step
step
step
'''
            result=subprocess.run(['sh','-c',script],capture_output=True,text=True,timeout=5)
            calls=(case/'operations').read_text().splitlines() if (case/'operations').exists() else []
            expected=['disconnected'] if latch else []
            records.append(dict(latch=latch,operations=calls,expected=expected,exit_code=result.returncode,passed=result.returncode==0 and calls==expected))
        receipt=dict(hardware=False,source=str(a.source),source_sha256=hashlib.sha256(a.source.read_bytes()).hexdigest(),actual_step=True,passed=all(r['passed'] for r in records),controls=records)
        (out/'result.json').write_text(json.dumps(receipt,indent=2)+'\n');print(out/'result.json')
        raise SystemExit(not receipt['passed'])
    finally:
        pass  # Keep generated controls and the negative receipt under build/.


if __name__=='__main__':main()
