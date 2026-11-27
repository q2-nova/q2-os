#!/usr/bin/env python3
"""Append one passive formatter to the exact preserved cached-PS source."""
import argparse
from pathlib import Path
import cached_datapath as codec
import prepare_cached_ps as prep
BASELINE=prep.ROOT/'build/rf-ps-cache-module-02'
BASELINE_SHA='a442a0925e560c2a13065067191eeb5394740972637573d94072ff10fa6f7af6'
PATCH=Path(__file__).parent/'patches/0008-Expose-cached-data-path-counters.patch'
HOOK='\trtw_dump_cached_datapath(m, padapter);\n'

def projection(before,after):
    if after.count(codec.FORMATTER+'\n')!=1 or after.count(HOOK)!=1 or after.replace(codec.FORMATTER+'\n','').replace(HOOK,'')!=before:raise ValueError('only exact passive formatter and one hook may change')

def prepare(out):return prep.prepare(out,baseline=BASELINE,expected=BASELINE_SHA,patch=PATCH,projection=projection)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=prepare(a.out);print(r['source_commit'])
