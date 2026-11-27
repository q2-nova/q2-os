// SPDX-License-Identifier: GPL-2.0-only
/* Explicit physical qualification of TWO unused immutable-root tail blocks.
 * No generic address parameter, userdata, boot slots, recovery or raw dumps.
 * mode=0: verify erased only; mode=1: program generated pages and retain;
 * mode=2: verify generated pages after a reset; mode=3: verify then erase.
 * mode=4: explicit fixed-range cleanup after a failed/interrupted generated trial.
 * The primary must review and authorize mode=1/3/4 before using this tool.
 */
#include <linux/module.h>
#include <linux/mtd/mtd.h>
#include <linux/mtd/spinand.h>
#include <linux/mtd/ubi.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

#define ROOT_START 0x700000ULL
#define ROOT_SIZE 0x4b00000ULL
#define SCRATCH_START 0x51c0000ULL
#define ERASE_SIZE 0x20000U
#define PAGE_SIZE_NAND 2048U
#define SCRATCH_SIZE (2U * ERASE_SIZE)
#define PAGE_COUNT 128U

static unsigned int mode;
module_param(mode, uint, 0400);
MODULE_PARM_DESC(mode, "0=verify erased, 1=program generated/retain, 2=verify generated, 3=verify generated/erase, 4=cleanup failed/interrupted generated trial");
static bool legacy;
module_param(legacy, bool, 0400);
MODULE_PARM_DESC(legacy, "Explicitly permit generated scratch programming with pre-0050 kernel");

static int checked_read(struct mtd_info *mtd, loff_t start, size_t len, u8 *buf)
{
 size_t got = 0;
 u32 corrected = mtd->ecc_stats.corrected, failed = mtd->ecc_stats.failed;
 int ret = mtd_read(mtd, start, len, &got, buf);
 if (ret || got != len || mtd->ecc_stats.corrected != corrected ||
     mtd->ecc_stats.failed != failed) {
  pr_err("q2-scratch: read failed at relative=%llx ret=%d got=%zu corrected=%u failed=%u\n",
   (unsigned long long)start, ret, got,
   mtd->ecc_stats.corrected - corrected, mtd->ecc_stats.failed - failed);
  return ret ?: -EIO;
 }
 return 0;
}

static int erased(struct mtd_info *master, u8 *buf)
{
 unsigned int page;
 int ret;
 for (page = 0; page < PAGE_COUNT; page++) {
  ret = checked_read(master, SCRATCH_START + page * PAGE_SIZE_NAND, PAGE_SIZE_NAND, buf);
  if (ret) return ret;
  if (memchr_inv(buf, 0xff, PAGE_SIZE_NAND)) return -ENOTEMPTY;
  {
   struct mtd_oob_ops ops = { .mode = MTD_OPS_RAW, .ooblen = 64, .oobbuf = buf };
   ret = mtd_read_oob(master, SCRATCH_START + page * PAGE_SIZE_NAND, &ops);
   if (ret || ops.oobretlen != 64) return ret ?: -EIO;
   if (memchr_inv(buf, 0xff, 64)) return -ENOTEMPTY;
  }
 }
 pr_info("q2-scratch: all-FF verified main_bytes=%u visible_oob_bytes=8192 physical=%llx..%llx\n",
  SCRATCH_SIZE, SCRATCH_START, SCRATCH_START + SCRATCH_SIZE);
 return 0;
}

static void pattern(u8 *buf, unsigned int page)
{
 unsigned int i, length = page % 64 < 2 ? 64 : PAGE_SIZE_NAND;
 memset(buf, 0xff, PAGE_SIZE_NAND);
 for (i = 0; i < length; i++)
  buf[i] = (u8)((page * 73 + i * 17 + 0x39) ^ (i >> 3));
 /* Different EC/VID-like page prefixes, followed by alternating/changing data. */
 if (page % 64 == 0) memcpy(buf, "UBI#Q2-GENERATED", 16);
 if (page % 64 == 1) memcpy(buf, "UBI!Q2-GENERATED", 16);
 if (page % 64 == 2) memset(buf, 0xaa, PAGE_SIZE_NAND);
 if (page % 64 == 3) memset(buf, 0x55, PAGE_SIZE_NAND);
}

static int verify(struct mtd_info *master, u8 *expected, u8 *actual)
{
 unsigned int page;
 int ret;
 for (page = 0; page < PAGE_COUNT; page++) {
  pattern(expected, page);
  ret = checked_read(master, SCRATCH_START + page * PAGE_SIZE_NAND, PAGE_SIZE_NAND, actual);
  if (ret) return ret;
  if (memcmp(expected, actual, PAGE_SIZE_NAND)) {
   pr_err("q2-scratch: generated pattern mismatch page=%u; no bytes disclosed\n", page);
   return -EILSEQ;
  }
 }
 pr_info("q2-scratch: GENERATED_VERIFY_PASS pages=%u corrected=0 failed=0\n", PAGE_COUNT);
 return 0;
}

static int erase_scratch(struct mtd_info *master)
{
 struct erase_info erase = { .addr = SCRATCH_START, .len = SCRATCH_SIZE };
 int ret = mtd_erase(master, &erase);
 if (ret) pr_err("q2-scratch: erase failed ret=%d\n", ret);
 return ret;
}

static int __init scratch_init(void)
{
 struct mtd_info *root, *master, *userdata;
 struct spinand_device *spinand;
 struct ubi_device_info di;
 u8 *expected, *actual;
 u64 used, position;
 unsigned int page, ubi_number;
 int ret;
 bool initial;
 if (mode > 4) return -EINVAL;
 root = get_mtd_device_nm("rootfs");
 if (IS_ERR(root)) return PTR_ERR(root);
 master = mtd_get_master(root);
 if (!master->dev.parent || strcmp(dev_name(master->dev.parent), "spi0.0") ||
     strcmp(dev_driver_string(master->dev.parent), "spi-nand")) {
  ret = -ENODEV; goto put;
 }
 spinand = nand_to_spinand(mtd_to_nanddev(master));
 initial = !!(spinand->flags & SPINAND_NEEDS_PROGRAM_LOAD);
 pr_info("q2-scratch: metadata root_offset=%llx root_size=%llx root_flags=%x master_type=%u master_size=%llx erase=%x write=%x oob=%x master_flags=%x id=%02x:%02x:%02x:%02x chip_flags=%x initial=%u\n",
  mtd_get_master_ofs(root, 0), root->size, root->flags, master->type,
  master->size, master->erasesize, master->writesize, master->oobsize,
  master->flags, spinand->id.data[0], spinand->id.data[1],
  spinand->id.data[2], spinand->id.data[3], spinand->flags, initial);
 if (mtd_get_master_ofs(root, 0) != ROOT_START || root->size != ROOT_SIZE ||
     !mtd_type_is_nand(master) || master->erasesize != ERASE_SIZE ||
     master->writesize != PAGE_SIZE_NAND || master->oobsize != 64 ||
     master->size != 0x8000000ULL || (root->flags & MTD_WRITEABLE) ||
     SCRATCH_START + SCRATCH_SIZE != ROOT_START + ROOT_SIZE ||
     spinand->id.data[0] != 0xe5 || spinand->id.data[1] != 0x71 ||
     spinand_to_nand(spinand)->memorg.planes_per_lun != 1) {
  ret = -EINVAL; goto put;
 }
 /* Trial DT must not expose userdata, and no UBI may be attached. */
 userdata = get_mtd_device_nm("userdata");
 if (!IS_ERR(userdata)) {
  put_mtd_device(userdata);
  pr_err("q2-scratch: trial DT must omit userdata; refusing this kernel\n");
  ret = -EPERM; goto put;
 }
 if (PTR_ERR(userdata) != -ENODEV) { ret = PTR_ERR(userdata); goto put; }
 for (ubi_number = 0; ubi_number < 128; ubi_number++) {
  ret = ubi_get_device_info(ubi_number, &di);
  if (ret == -EINVAL) break;
  if (ret != -ENODEV) { ret = ret ?: -EBUSY; goto put; }
 }
 if (ubi_number == 128) { ret = -EOVERFLOW; goto put; }
 expected = kmalloc(PAGE_SIZE_NAND, GFP_KERNEL);
 actual = kmalloc(PAGE_SIZE_NAND, GFP_KERNEL);
 if (!expected || !actual) { ret = -ENOMEM; goto free; }
 ret = checked_read(root, 0, 96, actual);
 if (ret) goto free;
 used = get_unaligned_le64(actual + 40);
 if (memcmp(actual, "hsqs", 4) || used < 96 || used > ROOT_SIZE ||
     ALIGN(used, ERASE_SIZE) + ERASE_SIZE > SCRATCH_START - ROOT_START) {
  ret = -ERANGE; goto free;
 }
 /* With no skipped root blocks, the mounted image's physical extent is known. */
 for (position = 0; position < ROOT_SIZE; position += ERASE_SIZE) {
  ret = mtd_block_isbad(root, position);
  if (ret) { ret = ret < 0 ? ret : -EIO; goto free; }
 }
 pr_info("q2-scratch: guards pass squashfs_used=%llu root=%llx+%llx target=%llx+%x initial_load=%u mode=%u\n",
  used, ROOT_START, ROOT_SIZE, SCRATCH_START, SCRATCH_SIZE, initial, mode);
 if (mode == 4) {
  /* Explicit recovery of this fixed, guarded unused-root test extent only.
   * A failed ECC read must not prevent erasing our generated scratch data.
   */
  ret = erase_scratch(master);
  if (!ret) ret = erased(master, actual);
  if (!ret) pr_info("q2-scratch: FAILED_TRIAL_CLEANUP_PASS\n");
  goto free;
 }
 if (mode == 0 || mode == 1) {
  ret = erased(master, actual);
  if (ret || mode == 0) goto free;
 }
 if (mode == 1) {
  if (!initial && !legacy) { ret = -EPERM; goto free; }
  ret = erase_scratch(master);
  if (ret) goto free;
  for (page = 0; page < PAGE_COUNT; page++) {
   size_t written = 0;
   pattern(expected, page);
   ret = mtd_write(master, SCRATCH_START + page * PAGE_SIZE_NAND,
                   PAGE_SIZE_NAND, &written, expected);
   if (ret || written != PAGE_SIZE_NAND) { ret = ret ?: -EIO; goto free; }
   ret = checked_read(master, SCRATCH_START + page * PAGE_SIZE_NAND, PAGE_SIZE_NAND, actual);
   if (ret) goto free;
   if (memcmp(expected, actual, PAGE_SIZE_NAND)) { ret = -EILSEQ; goto free; }
  }
  pr_info("q2-scratch: GENERATED_PROGRAM_PASS retained-for-reset=1\n");
 }
 ret = verify(master, expected, actual);
 if (!ret && mode == 3) {
  ret = erase_scratch(master);
  if (!ret) ret = erased(master, actual);
  if (!ret) pr_info("q2-scratch: CLEANUP_PASS\n");
 }
free:
 kfree(actual); kfree(expected);
put:
 put_mtd_device(root);
 if (ret) pr_err("q2-scratch: REFUSED_OR_FAILED ret=%d mode=%u\n", ret, mode);
 return ret;
}
static void __exit scratch_exit(void) { }
module_init(scratch_init);
module_exit(scratch_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Q2 generated NAND write test in two fixed unused rootfs tail blocks");
