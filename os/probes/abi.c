#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#define _TIME_BITS 64
#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int constructor_ran;
__attribute__((constructor)) static void constructor(void) { constructor_ran = 1; }
static _Thread_local int tls_value;
static _Atomic unsigned counter;
static volatile sig_atomic_t signaled;
static void signal_handler(int sig) { (void)sig; signaled = 1; }
static void *worker(void *arg)
{
    int cpu = (int)(intptr_t)arg;
    cpu_set_t set;
    CPU_ZERO(&set); CPU_SET(cpu, &set);
    assert(pthread_setaffinity_np(pthread_self(), sizeof(set), &set) == 0);
    tls_value = cpu + 123;
    volatile double x = 1.25;
    for (unsigned i = 0; i < 10000; i++) {
        unsigned value = atomic_load(&counter);
        while (!atomic_compare_exchange_weak(&counter, &value, value + 1)) {}
        if (i == 5000) assert(pthread_kill(pthread_self(), SIGUSR1) == 0);
        assert(x * 2.0 == 2.5 && tls_value == cpu + 123);
    }
    return NULL;
}
int main(int argc, char **argv)
{
    assert(constructor_ran && argc >= 1 && argv[0]);
    assert(setenv("Q2_ABI_TEST", "synthetic", 1) == 0);
    assert(strcmp(getenv("Q2_ABI_TEST"), "synthetic") == 0);
    void *heap = malloc(12345);
    assert(heap); memset(heap, 0x5a, 12345); free(heap);
    _Static_assert(sizeof(void *) == 4 && sizeof(long) == 4, "requires o32");
    _Static_assert(sizeof(time_t) == 8 && sizeof(off_t) == 8, "time64/largefile");
    uint32_t nan_bits = 0x7fc00000;
    float nan;
    memcpy(&nan, &nan_bits, sizeof(nan));
    assert(isnan(nan));
    volatile double zero = -0.0, finite = 1.25;
    assert(signbit(zero) && !signbit(fabs(zero)) && finite * 2 == 2.5);
    assert(isinf(INFINITY) && isnan(NAN));
    assert(signal(SIGUSR1, signal_handler) != SIG_ERR);
    pthread_t a, b;
    tls_value = 99;
    assert(pthread_create(&a, NULL, worker, (void *)0) == 0);
    assert(pthread_create(&b, NULL, worker, (void *)1) == 0);
    assert(pthread_join(a, NULL) == 0 && pthread_join(b, NULL) == 0);
    assert(counter == 20000 && tls_value == 99);
    assert(signal(SIGUSR1, signal_handler) != SIG_ERR);
    assert(raise(SIGUSR1) == 0 && signaled);
    struct timespec before, after;
    assert(clock_gettime(CLOCK_MONOTONIC, &before) == 0);
    struct timespec delay = {.tv_nsec = 1000000};
    assert(nanosleep(&delay, NULL) == 0);
    assert(clock_gettime(CLOCK_MONOTONIC, &after) == 0);
    assert(after.tv_sec > before.tv_sec || (after.tv_sec == before.tv_sec && after.tv_nsec >= before.tv_nsec));
    time_t future = 2208988800LL;
    struct tm tm;
    assert(gmtime_r(&future, &tm) && tm.tm_year == 140);
    char path[] = "/tmp/q2-abi-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0 && ftruncate(fd, (off_t)3 * 1024 * 1024 * 1024) == 0);
    struct stat st;
    assert(fstat(fd, &st) == 0 && st.st_size == (off_t)3 * 1024 * 1024 * 1024);
    assert(close(fd) == 0 && unlink(path) == 0);
    void *lib = dlopen("libm.so.6", RTLD_NOW);
    assert(lib && dlsym(lib, "cos") && dlclose(lib) == 0);
    pid_t child = fork();
    assert(child >= 0);
    if (child == 0) _exit(37);
    int status;
    assert(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 37);
    puts("Q2_ABI_PASS o32 fp64 nan2008 pthread tls atomic32 signals time64 largefile dlopen fork");
    return 0;
}
