/* Standard evdev bridge, compiled with the generated SDK's time64 UAPI. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include "input.h"
static const unsigned codes[] = { KEY_POWER, KEY_UP, KEY_DOWN, KEY_PREVIOUSSONG, KEY_NEXTSONG };
int q2_input_state(int fd, int kind, struct q2_input_state *state)
{
    memset(state, 0, sizeof(*state));
    if (kind == 1) {
        struct input_absinfo pos, pressure;
        if (ioctl(fd, EVIOCGABS(ABS_X), &pos) < 0 || ioctl(fd, EVIOCGABS(ABS_PRESSURE), &pressure) < 0) return -1;
        if (pos.minimum != 1 || pos.maximum != 200) { errno = ENODEV; return -1; }
        state->position = pos.value; state->touch = pressure.value != 0;
    } else {
        unsigned char keys[(KEY_MAX + 8) / 8] = {0};
        if (ioctl(fd, EVIOCGKEY(sizeof(keys)), keys) < 0) return -1;
        for (unsigned i = 0; i < sizeof(codes) / sizeof(codes[0]); i++)
            if (keys[codes[i] / 8] & (1U << (codes[i] % 8))) state->keys |= 1U << i;
    }
    return 0;
}
int q2_input_open(int kind, int monotonic)
{
    if (kind != 0 && kind != 1) { errno = EINVAL; return -1; }
    const char *wanted = kind ? "hyn_sliderbar" : "Q2 buttons";
    DIR *directory = opendir("/dev/input");
    if (!directory) return -1;
    struct dirent *entry;
    int result = -1, saved = ENODEV;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "event", 5)) continue;
        char path[512], name[128] = {0};
        snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        struct q2_input_state state;
        if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) > 0 && !strcmp(name, wanted)) {
            int clock = monotonic ? CLOCK_MONOTONIC : CLOCK_REALTIME;
            if (ioctl(fd, EVIOCSCLOCKID, &clock) == 0 && q2_input_state(fd, kind, &state) == 0) {
                result = fd; break;
            }
            saved = errno;
        }
        close(fd);
    }
    closedir(directory);
    if (result < 0) errno = saved;
    return result;
}
int q2_input_read(int fd, struct q2_input_event *out)
{
    struct input_event event;
    ssize_t count = read(fd, &event, sizeof(event));
    if (count < 0 && (errno == EAGAIN || errno == EINTR)) return 0;
    if (count != sizeof(event)) { if (count >= 0) errno = EIO; return -1; }
    memset(out, 0, sizeof(*out));
    /* Linux preserves 32-bit unsigned evdev seconds on time64 o32. Do not
       interpret a 2040 timestamp as signed time32 or libc's 64-bit timeval. */
    out->seconds = sizeof(event.input_event_sec) == 4 ? (int64_t)(uint32_t)event.input_event_sec : (int64_t)event.input_event_sec;
    out->microseconds = event.input_event_usec;
    out->type = event.type; out->code = event.code; out->value = event.value;
    return 1;
}
unsigned q2_input_event_size(void) { return sizeof(struct input_event); }
unsigned q2_input_bridge_size(void) { return sizeof(struct q2_input_event); }

int q2_headphones_state(int fd)
{
    unsigned char supported[(SW_MAX + 8) / 8] = {0};
    unsigned char state[sizeof(supported)] = {0};
    const unsigned codes[] = { SW_HEADPHONE_INSERT, SW_LINEOUT_INSERT };
    if (ioctl(fd, EVIOCGBIT(EV_SW, sizeof(supported)), supported) < 0) return -1;
    for (unsigned i = 0; i < 2; i++) {
        if (!(supported[codes[i] / 8] & (1U << (codes[i] % 8)))) {
            errno = ENODEV; return -1;
        }
    }
    if (ioctl(fd, EVIOCGSW(sizeof(state)), state) < 0) return -1;
    int result = 0;
    for (unsigned i = 0; i < 2; i++)
        if (state[codes[i] / 8] & (1U << (codes[i] % 8))) result |= 1U << i;
    return result;
}

/* Keep input_event's time32/time64 layout inside the SDK-built bridge. */
int q2_clicker_set(int fd, int on)
{
    if (on != 0 && on != 1) { errno = EINVAL; return -1; }
    struct input_event event = { .type = EV_SND, .code = SND_BELL, .value = on };
    ssize_t count;
    do { count = write(fd, &event, sizeof(event)); } while (count < 0 && errno == EINTR);
    if (count == sizeof(event)) return 0;
    if (count >= 0) errno = EIO;
    return -1;
}

int q2_clicker_tone(int fd, int hz)
{
    if (hz < 0 || hz > 8000) { errno = EINVAL; return -1; }
    struct input_event event = { .type = EV_SND, .code = SND_TONE, .value = hz };
    ssize_t count;
    do { count = write(fd, &event, sizeof(event)); } while (count < 0 && errno == EINTR);
    if (count == sizeof(event)) return 0;
    if (count >= 0) errno = EIO;
    return -1;
}

int q2_clicker_open(void)
{
    DIR *directory = opendir("/dev/input");
    if (!directory) return -1;
    struct dirent *entry;
    int result = -1, saved = ENODEV;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "event", 5)) continue;
        char path[512], name[128] = {0};
        snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        int fd = open(path, O_WRONLY | O_CLOEXEC);
        if (fd < 0) continue;
        unsigned char sounds[(SND_MAX + 8) / 8] = {0};
        if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) > 0 && !strcmp(name, "pwm-beeper") &&
            ioctl(fd, EVIOCGBIT(EV_SND, sizeof(sounds)), sounds) >= 0 &&
            (sounds[SND_BELL / 8] & (1U << (SND_BELL % 8)))) {
            if (q2_clicker_set(fd, 0) == 0) { result = fd; break; }
            saved = errno;
        }
        close(fd);
    }
    closedir(directory);
    if (result < 0) errno = saved;
    return result;
}
