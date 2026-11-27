#!/usr/bin/env python3
"""Pin and reproduce the Q2 public stock kernel's UART baud settings.

Only a public pristine firmware xImage is accepted. No device or runtime files
are opened. Recovered symbols/disassembly are research evidence, not evidence
of what registers a running Q2 currently contains.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib

IMAGE_SHA256='39d54b09109db2b8fe9a975e3b24a0d105ef0b51a6708601af64aaa5186e11bc'
RAW_SHA256='5d776191e067262abcdeed0e81df8d5741cf9dbb3ab2554b62e830afec8be3d5'
BASE=0x80010000
TABLE=0x80b312e4
GZIP_OFFSET=16992

def table(raw,offset,count=30):
 if offset<0 or count<1 or offset+count*12>len(raw):raise ValueError('baud table out of bounds')
 rows=[];previous=0
 for n in range(count):
  baud,packed,uacr=struct.unpack_from('<III',raw,offset+n*12)
  divisor=packed&65535;umr=packed>>16
  if baud<=previous or not divisor or not 4<=umr<=32 or uacr>4095:raise ValueError('invalid stock baud entry')
  rows.append({'baud':baud,'divisor':divisor,'umr':umr,'uacr':uacr});previous=baud
 return rows

def inspect(image):
 if hashlib.sha256(image).hexdigest()!=IMAGE_SHA256:raise ValueError('not pinned pristine public Q2 stock xImage')
 if image[:4]!=bytes.fromhex('27051956'):raise ValueError('invalid uImage magic')
 header=bytearray(image[:64]);expected=struct.unpack_from('>I',header,4)[0];header[4:8]=b'\0'*4
 if zlib.crc32(header)!=expected:raise ValueError('uImage header CRC mismatch')
 size=struct.unpack_from('>I',image,12)[0];payload=image[64:64+size]
 if len(payload)!=size or zlib.crc32(payload)!=struct.unpack_from('>I',image,24)[0]:raise ValueError('uImage data CRC mismatch')
 raw=zlib.decompress(image[GZIP_OFFSET:],wbits=31)
 if hashlib.sha256(raw).hexdigest()!=RAW_SHA256:raise ValueError('unexpected decompressed public kernel')
 rows=table(raw,TABLE-BASE)
 # Exact opcodes in serial_ingenic_set_termios: write UMR and UACR with
 # 32-bit MMIO at offsets0x24/0x28. Address mapping independently recovered
 # from the full kallsyms table and verified against the surrounding code.
 for address,word in [(0x80829154,0xac430024),(0x80829164,0xac430028)]:
  if struct.unpack_from('<I',raw,address-BASE)[0]!=word:raise ValueError('stock termios write sequence differs')
 high=next(r for r in rows if r['baud']==3000000)
 if high!={'baud':3000000,'divisor':1,'umr':8,'uacr':0}:raise ValueError('stock 3Mbaud settings differ')
 return {'public_stock_only':True,'hardware_qualified':False,'ximage_sha256':IMAGE_SHA256,'raw_kernel_sha256':RAW_SHA256,'kernel_base_address':hex(BASE),'baudtoregs_address':hex(TABLE),'rows':rows,'radio_requested_baud':3000000,'clock_hz_for_exact_3M':24000000,'register_writes':{'UMR':'0x24','UACR':'0x28'}}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('public_stock_ximage',type=Path);a=p.parse_args()
 print(json.dumps(inspect(a.public_stock_ximage.read_bytes()),indent=2))

if __name__=='__main__':main()
