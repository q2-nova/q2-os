/* Emulator-only adapter for the stock supervisor's vendor watchdog ioctls.
 * Exercises the actual QEMU TCU register model; this is NOT a Linux driver
 * or evidence that the stock vendor module works with the mainline kernel.
 * The supervisor included below is unchanged from repository d850518.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

static int watchdog_fd = -1;
static volatile uint32_t *registers;

static int fixture_open(const char *path, int flags, ...)
{
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list ap;
        va_start(ap, flags); mode = va_arg(ap, unsigned int); va_end(ap);
    }
    if (strcmp(path, "/dev/jz_watchdog")) return open(path, flags, mode);
    watchdog_fd = open("/dev/mem", O_RDWR | O_SYNC | O_CLOEXEC);
    if (watchdog_fd < 0) return -1;
    void *mapping = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED,
                         watchdog_fd, 0x10002000);
    if (mapping == MAP_FAILED) { close(watchdog_fd); return -1; }
    registers = mapping;
    return watchdog_fd;
}

static int fixture_ioctl(int fd, unsigned long request, ...)
{
    va_list ap;
    va_start(ap, request);
    /* Stop/feed pass literal integer zero, so only read the pointer for start. */
    void *argument = request == 0x8004570du ? va_arg(ap, void *) : NULL;
    va_end(ap);
    if (fd != watchdog_fd || !registers) { errno = ENOTTY; return -1; }
    switch (request) {
    case 0x8004570du:
        if (registers[1] & 1) { errno = EBUSY; return -1; }
        if (!argument || *(int *)argument != 5000) { errno = EINVAL; return -1; }
        registers[1] = 0;
        registers[3] = 2 | (5 << 3); /* RTC 32768 / 1024 = 32 Hz. */
        registers[0] = 160;          /* Five virtual seconds, not a host deadline. */
        registers[2] = 0;
        __sync_synchronize();
        registers[1] = 1;
        return 0;
    case 0x2000570fu:
        registers[2] = 0;
        __sync_synchronize();
        return 0;
    case 0x2000570eu:
        registers[1] = 0;
        __sync_synchronize();
        return 0;
    default: errno = ENOTTY; return -1;
    }
}

#define open fixture_open
#define ioctl fixture_ioctl
#define main supervisor_main
#include "../../q2/recovery/trial.c"
#undef main
#undef ioctl
#undef open

int main(int argc, char **argv)
{
    if (!getenv("Q2_EMULATOR_TEST") || strcmp(getenv("Q2_EMULATOR_TEST"), "1")) {
        fputs("Emulator test fixture only; set Q2_EMULATOR_TEST=1 in disposable QEMU\n", stderr);
        return 2;
    }
    return supervisor_main(argc, argv);
}
