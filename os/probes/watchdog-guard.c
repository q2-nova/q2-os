/* Explicit emulation-only boot policy. Never an unbounded keepalive daemon. */
#define _GNU_SOURCE
#include <linux/watchdog.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <time.h>
#include "watchdog-transfer.h"
#define SOCKET "/run/q2-wdt/owner.sock"
static long long milliseconds(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts)) _exit(2);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}
static void fail(const char *operation) { perror(operation); _exit(2); }
static void owner(int ready, int lease)
{
    /* Opening adopts an inherited watchdog or starts a fresh one. */
    int fd = open("/dev/watchdog0", O_RDWR | O_CLOEXEC);
    if (fd < 0) fail("watchdog open");
    struct watchdog_info info = {0};
    int timeout = 5;
    if (ioctl(fd, WDIOC_GETSUPPORT, &info) || strcmp((char *)info.identity, "Ingenic X2000 watchdog") ||
        ioctl(fd, WDIOC_SETTIMEOUT, &timeout) || timeout != 5 || ioctl(fd, WDIOC_KEEPALIVE, NULL))
        fail("watchdog configure");
    if (mkdir("/run/q2-wdt", 0700) && errno != EEXIST) fail("watchdog directory");
    int listener = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    strcpy(address.sun_path, SOCKET);
    /* Do not unlink an existing socket: a second owner must fail closed. */
    if (listener < 0 || bind(listener, (void *)&address, sizeof(address)) ||
        chmod(SOCKET, 0600) || listen(listener, 1)) fail("watchdog socket");
    FILE *pid = fopen("/run/q2-wdt/owner.pid", "w");
    if (!pid) fail("watchdog pid");
    fprintf(pid, "%d\n", getpid()); fclose(pid);
    printf("Q2_WDT_BOOT_OWNER pid=%d timeout=5 lease=%d\n", getpid(), lease); fflush(stdout);
    if (write(ready, "R", 1) != 1) fail("watchdog ready");
    close(ready);
    long long end = milliseconds() + lease * 1000;
    while (milliseconds() < end) {
        struct pollfd p = {.fd = listener, .events = POLLIN};
        int rc = poll(&p, 1, 250);
        if (rc < 0 && errno == EINTR) continue;
        if (rc < 0) fail("watchdog poll");
        if (rc > 0) {
            int peer = accept4(listener, NULL, NULL, SOCK_CLOEXEC);
            if (peer < 0) fail("watchdog accept");
            struct ucred cred; socklen_t length = sizeof(cred);
            if (getsockopt(peer, SOL_SOCKET, SO_PEERCRED, &cred, &length) || cred.uid != 0)
                fail("watchdog peer");
            /* Last boot-owner ping precedes the offer. Never feed again, even
             * if send/ACK fails. A vanished client must cause a reset. */
            if (ioctl(fd, WDIOC_KEEPALIVE, NULL)) fail("watchdog last ping");
            char byte = 'W', ancillary[CMSG_SPACE(sizeof(int))] = {0};
            struct iovec iov = {.iov_base = &byte, .iov_len = 1};
            struct msghdr message = {.msg_iov = &iov, .msg_iovlen = 1,
                .msg_control = ancillary, .msg_controllen = sizeof(ancillary)};
            struct cmsghdr *c = CMSG_FIRSTHDR(&message);
            c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof(int));
            memcpy(CMSG_DATA(c), &fd, sizeof(fd));
            if (sendmsg(peer, &message, MSG_NOSIGNAL) != 1) fail("watchdog offer");
            p.fd = peer; p.revents = 0;
            int ack = poll(&p, 1, 1000) == 1 && recv(peer, &byte, 1, MSG_DONTWAIT) == 1 && byte == 'A';
            printf("Q2_WDT_TRANSFER ack=%d recipient=%d\n", ack, cred.pid); fflush(stdout);
            close(peer); close(listener); unlink(SOCKET); close(fd);
            _exit(ack ? 0 : 3);
        }
        if (milliseconds() < end && ioctl(fd, WDIOC_KEEPALIVE, NULL)) fail("watchdog ping");
    }
    puts("Q2_WDT_BOOT_LEASE_EXPIRED"); fflush(stdout);
    close(listener); unlink(SOCKET); close(fd); /* No magic close/disarm. */
    _exit(3);
}
int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "handoff")) {
        int fd = q2_watchdog_claim(SOCKET), timeout = 15;
        if (fd < 0 || ioctl(fd, WDIOC_SETTIMEOUT, &timeout) || timeout != 15 || ioctl(fd, WDIOC_KEEPALIVE, NULL)) fail("handoff watchdog");
        /* CLOEXEC closes without magic disarm; the replacement must adopt it. */
        execlp("kexec", "kexec", "-e", (char *)NULL);
        fail("kexec execute");
    }
    if (argc == 2 && !strcmp(argv[1], "abandon")) {
        const char *test = getenv("Q2_EMULATOR_TEST");
        if (!test || strcmp(test, "1")) return 2;
        int peer = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
        struct sockaddr_un address = {.sun_family = AF_UNIX};
        strcpy(address.sun_path, SOCKET);
        if (peer < 0 || connect(peer, (void *)&address, sizeof(address))) fail("abandoned offer");
        sleep(2); /* Deliberately neither receive nor acknowledge the offer. */
        close(peer); puts("Q2_WDT_OFFER_ABANDONED"); return 0;
    }
    if ((argc == 3 || argc == 4) && !strcmp(argv[1], "claim")) {
        /* Synthetic recipient used by the emulator harness only. */
        const char *test = getenv("Q2_EMULATOR_TEST");
        if (!test || strcmp(test, "1")) return 2;
        int duration = atoi(argv[2]);
        if (duration < 0 || duration > 120) return 2;
        int fd = q2_watchdog_claim(SOCKET);
        if (fd < 0) fail("watchdog claim");
        int timeout = argc == 4 ? atoi(argv[3]) : 5;
        if (timeout < 1 || timeout > 15) fail("watchdog requested timeout");
        int requested = timeout;
        if (ioctl(fd, WDIOC_SETTIMEOUT, &timeout) || timeout != requested) fail("watchdog transfer timeout");
        printf("Q2_WDT_CLAIMED timeout=%d\n", timeout); fflush(stdout);
        long long end = milliseconds() + duration * 1000;
        while (milliseconds() < end) {
            if (ioctl(fd, WDIOC_KEEPALIVE, NULL)) fail("watchdog claimed ping");
            usleep(200000);
        }
        int option = WDIOS_DISABLECARD;
        if (ioctl(fd, WDIOC_SETOPTIONS, &option)) fail("watchdog disarm");
        close(fd); puts("Q2_WDT_DISARMED"); return 0;
    }
    if (argc != 2 || strcmp(argv[1], "boot")) return 2;
    char command[4096] = {0};
    FILE *f = fopen("/proc/cmdline", "r");
    if (!f || !fgets(command, sizeof(command), f)) return 2;
    fclose(f);
    int enabled = 1, hang = 0;
    if (strstr(command, "q2.diagnostic=1")) enabled = 0;
    for (char *word = strtok(command, " \n"); word; word = strtok(NULL, " \n")) {
        if (!strcmp(word, "q2.emulator-wdt=boot")) enabled = 1;
        if (!strcmp(word, "q2.emulator-wdt=hang")) enabled = hang = 1;
    }
    if (!enabled) return 0;
    int pipefd[2]; if (pipe2(pipefd, O_CLOEXEC)) return 2;
    pid_t child = fork(); if (child < 0) return 2;
    if (!child) { close(pipefd[0]); if (setsid() < 0) fail("setsid"); owner(pipefd[1], hang ? 3 : 30); }
    close(pipefd[1]);
    char ready = 0;
    struct pollfd p = {.fd = pipefd[0], .events = POLLIN};
    int ok = poll(&p, 1, 2000) == 1 && read(pipefd[0], &ready, 1) == 1 && ready == 'R';
    close(pipefd[0]);
    if (!ok) { fputs("Q2_WDT_STARTUP_FAILED\n", stderr); for (;;) pause(); }
    if (hang) { puts("Q2_WDT_INJECTED_INIT_HANG"); fflush(stdout); for (;;) pause(); }
    return 0;
}
