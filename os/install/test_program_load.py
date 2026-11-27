#!/usr/bin/env python3
"""Compile the exact patched cache-write function with a generated SPI recorder.
Checks command/plane/chunk/error contracts; it does not model NAND ECC physics.
"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef unsigned char u8;
#define SPINAND_NEEDS_PROGRAM_LOAD (1U << 6)
#define SPINAND_HAS_PROG_PLANE_SELECT_BIT (1U << 2)
#define MTD_OPS_RAW 2
#define MTD_OPS_AUTO_OOB 1
struct nand_device { int unused; };
struct mtd_info { int unused; };
struct spi_mem_op {
 unsigned opcode;
 struct { uint64_t val; } addr;
 struct { unsigned nbytes; bool ecc; union { const void *out; void *in; } buf; } data;
};
struct spi_mem_dirmap_desc { struct { uint64_t offset; struct spi_mem_op *op_tmpl; } info; };
struct maps { struct spi_mem_dirmap_desc *wdesc; };
struct templates { struct spi_mem_op *write_cache, *update_cache; };
struct spinand_device {
 unsigned flags; void *spimem; u8 *databuf, *oobbuf;
 struct maps dirmaps[2]; struct templates *op_templates;
};
struct nand_page_io_req {
 unsigned mode, datalen, dataoffs, ooblen, ooboffs;
 struct { const void *out; } databuf, oobbuf;
 struct { unsigned plane; } pos;
};
static struct nand_device nand;
static struct mtd_info mtd;
static struct nand_device *spinand_to_nand(struct spinand_device *s) { (void)s; return &nand; }
static struct mtd_info *spinand_to_mtd(struct spinand_device *s) { (void)s; return &mtd; }
static unsigned nanddev_page_size(struct nand_device *n) { (void)n; return 2048; }
static unsigned nanddev_per_page_oobsize(struct nand_device *n) { (void)n; return 64; }
static bool nand_ecc_is_pipelined(struct nand_device *n) { (void)n; return true; }
static void mtd_ooblayout_set_databytes(struct mtd_info *m, const void *src, void *dst, unsigned o, unsigned l) { (void)m; memcpy((u8 *)dst+o, src, l); }
static unsigned fls(unsigned x) { return 32 - __builtin_clz(x); }
static unsigned chunk_limit, calls, adjust_error, exec_error, update_error;
static unsigned zero_update, oversized_update, codes[4096], addresses[4096], lengths[4096];
static bool expected_ecc;
static u8 cache[2][2112];
static ssize_t record(struct spi_mem_op *op) {
 unsigned plane = (op->addr.val >> 12) & 1, column = op->addr.val & 4095;
 assert(calls < 4096 && op->data.nbytes && op->data.nbytes <= chunk_limit);
 assert(column + op->data.nbytes <= 2112);
 assert(op->data.ecc == expected_ecc);
 codes[calls] = op->opcode; addresses[calls] = op->addr.val; lengths[calls] = op->data.nbytes; calls++;
 if (op->opcode == 0x32) memset(cache[plane], 0xff, 2112);
 else assert(op->opcode == 0x34);
 memcpy(cache[plane] + column, op->data.buf.out, op->data.nbytes);
 return op->data.nbytes;
}
static int spi_mem_adjust_op_size(void *m, struct spi_mem_op *op) {
 (void)m; if (adjust_error) return -EIO;
 if (op->data.nbytes > chunk_limit) op->data.nbytes = chunk_limit;
 return 0;
}
static int spi_mem_exec_op(void *m, struct spi_mem_op *op) {
 (void)m; if (exec_error) return -ETIMEDOUT; record(op); return 0;
}
static ssize_t spi_mem_dirmap_write(struct spi_mem_dirmap_desc *d, unsigned col, unsigned len, void *buf) {
 struct spi_mem_op op = *d->info.op_tmpl;
 if (update_error) return -EIO;
 if (zero_update) return 0;
 if (oversized_update) return len + 1;
 op.addr.val = d->info.offset + col; op.data.buf.out = buf;
 op.data.nbytes = len < chunk_limit ? len : chunk_limit;
 return record(&op);
}
'''
MAIN = r'''
int main(void) {
 struct spi_mem_op load = {.opcode=0x32}, update = {.opcode=0x34};
 struct templates templates = {&load, &update};
 struct spi_mem_dirmap_desc desc[2] = {{{0, &update}}, {{4096, &update}}};
 u8 buffer[2112], input[2048], expected[2112];
 struct spinand_device s = {.databuf=buffer, .oobbuf=buffer+2048, .op_templates=&templates,
                           .dirmaps={{&desc[0]}, {&desc[1]}}};
 struct nand_page_io_req req = {.databuf.out=input};
 unsigned limits[] = {1, 31, 256, 4096}, tested = 0;
 for (unsigned flagged=0; flagged<2; flagged++) for (unsigned plane=0; plane<2; plane++)
 for (unsigned size=0; size<4; size++) for (unsigned raw=0; raw<2; raw++) {
  s.flags = flagged ? SPINAND_NEEDS_PROGRAM_LOAD : 0; req.pos.plane=plane;
  req.mode = raw ? MTD_OPS_RAW : 0; req.datalen = size & 1 ? 64 : 2048;
  expected_ecc = !raw; chunk_limit=limits[size]; calls=0;
  for (unsigned i=0; i<2048; i++) input[i]=(u8)(i*37+tested*73);
  memset(buffer, 0xff, sizeof buffer); memset(cache, 0xa5, sizeof cache);
  memset(expected, 0xff, sizeof expected); memcpy(expected, input, req.datalen);
  assert(spinand_write_to_cache_op(&s, &req)==0);
  assert(calls == (2112+chunk_limit-1)/chunk_limit);
  assert(codes[0] == (flagged ? 0x32U : 0x34U));
  unsigned consumed=0;
  for (unsigned i=0; i<calls; i++) {
   assert(addresses[i] == plane*4096 + consumed);
   if (i) assert(codes[i]==0x34);
   consumed += lengths[i];
  }
  assert(consumed==2112 && !memcmp(expected, cache[plane],2112)); tested++;
 }
 s.flags=SPINAND_NEEDS_PROGRAM_LOAD; req.pos.plane=1; req.mode=0; expected_ecc=true;
 chunk_limit=256; memset(buffer,0xff,sizeof buffer);
 adjust_error=1; assert(spinand_write_to_cache_op(&s,&req)==-EIO); adjust_error=0;
 exec_error=1; assert(spinand_write_to_cache_op(&s,&req)==-ETIMEDOUT); exec_error=0;
 update_error=1; assert(spinand_write_to_cache_op(&s,&req)==-EIO); update_error=0;
 zero_update=1; assert(spinand_write_to_cache_op(&s,&req)==-EIO); zero_update=0;
 oversized_update=1; assert(spinand_write_to_cache_op(&s,&req)==-EIO); oversized_update=0;
 printf("PASS %u generated command/plane/chunk/ECC-mode cases and 5 error paths; NAND ECC physics unmodeled\n",tested);
 return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel-source', required=True, type=Path)
    p.add_argument('--sanitize', action='store_true')
    a = p.parse_args()
    source = (a.kernel_source / 'drivers/mtd/nand/spi/core.c').read_text()
    start = source.index('static int spinand_write_to_cache_op(')
    end = source.index('static int spinand_program_op(', start)
    actual = source[start:end]
    assert 'SPINAND_NEEDS_PROGRAM_LOAD' in actual, 'requires patched source'
    out = ROOT / 'build/os/nand-write-audit/host-sequence'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'sequence.c').write_text(FIXTURE + actual + MAIN)
    flags = ['-fsanitize=address,undefined'] if a.sanitize else []
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', *flags,
                    str(out/'sequence.c'), '-o', str(out/'sequence')], check=True)
    subprocess.run([str(out/'sequence')], check=True)

if __name__ == '__main__':
    main()
