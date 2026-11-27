/* Host unit test of q2_headphones_state() in input.c, on generated EV_SW
 * capability and state bits from a fake ioctl. host-checks.yml runs it:
 *
 *   gcc -Wall -Wextra -Werror -o test-headphones os/platform/test_headphones.c
 */
#define ioctl headphone_test_ioctl
#include "input.c"
#undef ioctl
#include <assert.h>
#include <stdarg.h>

enum { FD = 42, BYTES = (SW_MAX + 8) / 8 };
static unsigned capabilities, plugged, calls;
static int fail_call;

/* Bit 0 stands for SW_HEADPHONE_INSERT and bit 1 for SW_LINEOUT_INSERT. */
static void set_bits(unsigned char *bits, unsigned value)
{
    memset(bits, 0, BYTES);
    if (value & 1) bits[SW_HEADPHONE_INSERT / 8] |= 1U << (SW_HEADPHONE_INSERT % 8);
    if (value & 2) bits[SW_LINEOUT_INSERT / 8] |= 1U << (SW_LINEOUT_INSERT % 8);
}

int headphone_test_ioctl(int fd, unsigned long request, ...)
{
    assert(fd == FD);
    calls++;
    if ((int)calls == fail_call) { errno = EIO; return -1; }
    va_list args;
    va_start(args, request);
    unsigned char *bits = va_arg(args, unsigned char *);
    va_end(args);
    if (request == EVIOCGBIT(EV_SW, BYTES)) set_bits(bits, capabilities);
    else {
        assert(request == EVIOCGSW(BYTES));
        set_bits(bits, plugged);
    }
    return 0;
}

int main(void)
{
    /* Both switches must be supported; then each plug state maps to its bits. */
    for (capabilities = 0; capabilities < 4; capabilities++) {
        for (plugged = 0; plugged < 4; plugged++) {
            calls = 0;
            errno = 0;
            int result = q2_headphones_state(FD);
            assert(result == (capabilities == 3 ? (int)plugged : -1));
            assert(calls == (capabilities == 3 ? 2U : 1U));
            if (capabilities != 3) assert(errno == ENODEV);
        }
    }
    /* A failing capability or state query reports its error. */
    capabilities = 3;
    for (fail_call = 1; fail_call <= 2; fail_call++) {
        calls = 0;
        assert(q2_headphones_state(FD) == -1);
        assert(errno == EIO);
    }
    puts("PASS: both EV_SW capabilities, four jack states, an unsupported device and ioctl failures");
    return 0;
}
