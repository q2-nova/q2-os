/* Reuse the unchanged RAM-pivot supervisor with the standard Linux watchdog.
 * The compatibility layer is specific to its three vendor ioctl call sites;
 * this is an emulation qualification tool, not a stock-device deployment tool.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include "../probes/watchdog-transfer.h"

static int watchdog_fd = -1;
static int linux_open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags); mode = va_arg(ap, unsigned int); va_end(ap);
    }
    if (strcmp(path, "/dev/jz_watchdog")) return open(path, flags, mode);
    /* Opening transfers ownership and starts/pings the standard watchdog.
     * Unlike the stock ABI, it can adopt an already-armed unowned counter. */
    const char *owner = getenv("Q2_WATCHDOG_SOCKET");
    watchdog_fd = owner ? q2_watchdog_claim(owner) : open("/dev/watchdog0", flags);
    return watchdog_fd;
}
static int linux_ioctl(int fd, unsigned long request, ...)
{
    if (fd != watchdog_fd) { errno = ENOTTY; return -1; }
    switch (request) {
    case 0x8004570du: {
        va_list ap;
        va_start(ap, request); int *ms = va_arg(ap, int *); va_end(ap);
        if (!ms || *ms != 5000) { errno = EINVAL; return -1; }
        struct watchdog_info info = {0};
        if (ioctl(fd, WDIOC_GETSUPPORT, &info) < 0) return -1;
        if (strncmp((char *)info.identity, "Ingenic X2000 watchdog", sizeof(info.identity)) ||
            !(info.options & WDIOF_SETTIMEOUT) || !(info.options & WDIOF_KEEPALIVEPING)) {
            errno = ENODEV; return -1;
        }
        int duplicate = open("/dev/watchdog0", O_RDWR | O_CLOEXEC);
        if (duplicate >= 0) { close(duplicate); errno = EBUSY; return -1; }
        if (errno != EBUSY) return -1;
        int timeout = *ms / 1000;
        if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) < 0) return -1;
        if (timeout != 5) { errno = ERANGE; return -1; }
        printf("LINUX_WATCHDOG identity=%s timeout=%d\n", info.identity, timeout);
        return 0;
    }
    case 0x2000570fu:
        return ioctl(fd, WDIOC_KEEPALIVE, NULL);
    case 0x2000570eu: {
        int option = WDIOS_DISABLECARD;
        /* nowayout correctly refuses this: the supervisor then leaves the
         * counter armed and waits for reset instead of claiming cleanup. */
        return ioctl(fd, WDIOC_SETOPTIONS, &option);
    }
    default: errno = ENOTTY; return -1;
    }
}
#define open linux_open
#define ioctl linux_ioctl
#define main supervisor_main
#include "trial.c"
#undef main
#undef ioctl
#undef open

int main(int argc, char **argv)
{
    if (!getenv("Q2_EMULATOR_TEST") || strcmp(getenv("Q2_EMULATOR_TEST"), "1")) {
        fputs("Use only in disposable QEMU with Q2_EMULATOR_TEST=1\n", stderr);
        return 2;
    }
    return supervisor_main(argc, argv);
}
