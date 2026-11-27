/* Q2 recovery updater. Only apply may write the exact ota partition.
 * All image writes are performed later by the preserved stock recovery.
 * No shell evaluation of filenames, manifests or archive contents.
 */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <mntent.h>
#include <mtd/mtd-user.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define KMAX (6U * 1024 * 1024)
#define RMAX (75U * 1024 * 1024)
#define OTASIZE 0x20000U
#define OTAOFFSET 0x6420000U
struct member {
    off_t offset;
    size_t size;
    char md5[33], sha[65];
};
struct package {
    int fd;
    off_t size;
    struct member m[3];
    char version[6];
};
static const char *names[] = {"firmware_v20.info", "recovery-update/xImage",
                              "recovery-update/rootfs.squashfs"};
static const size_t limits[] = {512, KMAX, RMAX};
static void fail(const char *s) {
    fprintf(stderr, "q2-hw-update: %s%s%s\n", s, errno ? ": " : "", errno ? strerror(errno) : "");
    exit(1);
}
static void require(int ok, const char *s) {
    if (!ok) {
        errno = 0;
        fail(s);
    }
}
static int open_ro(const char *p) {
    int f = open(p, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (f < 0)
        fail(p);
    struct stat st;
    if (fstat(f, &st))
        fail("stat");
    require(S_ISREG(st.st_mode), "expected regular file");
    return f;
}
static void read_at(int fd, void *b, size_t n, off_t pos) {
    unsigned char *p = b;
    while (n) {
        ssize_t r = pread(fd, p, n, pos);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            fail("short read");
        p += r;
        n -= r;
        pos += r;
    }
}
static void write_all(int f, const void *b, size_t n) {
    const unsigned char *p = b;
    while (n) {
        ssize_t r = write(f, p, n);
        if (r < 0 && errno == EINTR)
            continue;
        if (r <= 0)
            fail("write");
        n -= r;
        p += r;
    }
}
static void syncfd(int f) {
    if (fsync(f))
        fail("fsync");
}
static off_t length(int f) {
    struct stat st;
    if (fstat(f, &st))
        fail("stat");
    return st.st_size;
}
static int hex64(const char *s) {
    if (strlen(s) != 64)
        return 0;
    for (int i = 0; i < 64; i++)
        if (!strchr("0123456789abcdef", s[i]))
            return 0;
    return 1;
}
static uint32_t be32(const unsigned char *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static uint64_t le(const unsigned char *p, int n) {
    uint64_t v = 0;
    while (n--)
        v = v * 256 + p[n];
    return v;
}
static uint32_t crc_part(uint32_t c, const unsigned char *b, size_t n) {
    while (n--) {
        c ^= *b++;
        for (int j = 0; j < 8; j++)
            c = (c >> 1) ^ (0xedb88320U & -(c & 1));
    }
    return c;
}
static uint32_t crc_range(int f, off_t o, size_t n) {
    unsigned char b[8192];
    uint32_t c = ~0U;
    while (n) {
        size_t k = n < sizeof b ? n : sizeof b;
        read_at(f, b, k, o);
        c = crc_part(c, b, k);
        o += k;
        n -= k;
    }
    return ~c;
}
static void hash_range(int fd, off_t off, off_t n, const char *alg, char *out) {
    int in[2], result[2];
    if (pipe(in) || pipe(result))
        fail("pipe");
    pid_t pid = fork();
    if (pid < 0)
        fail("fork");
    if (!pid) {
        dup2(in[0], 0);
        dup2(result[1], 1);
        close(in[0]);
        close(in[1]);
        close(result[0]);
        close(result[1]);
#ifdef Q2_UPDATE_TEST
        char path[64];
        snprintf(path, sizeof path, "/usr/bin/%s", alg);
        execl(path, alg, (char *)NULL);
#else
        execl("/bin/busybox", "busybox", alg, (char *)NULL);
#endif
        _exit(127);
    }
    close(in[0]);
    close(result[1]);
    unsigned char b[8192];
    while (n) {
        size_t k = n < (off_t)sizeof b ? (size_t)n : sizeof b;
        read_at(fd, b, k, off);
        write_all(in[1], b, k);
        off += k;
        n -= k;
    }
    close(in[1]);
    char line[128];
    size_t used = 0;
    ssize_t r;
    while ((r = read(result[0], line + used, sizeof(line) - used - 1)) > 0) {
        used += r;
        if (used == sizeof(line) - 1)
            break;
    }
    close(result[0]);
    int st;
    if (waitpid(pid, &st, 0) < 0)
        fail("wait");
    require(WIFEXITED(st) && WEXITSTATUS(st) == 0, "hash command failed");
    size_t digits = !strcmp(alg, "sha256sum") ? 64 : 32;
    require(used > digits && line[digits] == ' ', "invalid hash output");
    memcpy(out, line, digits);
    out[digits] = 0;
}
static unsigned long octal(const unsigned char *p, size_t n) {
    unsigned long v = 0;
    int end = 0, seen = 0;
    for (size_t i = 0; i < n; i++) {
        if (p[i] == ' ' || p[i] == 0) {
            if (seen)
                end = 1;
            continue;
        }
        require(!end && p[i] >= '0' && p[i] <= '7', "invalid tar number");
        require(v < ULONG_MAX / 8 - 8, "tar number overflow");
        v = v * 8 + p[i] - '0';
        seen = 1;
    }
    return v;
}
static int zeros(const unsigned char *p, size_t n) {
    while (n--)
        if (*p++)
            return 0;
    return 1;
}
static struct package inspect(const char *path, const char *expected) {
    require(hex64(expected), "expected a trusted lowercase SHA-256 digest");
    struct package p = {.fd = open_ro(path)};
    p.size = length(p.fd);
    require(p.size >= 1024 && p.size <= KMAX + RMAX + 65536 && p.size % 512 == 0, "archive size");
    char hash[65];
    hash_range(p.fd, 0, p.size, "sha256sum", hash);
    require(!strcmp(hash, expected), "package SHA-256 mismatch");
    unsigned char h[512];
    off_t pos = 0;
    int seen = 0, dir = 0, ended = 0;
    while (pos < p.size) {
        read_at(p.fd, h, 512, pos);
        pos += 512;
        if (zeros(h, 512)) {
            require(pos + 512 <= p.size, "missing tar end block");
            while (pos < p.size) {
                read_at(p.fd, h, 512, pos);
                require(zeros(h, 512), "data after tar end");
                pos += 512;
            }
            ended = 1;
            break;
        }
        unsigned long sum = 0, want = octal(h + 148, 8);
        for (int j = 0; j < 512; j++)
            sum += (j >= 148 && j < 156) ? ' ' : h[j];
        require(sum == want, "tar header checksum");
        require(memchr(h, 0, 100) != NULL && h[345] == 0, "tar path/prefix");
        size_t n = octal(h + 124, 12);
        require(n <= RMAX && pos + (off_t)((n + 511) & ~511U) <= p.size, "tar member size");
        if (h[156] == '5' &&
            (!strcmp((char *)h, "recovery-update") || !strcmp((char *)h, "recovery-update/"))) {
            require(!dir++ && n == 0, "duplicate directory");
            continue;
        }
        require(h[156] == 0 || h[156] == '0', "non-regular archive entry");
        int k;
        for (k = 0; k < 3; k++)
            if (!strcmp((char *)h, names[k]))
                break;
        require(k < 3 && !(seen & (1 << k)) && n > 0 && n <= limits[k],
                "unexpected/duplicate/oversized archive member");
        seen |= 1 << k;
        p.m[k].offset = pos;
        p.m[k].size = n;
        pos += (n + 511) & ~511U;
    }
    require(ended && seen == 7, "incomplete archive");
    for (int k = 1; k < 3; k++) {
        hash_range(p.fd, p.m[k].offset, p.m[k].size, "md5sum", p.m[k].md5);
        hash_range(p.fd, p.m[k].offset, p.m[k].size, "sha256sum", p.m[k].sha);
    }
    char info[513] = {0}, want[513];
    read_at(p.fd, info, p.m[0].size, p.m[0].offset);
    require(p.m[0].size >= 17 && !memcmp(info, "Shanling Q2\nV", 13), "model/version");
    memcpy(p.version, info + 12, 5); /* model plus newline: 12 bytes */
    for (int j = 1; j < 5; j++)
        require(isalnum((unsigned char)p.version[j]) || p.version[j] == '.', "version tag");
    snprintf(want, sizeof want, "Shanling Q2\n%s\n%s  %s\n%s  %s\n", p.version, p.m[1].md5,
             names[1], p.m[2].md5, names[2]);
    require(strlen(want) == p.m[0].size && !memcmp(want, info, p.m[0].size),
            "manifest checksums/format");
    unsigned char u[64];
    require(p.m[1].size > 64, "short kernel");
    read_at(p.fd, u, 64, p.m[1].offset);
    uint32_t hc = be32(u + 4);
    memset(u + 4, 0, 4);
    uint64_t load = be32(u + 16), entry = be32(u + 20), size = be32(u + 12);
    require(be32(u) == 0x27051956 && ~crc_part(~0U, u, 64) == hc, "uImage header CRC");
    require(size == p.m[1].size - 64 && crc_range(p.fd, p.m[1].offset + 64, size) == be32(u + 24),
            "uImage payload CRC/size");
    require(u[28] == 5 && u[29] == 5 && u[30] == 2 && u[31] == 0,
            "SPL requires executable MIPS kernel, no loader compression");
    require(load >= 0x80000000 && load + size <= 0x88000000 && entry >= load &&
                entry < load + size && !(entry % 4) && !(load % 4),
            "uImage RAM/entry bounds");
    unsigned char sq[96];
    require(p.m[2].size >= 96, "short rootfs");
    read_at(p.fd, sq, 96, p.m[2].offset);
    require(!memcmp(sq, "hsqs", 4) && le(sq + 28, 2) == 4 && le(sq + 30, 2) == 0 &&
                le(sq + 40, 8) >= 96 && le(sq + 40, 8) <= p.m[2].size,
            "squashfs header/bounds");
    return p;
}
static void join(char *out, const char *a, const char *b) {
    require(snprintf(out, PATH_MAX, "%s/%s", a, b) < PATH_MAX, "path too long");
}
static int dirfd_safe(const char *p) {
    int f = open(p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (f < 0)
        fail(p);
    return f;
}
static void mkdir_safe(const char *p) {
    if (mkdir(p, 0700) && errno != EEXIST)
        fail(p);
    int f = dirfd_safe(p);
    close(f);
}
static int exists(const char *p) {
    struct stat st;
    if (lstat(p, &st) == 0)
        return 1;
    if (errno == ENOENT)
        return 0;
    fail(p);
    return 0;
}
static void syncdir(const char *p) {
    int f = dirfd_safe(p);
    syncfd(f);
    close(f);
}
static void card_check(const char *card) {
    int f = dirfd_safe(card);
    close(f);
#ifndef Q2_UPDATE_TEST
    char resolved[PATH_MAX];
    require(realpath(card, resolved) != NULL && !strcmp(card, resolved),
            "use canonical card mount path");
    FILE *t = setmntent("/proc/mounts", "r");
    if (!t)
        fail("mount table");
    struct mntent *m;
    int found = 0;
    while ((m = getmntent(t)))
        if (!strcmp(m->mnt_dir, card) && (!strcmp(m->mnt_type, "vfat") || !strcmp(m->mnt_type, "exfat")) &&
            !strncmp(m->mnt_fsname, "/dev/mmcblk", 11) && hasmntopt(m, "rw"))
            found = 1;
    endmntent(t);
    require(found, "card must be a mounted read-write MMC FAT/exFAT filesystem");
#endif
}
static void copy_range(int f, off_t off, size_t n, const char *to) {
    int out = open(to, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (out < 0)
        fail(to);
    unsigned char b[8192];
    while (n) {
        size_t k = n < sizeof b ? n : sizeof b;
        read_at(f, b, k, off);
        write_all(out, b, k);
        n -= k;
        off += k;
    }
    syncfd(out);
    if (close(out))
        fail("close");
}
static void verify_payload(const char *folder, const struct package *p) {
    for (int k = 1; k < 3; k++) {
        char path[PATH_MAX], hash[65];
        join(path, folder, k == 1 ? "xImage" : "rootfs.squashfs");
        int f = open_ro(path);
        require(length(f) == (off_t)p->m[k].size, "staged size mismatch");
        hash_range(f, 0, p->m[k].size, "sha256sum", hash);
        syncfd(f);
        close(f);
        require(!strcmp(hash, p->m[k].sha), "staged SHA-256 mismatch");
    }
    DIR *d = opendir(folder);
    if (!d)
        fail(folder);
    struct dirent *e;
    int count = 0;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
            continue;
        require(!strcmp(e->d_name, "xImage") || !strcmp(e->d_name, "rootfs.squashfs"),
                "unexpected recovery file");
        count++;
    }
    closedir(d);
    require(count == 2, "missing recovery file");
}
#ifndef Q2_UPDATE_TEST
static unsigned long sysnum(const char *base, const char *name) {
    char path[PATH_MAX], b[64] = {0};
    join(path, base, name);
    int f = open(path, O_RDONLY | O_CLOEXEC);
    if (f < 0)
        fail(path);
    ssize_t n = read(f, b, sizeof b - 1);
    close(f);
    require(n > 0, "MTD attribute");
    return strtoul(b, NULL, 0);
}
#endif
static void selector_page(unsigned char page[2048]) {
    memset(page, 255, 2048);
    memset(page, ' ', 256);
    memcpy(page, "ota:kernel2", sizeof("ota:kernel2") - 1);
}
static void arm(const char *card, int commit) {
#ifdef Q2_UPDATE_TEST
    if (!commit)
        return;
    /* Test binary only. Production has no environment bypass or fake-device path. */
    char path[PATH_MAX];
    join(path, card, "TEST-selector-written");
    int f = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (f < 0)
        fail(path);
    unsigned char page[2048];
    selector_page(page);
    write_all(f, page, sizeof page);
    syncfd(f);
    close(f);
    puts("TEST: selector and reboot simulated");
#else
    (void)card;
    DIR *d = opendir("/sys/class/mtd");
    if (!d)
        fail("MTD not available; update transport is not enabled");
    struct dirent *e;
    char dev[PATH_MAX] = {0};
    int count = 0;
    while ((e = readdir(d))) {
        unsigned id;
        char rest;
        if (sscanf(e->d_name, "mtd%u%c", &id, &rest) != 1)
            continue;
        char base[PATH_MAX], path[PATH_MAX], name[64] = {0};
        join(base, "/sys/class/mtd", e->d_name);
        join(path, base, "name");
        int f = open(path, O_RDONLY | O_CLOEXEC);
        if (f < 0)
            continue;
        ssize_t n = read(f, name, sizeof name - 1);
        close(f);
        if (n < 0)
            fail("MTD name");
        if (strcmp(name, "ota\n"))
            continue;
        require(sysnum(base, "size") == OTASIZE && sysnum(base, "offset") == OTAOFFSET &&
                    sysnum(base, "erasesize") == OTASIZE && sysnum(base, "writesize") == 2048,
                "ota geometry/offset differs");
        snprintf(dev, sizeof dev, "/dev/mtd%u", id);
        count++;
    }
    closedir(d);
    require(count == 1, "need exactly one correctly mapped ota partition");
    int f = open(dev, O_RDWR | O_NOFOLLOW | O_CLOEXEC);
    if (f < 0)
        fail(dev);
    struct stat st;
    if (fstat(f, &st))
        fail("mtd stat");
    require(S_ISCHR(st.st_mode), "ota must be an MTD character device");
    struct mtd_info_user m;
    if (ioctl(f, MEMGETINFO, &m))
        fail("MTD info");
    require(m.type == MTD_NANDFLASH && m.size == OTASIZE && m.erasesize == OTASIZE &&
                m.writesize == 2048 && (m.flags & MTD_WRITEABLE),
            "ota MTD geometry/type/permissions");
    loff_t zero = 0;
    require(ioctl(f, MEMGETBADBLOCK, &zero) == 0, "ota block is bad or cannot be checked");
    unsigned char old[2048], page[2048];
    read_at(f, old, sizeof old, 0);
    selector_page(page);
    int erased = 1;
    for (size_t i = 0; i < sizeof old; i++)
        if (old[i] != 255)
            erased = 0;
    require(erased || !memcmp(old, page, sizeof page), "unknown existing recovery selector");
    if (!commit) {
        close(f);
        return;
    }
    if (erased) {
        struct erase_info_user erase = {0, OTASIZE};
        if (ioctl(f, MEMERASE, &erase))
            fail("erase ota");
        write_all(f, page, sizeof page);
    }
    read_at(f, old, sizeof old, 0);
    require(!memcmp(old, page, sizeof page), "ota readback mismatch; use key recovery");
    close(f);
    sync();
    puts("Recovery selected; rebooting. Stock recovery may reset unmountable userdata.");
    fflush(stdout);
    if (reboot(RB_AUTOBOOT))
        fail("selector is armed but reboot failed; reboot or use key recovery");
#endif
}
int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: q2-hw-update check PACKAGE SHA256 | stage PACKAGE SHA256 CARD | "
                        "apply SHA256 CARD | status CARD\n");
        return 2;
    }
    if (!strcmp(argv[1], "check")) {
        require(argc == 4, "check PACKAGE SHA256");
        struct package p = inspect(argv[2], argv[3]);
        printf("Validated %s: kernel=%zu rootfs=%zu. SHA-256 %s\n", p.version, p.m[1].size,
               p.m[2].size, argv[3]);
        close(p.fd);
        return 0;
    }
    int staging = !strcmp(argv[1], "stage"), applying = !strcmp(argv[1], "apply"),
        status = !strcmp(argv[1], "status");
    require(staging || applying || status, "unknown command");
    require(argc == (staging ? 5 : applying ? 4 : 3), "incorrect arguments");
    const char *card = argv[argc - 1];
    card_check(card);
    char store[PATH_MAX], lockpath[PATH_MAX], final[PATH_MAX];
    join(store, card, ".q2-update");
    join(lockpath, card, ".q2-update.lock");
    join(final, card, "recovery-update");
    int lock = open(lockpath, O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB))
        fail("another update is active or lock is unsafe");
    if (status) {
        printf("Recovery staging: %s\nArchive store: %s\n",
               exists(final) ? "published (key recovery may apply it)" : "not published",
               exists(store) ? store : "absent");
        return 0;
    }
    const char *hash = argv[staging ? 3 : 2];
    require(hex64(hash), "expected trusted SHA-256");
    mkdir_safe(store);
    char release[PATH_MAX], archive[PATH_MAX], payload[PATH_MAX];
    join(release, store, hash);
    join(archive, release, "update.tar");
    join(payload, release, "recovery-update");
    if (staging) {
        require(!exists(final),
                "recovery-update already exists; inspect it before staging another update");
        struct package src = inspect(argv[2], hash);
        struct statvfs v;
        if (statvfs(card, &v))
            fail("free space");
        require((uint64_t)v.f_bavail * v.f_frsize >
                    (uint64_t)src.size + src.m[1].size + src.m[2].size + 4 * 1024 * 1024,
                "insufficient card space");
        if (exists(release)) {
            int r = dirfd_safe(release);
            close(r);
            struct package retained = inspect(archive, hash);
            close(src.fd);
            if (exists(payload)) {
                verify_payload(payload, &retained);
            } else {
                char temp[PATH_MAX], file[PATH_MAX];
                join(temp, release, ".restage-XXXXXX");
                if (!mkdtemp(temp))
                    fail("restage directory");
                join(file, temp, "xImage");
                copy_range(retained.fd, retained.m[1].offset, retained.m[1].size, file);
                join(file, temp, "rootfs.squashfs");
                copy_range(retained.fd, retained.m[2].offset, retained.m[2].size, file);
                verify_payload(temp, &retained);
                syncdir(temp);
                if (rename(temp, payload))
                    fail("publish restored private stage");
            }
            close(retained.fd);
            syncdir(payload);
            syncdir(release);
            syncdir(store);
            syncdir(card);
            puts("Retained package staged and verified; recovery NOT selected.");
            return 0;
        }
        char tmp[PATH_MAX], p[PATH_MAX];
        join(tmp, store, ".stage-XXXXXX");
        if (!mkdtemp(tmp))
            fail("stage directory");
        join(p, tmp, "update.tar");
        copy_range(src.fd, 0, src.size, p);
        close(src.fd);
        struct package copy = inspect(p, hash);
        char rec[PATH_MAX];
        join(rec, tmp, "recovery-update");
        mkdir_safe(rec);
        join(p, rec, "xImage");
        copy_range(copy.fd, copy.m[1].offset, copy.m[1].size, p);
        join(p, rec, "rootfs.squashfs");
        copy_range(copy.fd, copy.m[2].offset, copy.m[2].size, p);
        verify_payload(rec, &copy);
        close(copy.fd);
        syncdir(rec);
        syncdir(tmp);
        if (rename(tmp, release))
            fail("publish private stage");
        syncdir(store);
        syncdir(card);
        puts("Staged and synced. Recovery is NOT selected. To install, run apply with the same "
             "SHA-256 and card path.");
        return 0;
    }
    int r = dirfd_safe(release);
    close(r);
    struct package p = inspect(archive, hash);
    int f = exists(final);
    require(!(f && exists(payload)),
            "both private and published stages exist; inspect before applying");
    verify_payload(f ? final : payload, &p);
    close(p.fd);
    syncdir(f ? final : payload);
    syncdir(release);
    fprintf(stderr, "Applying via stock recovery. Unmountable userdata may be erased/reset. Main "
                    "kernel/rootfs will be replaced.\n");
    arm(card, 0); /* Refuse unavailable/unknown MTD before publishing the recovery directory. */
    if (!f && rename(payload, final))
        fail("publish recovery-update");
    syncdir(release);
    syncdir(card);
    arm(card, 1);
    return 0;
}
