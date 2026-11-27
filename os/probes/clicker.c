/* Host or generated-SDK test of the exact evdev wire ABI used by the clicker. */
#include <assert.h>
#include <errno.h>
#include <linux/input.h>
#include <unistd.h>
#include "../platform/input.h"
int main(void)
{
    int fds[2];
    assert(pipe(fds) == 0);
    for (int on = 1; on >= 0; on--) {
        struct input_event event;
        assert(q2_clicker_set(fds[1], on) == 0);
        assert(read(fds[0], &event, sizeof(event)) == sizeof(event));
        assert(event.type == EV_SND && event.code == SND_BELL && event.value == on);
        assert(event.input_event_sec == 0 && event.input_event_usec == 0);
    }
    assert(q2_clicker_set(fds[1], 2) == -1 && errno == EINVAL);
    close(fds[0]);
    close(fds[1]);
    assert(q2_clicker_set(-1, 0) == -1 && errno == EBADF);
    return 0;
}
