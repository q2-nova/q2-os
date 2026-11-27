#define _GNU_SOURCE
#define _TIME_BITS 64
#define _FILE_OFFSET_BITS 64
#include <assert.h>
#include <poll.h>
#include <q2-input.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
int main(int argc, char **argv)
{
    _Static_assert(sizeof(time_t) == 8, "time64 SDK required");
    assert(argc == 3);
    int kind = atoi(argv[1]), count = atoi(argv[2]);
    assert((kind == 0 || kind == 1) && count > 0 && count < 10000);
    int fd = q2_input_open(kind, 0);
    assert(fd >= 0 && q2_input_event_size() == 16 && q2_input_bridge_size() == sizeof(struct q2_input_event));
    setbuf(stdout, NULL);
    printf("INPUT_READY wire=%u bridge=%u time_t=%u\n", q2_input_event_size(), q2_input_bridge_size(), (unsigned)sizeof(time_t));
    for (int i = 0; i < count;) {
        struct pollfd wait = {.fd = fd, .events = POLLIN};
        assert(poll(&wait, 1, 15000) > 0 && wait.revents == POLLIN);
        struct { uint64_t before; struct q2_input_event event; uint64_t after; } guarded;
        memset(&guarded, 0xa5, sizeof(guarded));
        int ret = q2_input_read(fd, &guarded.event);
        if (ret == 0) continue;
        assert(ret == 1 && guarded.before == UINT64_C(0xa5a5a5a5a5a5a5a5) && guarded.after == guarded.before);
        assert(guarded.event.seconds >= 2208988801LL && guarded.event.seconds < 2208988801LL + 600);
        assert(guarded.event.microseconds >= 0 && guarded.event.microseconds < 1000000);
        printf("EVENT %lld %d %u %u %d\n", (long long)guarded.event.seconds, guarded.event.microseconds,
                guarded.event.type, guarded.event.code, guarded.event.value);
        i++;
    }
    assert(close(fd) == 0);
    puts("INPUT_PASS");
    return 0;
}
