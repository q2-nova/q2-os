/* Minimal Q2 KMS bridge. All ioctl layouts come from the generated SDK UAPI. */
#define _GNU_SOURCE
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

struct buffer {
    uint32_t handle, fb, pitch;
    uint64_t size;
    uint8_t *map;
};
struct display {
    int fd, shown, enabled, pending;
    uint32_t connector, crtc;
    struct drm_mode_modeinfo mode;
    struct buffer buffers[2];
};

static int modeset(struct display *d, int on)
{
    struct drm_mode_crtc mode = { .crtc_id = d->crtc };
    if (on) {
        mode.fb_id = d->buffers[d->shown].fb;
        mode.set_connectors_ptr = (uintptr_t)&d->connector;
        mode.count_connectors = 1;
        mode.mode_valid = 1;
        mode.mode = d->mode;
    }
    if (ioctl(d->fd, DRM_IOCTL_MODE_SETCRTC, &mode) < 0) return -1;
    d->enabled = on;
    return 0;
}

void q2_display_close(struct display *d)
{
    if (!d) return;
    if (d->fd >= 0) {
        if (d->crtc) modeset(d, 0);
        for (int i = 0; i < 2; i++) {
            struct buffer *b = &d->buffers[i];
            if (b->map) munmap(b->map, b->size);
            if (b->fb) ioctl(d->fd, DRM_IOCTL_MODE_RMFB, &b->fb);
            if (b->handle) {
                struct drm_mode_destroy_dumb destroy = { .handle = b->handle };
                ioctl(d->fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
            }
        }
        close(d->fd);
    }
    free(d);
}

struct display *q2_display_open(void)
{
    struct display *d = calloc(1, sizeof(*d));
    if (!d) return NULL;
    d->fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC | O_NONBLOCK);
    if (d->fd < 0) goto failed;
    if (ioctl(d->fd, DRM_IOCTL_SET_MASTER, 0) < 0) goto failed;
    uint32_t crtcs[8], connectors[8];
    struct drm_mode_card_res res = {
        .crtc_id_ptr = (uintptr_t)crtcs, .connector_id_ptr = (uintptr_t)connectors,
        .count_crtcs = 8, .count_connectors = 8,
    };
    if (ioctl(d->fd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0) goto failed;
    if (res.count_crtcs != 1 || res.count_connectors != 1) { errno = ENODEV; goto failed; }
    d->crtc = crtcs[0]; d->connector = connectors[0];
    struct drm_mode_modeinfo modes[8];
    struct drm_mode_get_connector con = {
        .connector_id = d->connector, .modes_ptr = (uintptr_t)modes, .count_modes = 8,
    };
    if (ioctl(d->fd, DRM_IOCTL_MODE_GETCONNECTOR, &con) < 0) goto failed;
    if (con.connection != 1 || con.count_modes != 1 || modes[0].hdisplay != 320 || modes[0].vdisplay != 375) {
        errno = ENODEV; goto failed;
    }
    d->mode = modes[0];
    for (int i = 0; i < 2; i++) {
        struct buffer *b = &d->buffers[i];
        struct drm_mode_create_dumb create = { .width = 320, .height = 375, .bpp = 32 };
        if (ioctl(d->fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) < 0) goto failed;
        b->handle = create.handle; b->pitch = create.pitch; b->size = create.size;
        if (b->pitch < 320 * 4 || b->size < (uint64_t)b->pitch * 375 || b->size > SIZE_MAX) {
            errno = EINVAL; goto failed;
        }
        struct drm_mode_map_dumb map = { .handle = b->handle };
        if (ioctl(d->fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) goto failed;
        b->map = mmap(NULL, b->size, PROT_READ | PROT_WRITE, MAP_SHARED, d->fd, map.offset);
        if (b->map == MAP_FAILED) { b->map = NULL; goto failed; }
        memset(b->map, 0, b->size);
        struct drm_mode_fb_cmd2 fb = {
            .width = 320, .height = 375, .pixel_format = DRM_FORMAT_XRGB8888,
            .handles = {b->handle}, .pitches = {b->pitch},
        };
        if (ioctl(d->fd, DRM_IOCTL_MODE_ADDFB2, &fb) < 0) goto failed;
        b->fb = fb.fb_id;
    }
    if (modeset(d, 1) < 0) goto failed;
    return d;
failed: {
    int saved = errno;
    q2_display_close(d);
    errno = saved;
    return NULL;
}}

int q2_display_enable(struct display *d, int on)
{
    if (d->pending) { errno = EBUSY; return -1; }
    return modeset(d, !!on);
}

/* Borrowed nonblocking descriptor: callers may duplicate it for a reactor,
 * but only this display owner may consume its page-flip events. */
int q2_display_descriptor(struct display *d) { return d->fd; }

/* Copy/submit only. 0 is complete (disabled scanout), 1 needs readiness. */
int q2_display_begin(struct display *d, const uint32_t *pixels, unsigned count)
{
    if (count != 375 * 320) { errno = EINVAL; return -1; }
    if (d->pending) { errno = EBUSY; return -1; }
    int next = 1 - d->shown;
    struct buffer *b = &d->buffers[next];
    /* Increasing destination stores use the Q2 write-accelerated mapping.
     * Keep the same clockwise rotation, opaque alpha and row padding. */
    for (unsigned x = 0; x < 375; x++)
        for (unsigned column = 0; column < 320; column++)
            *(uint32_t *)(b->map + x * b->pitch + column * 4) =
                pixels[(319 - column) * 375 + x] | 0xff000000;
    if (!d->enabled) { d->shown = next; return 0; }
    struct drm_mode_crtc_page_flip flip = {
        .crtc_id = d->crtc, .fb_id = b->fb,
        .flags = DRM_MODE_PAGE_FLIP_EVENT, .user_data = (uint64_t)next + 1,
    };
    if (ioctl(d->fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip) < 0) return -1;
    d->pending = next + 1;
    return 1;
}

/* Never wait: EAGAIN rearms the caller's reactor. Pending ownership survives
 * cancellation and stale readiness; no new buffer can be submitted over it. */
int q2_display_finish(struct display *d)
{
    if (!d->pending) { errno = EINVAL; return -1; }
    struct drm_event_vblank event;
    ssize_t n;
    do { n = read(d->fd, &event, sizeof(event)); } while (n < 0 && errno == EINTR);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return -1;
    if (n != sizeof(event) || event.base.type != DRM_EVENT_FLIP_COMPLETE ||
            event.base.length != sizeof(event) || event.user_data != (uint64_t)d->pending) {
        errno = EIO; return -1;
    }
    d->shown = d->pending - 1;
    d->pending = 0;
    return 0;
}

/* Standalone C tools keep their synchronous interface. The async player uses
 * begin/finish and reactor readiness, never this poll wrapper. */
int q2_display_present(struct display *d, const uint32_t *pixels, unsigned count)
{
    int submitted = q2_display_begin(d, pixels, count);
    if (submitted <= 0) return submitted;
    struct pollfd pollfd = { .fd = d->fd, .events = POLLIN };
    int ready;
    do { ready = poll(&pollfd, 1, 2000); } while (ready < 0 && errno == EINTR);
    if (ready <= 0) { if (ready == 0) errno = ETIMEDOUT; return -1; }
    return q2_display_finish(d);
}
