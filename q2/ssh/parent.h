/* Close SSH connections and their commands when their server exits. This
 * also prevents a leftover shell using the card during stock USB storage.
 * The server must run with -F (no daemonising fork). */
#ifndef Q2_SSH_PARENT_H
#define Q2_SSH_PARENT_H
#include <unistd.h>
#include <signal.h>
#include <sys/prctl.h>
static inline void q2_ssh_bind_parent(void) {
    pid_t parent = getppid();
    if (parent == 1 || prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() != parent)
        _exit(1);
}
static inline pid_t q2_ssh_fork(void) {
    pid_t parent = getpid();
    pid_t child = fork();
    if (child == 0) {
        if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0 || getppid() != parent)
            _exit(1);
    }
    return child;
}
#define fork q2_ssh_fork
#endif
