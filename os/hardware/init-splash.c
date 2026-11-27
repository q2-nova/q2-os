/* One-shot boot image on the kernel's existing DRM fbdev buffer.
 * No DRM master, persistent process, font/config access or minimum hold.
 * The player subsequently modesets its own buffers through normal KMS.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <linux/kd.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

static int layout(const struct fb_fix_screeninfo *f, const struct fb_var_screeninfo *v)
{
    return f->type == FB_TYPE_PACKED_PIXELS && f->visual == FB_VISUAL_TRUECOLOR &&
        v->xres == 320 && v->yres == 375 && v->xres_virtual == 320 &&
        v->yres_virtual == 375 && !v->xoffset && !v->yoffset &&
        v->bits_per_pixel == 32 && !v->nonstd && !v->grayscale &&
        v->red.offset == 16 && v->green.offset == 8 && v->blue.offset == 0 &&
        v->red.length == 8 && v->green.length == 8 && v->blue.length == 8 &&
        !v->red.msb_right && !v->green.msb_right && !v->blue.msb_right &&
        (!v->transp.length || (v->transp.offset == 24 && v->transp.length == 8 && !v->transp.msb_right)) &&
        f->line_length >= 1280 && f->line_length <= 4096 && !(f->line_length % 4) &&
        f->smem_len >= f->line_length * 375;
}

static int note(int x, int y)
{
    /* Same original paired-note geometry as the player, neutral on dark before
     * preferences are available. Logical landscape coordinates:375 by320. */
    x -= 375 / 2 - 24;
    y -= 320 / 2 - 46;
    for (int i = 0; i < 2; ++i) {
        int dx = x - i * 48, dy = y - (i ? 0 : 10);
        int cy = dy - 68;
        if (dx * dx + cy * cy <= 13 * 13) return 1;
        if (dx >= 6 && dx < 13 && dy >= 0 && dy < 68) return 1;
    }
    /* Integer sloping beam; bounds avoid signed division/rounding ambiguity. */
    return x >= 6 && x < 61 && (y * 55 + (x - 6) * 10) >= 550 &&
        (y * 55 + (x - 6) * 10) < 1265;
}

static void paint(uint8_t *pixels, uint32_t pitch)
{
    memset(pixels, 0, (size_t)pitch * 375);
    for (int row = 0; row < 375; ++row)
        for (int col = 0; col < 320; ++col) {
            /* Same clockwise scanout mapping as os/platform/display.c. */
            uint32_t pixel = note(row, 319 - col) ? 0xffc0c0c0 : 0xff101010;
            memcpy(pixels + (size_t)row * pitch + col * 4, &pixel, 4);
        }
}

int main(void)
{
    int fd = open("/dev/fb0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return 1;
    int status = 1, tty = -1, console_mode = KD_TEXT, changed_console = 0;
    struct stat st;
    struct fb_fix_screeninfo fixed;
    struct fb_var_screeninfo variable;
    uint8_t *pixels = NULL;
    if (fstat(fd, &st) || !S_ISCHR(st.st_mode) ||
        ioctl(fd, FBIOGET_FSCREENINFO, &fixed) ||
        ioctl(fd, FBIOGET_VSCREENINFO, &variable) || !layout(&fixed, &variable)) goto done;
    size_t size = (size_t)fixed.line_length * 375;
    pixels = malloc(size);
    if (!pixels) goto done;
    /* fbcon owns a live cursor even though boot logs use the serial port.
     * Its next redraw can overwrite our pixels. Keep the local VT graphical
     * for the graphical player; UART diagnostics are unaffected. */
    tty = open("/dev/tty0", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (tty < 0 || fstat(tty, &st) || !S_ISCHR(st.st_mode) ||
        ioctl(tty, KDGETMODE, &console_mode) ||
        (console_mode != KD_TEXT && console_mode != KD_GRAPHICS)) goto done;
    if (console_mode == KD_TEXT) {
        if (ioctl(tty, KDSETMODE, KD_GRAPHICS)) goto done;
        changed_console = 1;
    }
    paint(pixels, fixed.line_length);
    size_t offset = 0;
    while (offset < size) {
        ssize_t count = pwrite(fd, pixels + offset, size - offset, (off_t)offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0 || (size_t)count > size - offset) goto done;
        offset += (size_t)count;
    }
    /* No delay: this requests scanout; optical visibility is a separate metric.
     * Close only releases this file reference, not the kernel client's buffer. */
    if (ioctl(fd, FBIOBLANK, FB_BLANK_UNBLANK)) goto done;
    status = 0;
done:
    free(pixels);
    if (close(fd)) status = 1;
    if (tty >= 0) {
        if (status && changed_console) (void)ioctl(tty, KDSETMODE, console_mode);
        if (close(tty)) status = 1;
    }
    return status;
}
