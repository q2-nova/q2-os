"""Generated FDT boundary fixtures; no device or owner DT is read."""
import random
import struct
import unittest
from stock_usb import fdt_nodes,inspect,kernel_usb_evidence


def fixture(properties):
 names=b'';offsets={}
 for name in properties:offsets[name]=len(names);names+=name.encode()+b'\0'
 def aligned(b):return b+b'\0'*((-len(b))%4)
 data=struct.pack('>I',1)+b'\0'*4+struct.pack('>I',1)+aligned(b'usb@13500000\0')
 for name,value in properties.items():data+=struct.pack('>III',3,len(value),offsets[name])+aligned(value)
 data+=struct.pack('>III',2,2,9)
 return struct.pack('>10I',0xd00dfeed,56+len(data)+len(names),56,56+len(data),40,17,16,0,len(names),len(data))+b'\0'*16+data+names

class StockUsbTest(unittest.TestCase):
 def test_seeded_properties_and_truncations(self):
  rng=random.Random(0x13500000)
  for case in range(100):
   properties={f'field-{n}':rng.randbytes(rng.randrange(65)) for n in range(rng.randrange(1,10))}
   blob=fixture(properties);self.assertEqual(fdt_nodes(blob)['/usb@13500000'],properties)
   for cut in [0,1,8,39,len(blob)-1,rng.randrange(len(blob))]:
    with self.assertRaises(ValueError):fdt_nodes(blob[:cut])
 def test_corrupt_block_and_token_negative_controls(self):
  blob=fixture({'compatible':b'generated,usb\0','dr_mode':b'otg\0'})
  for offset,value in [(0,0),(4,2**32-1),(8,2**32-1),(12,2**32-1),(32,2**32-1),(36,2**32-1),(56,99)]:
   bad=bytearray(blob);struct.pack_into('>I',bad,offset,value)
   with self.assertRaises(ValueError):fdt_nodes(bad)
  with self.assertRaises(ValueError):inspect(b'generated input must not impersonate public stock')
  with self.assertRaises(ValueError):kernel_usb_evidence(b'generated input must not impersonate public code')

if __name__=='__main__':unittest.main()
