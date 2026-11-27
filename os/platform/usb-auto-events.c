/* Event pulses for q2-usb-auto. No card/config access and no idle shell polling. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <glob.h>
#include <linux/netlink.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MAX_UDCS 8
static const char *sysroot = "/sys";
static const char *runroot = "/run/q2";
static int idle_ms = 30000, active_ms = 1000;

static void recheck(int signal_number) { (void)signal_number; }
static long long now_ms(void)
{
    struct timespec time;
    if (clock_gettime(CLOCK_MONOTONIC, &time) < 0) exit(1);
    return (long long)time.tv_sec * 1000 + time.tv_nsec / 1000000;
}

static bool relevant(const char *message, size_t length)
{
    size_t offset = 0;
    while (offset < length) {
        const char *end = memchr(message + offset, 0, length - offset);
        if (!end) return false;
        if (!strcmp(message + offset, "SUBSYSTEM=udc") ||
            !strcmp(message + offset, "SUBSYSTEM=power_supply")) return true;
        offset = (size_t)(end - message) + 1;
    }
    return false;
}

static int interval(void)
{
    char path[1024], phase[32] = {0};
    snprintf(path, sizeof(path), "%s/usb-storage/phase", runroot);
    FILE *file = fopen(path, "r");
    if (file) {
        if (!fgets(phase, sizeof(phase), file)) phase[0] = 0;
        fclose(file);
    }
    /* Configfs LUN empty-on-eject has no notification in this kernel. Only
       during an active ownership transition/export do we check it every 1 s. */
    if (!strcmp(phase, "armed\n") || !strcmp(phase, "exported\n")) return active_ms;
    snprintf(path, sizeof(path), "%s/usb-storage.lock", runroot);
    return access(path, F_OK) == 0 ? active_ms : idle_ms;
}

static void acknowledge(int fd)
{
    char state[64];
    if (lseek(fd, 0, SEEK_SET) < 0) return;
    ssize_t count;
    do {
        count = read(fd, state, sizeof(state));
    } while (count < 0 && errno == EINTR);
}

static int open_states(struct pollfd *fds)
{
    char pattern[1024];
    glob_t paths = {0};
    int count = 0;
    snprintf(pattern, sizeof(pattern), "%s/class/udc/*/state", sysroot);
    if (glob(pattern, 0, NULL, &paths) == 0) {
        for (size_t i = 0; i < paths.gl_pathc && count < MAX_UDCS; i++) {
            int fd = open(paths.gl_pathv[i], O_RDONLY | O_CLOEXEC | O_NONBLOCK);
            if (fd < 0) continue;
            acknowledge(fd);
            fds[count++] = (struct pollfd){ .fd = fd, .events = POLLPRI | POLLERR };
        }
    }
    globfree(&paths);
    return count;
}

static int event_socket(void)
{
    int fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC,
                    NETLINK_KOBJECT_UEVENT);
    if (fd < 0) return -1;
    struct sockaddr_nl address = { .nl_family = AF_NETLINK, .nl_groups = 1 };
    if (bind(fd, (struct sockaddr *)&address, sizeof(address)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static bool drain_events(int fd, bool fixture)
{
    bool changed = false;
    char message[8192];
    for (;;) {
        struct sockaddr_nl source = {0};
        socklen_t length = sizeof(source);
        ssize_t received = fixture ? read(fd, message, sizeof(message)) :
            recvfrom(fd, message, sizeof(message), 0, (struct sockaddr *)&source, &length);
        if (received <= 0) {
            if (received < 0 && errno == ENOBUFS) changed = true;
            break;
        }
        if ((fixture || source.nl_pid == 0) && relevant(message, (size_t)received)) changed = true;
    }
    return changed;
}

int main(int argc, char **argv)
{
    bool fixture = false;
    int events;
#ifdef USB_AUTO_TEST
    if (argc == 6) {
        fixture = true;
        sysroot = argv[1]; runroot = argv[2];
        idle_ms = atoi(argv[4]); active_ms = atoi(argv[5]);
        if (idle_ms < 1 || active_ms < 1) return 2;
        events = open(argv[3], O_RDWR | O_NONBLOCK | O_CLOEXEC);
    } else
#endif
    {
        (void)argv;
        if (argc != 1) return 2;
        events = event_socket();
    }
    /* Failed netlink availability retains the slower safety timer and UDC
       state notification. Report one fixed diagnostic, never event payloads. */
    if (events < 0) fputs("q2-usb-auto-events: uevents unavailable; using state/timer fallback\n", stderr);
    struct sigaction action = { .sa_handler = recheck };
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGUSR1, &action, NULL) < 0) return 1;
    bool first = true;
    long long deadline = now_ms() + interval();
    for (;;) {
        struct pollfd fds[MAX_UDCS + 1];
        int count = open_states(fds);
        int event_index = count;
        if (events >= 0) fds[count++] = (struct pollfd){ .fd = events, .events = POLLIN };
        /* Watchers exist before the first policy pulse: attach events arriving
           during shell actions stay queued rather than being lost on reopen. */
        if (first) {
            if (puts("ready") < 0 || fflush(stdout) == EOF) return 1;
            first = false;
        }
        long long now = now_ms(), next = now + interval();
        if (next < deadline) deadline = next;
        int remaining = deadline > now ? (int)(deadline - now) : 0;
        int ret = poll(fds, (nfds_t)count, remaining);
        bool changed = ret == 0;
        if (ret < 0 && errno != EINTR) return 1;
        if (ret > 0) {
            if (events >= 0 && (fds[event_index].revents & POLLIN)) changed |= drain_events(events, fixture);
            if (events >= 0 && (fds[event_index].revents & (POLLERR | POLLHUP | POLLNVAL))) {
                close(events); events = -1; changed = true;
                fputs("q2-usb-auto-events: event stream lost; using state/timer fallback\n", stderr);
            }
            for (int i = 0; i < event_index; i++) {
                if (fds[i].revents) { acknowledge(fds[i].fd); changed = true; }
            }
        }
        for (int i = 0; i < event_index; i++) close(fds[i].fd);
        if (changed) {
            if (puts("change") < 0 || fflush(stdout) == EOF) return 1;
            deadline = now_ms() + interval();
        }
    }
}
