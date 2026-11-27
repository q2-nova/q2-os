"""Generated file-backed ELF boundaries; no live device/config is opened."""
import struct
import unittest
from stock_board_usb import Elf,inspect

def generated():
 data=bytearray(160);data[:7]=b'\x7fELF\x01\x01\x01'
 struct.pack_into('<H',data,18,8);struct.pack_into('<I',data,28,52);struct.pack_into('<HH',data,42,32,1)
 struct.pack_into('<8I',data,52,1,128,0x400000,0x400000,32,64,4,16)
 data[128:132]=b'pin\0'
 return data

class StockBoardUsb(unittest.TestCase):
 def test_segments_strings_and_file_not_memory_bounds(self):
  elf=Elf(generated());self.assertEqual(elf.string(0x400000),'pin')
  self.assertEqual(elf.word(0x400004),0)
  for address,length in [(0x3fffff,1),(0x40001f,2),(0x400020,1),(0x400000,-1)]:
   with self.assertRaises(ValueError):elf.read(address,length)
  with self.assertRaises(ValueError):inspect(generated())
 def test_truncation_and_header_negative_controls(self):
  good=generated()
  for cut in range(len(good)):
   with self.assertRaises(ValueError):Elf(good[:cut])
  for offset,fmt,value in [(18,'<H',0),(28,'<I',2**32-1),(42,'<H',0),(44,'<H',65535),(52+4,'<I',2**32-1)]:
   bad=good.copy();struct.pack_into(fmt,bad,offset,value)
   with self.assertRaises(ValueError):Elf(bad)
if __name__=='__main__':unittest.main()
