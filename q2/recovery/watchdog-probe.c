/* Exact stock soc_watchdog.ko ABI, qualified separately from mainline WDIOC.
 * No flash or boot-selection writes. --expire deliberately kills this process
 * with the watchdog armed. The host runner must verify a new ready boot.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "--stop-test") &&
                     strcmp(argv[1], "--expire") && strcmp(argv[1], "--disarm")))
        return 2;
    int fd = open("/dev/jz_watchdog", O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("watchdog open"); return 1; }
    if (!strcmp(argv[1], "--disarm")) {
        int rc = ioctl(fd, 0x2000570eu, 0);
        close(fd);
        return rc < 0;
    }
    int milliseconds = 5000;
    if (ioctl(fd, 0x8004570du, &milliseconds) < 0) {
        perror("exclusive start"); close(fd); return 1;
    }
    puts("Q2_WATCHDOG_ARMED"); fflush(stdout);
    if (!strcmp(argv[1], "--expire")) {
        kill(getpid(), SIGKILL);
        _exit(3);
    }
    sleep(1);
    int fed = ioctl(fd, 0x2000570fu, 0);
    sleep(1);
    int stopped = ioctl(fd, 0x2000570eu, 0);
    close(fd);
    if (fed < 0 || stopped < 0) return 1;
    puts("Q2_WATCHDOG_STOPPED");
    return 0;
}
