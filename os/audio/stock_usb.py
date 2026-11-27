#!/usr/bin/env python3
"""Reproduce USB role metadata from the pinned public Q2 embedded DTB.

This inspects only a pristine public image, never the booted/owner DT or PMIC.
An embedded OTG node establishes stock software intent, not safe live VBUS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib
from stock_uart import BASE,GZIP_OFFSET,inspect as inspect_image

DTB_ADDRESS=0x80b7cba0
DTB_SHA256='130bc3e42d371ff712beade04c8dcf7cd336def8b89d2da53d36b4c6c06aa8a6'
USB_FUNCTIONS=[
 ('usb_phy_set_vbus',0x8047bbf8,0x8047bc84,'4896adbad72b81f0bd70e939881002916f493db8ce236599bda678f13fe7f6e3'),
 ('ingenic_usb_phy_probe',0x8047be50,0x8047c42c,'842d57c3532493bf929eb0f1419b22102f832df3b53406376f024ddc22d31226'),
 ('x2000_phy_init',0x8047cd10,0x8047cf00,'70b0c6609dbd654316d38cc82a16b6f937a0a0ea8d8e2d42ab96b4253735eb6a'),
]
GPIO_LOOKUPS=[
 (0x80a47d8c,'ingenic,drvvbus'),(0x80a47dc4,'ingenic,vbus-dete'),
 (0x80a47e2c,'ingenic,id-dete'),(0x80a47ec8,'ingenic,usb-switch'),
 (0x80a47f10,'ingenic,usb-wakeup'),
]

def kernel_usb_evidence(raw):
 """Pin independently recovered symbols and their entire public code spans.

 These are evidence addresses, not a register-writing API. The VBUS callback
 calls optional gpiod_set_value but does not identify a supply when the board
 DT omits the GPIO. A generic vendor driver alone is not a board schematic.
 """
 records=[]
 for name,start,end,expected in USB_FUNCTIONS:
  actual=hashlib.sha256(raw[start-BASE:end-BASE]).hexdigest()
  if actual!=expected:raise ValueError('unexpected stock USB function '+name)
  records.append({'name':name,'address':hex(start),'bytes':end-start,'sha256':actual})
 names=[]
 for address,expected in GPIO_LOOKUPS:
  offset=address-BASE;end=raw.find(b'\0',offset)
  if offset<0 or end<0 or raw[offset:end]!=expected.encode():raise ValueError('unexpected stock USB GPIO lookup')
  names.append(expected)
 # Probe requests optional GPIOs; usb_phy_set_vbus tests for a descriptor
 # before calling gpiod_set_value. Pin the independently inspected opcodes.
 for address,word in [(0x8047bc24,0x8e04fffc),(0x8047bc28,0x10800003),(0x8047bc30,0x0c0d86c0),
                      (0x8047bfa0,0x24a57d8c),(0x8047bfd8,0x24a57dc4),(0x8047c088,0x24a57e2c)]:
  if struct.unpack_from('<I',raw,address-BASE)[0]!=word:raise ValueError('stock USB probe/VBUS sequence differs')
 return {'public_function_spans':records,'optional_gpio_lookup_names':names,
         'vbus_callback_requires_optional_descriptor':True,'board_vbus_circuit_identified':False}

def fdt_nodes(blob):
 if len(blob)<40:raise ValueError('truncated FDT header')
 magic,total,structure,strings,_,version,_,_,string_size,structure_size=struct.unpack_from('>10I',blob)
 if magic!=0xd00dfeed or total!=len(blob) or version<17:raise ValueError('unexpected FDT header')
 if structure<40 or strings<40 or structure+structure_size>total or strings+string_size>total:raise ValueError('FDT blocks out of bounds')
 names=blob[strings:strings+string_size];data=blob[structure:structure+structure_size];pos=0;stack=[];nodes={}
 def terminated(data,start):
  end=data.find(b'\0',start)
  if end<0:raise ValueError('unterminated FDT name')
  return data[start:end].decode('ascii'),end+1
 while pos+4<=len(data):
  token=struct.unpack_from('>I',data,pos)[0];pos+=4
  if token==1:
   name,pos=terminated(data,pos);pos=(pos+3)&~3
   if '/' in name or (not stack and name):raise ValueError('invalid FDT node name')
   stack.append(name);path='/'.join(stack) or '/'
   if path in nodes:raise ValueError('duplicate FDT node')
   nodes[path]={}
  elif token==2:
   if not stack:raise ValueError('unbalanced FDT end-node')
   stack.pop()
  elif token==3:
   if not stack or pos+8>len(data):raise ValueError('invalid FDT property')
   size,offset=struct.unpack_from('>II',data,pos);pos+=8
   if offset>=len(names) or pos+size>len(data):raise ValueError('FDT property out of bounds')
   name,_=terminated(names,offset);path='/'.join(stack) or '/'
   if name in nodes[path]:raise ValueError('duplicate FDT property')
   nodes[path][name]=data[pos:pos+size];pos=(pos+size+3)&~3
  elif token==4:continue
  elif token==9:
   if stack:raise ValueError('unclosed FDT node')
   return nodes
  else:raise ValueError('unknown FDT token')
 raise ValueError('missing FDT end token')

def inspect(image):
 inspect_image(image) # Strict source/hash/CRC validation shared with UART evidence.
 raw=zlib.decompress(image[GZIP_OFFSET:],wbits=31);start=DTB_ADDRESS-BASE
 size=struct.unpack_from('>I',raw,start+4)[0];dtb=raw[start:start+size]
 if hashlib.sha256(dtb).hexdigest()!=DTB_SHA256:raise ValueError('unexpected embedded Q2 DTB')
 nodes=fdt_nodes(dtb);usb=[]
 for path,properties in nodes.items():
  compatibles=properties.get('compatible',b'').rstrip(b'\0').split(b'\0')
  if b'ingenic,usbphy-x2000' not in compatibles and b'ingenic,x2000-dwc2-hsotg' not in compatibles:continue
  # Deliberately report only USB properties, never unrelated address/config data.
  record={'path':path,'compatible':[v.decode() for v in compatibles],'property_names':sorted(properties)}
  for name in ['dr_mode','status']:
   if name in properties:record[name]=properties[name].rstrip(b'\0').decode()
  record['vbus_gpio_properties']=[name for name in properties if name.startswith(('ingenic,drvvbus','ingenic,usb-switch','vbus','id-gpio'))]
  usb.append(record)
 if len(usb)!=2 or not any(n.get('dr_mode')=='otg' for n in usb):raise ValueError('stock USB nodes differ')
 absent=[]
 for name in [name for _,name in GPIO_LOOKUPS]:
  if not any(any(key==name or key.startswith(name+'-') for key in properties) for properties in nodes.values()):absent.append(name)
 return {'public_embedded_dtb_only':True,'dtb_sha256':DTB_SHA256,'dtb_address':hex(DTB_ADDRESS),'usb_nodes':usb,
         'kernel_usb_evidence':kernel_usb_evidence(raw),'gpio_lookup_names_absent_from_entire_dtb':absent,
         'runtime_role_qualified':False,'safe_vbus_host_switch_established':False}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('public_stock_ximage',type=Path);a=p.parse_args()
 print(json.dumps(inspect(a.public_stock_ximage.read_bytes()),indent=2))

if __name__=='__main__':main()
