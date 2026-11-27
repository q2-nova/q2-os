/* SPI NAND faults through the MTD character device of a scratch partition
 * that os/test_storage_faults.py generates and injects faults into.
 *
 *   q2-nandfault MODE DEVICE
 *
 * Block numbers are relative to the partition. The harness fills blocks 0,
 * 1 and 3 with 0xa5 data pages and leaves the rest erased.
 *
 *   io       erase blocks 8 and 9, check them erased, program both with a
 *            position pattern, read unaligned spans that cross page and block
 *            boundaries, erase block 8 again and check it erased; block 9
 *            stays programmed for the harness to read back
 *   inject   with these faults armed: block 0 page 0 corrected ECC, block 1
 *            page 0 uncorrectable ECC, block 2 page 0 program failure, block
 *            3 erase failure, block 4 marked bad on the medium. Expect
 *            EUCLEAN, EBADMSG, EIO and EIO, the ECC counters to move by the
 *            same amounts, block 4 reported bad, the failed program and
 *            erase to leave the medium unchanged, and MEMSETBADBLOCK to mark
 *            block 5
 *   recover  after the faults are cleared: clean reads, unchanged counters,
 *            the program and erase succeed, and blocks 4 and 5 stay bad
 */
#define PROBE "nandfault"
#include "probe.h"
#include <asm/fcntl.h>
#include <asm/ioctl.h>
#include <asm/errno.h>
#include <mtd/mtd-abi.h>

#define BLOCK 131072
#define PAGE 2048
/* The SPI NAND reports a 1-4 bitflip bucket; Linux takes its upper bound. */
#define CORRECTED 4

static unsigned char buf[PAGE];

static unsigned char pattern(unsigned int offset)
{
	return (offset * 17 + (offset >> 8) * 37 + (offset >> 17) * 53) & 255;
}

static void read_page(long fd, unsigned int block, int raw, long expected,
		      unsigned int first, unsigned int rest,
		      unsigned int corrected, unsigned int failed, int code)
{
	struct mtd_read_req req = {
		.start = block * BLOCK, .len = PAGE, .usr_data = (unsigned long)buf,
		.mode = raw ? MTD_OPS_RAW : MTD_OPS_PLACE_OOB,
	};
	long ret = call(__NR_ioctl, fd, MEMREAD, (long)&req);
	unsigned int i;

	if (ret != expected)
		fail(code, ret);
	if (req.len != PAGE || req.ecc_stats.corrected_bitflips != corrected ||
	    req.ecc_stats.uncorrectable_errors != failed ||
	    req.ecc_stats.max_bitflips != corrected)
		fail(code + 1, req.ecc_stats.corrected_bitflips);
	if (buf[0] != first)
		fail(code + 2, buf[0]);
	for (i = 1; i < PAGE; i++)
		if (buf[i] != rest)
			fail(code + 3, i);
}

static void check_erased(long fd, unsigned int start, unsigned int length, int code)
{
	unsigned int off, i;

	seek(fd, start, code);
	for (off = 0; off < length; off += PAGE) {
		if (call(__NR_read, fd, (long)buf, PAGE) != PAGE)
			fail(code + 1, off);
		for (i = 0; i < PAGE; i++)
			if (buf[i] != 255)
				fail(code + 2, off + i);
	}
}

static void io(long fd)
{
	struct erase_info_user erase = { .start = 8 * BLOCK, .length = 2 * BLOCK };
	unsigned int off, i;
	long ret;

	if ((ret = call(__NR_ioctl, fd, MEMERASE, (long)&erase)))
		fail(20, ret);
	check_erased(fd, erase.start, erase.length, 21);
	seek(fd, erase.start, 24);
	for (off = 0; off < erase.length; off += PAGE) {
		for (i = 0; i < PAGE; i++)
			buf[i] = pattern(off + i);
		if ((ret = call(__NR_write, fd, (long)buf, PAGE)) != PAGE)
			fail(25, ret);
	}
	/* Short unaligned reads, some crossing page and block boundaries. */
	for (off = 17; off + 1503 < erase.length; off += 997) {
		seek(fd, erase.start + off, 26);
		if ((ret = call(__NR_read, fd, (long)buf, 1503)) != 1503)
			fail(26, ret);
		for (i = 0; i < 1503; i++)
			if (buf[i] != pattern(off + i))
				fail(27, off + i);
	}
	erase.length = BLOCK;
	if ((ret = call(__NR_ioctl, fd, MEMERASE, (long)&erase)))
		fail(28, ret);
	check_erased(fd, erase.start, erase.length, 29);
}

static void faults(long fd, int recover)
{
	struct mtd_ecc_stats before, after;
	struct erase_info_user erase = { .start = 3 * BLOCK, .length = BLOCK };
	long long bad = 4 * BLOCK;
	unsigned int i;
	long ret;

	if ((ret = call(__NR_ioctl, fd, ECCGETSTATS, (long)&before)))
		fail(30, ret);
	read_page(fd, 0, 0, recover ? 0 : -EUCLEAN, 0xa5, 0xa5,
		  recover ? 0 : CORRECTED, 0, 31);
	/* The model returns the uncorrectable page with its first byte flipped. */
	read_page(fd, 1, 0, recover ? 0 : -EBADMSG, recover ? 0xa5 : 0xba, 0xa5,
		  0, recover ? 0 : 1, 35);
	/* A raw read shows the single flipped bit the ECC corrected. */
	read_page(fd, 0, 1, 0, recover ? 0xa5 : 0xa4, 0xa5, 0, 0, 39);
	if ((ret = call(__NR_ioctl, fd, ECCGETSTATS, (long)&after)))
		fail(43, ret);
	if (after.corrected - before.corrected != (recover ? 0 : CORRECTED))
		fail(44, after.corrected - before.corrected);
	if (after.failed - before.failed != (recover ? 0 : 1))
		fail(45, after.failed - before.failed);
	if ((ret = call(__NR_ioctl, fd, MEMGETBADBLOCK, (long)&bad)) != 1)
		fail(46, ret);
	seek(fd, 2 * BLOCK, 47);
	for (i = 0; i < PAGE; i++)
		buf[i] = 0x3c;
	ret = call(__NR_write, fd, (long)buf, PAGE);
	if (ret != (recover ? PAGE : -EIO))
		fail(48, ret);
	read_page(fd, 2, 0, 0, recover ? 0x3c : 255, recover ? 0x3c : 255, 0, 0, 49);
	ret = call(__NR_ioctl, fd, MEMERASE, (long)&erase);
	if (ret != (recover ? 0 : -EIO))
		fail(53, ret);
	read_page(fd, 3, 0, 0, recover ? 255 : 0xa5, recover ? 255 : 0xa5, 0, 0, 54);
	bad = 5 * BLOCK;
	if ((ret = call(__NR_ioctl, fd, MEMSETBADBLOCK, (long)&bad)))
		fail(58, ret);
	if ((ret = call(__NR_ioctl, fd, MEMGETBADBLOCK, (long)&bad)) != 1)
		fail(59, ret);
}

static int run(int argc, char **argv)
{
	struct mtd_info_user info;
	const char *mode = argc == 3 ? argv[1] : "";
	long fd;

	if (!same(mode, "io") && !same(mode, "inject") && !same(mode, "recover"))
		return 1;
	fd = call(__NR_open, (long)argv[2], O_RDWR, 0);
	if (fd < 0)
		fail(2, fd);
	if (call(__NR_ioctl, fd, MEMGETINFO, (long)&info) || info.type != MTD_NANDFLASH)
		fail(3, info.type);
	if (info.erasesize != BLOCK || info.writesize != PAGE || info.size < 16 * BLOCK)
		fail(4, info.size);
	if (same(mode, "io"))
		io(fd);
	else
		faults(fd, same(mode, "recover"));
	call(__NR_close, fd, 0, 0);
	return 0;
}
