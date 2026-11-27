/* Thrash guard: end sustained file-page thrash within seconds, with a record.
 *
 * The Q2 has 118 MB and a kernel without PSI or cgroups; pm-q2-perf-mem adds
 * 24 MiB of zram swap and MGLRU. When memory that cannot be reclaimed leaves about 12 MB available the
 * kernel does not run out of memory: it evicts and refaults program text
 * from NAND at about 100 major faults a second. On V0.122 and V0.123 the
 * whole Q2 stopped after 15 to 42 s of that, about once in 100 thrash
 * seconds, and the watchdog reset it with no record
 * (docs/bugs/thrash-watchdog-reset.md). Thrash must therefore not last.
 *
 * Trigger: MemAvailable below 20 MB and at least 30 major faults in the
 * second, 2 seconds running. In the 22 recorded blocks this never fires
 * when healthy (soaks at 23-35 MB with bursts to 72 faults, dropped caches
 * at 21-27 MB, 8 MB taken at 15-23 MB with at most 17 faults, 16 MB taken
 * soon after boot at 12-17 MB with at most 38) and fires within 3 thrash
 * seconds in every block that thrashed.
 *
 * Actions, one step up while the thrash goes on or returns before 60 calm
 * seconds:
 *   1  after 2 s: SIGKILL the Tailscale daemon (tailscaled or tsrsd),
 *      tailscaled being the busiest refaulter in every record;
 *      the player restarts it after 15 s. 5 s to recover.
 *   2  after 4 more thrash seconds in a row: SysRq f, the kernel's OOM
 *      killer: the largest process, normally the player (tailscaled dies
 *      with it; the launcher restarts both; a tailscaled already restarted
 *      goes first, see below). The watchdog owner and this guard are
 *      exempt. 6 s to recover.
 *   3  after 5 more in a row: sync and reboot(2). What is left holds memory no
 *      kill frees (tmpfs, the kernel), and a clean reboot is better than the
 *      stop that follows. At most 22 s of thrash from first sample to here.
 *   4  if the reboot has not happened after 30 s: SysRq s, u, b.
 * /run/q2/memory-guard.hold (a rig that fills memory on purpose) limits the
 * guard to steps 1 and 2.
 *
 * With zram swap (pm-q2-perf-mem) a swap-in is also a major fault, but it
 * costs a decompression, not a NAND read: the guard counts pgmajfault less
 * pswpin. MGLRU's min_ttl_ms can ask the kernel's OOM killer before the
 * guard does, so the Tailscale daemon's (tailscaled or tsrsd) oom_score_adj
 * is kept at 300 at all times (every 10 s; the player restarts it with a new PID).
 *
 * Every action goes to the kernel log (KERN_CRIT, so the ramoops console)
 * and to pmsg before it is taken. The guard locks its pages and keeps its
 * files open, so a sample is a few system calls on locked memory. */
#define _GNU_SOURCE
#include <dirent.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/reboot.h>
#include <time.h>
#include <unistd.h>

#define AVAILABLE_KB 20480
#define MAJOR_FAULTS 30
#define SECONDS 2
#define CALM_MS 60000
#define PREFER_SECONDS 10
enum { NOTHING, KILL_TAILSCALED, OOM_KILL, REBOOT, FORCE_REBOOT };
/* Thrash seconds in a row before each step, and the pause after it. A burst
 * of 2 or 3 s costs tailscaled only; the later steps need lasting thrash. */
static const int needed[] = {0, SECONDS, 4, 5, 5};
static const long long recovery_ms[] = {0, 5000, 6000, 30000, 30000};

struct guard { int streak, step; long long quiet_until, last_thrash; };

/* One sample a second. Returns the action to take now, or NOTHING. */
static int guard_step(struct guard *g, long available_kb, long major_faults, long long now_ms)
{
    if (available_kb >= AVAILABLE_KB || major_faults < MAJOR_FAULTS) {
        g->streak = 0;
        if (g->step && now_ms - g->last_thrash >= CALM_MS) g->step = 0;
        return NOTHING;
    }
    g->last_thrash = now_ms;
    if (now_ms < g->quiet_until) { g->streak = 0; return NOTHING; }
    int next = g->step < FORCE_REBOOT ? g->step + 1 : FORCE_REBOOT;
    if (++g->streak < needed[next]) return NOTHING;
    g->streak = 0;
    g->step = next;
    g->quiet_until = now_ms + recovery_ms[g->step];
    return g->step;
}

/* File-page major faults: all major faults less swap-ins. A kernel without
 * the pswpin line (-1) counts all. */
static long file_faults(long major, long swapins)
{
    return swapins < 0 ? major : major - swapins;
}

/* The value after `key` (with its separator) at the start of a line. */
static long field(const char *text, const char *key)
{
    size_t length = strlen(key);
    for (const char *line = text; line && *line; line = strchr(line, '\n'), line = line ? line + 1 : NULL)
        if (!strncmp(line, key, length)) return strtol(line + length, NULL, 10);
    return -1;
}

#ifndef Q2_MEMORY_GUARD_POLICY_ONLY
static int kmsg = -1, pmsg = -1;
static void journal(const char *format, ...)
{
    char line[200] = "<2>q2-memory-guard: "; int head = strlen(line);
    va_list arguments; va_start(arguments, format);
    int n = vsnprintf(line + head, sizeof(line) - head - 1, format, arguments);
    va_end(arguments);
    if (n < 0) return;
    if (n > (int)sizeof(line) - head - 2) n = sizeof(line) - head - 2;
    line[head + n] = '\n';
    if (kmsg >= 0 && write(kmsg, line, head + n + 1) < 0) {}
    if (pmsg >= 0 && write(pmsg, line + 3, head + n - 2) < 0) {}
}
/* For every process named `name`: SIGKILL it (kill_it) or make it the OOM
 * killer's first choice (oom_score_adj 300). Returns how many. procfs only. */
static int each_named(const char *name, int kill_it)
{
    int killed = 0;
    DIR *proc = opendir("/proc");
    if (!proc) return 0;
    for (struct dirent *entry; (entry = readdir(proc));) {
        if (entry->d_name[0] < '1' || entry->d_name[0] > '9') continue;
        char path[64], comm[32] = {0};
        snprintf(path, sizeof(path), "/proc/%.16s/comm", entry->d_name);
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        ssize_t size = read(fd, comm, sizeof(comm) - 1);
        close(fd);
        if (size <= 0) continue;
        comm[strcspn(comm, "\n")] = 0;
        if (strcmp(comm, name)) continue;
        if (kill_it) {
            if (!kill(atoi(entry->d_name), SIGKILL)) killed++;
            continue;
        }
        snprintf(path, sizeof(path), "/proc/%.16s/oom_score_adj", entry->d_name);
        fd = open(path, O_WRONLY | O_CLOEXEC);
        if (fd < 0) continue;
        if (write(fd, "300\n", 4) == 4) killed++;
        close(fd);
    }
    closedir(proc);
    return killed;
}
static char text[16384];
static int load(int fd)
{
    ssize_t size = pread(fd, text, sizeof(text) - 1, 0);
    if (size <= 0) return 0;
    text[size] = 0;
    return 1;
}
static long sample(int fd, const char *key)
{
    return load(fd) ? field(text, key) : -1;
}
/* File-page major faults so far, or -1. */
static long faults_now(int vmstat)
{
    if (!load(vmstat)) return -1;
    long major = field(text, "pgmajfault ");
    return major < 0 ? -1 : file_faults(major, field(text, "pswpin "));
}
static void pause_seconds(int seconds)
{
    struct timespec t = {.tv_sec = seconds};
    nanosleep(&t, NULL);
}
int main(void)
{
    int meminfo = open("/proc/meminfo", O_RDONLY | O_CLOEXEC), vmstat = open("/proc/vmstat", O_RDONLY | O_CLOEXEC);
    int sysrq = open("/proc/sysrq-trigger", O_WRONLY | O_CLOEXEC);
    if (meminfo < 0 || vmstat < 0 || sysrq < 0) { perror("q2-memory-guard"); return 2; }
    kmsg = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    pmsg = open("/dev/pmsg0", O_WRONLY | O_CLOEXEC);
    signal(SIGCHLD, SIG_IGN);
    int locked = mlockall(MCL_CURRENT | MCL_FUTURE) == 0;
    struct sched_param priority = {.sched_priority = 5};
    int realtime = sched_setscheduler(0, SCHED_FIFO, &priority) == 0;
    int adj = open("/proc/self/oom_score_adj", O_WRONLY | O_CLOEXEC), exempt = 0;
    if (adj >= 0) { exempt = write(adj, "-1000\n", 6) == 6; close(adj); }
    printf("Q2_MEMORY_GUARD_STARTED locked=%d realtime=%d oom_exempt=%d available_kb=%d major_faults=%d seconds=%d\n",
           locked, realtime, exempt, AVAILABLE_KB, MAJOR_FAULTS, SECONDS);
    fflush(stdout);
    struct guard g = {0};
    long previous = faults_now(vmstat);
    each_named("tailscaled", 0);
    each_named("tsrsd", 0);
    for (unsigned tick = 1;; tick++) {
        struct timespec t;
        pause_seconds(1);
        clock_gettime(CLOCK_MONOTONIC, &t);
        if (tick % PREFER_SECONDS == 0) { each_named("tailscaled", 0); each_named("tsrsd", 0); }
        long available = sample(meminfo, "MemAvailable:"), faults = faults_now(vmstat);
        if (available < 0 || faults < 0 || previous < 0) { previous = faults; continue; }
        long delta = faults - previous; previous = faults;
        int action = guard_step(&g, available, delta, t.tv_sec * 1000LL + t.tv_nsec / 1000000);
        if (action == NOTHING) continue;
        int hold = action >= REBOOT && !access("/run/q2/memory-guard.hold", F_OK);
        if (action == KILL_TAILSCALED) {
            int killed = each_named("tailscaled", 1) + each_named("tsrsd", 1);
            journal("thrash: %ld kB available, %ld major faults/s; step 1, killed %d Tailscale daemon(s)", available, delta, killed);
            if (killed) continue;
            action = g.step = OOM_KILL;   /* nothing to kill: go on at once */
        }
        if (action == OOM_KILL) {
            journal("thrash: %ld kB available, %ld major faults/s; step 2, asking the OOM killer", available, delta);
            if (write(sysrq, "f", 1) != 1) journal("SysRq f failed");
        } else if (hold) {
            journal("thrash persists: %ld kB available, %ld major faults/s; held, no reboot", available, delta);
            g.step = OOM_KILL;
        } else if (action == REBOOT) {
            journal("thrash persists: %ld kB available, %ld major faults/s; step 3, orderly reboot", available, delta);
            /* Not /sbin/reboot: without -f BusyBox only signals PID 1, a
             * shell on the installed Q2 that ignores it (q2-safe-poweroff
             * says the same of poweroff). The child syncs and asks the kernel,
             * which shuts the devices down; the guard goes on sampling, so
             * step 4 follows if the sync never returns. */
            if (!fork()) {
                struct sched_param normal = {0};
                sched_setscheduler(0, SCHED_OTHER, &normal);
                sync();
                reboot(RB_AUTOBOOT);
                _exit(127);
            }
        } else {
            journal("thrash persists and the reboot did not happen; step 4, SysRq s u b");
            if (write(sysrq, "s", 1) != 1) {}
            pause_seconds(2);
            if (write(sysrq, "u", 1) != 1) {}
            pause_seconds(2);
            if (write(sysrq, "b", 1) != 1) {}
        }
    }
}
#endif
