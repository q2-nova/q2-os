/* Generated-SDK standard RTC/time64 integration; synthetic disposable clocks. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/rtc.h>
#include <poll.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
_Static_assert(sizeof(time_t) == 8, "SDK must use time64");
int main(void)
{
    int fd = open("/dev/rtc0", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    assert(fd >= 0);
    int duplicate = open("/dev/rtc0", O_RDONLY);
    assert(duplicate < 0 && errno == EBUSY);
    struct rtc_time time = {.tm_year = 140, .tm_mon = 0, .tm_mday = 1};
    assert(ioctl(fd, RTC_SET_TIME, &time) == 0);
    struct rtc_time readback;
    assert(ioctl(fd, RTC_RD_TIME, &readback) == 0);
    assert(readback.tm_year == 140 && readback.tm_mon == 0 && readback.tm_mday == 1 && readback.tm_sec < 2);
    struct rtc_wkalrm alarm = {.enabled = 1, .time = time};
    alarm.time.tm_sec = 3;
    assert(ioctl(fd, RTC_WKALM_SET, &alarm) == 0);
    assert(ioctl(fd, RTC_WKALM_RD, &alarm) == 0 && alarm.enabled && alarm.time.tm_year == 140);
    struct pollfd p = {.fd = fd, .events = POLLIN};
    assert(poll(&p, 1, 6000) == 1);
    unsigned long events = 0;
    assert(read(fd, &events, sizeof(events)) == sizeof(events));
    assert((events & 0xff) == (RTC_AF | RTC_IRQF));
    assert(ioctl(fd, RTC_RD_TIME, &readback) == 0 && readback.tm_sec >= 3);
    alarm.time = readback; alarm.time.tm_sec += 2; alarm.enabled = 1;
    assert(ioctl(fd, RTC_WKALM_SET, &alarm) == 0);
    assert(ioctl(fd, RTC_AIE_OFF, 0) == 0);
    assert(poll(&p, 1, 2500) == 0);
    assert(read(fd, &events, sizeof(events)) < 0 && errno == EAGAIN);
    time.tm_year = 300;
    assert(ioctl(fd, RTC_SET_TIME, &time) < 0 && (errno == ERANGE || errno == EINVAL));
    close(fd);
    puts("Q2_RTC_TIME64_PASS post2038 alarm disabled-alarm range-check");
    return 0;
}
