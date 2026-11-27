/* Standard watchdog UAPI on /dev/watchdog0, for os/test_watchdog_uapi.py.
 *
 *   q2-wdtcheck magic MAX_TIMEOUT
 *        exclusive open (a second open is EBUSY), keepalive and magic-close
 *        support, timeouts 0 and MAX_TIMEOUT + 1 refused, 2 s set and read
 *        back, time left within it, disable and enable, then magic close;
 *        the probe outlives the timeout after each disable and after the
 *        close, so a reset during them fails the harness
 *   q2-wdtcheck hold
 *        arm a 2 s timeout, print "Q2_WDT_ARMED pid=PID" and feed every
 *        200 ms until killed; the harness kills or stops it and expects a
 *        watchdog reset
 *   q2-wdtcheck nowayout
 *        with nowayout set: disable is EBUSY, magic close is accepted, then
 *        print "Q2_WDT_NOWAYOUT_ARMED" and stop feeding; the harness expects
 *        a watchdog reset
 */
#define PROBE "wdtcheck"
#include "probe.h"
#include <asm/fcntl.h>
#include <asm/ioctl.h>
#include <asm/errno.h>
#include <linux/time.h>
#include <linux/watchdog.h>

static long fd;

static void ioctl_ok(unsigned long request, void *arg, int code)
{
	long ret = call(__NR_ioctl, fd, request, (long)arg);

	if (ret < 0)
		fail(code, ret);
}

static void sleep_ms(unsigned int ms)
{
	struct timespec ts = { ms / 1000, (ms % 1000) * 1000000 };
	long ret = call(__NR_nanosleep, (long)&ts, 0, 0);

	if (ret)
		fail(91, ret);
}

static long parse(const char *s)
{
	long value = 0;

	if (!*s)
		return -1;
	for (; *s; s++) {
		if (*s < '0' || *s > '9' || value > 100000)
			return -1;
		value = value * 10 + (*s - '0');
	}
	return value;
}

static void arm(void)
{
	struct watchdog_info info;
	int timeout = 2, left;
	long ret;

	fd = call(__NR_open, (long)"/dev/watchdog0", O_WRONLY, 0);
	if (fd < 0)
		fail(2, fd);
	ioctl_ok(WDIOC_GETSUPPORT, &info, 3);
	if (!(info.options & WDIOF_KEEPALIVEPING) || !(info.options & WDIOF_MAGICCLOSE) ||
	    !(info.options & WDIOF_SETTIMEOUT))
		fail(4, info.options);
	ret = call(__NR_open, (long)"/dev/watchdog0", O_WRONLY, 0);
	if (ret != -EBUSY)
		fail(5, ret);
	ioctl_ok(WDIOC_SETTIMEOUT, &timeout, 6);
	ioctl_ok(WDIOC_GETTIMEOUT, &timeout, 7);
	if (timeout != 2)
		fail(8, timeout);
	sleep_ms(200);
	ioctl_ok(WDIOC_GETTIMELEFT, &left, 9);
	if (left < 1 || left > 2)
		fail(10, left);
	ioctl_ok(WDIOC_KEEPALIVE, 0, 11);
}

static void bounds(long max)
{
	int timeout = 0;
	long ret = call(__NR_ioctl, fd, WDIOC_SETTIMEOUT, (long)&timeout);

	if (ret != -EINVAL)
		fail(20, ret);
	timeout = max + 1;
	ret = call(__NR_ioctl, fd, WDIOC_SETTIMEOUT, (long)&timeout);
	if (ret != -EINVAL)
		fail(21, ret);
	ioctl_ok(WDIOC_GETTIMEOUT, &timeout, 22);
	if (timeout != 2)
		fail(23, timeout);
}

static void magic(long max)
{
	int options;
	long ret;

	bounds(max);
	options = WDIOS_DISABLECARD;
	ioctl_ok(WDIOC_SETOPTIONS, &options, 30);
	sleep_ms(2500);
	options = WDIOS_ENABLECARD;
	ioctl_ok(WDIOC_SETOPTIONS, &options, 31);
	ioctl_ok(WDIOC_KEEPALIVE, 0, 32);
	if ((ret = call(__NR_write, fd, (long)"V", 1)) != 1)
		fail(33, ret);
	if ((ret = call(__NR_close, fd, 0, 0)))
		fail(34, ret);
	sleep_ms(2500);
}

static void __attribute__((noreturn)) hold(void)
{
	say("Q2_WDT_ARMED pid=");
	number(call(__NR_getpid, 0, 0, 0));
	say("\n");
	for (;;) {
		sleep_ms(200);
		ioctl_ok(WDIOC_KEEPALIVE, 0, 40);
	}
}

static void __attribute__((noreturn)) nowayout(void)
{
	int options = WDIOS_DISABLECARD;
	long ret = call(__NR_ioctl, fd, WDIOC_SETOPTIONS, (long)&options);

	if (ret != -EBUSY)
		fail(50, ret);
	if ((ret = call(__NR_write, fd, (long)"V", 1)) != 1)
		fail(51, ret);
	if ((ret = call(__NR_close, fd, 0, 0)))
		fail(52, ret);
	say("Q2_WDT_NOWAYOUT_ARMED\n");
	for (;;)
		sleep_ms(200);
}

static int run(int argc, char **argv)
{
	long max = argc == 3 ? parse(argv[2]) : -1;

	if (argc == 3 && same(argv[1], "magic") && max > 2) {
		arm();
		magic(max);
		return 0;
	}
	if (argc == 2 && same(argv[1], "hold")) {
		arm();
		hold();
	}
	if (argc == 2 && same(argv[1], "nowayout")) {
		arm();
		nowayout();
	}
	return 1;
}
