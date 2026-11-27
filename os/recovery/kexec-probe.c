/* Load-only SMP handoff probe. Never jumps into the supplied dummy segment. */
#define _GNU_SOURCE
#include <errno.h>
#include <linux/kexec.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void)
{
    unsigned char buffer[4096] = {0};
    struct kexec_segment segment = {
        .buf = buffer, .bufsz = sizeof(buffer),
        .mem = (void *)0x01000000, .memsz = sizeof(buffer),
    };
    /* Linux MIPS uses EM_MIPS even on little-endian kernels, not EM_MIPS_RS3_LE. */
    if (syscall(SYS_kexec_load, 0, 0, NULL, KEXEC_ARCH_MIPS)) {
        perror("native-architecture empty unload"); return 2;
    }
    puts("Q2_KEXEC_NATIVE_ARCH_ACCEPTED");
    long result = syscall(SYS_kexec_load, 0x01000000, 1, &segment, KEXEC_ARCH_MIPS);
    int error = errno;
    if (result == 0) {
        if (syscall(SYS_kexec_load, 0, 0, NULL, KEXEC_ARCH_MIPS)) return 2;
        puts("Q2_KEXEC_LOAD_ACCEPTED_AND_UNLOADED no kernel execution attempted");
        return 0;
    }
    printf("Q2_KEXEC_LOAD_REJECTED errno=%d %s\n", error, strerror(error));
    return error == EINVAL ? 0 : 1;
}
