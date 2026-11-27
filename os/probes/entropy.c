/* Emulator fixture only: credit fresh host-generated entropy supplied on stdin. */
#include <fcntl.h>
#include <linux/random.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <unistd.h>
int main(void)
{
    struct { int entropy_count, buf_size; uint32_t buf[16]; } seed = {512, 64, {0}};
    if (fread(seed.buf, 1, 64, stdin) != 64) return 2;
    int fd = open("/dev/random", O_RDWR);
    if (fd < 0 || ioctl(fd, RNDADDENTROPY, &seed) < 0) { perror("entropy fixture"); return 1; }
    close(fd);
    unsigned char check[16];
    if (getrandom(check, sizeof(check), GRND_NONBLOCK) != sizeof(check)) return 1;
    puts("Q2_ENTROPY_PASS fresh-host-fixture (not hardware RNG validation)");
    return 0;
}
