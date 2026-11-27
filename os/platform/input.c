/* Standard evdev bridge, compiled with the generated SDK's time64 UAPI. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdint.h>
#include <limits.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include "input.h"
static const unsigned codes[] = { KEY_POWER, KEY_UP, KEY_DOWN, KEY_PREVIOUSSONG, KEY_NEXTSONG };
static int touchscreen_registered(void)
{
    struct stat st;
    return stat("/sys/bus/i2c/drivers/shanling-q2-touchscreen", &st) == 0 ||
           (errno != ENOENT && errno != ENOTDIR);
}

static int input_named(int fd, const char *wanted)
{
    char name[128] = {0};
    int length = ioctl(fd, EVIOCGNAME(sizeof(name) - 1), name);
    if (length < 0) return 0;
    if (length > 0 && (size_t)length < sizeof(name) && name[length - 1] == '\0' && !strcmp(name, wanted))
        return 1;
    errno = ENODEV;
    return 0;
}
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
/* Sysfs is only a bounded discovery hint. The opened evdev descriptor still
 * supplies the authoritative name, initial state and selected clock. */
static int metadata_line(const char *path, char *buffer, size_t size, struct stat *identity)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 0;
    struct stat st;
    ssize_t length = -1;
    if (fstat(fd, &st) == 0 && S_ISREG(st.st_mode))
        length = read(fd, buffer, size - 1);
    close(fd);
    if (length <= 0 || (size_t)length >= size - 1 || buffer[length - 1] != '\n' ||
        memchr(buffer, '\0', (size_t)length) || memchr(buffer, '\n', (size_t)length - 1)) return 0;
    buffer[length - 1] = '\0';
    if (identity) *identity = st;
    return 1;
}
/* Opening evdev activates the panel. Require its neighbours' exact names
 * before opening during a fallback when this driver is registered. */
static int named_event(const char *entry, const char *wanted)
{
    char path[512], name[128];
    if (snprintf(path, sizeof(path), "/sys/class/input/%s/device/name", entry) >= (int)sizeof(path))
        return 0;
    return metadata_line(path, name, sizeof(name), NULL) && !strcmp(name, wanted);
}
static int decimal_number(const char *text, uint64_t *number)
{
    if (!*text || strspn(text, "0123456789") != strlen(text)) return 0;
    char *end;
    errno = 0;
    unsigned long long value = strtoull(text, &end, 10);
    if (errno || *end) return 0;
    *number = value;
    return 1;
}
static int input_epoch(uint64_t *epoch)
{
    char text[32];
    return metadata_line("/sys/kernel/uevent_seqnum", text, sizeof(text), NULL) && decimal_number(text, epoch);
}
static int same_node(const struct stat *left, const struct stat *right)
{
    return left->st_dev == right->st_dev && left->st_ino == right->st_ino && left->st_rdev == right->st_rdev;
}
/* Return unknown (-1), a proved current nonmatch (0), or a possible match (1).
 * Pair the sysfs object with the actual char node; event-number reuse must not
 * make an old name authoritative for a replacement. */
static int input_hint(const char *event, const char *wanted, struct stat *node)
{
    char path[512], namepath[512], devpath[512], name[128], number[32];
    struct stat before, after, name_identity, name_after;
    if (snprintf(path, sizeof(path), "/dev/input/%s", event) >= (int)sizeof(path) ||
        snprintf(namepath, sizeof(namepath), "/sys/class/input/%s/device/name", event) >= (int)sizeof(namepath) ||
        snprintf(devpath, sizeof(devpath), "/sys/class/input/%s/dev", event) >= (int)sizeof(devpath)) return -1;
    if (stat(path, &before) || !S_ISCHR(before.st_mode) ||
        !metadata_line(namepath, name, sizeof(name), &name_identity) ||
        !metadata_line(devpath, number, sizeof(number), NULL)) return -1;
    char *colon = strchr(number, ':');
    if (!colon || strchr(colon + 1, ':')) return -1;
    *colon++ = '\0';
    uint64_t major_id, minor_id;
    if (!decimal_number(number, &major_id) || !decimal_number(colon, &minor_id) ||
        major_id > UINT_MAX || minor_id > UINT_MAX || before.st_rdev != makedev((unsigned)major_id, (unsigned)minor_id)) return -1;
    if (stat(path, &after) || !same_node(&before, &after) || stat(namepath, &name_after) ||
        name_identity.st_dev != name_after.st_dev || name_identity.st_ino != name_after.st_ino) return -1;
    *node = after;
    return !strcmp(name, wanted);
}
static int input_scan(int kind, int monotonic, int hints, int *skipped, int *changed)
{
    const char *wanted = kind ? "hyn_sliderbar" : "Q2 buttons";
    DIR *directory = opendir("/dev/input");
    if (!directory) return -1;
    struct dirent *entry;
    int result = -1, saved = ENODEV, protect_panel = -1;
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "event", 5)) continue;
        char path[512];
        snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        struct stat hinted, opened;
        int hint = hints ? input_hint(entry->d_name, wanted, &hinted) : -1;
        if (hint == 0) { *skipped = 1; continue; }
        /* Keep the ioctl fallback on older kernels. A kernel with the
         * panel driver needs a matching name before we can safely open it. */
        if (hint < 0) {
            if (protect_panel < 0) protect_panel = touchscreen_registered();
            if (protect_panel && !named_event(entry->d_name, wanted)) continue;
        }
        int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        if (hint == 1 && (fstat(fd, &opened) || !same_node(&hinted, &opened))) {
            close(fd); *changed = 1; continue;
        }
        struct q2_input_state state;
        if (input_named(fd, wanted)) {
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
int q2_input_open(int kind, int monotonic)
{
    if (kind != 0 && kind != 1) { errno = EINVAL; return -1; }
    uint64_t before, after;
    int skipped = 0, changed = 0;
    if (input_epoch(&before)) {
        int result = input_scan(kind, monotonic, 1, &skipped, &changed);
        int saved = errno;
        int stable = input_epoch(&after) && before == after && !changed;
        errno = saved;
        if (stable && (result >= 0 || !skipped)) return result;
        if (result >= 0) close(result);
    }
    /* A stale/unavailable catalog or concurrent hotplug falls back once to the
     * original discovery order. No descriptor or catalog survives this call. */
    return input_scan(kind, monotonic, 0, &skipped, &changed);
}

int q2_touchscreen_state(int fd, struct q2_touchscreen_state *state)
{
    struct input_absinfo x, y, pressure;
    if (ioctl(fd, EVIOCGABS(ABS_X), &x) < 0 ||
        ioctl(fd, EVIOCGABS(ABS_Y), &y) < 0 ||
        ioctl(fd, EVIOCGABS(ABS_PRESSURE), &pressure) < 0) return -1;
    if (x.minimum != 0 || x.maximum != 319 || y.minimum != 0 || y.maximum != 374 ||
        pressure.minimum != 0 || pressure.maximum <= 0) {
        errno = ENODEV;
        return -1;
    }
    *state = (struct q2_touchscreen_state){ x.value, y.value, pressure.value };
    return 0;
}

int q2_touchscreen_open(const char *path)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -1;
    int clock = CLOCK_MONOTONIC;
    if (input_named(fd, "Q2 touchscreen") && ioctl(fd, EVIOCSCLOCKID, &clock) == 0) return fd;
    int saved = errno;
    close(fd);
    errno = saved;
    return -1;
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
    int protect_panel = touchscreen_registered();
    while ((entry = readdir(directory))) {
        if (strncmp(entry->d_name, "event", 5)) continue;
        if (protect_panel && !named_event(entry->d_name, "pwm-beeper")) continue;
        char path[512];
        snprintf(path, sizeof(path), "/dev/input/%s", entry->d_name);
        int fd = open(path, O_WRONLY | O_CLOEXEC);
        if (fd < 0) continue;
        unsigned char sounds[(SND_MAX + 8) / 8] = {0};
        if (input_named(fd, "pwm-beeper") &&
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
