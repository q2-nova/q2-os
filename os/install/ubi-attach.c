/* SPDX-License-Identifier: GPL-2.0-only
 * Attach only Q2's existing userdata UBI. No erase, volume-create or format.
 */
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <mtd/mtd-user.h>
#include <mtd/ubi-user.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int valid_ec_header(const unsigned char *header)
{
    uint32_t crc = 0xffffffff;
    if (memcmp(header, "UBI#", 4) || header[4] != 1) return 0;
    for (unsigned int i = 0; i < 60; i++) {
        crc ^= header[i];
        for (int bit = 0; bit < 8; bit++)
            crc = (crc >> 1) ^ (0xedb88320U & (0U - (crc & 1)));
    }
    uint32_t stored = (uint32_t)header[60] << 24 | (uint32_t)header[61] << 16 |
                      (uint32_t)header[62] << 8 | header[63];
    return crc == stored;
}

int main(void)
{
    FILE *f = fopen("/proc/mtd", "r");
    char line[256], name[128], path[128];
    unsigned int index, size, erase, found = 0, selected = 0;
    struct mtd_info_user info;
    struct ubi_attach_req request = { .ubi_num = UBI_DEV_NUM_AUTO };
    unsigned char header[64];
    int fd, control, valid = 0;
    if (!f) { perror("/proc/mtd"); return 1; }
    while (fgets(line, sizeof(line), f))
        if (sscanf(line, "mtd%u: %x %x \"%127[^\"]\"", &index, &size, &erase, name) == 4 &&
            !strcmp(name, "userdata")) { selected = index; found++; }
    fclose(f);
    if (found != 1) { fputs("expected one userdata partition\n", stderr); return 1; }
    snprintf(path, sizeof(path), "/sys/class/mtd/mtd%u/offset", selected);
    f = fopen(path, "r");
    unsigned long long offset;
    if (!f || fscanf(f, "%llu", &offset) != 1 || offset != 0x6440000) {
        if (f) fclose(f);
        fputs("userdata offset mismatch\n", stderr); return 1;
    }
    fclose(f);
    snprintf(path, sizeof(path), "/dev/mtd%u", selected);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0 || ioctl(fd, MEMGETINFO, &info) || info.type != MTD_NANDFLASH ||
        info.size != 0x18c0000 || info.erasesize != 0x20000 || info.writesize != 2048) {
        fputs("userdata geometry mismatch\n", stderr); if (fd >= 0) close(fd); return 1;
    }
    for (int64_t pos = 0; pos < info.size; pos += info.erasesize) {
        int bad = ioctl(fd, MEMGETBADBLOCK, &pos);
        if (bad < 0) { perror("bad-block query"); close(fd); return 1; }
        if (bad) continue;
        ssize_t n = pread(fd, header, sizeof(header), pos);
        if (n != (ssize_t)sizeof(header)) { perror("UBI header read"); close(fd); return 1; }
        if (valid_ec_header(header)) { valid = 1; break; }
    }
    close(fd);
    if (!valid) {
        fputs("no existing UBI header; refusing blank/unrecognized userdata\n", stderr); return 1;
    }
    control = open("/dev/ubi_ctrl", O_RDWR | O_CLOEXEC);
    if (control < 0) { perror("ubi_ctrl"); return 1; }
    request.mtd_num = selected;
    if (ioctl(control, UBI_IOCATT, &request)) { perror("UBI attach"); close(control); return 1; }
    close(control);
    printf("%d\n", request.ubi_num);
    return 0;
}
