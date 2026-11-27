#!/usr/bin/env python3
"""Preserve data01 exactly except proper cold association teardown."""
import argparse
from pathlib import Path
import prepare_cached_ps as prep
import cold_association as change
BASELINE=prep.ROOT/'build/rf-datapath-module-01'
BASELINE_SHA='0c8fd5686889e478f54704aaa932e6eb7897ace2363812fa7dfe4d52a58628b5'
CHANGED='os_dep/linux/os_intfs.c'
PATCH=Path(__file__).parent/'patches/0009-Report-disconnect-before-cold-firmware-teardown.patch'

def prepare(out):
    return prep.prepare(out,baseline=BASELINE,expected=BASELINE_SHA,patch=PATCH,projection=change.projection,changed_file=CHANGED,commit_message='Report disconnect before cold firmware teardown; preserve WoW roaming')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=prepare(a.out);print(r['source_commit'])
