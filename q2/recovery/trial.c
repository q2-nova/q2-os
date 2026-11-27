/* One-shot userspace pivot. The stock kernel/init/SSH stay alive.
 * stdin carries host heartbeats; EOF, lease expiry and deadline terminate
 * the candidate. Nothing installs a boot selection on persistent storage.
 * Trusted development images only: mount namespaces are not kernel isolation.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t stopped;
static void stop(int sig) { (void)sig; stopped = 1; }
static double now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}
static void die(const char *s) { perror(s); _exit(125); }

/* Adopt double-forked children, including those that leave the process group.
 * Called only by the supervisor, with the real /proc still mounted. */
static void kill_adopted(void) {
    DIR *dir = opendir("/proc");
    if (!dir) return;
    struct dirent *e;
    while ((e = readdir(dir))) {
        char *end;
        long pid = strtol(e->d_name, &end, 10);
        if (*end || pid <= 1 || pid == getpid()) continue;
        char path[80], line[256];
        snprintf(path, sizeof(path), "/proc/%ld/status", pid);
        FILE *f = fopen(path, "r");
        if (!f) continue;
        while (fgets(line, sizeof(line), f)) {
            long parent;
            if (sscanf(line, "PPid: %ld", &parent) == 1 && parent == getpid())
                kill((pid_t)pid, SIGKILL);
        }
        fclose(f);
    }
    closedir(dir);
}

static void enter_root(const char *root, int output, pid_t parent) {
    if (setsid() < 0 || prctl(PR_SET_PDEATHSIG, SIGKILL) < 0 || getppid() != parent)
        die("child lifetime");
    if (dup2(output, 1) < 0 || dup2(output, 2) < 0) die("output");
    close(output);
    int null = open("/dev/null", O_RDONLY);
    if (null < 0 || dup2(null, 0) < 0) die("stdin");
    if (null > 2) close(null);
    struct rlimit limit = {1024 * 1024, 1024 * 1024};
    if (setrlimit(RLIMIT_FSIZE, &limit)) die("log limit");
    if (unshare(CLONE_NEWNS)) die("mount namespace");
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL)) die("private mounts");
    if (mount(root, root, NULL, MS_BIND, NULL) || chdir(root)) die("root mount");
    if (syscall(SYS_pivot_root, ".", "oldroot")) die("pivot_root");
    if (chdir("/") || umount2("/oldroot", MNT_DETACH)) die("detach stock root");
    /* No host proc/sys/dev/card is exposed. Candidate changes disappear with
     * this private mount namespace and its RAM-backed staging directory. */
    if (mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID | MS_NODEV, "size=4m,mode=1777"))
        die("temporary files");
    char *argv[] = {"/bin/sh", "/init", NULL};
    char *env[] = {"PATH=/bin", "HOME=/", "Q2_TRIAL=1", NULL};
    execve(argv[0], argv, env);
    die("exec init");
}

int main(int argc, char **argv) {
    if (argc != 4 && argc != 5) {
        fputs("usage: trial ROOT DEADLINE_SECONDS LEASE_SECONDS [--jz-watchdog]\n", stderr);
        return 2;
    }
    char *end;
    long deadline = strtol(argv[2], &end, 10);
    if (*end || deadline < 2 || deadline > 600) return 2;
    long lease = strtol(argv[3], &end, 10);
    if (*end || lease < 2 || lease > 30 || lease > deadline) return 2;
    int hardware = argc == 5 && !strcmp(argv[4], "--jz-watchdog");
    if (argc == 5 && !hardware) return 2;
    struct stat st;
    char root[4096], path[4200];
    if (!realpath(argv[1], root) || !strcmp(root, "/")) return 2;
    snprintf(path, sizeof(path), "%s/init", root);
    if (lstat(path, &st) || !S_ISREG(st.st_mode)) return 2;
    snprintf(path, sizeof(path), "%s/oldroot", root);
    if (lstat(path, &st) || !S_ISDIR(st.st_mode)) return 2;
    snprintf(path, sizeof(path), "%s/../candidate.log", root);
    int output = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (output < 0) { perror("candidate log"); return 2; }
    struct sigaction action = {0};
    action.sa_handler = stop;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGHUP, &action, NULL);
    signal(SIGPIPE, SIG_IGN);
    if (prctl(PR_SET_CHILD_SUBREAPER, 1)) { perror("subreaper"); return 2; }
    int watchdog = -1;
    if (hardware) {
        /* Stock soc_watchdog.ko ABI, milliseconds, NOT Linux WDIOC_*. Opening
         * and closing are no-ops. Start refuses an already active watchdog.
         * Killing this supervisor therefore leaves the timeout armed. */
        watchdog = open("/dev/jz_watchdog", O_RDWR | O_CLOEXEC);
        int milliseconds = 5000;
        if (watchdog < 0 || ioctl(watchdog, 0x8004570du, &milliseconds) < 0) {
            perror("exclusive watchdog start"); return 2;
        }
    }
    pid_t parent = getpid(), child = fork();
    if (child < 0) {
        if (watchdog >= 0) ioctl(watchdog, 0x2000570eu, 0);
        perror("fork"); return 2;
    }
    if (!child) {
        if (watchdog >= 0) close(watchdog);
        enter_root(root, output, parent);
    }
    close(output);
    printf("START pid=%ld hardware_watchdog=%d\n", (long)child, hardware);
    fflush(stdout);
    double start = now(), heartbeat = start;
    int status = 0, result = 124, child_reaped = 0;
    const char *reason = "deadline";
    for (;;) {
        pid_t done = waitpid(child, &status, WNOHANG);
        if (done == child) {
            child_reaped = 1;
            result = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            reason = "candidate_exit"; break;
        }
        if (done < 0 && errno != EINTR) { reason = "wait_error"; break; }
        if (stopped) { reason = "signal"; break; }
        double t = now();
        if (t - start >= deadline) break;
        if (t - heartbeat >= lease) { reason = "host_lease"; break; }
        struct pollfd p = {.fd = 0, .events = POLLIN};
        int r = poll(&p, 1, 250);
        if (r < 0 && errno != EINTR) { reason = "poll_error"; break; }
        if (r > 0) {
            char data[64];
            ssize_t n = read(0, data, sizeof(data));
            if (n <= 0) { reason = "host_eof"; break; }
            for (ssize_t i = 0; i < n; ++i) if (data[i] == 'P') heartbeat = now();
        }
        if (watchdog >= 0 && ioctl(watchdog, 0x2000570fu, 0) < 0) {
            reason = "watchdog_feed"; break;
        }
    }
    if (!child_reaped) {
        kill(-child, SIGKILL);
        kill(child, SIGKILL);
    }
    double cleanup_end = now() + 2;
    int clean = 0;
    while (now() < cleanup_end) {
        kill_adopted();
        pid_t reaped;
        do { reaped = waitpid(-1, NULL, WNOHANG); } while (reaped > 0);
        if (reaped < 0 && errno == ECHILD) { clean = 1; break; }
        usleep(20000);
    }
    if (watchdog >= 0) {
        if (!clean || ioctl(watchdog, 0x2000570eu, 0) < 0) {
            puts("RESET_REQUIRED watchdog left armed"); fflush(stdout);
            for (;;) pause();
        }
        close(watchdog);
    }
    printf("END reason=%s clean=%d result=%d\n", reason, clean, result);
    return clean ? result : 125;
}
