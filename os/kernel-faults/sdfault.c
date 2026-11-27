/* microSD direct I/O on the raw areas os/test_storage_faults.py generates
 * beyond the card's filesystem.
 *
 *   q2-sdfault MODE DEVICE
 *
 *   read         read 64 KiB at 48 MiB and expect 0xa5
 *   read-fails   read 64 KiB at 48 MiB and expect EIO
 *   restore      write 0xa5 over 64 KiB at 52 MiB and fsync
 *   write        write 0x5a over 64 KiB at 52 MiB and fsync
 *   write-fails  write 0x5a over 64 KiB at 52 MiB and expect EIO
 *   verify       read 64 KiB at 52 MiB and expect 0x5a
 *
 * O_DIRECT keeps the page cache out, so every call reaches the controller.
 */
#define PROBE "sdfault"
#include "probe.h"
#include <asm/fcntl.h>
#include <asm/errno.h>

#define READ_AT (48L << 20)
#define WRITE_AT (52L << 20)

static unsigned char buf[65536] __attribute__((aligned(4096)));

static int run(int argc, char **argv)
{
	const char *mode = argc == 3 ? argv[1] : "";
	int read_fails = same(mode, "read-fails"), write_fails = same(mode, "write-fails");
	int writing = same(mode, "restore") || same(mode, "write") || write_fails;
	int reading = same(mode, "read") || read_fails || same(mode, "verify");
	unsigned char pattern = same(mode, "restore") || same(mode, "read") ? 0xa5 : 0x5a;
	long offset = same(mode, "read") || read_fails ? READ_AT : WRITE_AT;
	long fd, ret;
	unsigned int i;

	if (!writing && !reading)
		return 1;
	fd = call(__NR_open, (long)argv[2], O_RDWR | O_DIRECT, 0);
	if (fd < 0)
		fail(2, fd);
	seek(fd, offset, 3);
	for (i = 0; i < sizeof(buf); i++)
		buf[i] = writing ? pattern : 0;
	ret = call(writing ? __NR_write : __NR_read, fd, (long)buf, sizeof(buf));
	if (read_fails || write_fails) {
		if (ret != -EIO)
			fail(4, ret);
	} else {
		if (ret != (long)sizeof(buf))
			fail(5, ret);
		if (writing && (ret = call(__NR_fsync, fd, 0, 0)))
			fail(6, ret);
		for (i = 0; reading && i < sizeof(buf); i++)
			if (buf[i] != pattern)
				fail(7, i);
	}
	call(__NR_close, fd, 0, 0);
	return 0;
}
