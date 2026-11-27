/* Physical SMP coherence/affinity qualification; no device registers or files. */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define ROUNDS 200000U
#define WORDS 256U
static struct {
    unsigned int ready;
    uint32_t words[WORDS];
} shared;

static void pin(unsigned int cpu)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    if (sched_setaffinity(0, sizeof(set), &set) || sched_getcpu() != (int)cpu) {
        perror("CPU affinity");
        exit(1);
    }
}

static uint32_t value(unsigned int round, unsigned int word)
{
    return (round * 2654435761U) ^ (word * 2246822519U);
}

static void *consumer(void *unused)
{
    (void)unused;
    pin(1);
    for (unsigned int round = 1; round <= ROUNDS; ++round) {
        while (!__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE)) {}
        for (unsigned int word = 0; word < WORDS; ++word) {
            if (shared.words[word] != value(round, word)) {
                fprintf(stderr, "coherence failure: round=%u word=%u\n", round, word);
                exit(1);
            }
        }
        __atomic_store_n(&shared.ready, 0, __ATOMIC_RELEASE);
    }
    if (sched_getcpu() != 1) exit(1);
    return NULL;
}

int main(void)
{
    pthread_t thread;
    alarm(30);
    pin(0);
    if (pthread_create(&thread, NULL, consumer, NULL)) return 1;
    for (unsigned int round = 1; round <= ROUNDS; ++round) {
        while (__atomic_load_n(&shared.ready, __ATOMIC_ACQUIRE)) {}
        for (unsigned int word = 0; word < WORDS; ++word)
            shared.words[word] = value(round, word);
        __atomic_store_n(&shared.ready, 1, __ATOMIC_RELEASE);
    }
    if (pthread_join(thread, NULL) || sched_getcpu() != 0) return 1;
    alarm(0);
    printf("PASS: CPU0/CPU1 affinity, %u release/acquire transfers, %u words each\n",
           ROUNDS, WORDS);
    return 0;
}
