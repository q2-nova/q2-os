/* Synthetic double-fork fixture. No /dev, /proc or shell background redirects. */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>

int main(void)
{
    int ready[2];
    if (pipe(ready)) return 2;
    pid_t first = fork();
    if (first < 0) return 2;
    if (first == 0) {
        close(ready[0]);
        if (setsid() < 0) _exit(2);
        pid_t second = fork();
        if (second < 0) _exit(2);
        if (second > 0) _exit(0);
        pid_t own = getpid();
        if (write(ready[1], &own, sizeof(own)) != sizeof(own)) _exit(2);
        close(ready[1]);
        for (;;) pause();
    }
    close(ready[1]);
    pid_t detached;
    ssize_t bytes;
    do { bytes = read(ready[0], &detached, sizeof(detached)); } while (bytes < 0 && errno == EINTR);
    close(ready[0]);
    int status;
    while (waitpid(first, &status, 0) < 0) if (errno != EINTR) return 2;
    if (bytes != sizeof(detached) || !WIFEXITED(status) || WEXITSTATUS(status) || detached <= 1) return 2;
    printf("DETACHED=%ld\n", (long)detached);
    return 0;
}
