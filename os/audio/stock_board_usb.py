#!/usr/bin/env python3
"""Reproduce Q2 board USB GPIO calls from the pristine public stock player.

Read-only public ELF analysis. Reported pins are code evidence, not permission
to drive an unknown VBUS source or an electrical isolation qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

SHA256='2c5f06142850b4fc168f82b44a81550cce0a5b4b9fe1c179dced4a08a3049138'
GP=0xa26cc0

class Elf:
 def __init__(self,data):
  self.data=data
  if len(data)<52 or data[:7]!=b'\x7fELF\x01\x01\x01':raise ValueError('expected complete ELF32 little endian')
  if struct.unpack_from('<H',data,18)[0]!=8:raise ValueError('expected MIPS ELF')
  start=struct.unpack_from('<I',data,28)[0];size,count=struct.unpack_from('<HH',data,42)
  if size!=32 or not count or start+size*count>len(data):raise ValueError('invalid ELF program headers')
  self.segments=[]
  for n in range(count):
   kind,offset,address,_,length,*_=struct.unpack_from('<8I',data,start+n*size)
   if kind!=1:continue
   if offset+length>len(data):raise ValueError('ELF segment exceeds file')
   self.segments.append((address,length,offset))
 def read(self,address,length):
  if length<0:raise ValueError('negative ELF read length')
  for base,size,offset in self.segments:
   if base<=address and address+length<=base+size:
    start=offset+address-base;return self.data[start:start+length]
  raise ValueError('ELF address not file backed')
 def word(self,address):return struct.unpack('<I',self.read(address,4))[0]
 def string(self,address):
  result=bytearray()
  for n in range(128):
   byte=self.read(address+n,1)
   if byte==b'\0':return result.decode('ascii')
   result+=byte
  raise ValueError('unbounded ELF string')

def inspect(data):
 if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('not pinned pristine public Q2 demo')
 elf=Elf(data);base=elf.word(GP-32736)
 strings={address:elf.string(address) for address in [base-7100,base-7092,base-7084,base-3612]}
 if strings!={0x76e444:'PD18',0x76e44c:'PC20',0x76e454:'PE22',0x76f1e4:'PD17'}:raise ValueError('stock USB pin strings differ')
 if elf.word(GP-19488)!=0x5bf090 or elf.word(GP-21232)!=0x5be5dc:raise ValueError('stock GPIO call targets differ')
 expected={
  0x4e8ef4:0x2484e444, # toolsGpioGetValue(PD18)
  0x4e8f00:0x2c520001, # cached asserted = (PD18 == 0)
  0x4e8f1c:0x16200029, # nonzero input takes disable path
  0x4e8f24:0x24050001,0x4e8f2c:0x2484e44c, # PC20=1
  0x4e8f30:0x24050001,0x4e8f40:0x2664e454, # PE22=1
  0x4e8fc4:0x00002825,0x4e8fcc:0x2484e44c, # PC20=0
  0x4e8fd0:0x00002825,0x4e8fd4:0x1000ffd8, # common PE22=0 call
  0x4f4ae8:0x2484f1e4,0x4f4af0:0x2c420001, # usb_detect PD17 active low
 }
 for address,word in expected.items():
  if elf.word(address)!=word:raise ValueError('stock USB branch/call differs')
 spans=[]
 for name,address,length in [('check_otg_status',0x4e8ec0,828),('usb_detect',0x4f4ac4,56),
                             ('toolsGpioGetValue',0x5bf090,160),('toolsGpioSetValue',0x5be5dc,200)]:
  spans.append({'name':name,'address':hex(address),'bytes':length,'sha256':hashlib.sha256(elf.read(address,length)).hexdigest()})
 return {'public_stock_only':True,'demo_sha256':SHA256,'function_spans':spans,
         'stock_otg_input':{'pin':'PD18','asserted_value':0},
         'stock_asserted_transition_order':[{'pin':'PC20','value':1},{'pin':'PE22','value':1}],
         'stock_deasserted_transition_order':[{'pin':'PC20','value':0},{'pin':'PE22','value':0}],
         'separate_usb_detect':{'pin':'PD17','asserted_value':0},
         'electrical_pin_functions_identified':False,'safe_host_vbus_qualified':False}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('public_stock_demo',type=Path);args=parser.parse_args()
 print(json.dumps(inspect(args.public_stock_demo.read_bytes()),indent=2))
if __name__=='__main__':main()
