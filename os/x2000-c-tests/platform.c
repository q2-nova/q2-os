/* os/x2000-c-build.py: libq2platform.so called on generated buffers, without
 * a DRM device. Built once with the SDK's GCC; each arm runs it with its own
 * library (LD_LIBRARY_PATH). "platform-display.h" is display.c's own struct
 * definitions, copied from the frozen source by os/x2000-c-build.py, so the
 * harness can hand q2_display_begin a display whose scanout is disabled: the
 * function then rotates into the next buffer and returns 0 with no ioctl.
 *
 *   platform rotate PITCH     two 375x320 frames on stdin; prints the two
 *                             return values and `shown` (int32 each), then
 *                             buffer 1 and buffer 0 with their padding and a
 *                             4096-byte tail (prefilled 0xc3 and 0x5a)
 *   platform api              error paths, the page-flip completion read from
 *                             a pipe, evdev reads and clicker writes through
 *                             pipes, q2_display_close; one line per call
 *   platform bench N TARGET...  N frames through q2_display_begin per TARGET:
 *                             `heap` (pitch 1280) or `dumb` (two DRM dumb
 *                             buffers of /dev/dri/card0, no modeset and no
 *                             master needed); prints the library's path, then
 *                             `rotate-TARGET HASH MICROSECONDS-PER-FRAME` */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>

#include "platform-display.h"
#include "input.h"

int q2_display_begin(struct display *d, const uint32_t *pixels, unsigned count);
int q2_display_finish(struct display *d);
int q2_display_present(struct display *d, const uint32_t *pixels, unsigned count);
int q2_display_enable(struct display *d, int on);
int q2_display_descriptor(struct display *d);
void q2_display_close(struct display *d);

enum { WIDTH = 375, HEIGHT = 320, PIXELS = WIDTH * HEIGHT, TAIL = 4096 };

static uint64_t fnv(const uint8_t *p, size_t n, uint64_t h)
{
	for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 1099511628211ULL;
	return h;
}

static void generate(uint32_t *frame, uint32_t seed)
{
	for (unsigned i = 0; i < PIXELS; i++) {
		seed = seed * 1103515245u + 12345u;
		frame[i] = seed ^ (seed >> 16) * 2654435761u;
	}
}

/* The rotation as display.c's comment states it, for the api checks. */
static int rotated(const struct buffer *b, const uint32_t *pixels)
{
	for (unsigned x = 0; x < WIDTH; x++)
		for (unsigned column = 0; column < HEIGHT; column++) {
			uint32_t v;
			memcpy(&v, b->map + x * b->pitch + column * 4, 4);
			if (v != (pixels[(HEIGHT - 1 - column) * WIDTH + x] | 0xff000000u)) return 0;
		}
	return 1;
}

static int readall(int fd, void *buffer, size_t n)
{
	for (size_t done = 0; done < n;) {
		ssize_t r = read(fd, (char *)buffer + done, n - done);
		if (r <= 0) return -1;
		done += r;
	}
	return 0;
}

static int rotate(unsigned pitch)
{
	if (pitch < HEIGHT * 4 || pitch % 4) return 2;
	uint32_t *frames = malloc(2 * PIXELS * 4);
	if (!frames || readall(0, frames, 2 * PIXELS * 4)) return 2;
	struct display d;
	memset(&d, 0, sizeof d);
	d.fd = -1;
	size_t size = (size_t)pitch * WIDTH + TAIL;
	for (int i = 0; i < 2; i++) {
		d.buffers[i].map = malloc(size);
		if (!d.buffers[i].map) return 2;
		memset(d.buffers[i].map, i ? 0xc3 : 0x5a, size);
		d.buffers[i].pitch = pitch;
		d.buffers[i].size = size;
	}
	int32_t header[4];
	header[0] = q2_display_begin(&d, frames, PIXELS);
	header[1] = d.shown;
	header[2] = q2_display_begin(&d, frames + PIXELS, PIXELS);
	header[3] = d.shown;
	if (fwrite(header, sizeof header, 1, stdout) != 1 || fwrite(d.buffers[1].map, size, 1, stdout) != 1 ||
	    fwrite(d.buffers[0].map, size, 1, stdout) != 1)
		return 2;
	return fflush(stdout) ? 2 : 0;
}

static void result(const char *name, int r, int error)
{
	if (r < 0) printf("%s: %d errno %d\n", name, r, error);
	else printf("%s: %d\n", name, r);
}

static int flip(struct display *d, int fd, uint32_t type, uint32_t length, uint64_t data, size_t bytes)
{
	struct drm_event_vblank event;
	memset(&event, 0, sizeof event);
	event.base.type = type;
	event.base.length = length;
	event.user_data = data;
	if (bytes && write(fd, &event, bytes) != (ssize_t)bytes) return -2;
	errno = 0;
	return q2_display_finish(d);
}

static void hex(const char *name, int fd, size_t n)
{
	unsigned char bytes[64];
	ssize_t r = read(fd, bytes, n < sizeof bytes ? n : sizeof bytes);
	printf("%s:", name);
	for (ssize_t i = 0; i < r; i++) printf(" %02x", bytes[i]);
	printf(" (%zd bytes)\n", r);
}

static int api(void)
{
	static uint32_t frame[PIXELS], second[PIXELS];
	generate(frame, 1);
	generate(second, 2);
	struct display *d = calloc(1, sizeof *d);
	if (!d) return 2;
	d->fd = -1;
	size_t size = (size_t)HEIGHT * 4 * WIDTH;
	for (int i = 0; i < 2; i++) {
		struct buffer *b = &d->buffers[i];
		b->map = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
		if (b->map == MAP_FAILED) return 2;
		memset(b->map, 0x11 * (i + 1), size);
		b->pitch = HEIGHT * 4;
		b->size = size;
	}
	uint64_t before = fnv(d->buffers[1].map, size, fnv(d->buffers[0].map, size, 14695981039346656037ULL));
	const unsigned counts[] = {0, PIXELS - 1, PIXELS + 1, 2 * PIXELS, (unsigned)-1};
	for (unsigned i = 0; i < sizeof counts / sizeof *counts; i++) {
		char name[48];
		snprintf(name, sizeof name, "begin count %u", counts[i]);
		errno = 0;
		int r = q2_display_begin(d, frame, counts[i]);
		result(name, r, errno);
	}
	d->pending = 2;
	errno = 0; int r = q2_display_begin(d, frame, PIXELS); result("begin while pending", r, errno);
	errno = 0; r = q2_display_enable(d, 1); result("enable 1 while pending", r, errno);
	errno = 0; r = q2_display_enable(d, 0); result("enable 0 while pending", r, errno);
	uint64_t after = fnv(d->buffers[1].map, size, fnv(d->buffers[0].map, size, 14695981039346656037ULL));
	printf("buffers unchanged by refused calls: %d; shown %d pending %d\n", before == after, d->shown, d->pending);
	d->pending = 0;
	errno = 0; r = q2_display_finish(d); result("finish with nothing pending", r, errno);

	errno = 0; r = q2_display_begin(d, frame, PIXELS);
	result("begin", r, errno);
	printf("shown %d pending %d rotated %d other buffer untouched %d\n", d->shown, d->pending,
	       rotated(&d->buffers[1], frame), d->buffers[0].map[0] == 0x11 && d->buffers[0].map[size - 1] == 0x11);
	errno = 0; r = q2_display_present(d, second, PIXELS);
	result("present (scanout disabled)", r, errno);
	printf("shown %d pending %d rotated %d first frame kept %d\n", d->shown, d->pending,
	       rotated(&d->buffers[0], second), rotated(&d->buffers[1], frame));
	errno = 0; r = q2_display_present(d, second, PIXELS - 1); result("present count 119999", r, errno);

	int pipes[2];
	if (pipe2(pipes, O_NONBLOCK | O_CLOEXEC)) return 2;
	d->fd = pipes[0];
	printf("descriptor is the device fd: %d\n", q2_display_descriptor(d) == pipes[0]);
	const struct { const char *name; int pending; uint32_t type, length; uint64_t data; size_t bytes; } flips[] = {
		{"finish, no event yet", 2, DRM_EVENT_FLIP_COMPLETE, sizeof(struct drm_event_vblank), 2, 0},
		{"finish, flip of buffer 1", 2, DRM_EVENT_FLIP_COMPLETE, sizeof(struct drm_event_vblank), 2, sizeof(struct drm_event_vblank)},
		{"finish, other user_data", 1, DRM_EVENT_FLIP_COMPLETE, sizeof(struct drm_event_vblank), 2, sizeof(struct drm_event_vblank)},
		{"finish, vblank event", 1, DRM_EVENT_VBLANK, sizeof(struct drm_event_vblank), 1, sizeof(struct drm_event_vblank)},
		{"finish, wrong length", 1, DRM_EVENT_FLIP_COMPLETE, 8, 1, sizeof(struct drm_event_vblank)},
		{"finish, short event", 1, DRM_EVENT_FLIP_COMPLETE, sizeof(struct drm_event_vblank), 1, 8},
		{"finish, flip of buffer 0", 1, DRM_EVENT_FLIP_COMPLETE, sizeof(struct drm_event_vblank), 1, sizeof(struct drm_event_vblank)},
	};
	for (unsigned i = 0; i < sizeof flips / sizeof *flips; i++) {
		d->pending = flips[i].pending;
		r = flip(d, pipes[1], flips[i].type, flips[i].length, flips[i].data, flips[i].bytes);
		int error = errno;
		result(flips[i].name, r, error);
		printf("  shown %d pending %d\n", d->shown, d->pending);
	}
	d->pending = 0;
	close(pipes[1]);
	/* Mapped buffers, no CRTC, framebuffer or dumb handle: close unmaps,
	 * closes the descriptor and frees. */
	q2_display_close(d);
	errno = 0;
	r = fcntl(pipes[0], F_GETFD);
	result("descriptor after q2_display_close", r, errno);
	q2_display_close(NULL);
	printf("q2_display_close(NULL) returned\n");

	printf("q2_input_event_size %u (harness %zu)\n", q2_input_event_size(), sizeof(struct input_event));
	printf("q2_input_bridge_size %u (harness %zu)\n", q2_input_bridge_size(), sizeof(struct q2_input_event));
	if (pipe2(pipes, O_NONBLOCK | O_CLOEXEC)) return 2;
	struct q2_input_event out;
	memset(&out, 0x77, sizeof out);
	errno = 0; r = q2_input_read(pipes[0], &out); result("q2_input_read, empty", r, errno);
	const struct { unsigned long seconds, microseconds; uint16_t type, code; int32_t value; } events[] = {
		{0, 0, EV_KEY, KEY_POWER, 1}, {1793404800, 999999, EV_KEY, KEY_NEXTSONG, 0},
		{0x7fffffff, 5, EV_ABS, ABS_X, 200}, {0x80000000ul, 6, EV_ABS, ABS_PRESSURE, -1},
		{0xfffffffful, 7, EV_SW, SW_HEADPHONE_INSERT, 1}, {12, 13, EV_SYN, SYN_REPORT, 0},
	};
	for (unsigned i = 0; i < sizeof events / sizeof *events; i++) {
		struct input_event event;
		memset(&event, 0, sizeof event);
		event.input_event_sec = events[i].seconds;
		event.input_event_usec = events[i].microseconds;
		event.type = events[i].type;
		event.code = events[i].code;
		event.value = events[i].value;
		if (write(pipes[1], &event, sizeof event) != sizeof event) return 2;
		memset(&out, 0x77, sizeof out);
		errno = 0;
		r = q2_input_read(pipes[0], &out);
		printf("q2_input_read: %d seconds %lld microseconds %d type %u code %u value %d\n", r,
		       (long long)out.seconds, out.microseconds, out.type, out.code, out.value);
	}
	if (write(pipes[1], "1234567", 7) != 7) return 2;
	errno = 0; r = q2_input_read(pipes[0], &out); result("q2_input_read, 7 bytes", r, errno);
	close(pipes[1]);
	errno = 0; r = q2_input_read(pipes[0], &out); result("q2_input_read, writer closed", r, errno);
	close(pipes[0]);

	if (pipe2(pipes, O_NONBLOCK | O_CLOEXEC)) return 2;
	const int sets[] = {1, 0, 2, -1};
	for (unsigned i = 0; i < sizeof sets / sizeof *sets; i++) {
		char name[48];
		snprintf(name, sizeof name, "q2_clicker_set %d", sets[i]);
		errno = 0; r = q2_clicker_set(pipes[1], sets[i]); result(name, r, errno);
		hex("  written", pipes[0], sizeof(struct input_event));
	}
	const int tones[] = {440, 0, 8000, 8001, -1};
	for (unsigned i = 0; i < sizeof tones / sizeof *tones; i++) {
		char name[48];
		snprintf(name, sizeof name, "q2_clicker_tone %d", tones[i]);
		errno = 0; r = q2_clicker_tone(pipes[1], tones[i]); result(name, r, errno);
		hex("  written", pipes[0], sizeof(struct input_event));
	}
	errno = 0; r = q2_clicker_set(pipes[0], 1); result("q2_clicker_set on the read end", r, errno);
	errno = 0; r = q2_clicker_tone(pipes[0], 440); result("q2_clicker_tone on the read end", r, errno);
	close(pipes[0]);
	close(pipes[1]);
	return fflush(stdout) ? 2 : 0;
}

static double now(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1e6 + t.tv_nsec / 1e3;
}

static int dumb(int fd, struct buffer *b)
{
	struct drm_mode_create_dumb create = { .width = HEIGHT, .height = WIDTH, .bpp = 32 };
	if (ioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &create) < 0) return -1;
	b->handle = create.handle; b->pitch = create.pitch; b->size = create.size;
	struct drm_mode_map_dumb map = { .handle = b->handle };
	if (ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &map) < 0) return -1;
	b->map = mmap(NULL, b->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, map.offset);
	if (b->map == MAP_FAILED) { b->map = NULL; return -1; }
	return 0;
}

static int bench(unsigned frames, const char *target)
{
	static uint32_t pixels[2][PIXELS];
	generate(pixels[0], 1);
	generate(pixels[1], 2);
	struct display d;
	memset(&d, 0, sizeof d);
	d.fd = -1;
	if (!strcmp(target, "heap")) {
		for (int i = 0; i < 2; i++) {
			d.buffers[i].pitch = HEIGHT * 4;
			d.buffers[i].size = (size_t)HEIGHT * 4 * WIDTH;
			d.buffers[i].map = aligned_alloc(4096, d.buffers[i].size);
			if (!d.buffers[i].map) return 2;
			memset(d.buffers[i].map, 0, d.buffers[i].size);
		}
	} else if (!strcmp(target, "dumb")) {
		d.fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
		if (d.fd < 0 || dumb(d.fd, &d.buffers[0]) || dumb(d.fd, &d.buffers[1])) {
			fprintf(stderr, "rotate-dumb: no dumb buffers: %s\n", strerror(errno));
			return 1;
		}
	} else {
		return 2;
	}
	for (int i = 0; i < 2; i++)
		if (q2_display_begin(&d, pixels[i], PIXELS)) return 2;
	double start = now();
	for (unsigned i = 0; i < frames; i++)
		if (q2_display_begin(&d, pixels[i & 1], PIXELS)) return 2;
	double elapsed = now() - start;
	uint64_t h = 14695981039346656037ULL;
	for (int i = 0; i < 2; i++)
		for (unsigned row = 0; row < WIDTH; row++)
			h = fnv(d.buffers[i].map + row * d.buffers[i].pitch, HEIGHT * 4, h);
	printf("rotate-%s %016llx %.1f\n", target, (unsigned long long)h, elapsed / frames);
	fflush(stdout);
	if (d.fd >= 0)
		for (int i = 0; i < 2; i++) {
			munmap(d.buffers[i].map, d.buffers[i].size);
			struct drm_mode_destroy_dumb destroy = { .handle = d.buffers[i].handle };
			ioctl(d.fd, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
		}
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 3 && !strcmp(argv[1], "rotate")) return rotate(strtoul(argv[2], NULL, 10));
	if (argc == 2 && !strcmp(argv[1], "api")) return api();
	if (argc >= 4 && !strcmp(argv[1], "bench")) {
		Dl_info info;
		void *begin = dlsym(RTLD_DEFAULT, "q2_display_begin");
		if (!begin || !dladdr(begin, &info) || !info.dli_fname) return 2;
		printf("library %s\n", info.dli_fname);
		unsigned frames = strtoul(argv[2], NULL, 10);
		if (!frames) return 2;
		int status = 0;
		for (int i = 3; i < argc; i++) {
			int r = bench(frames, argv[i]);
			if (r > status) status = r;
		}
		return status;
	}
	fprintf(stderr, "usage: platform rotate PITCH | api | bench FRAMES heap|dumb...\n");
	return 2;
}
